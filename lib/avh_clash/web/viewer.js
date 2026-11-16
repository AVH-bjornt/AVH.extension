// The 3D side of the Clash Detector: a three.js scene that knows about
// elements, clashes and nothing about the page around it.
//
// Coordinates are Revit's, in metres from the run's origin, Z up.
//
// What is drawn:
//   context   every element sent, merged into one mesh per set (two draw
//             calls however many elements there are)
//   focus     the two elements of the chosen clash, each in its own
//             group so they can be pulled apart
//   volume    the space the two share, as Revit computed it
//   markers   a dot at every listed clash, drawn over everything
//
// It renders on demand: a frame is drawn when something changed or an
// animation is running, not sixty times a second inside Revit.

import * as THREE from 'three';
import { OrbitControls } from './vendor/OrbitControls.js';

const UP = new THREE.Vector3(0, 0, 1);
const HOME_DIRECTION = new THREE.Vector3(1, -1.25, 0.85).normalize();
const EDGE_LIMIT = 150000;          // triangles; past this, no outline
const AXES = ['x', 'y', 'z'];

const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);

function boxFrom(values) {
  return new THREE.Box3(
    new THREE.Vector3(values[0], values[1], values[2]),
    new THREE.Vector3(values[3], values[4], values[5]));
}

function dotTexture() {
  const size = 64;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const g = canvas.getContext('2d');
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2 - 2, 0, Math.PI * 2);
  g.fillStyle = '#9a9a9a';            // rim: the fill colour, darkened
  g.fill();
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2 - 9, 0, Math.PI * 2);
  g.fillStyle = '#ffffff';
  g.fill();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

function ringTexture() {
  const size = 96;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const g = canvas.getContext('2d');
  g.lineWidth = 9;
  g.strokeStyle = '#ffffff';
  g.beginPath();
  g.arc(size / 2, size / 2, size / 2 - 8, 0, Math.PI * 2);
  g.stroke();
  const texture = new THREE.CanvasTexture(canvas);
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

export class Viewer {
  constructor(canvas, callbacks = {}) {
    this.canvas = canvas;
    this.callbacks = callbacks;
    this.reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.localClippingEnabled = true;

    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(42, 1, 0.05, 5000);
    this.camera.up.copy(UP);
    this.camera.position.copy(HOME_DIRECTION).multiplyScalar(30);
    this.scene.add(this.camera);

    this.sky = new THREE.HemisphereLight(0xffffff, 0x8a8f96, 1.15);
    this.sky.position.copy(UP);
    this.scene.add(this.sky);
    this.headlight = new THREE.DirectionalLight(0xffffff, 1.35);
    this.headlight.position.set(0.6, 0.9, 1);
    this.headlight.target.position.set(0, 0, -1);
    this.camera.add(this.headlight);
    this.camera.add(this.headlight.target);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.12;
    this.controls.autoRotateSpeed = 1.1;
    this.controls.addEventListener('start', () => { this.flight = null; });

    this.cutPlane = new THREE.Plane(new THREE.Vector3(0, 0, -1), 0);
    this.colors = {
      a: new THREE.Color('#1f6fb2'), b: new THREE.Color('#d08a1e'),
      clash: new THREE.Color('#d8332a'), done: new THREE.Color('#7d8b96'),
      ink: new THREE.Color('#14222e'), stage: new THREE.Color('#e4e9ee'),
    };

    this.materials = {
      contextA: this.contextMaterial(this.colors.a),
      contextB: this.contextMaterial(this.colors.b),
      focusA: this.focusMaterial(this.colors.a),
      focusB: this.focusMaterial(this.colors.b),
      edgeA: new THREE.LineBasicMaterial({ color: this.colors.a }),
      edgeB: new THREE.LineBasicMaterial({ color: this.colors.b }),
      volume: new THREE.MeshBasicMaterial({
        color: this.colors.clash, side: THREE.DoubleSide,
        transparent: true, opacity: 0.92 }),
      volumeEdge: new THREE.LineBasicMaterial({ color: 0x6d120d }),
      outline: new THREE.LineBasicMaterial({ color: this.colors.clash }),
      markers: new THREE.PointsMaterial({
        size: 13, sizeAttenuation: false, map: dotTexture(),
        alphaTest: 0.5, vertexColors: true, depthTest: false,
        transparent: true }),
      ring: new THREE.PointsMaterial({
        size: 30, sizeAttenuation: false, map: ringTexture(),
        alphaTest: 0.2, depthTest: false, transparent: true,
        color: this.colors.clash }),
    };

    this.elements = new Map();        // key -> {pos, idx, box, set}
    this.volumes = new Map();         // clash number -> {pos, idx}
    this.clashes = [];
    this.context = { a: null, b: null };
    this.layers = { a: true, b: true, volume: true, markers: true, xray: true };
    this.contextMode = 'ghost';
    this.focused = null;
    this.focusParts = null;
    this.pull = 0;
    this.cut = 0;
    this.cutAxis = 2;
    this.sceneBox = new THREE.Box3();
    this.sceneRadius = 20;
    this.markerList = [];
    this.markerPoints = null;
    this.ring = null;
    this.flight = null;
    this.pulseUntil = 0;
    this.dirty = true;

    this.raycaster = new THREE.Raycaster();
    this.bindPointer();
    new ResizeObserver(() => this.resize()).observe(canvas.parentElement);
    this.resize();
    this.applyBackground();
    this.loop = this.loop.bind(this);
    requestAnimationFrame(this.loop);
  }

  // -- materials and theme ------------------------------------------------

  contextMaterial(color) {
    return new THREE.MeshLambertMaterial({
      color, side: THREE.DoubleSide, transparent: true, opacity: 0.12,
      depthWrite: false, clippingPlanes: [] });
  }

  focusMaterial(color) {
    return new THREE.MeshPhongMaterial({
      color, side: THREE.DoubleSide, shininess: 18, transparent: true,
      opacity: 0.42, depthWrite: false, clippingPlanes: [] });
  }

  setTheme(theme) {
    for (const name of Object.keys(theme)) {
      if (this.colors[name]) this.colors[name].set(theme[name]);
    }
    const m = this.materials;
    m.contextA.color.copy(this.colors.a);
    m.contextB.color.copy(this.colors.b);
    m.focusA.color.copy(this.colors.a);
    m.focusB.color.copy(this.colors.b);
    m.edgeA.color.copy(this.colors.a).lerp(this.colors.ink, 0.45);
    m.edgeB.color.copy(this.colors.b).lerp(this.colors.ink, 0.45);
    m.volume.color.copy(this.colors.clash);
    m.outline.color.copy(this.colors.clash);
    m.ring.color.copy(this.colors.clash);
    this.applyBackground();
    this.paintMarkers();
    this.invalidate();
  }

  applyBackground() {
    this.scene.background = this.colors.stage.clone();
  }

  // -- data in ------------------------------------------------------------

  clear() {
    this.dropFocus();
    for (const side of ['a', 'b']) {
      const part = this.context[side];
      if (part) {
        this.scene.remove(part.mesh);
        part.mesh.geometry.dispose();
      }
      this.context[side] = null;
    }
    if (this.markerPoints) {
      this.scene.remove(this.markerPoints);
      this.markerPoints.geometry.dispose();
      this.markerPoints = null;
    }
    this.elements.clear();
    this.volumes.clear();
    this.clashes = [];
    this.markerList = [];
    this.focused = null;
    this.invalidate();
  }

  addElementMesh(key, pos, idx) {
    this.elements.set(key, { pos, idx, box: null, set: 0 });
  }

  addVolumeMesh(number, pos, idx) {
    this.volumes.set(number, { pos, idx });
  }

  // Called once everything has arrived.
  build(elementMeta, clashes) {
    this.clashes = clashes;
    for (const meta of elementMeta) {
      const element = this.elements.get(meta.key);
      if (element) element.set = meta.set;
    }
    this.sceneBox.makeEmpty();
    const point = new THREE.Vector3();
    for (const element of this.elements.values()) {
      const box = new THREE.Box3();
      const pos = element.pos;
      for (let i = 0; i < pos.length; i += 3) {
        box.expandByPoint(point.set(pos[i], pos[i + 1], pos[i + 2]));
      }
      element.box = box;
      this.sceneBox.union(box);
    }
    for (const clash of clashes) this.sceneBox.union(boxFrom(clash.box));
    if (this.sceneBox.isEmpty()) {
      this.sceneBox.set(new THREE.Vector3(-5, -5, -5), new THREE.Vector3(5, 5, 5));
    }
    this.sceneRadius = Math.max(
      this.sceneBox.getSize(new THREE.Vector3()).length() / 2, 1);

    this.context.a = this.mergeSide((set) => (set & 1) === 1, this.materials.contextA);
    this.context.b = this.mergeSide((set) => set === 2, this.materials.contextB);
    this.applyContextMode();
    this.applyLayers();
    this.fitAll(false);
  }

  mergeSide(belongs, material) {
    let vertices = 0;
    let indices = 0;
    const members = [];
    for (const [key, element] of this.elements) {
      if (!belongs(element.set) || !element.idx.length) continue;
      members.push([key, element]);
      vertices += element.pos.length;
      indices += element.idx.length;
    }
    if (!members.length) return null;
    const pos = new Float32Array(vertices);
    const idx = new Uint32Array(indices);
    const ranges = [];
    let v = 0;
    let i = 0;
    for (const [key, element] of members) {
      pos.set(element.pos, v);
      const base = v / 3;
      const source = element.idx;
      for (let n = 0; n < source.length; n++) idx[i + n] = source[n] + base;
      ranges.push({ key, start: i, count: source.length });
      v += element.pos.length;
      i += source.length;
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    geometry.setIndex(new THREE.BufferAttribute(idx, 1));
    geometry.computeVertexNormals();
    geometry.addGroup(0, idx.length, 0);
    const mesh = new THREE.Mesh(geometry, [material]);
    mesh.renderOrder = 1;
    this.scene.add(mesh);
    const byKey = new Map(ranges.map((range) => [range.key, range]));
    return { mesh, ranges, byKey, total: idx.length };
  }

  // Leave the focused elements out of the merged meshes, so they are
  // not drawn twice when they are drawn on their own.
  excludeFromContext(keys) {
    for (const side of ['a', 'b']) {
      const part = this.context[side];
      if (!part) continue;
      const holes = keys.map((key) => part.byKey.get(key)).filter(Boolean)
        .sort((p, q) => p.start - q.start);
      const geometry = part.mesh.geometry;
      geometry.clearGroups();
      let at = 0;
      for (const hole of holes) {
        if (hole.start > at) geometry.addGroup(at, hole.start - at, 0);
        at = hole.start + hole.count;
      }
      if (part.total > at) geometry.addGroup(at, part.total - at, 0);
    }
  }

  // -- markers ------------------------------------------------------------

  // `list` is [{n, c, reviewed}] for the clashes the list currently shows.
  setMarkers(list) {
    this.markerList = list;
    if (this.markerPoints) {
      this.scene.remove(this.markerPoints);
      this.markerPoints.geometry.dispose();
      this.markerPoints = null;
    }
    if (list.length) {
      const pos = new Float32Array(list.length * 3);
      list.forEach((entry, n) => pos.set(entry.c, n * 3));
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(pos, 3));
      geometry.setAttribute('color',
        new THREE.BufferAttribute(new Float32Array(list.length * 3), 3));
      this.markerPoints = new THREE.Points(geometry, this.materials.markers);
      this.markerPoints.renderOrder = 20;
      this.markerPoints.frustumCulled = false;
      this.markerPoints.visible = this.layers.markers;
      this.scene.add(this.markerPoints);
      this.paintMarkers();
    }
    this.invalidate();
  }

  paintMarkers() {
    if (!this.markerPoints) return;
    const colors = this.markerPoints.geometry.getAttribute('color');
    this.markerList.forEach((entry, n) => {
      const color = entry.reviewed ? this.colors.done : this.colors.clash;
      colors.setXYZ(n, color.r, color.g, color.b);
    });
    colors.needsUpdate = true;
    this.invalidate();
  }

  markerAt(x, y, reach = 11) {
    const rect = this.canvas.getBoundingClientRect();
    const v = new THREE.Vector3();
    let best = null;
    let bestDistance = reach * reach;
    for (const entry of this.markerList) {
      v.set(entry.c[0], entry.c[1], entry.c[2]).project(this.camera);
      if (v.z < -1 || v.z > 1) continue;
      const px = (v.x * 0.5 + 0.5) * rect.width;
      const py = (-v.y * 0.5 + 0.5) * rect.height;
      const d = (px - x) * (px - x) + (py - y) * (py - y);
      if (d < bestDistance) {
        bestDistance = d;
        best = entry;
      }
    }
    return best;
  }

  // -- focus on one clash ---------------------------------------------------

  elementGeometry(key) {
    const element = this.elements.get(key);
    if (!element || !element.idx.length) return null;
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute('position', new THREE.BufferAttribute(element.pos, 3));
    geometry.setIndex(new THREE.BufferAttribute(element.idx, 1));
    geometry.computeVertexNormals();
    return geometry;
  }

  focusGroup(key, material, edgeMaterial) {
    const group = new THREE.Group();
    const geometry = this.elementGeometry(key);
    if (geometry) {
      const mesh = new THREE.Mesh(geometry, material);
      mesh.renderOrder = 3;
      mesh.userData.key = key;
      group.add(mesh);
      if (geometry.index.count / 3 <= EDGE_LIMIT) {
        const edges = new THREE.LineSegments(
          new THREE.EdgesGeometry(geometry, 28), edgeMaterial);
        edges.renderOrder = 4;
        group.add(edges);
      }
    }
    this.scene.add(group);
    return group;
  }

  dropFocus() {
    if (!this.focusParts) return;
    for (const object of Object.values(this.focusParts.objects)) {
      if (!object) continue;
      this.scene.remove(object);
      object.traverse((child) => { if (child.geometry) child.geometry.dispose(); });
    }
    this.focusParts = null;
    this.excludeFromContext([]);
  }

  focus(clash, fly = true) {
    this.dropFocus();
    this.focused = clash;
    if (!clash) {
      this.applyCut();
      this.invalidate();
      return;
    }
    const groupA = this.focusGroup(clash.a, this.materials.focusA, this.materials.edgeA);
    const groupB = this.focusGroup(clash.b, this.materials.focusB, this.materials.edgeB);

    const box = boxFrom(clash.box);
    let volume = null;
    const data = this.volumes.get(clash.n);
    if (data) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.BufferAttribute(data.pos, 3));
      geometry.setIndex(new THREE.BufferAttribute(data.idx, 1));
      volume = new THREE.Group();
      const mesh = new THREE.Mesh(geometry, this.materials.volume);
      mesh.renderOrder = 2;
      volume.add(mesh);
      const edges = new THREE.LineSegments(
        new THREE.EdgesGeometry(geometry, 28), this.materials.volumeEdge);
      edges.renderOrder = 5;
      volume.add(edges);
    } else {
      // No shared volume from Revit: outline where the surfaces cross.
      const shown = box.clone();
      const least = Math.max(shown.getSize(new THREE.Vector3()).length() * 0.04, 0.01);
      shown.expandByScalar(least);
      volume = new THREE.Box3Helper(shown, this.colors.clash);
      volume.material = this.materials.outline;
      volume.renderOrder = 5;
    }
    this.scene.add(volume);

    const ringGeometry = new THREE.BufferGeometry();
    ringGeometry.setAttribute('position',
      new THREE.BufferAttribute(new Float32Array(clash.c), 3));
    const ring = new THREE.Points(ringGeometry, this.materials.ring);
    ring.renderOrder = 21;
    ring.frustumCulled = false;
    this.scene.add(ring);

    // Which way to pull the two apart: from the middle of one to the
    // middle of the other, or across the thin way of the overlap when
    // one sits around the other.
    const elementA = this.elements.get(clash.a);
    const elementB = this.elements.get(clash.b);
    const size = box.getSize(new THREE.Vector3());
    const direction = new THREE.Vector3();
    let reach = Math.max(size.length() * 2, 0.3);
    if (elementA && elementB && elementA.box && elementB.box) {
      const ca = elementA.box.getCenter(new THREE.Vector3());
      const cb = elementB.box.getCenter(new THREE.Vector3());
      direction.subVectors(cb, ca);
      const da = elementA.box.getSize(new THREE.Vector3()).length();
      const db = elementB.box.getSize(new THREE.Vector3()).length();
      reach += Math.min(da, db) * 0.2;
    }
    if (direction.length() < size.length() * 0.05) {
      const thin = size.x <= size.y && size.x <= size.z ? 0
        : (size.y <= size.z ? 1 : 2);
      direction.set(thin === 0 ? 1 : 0, thin === 1 ? 1 : 0, thin === 2 ? 1 : 0);
    }
    direction.normalize();

    this.focusParts = {
      objects: { a: groupA, b: groupB, volume, ring },
      box, direction, reach,
    };
    this.excludeFromContext([clash.a, clash.b]);
    this.applyLayers();
    this.applyPull();
    this.applyCut();
    this.pulseUntil = this.reducedMotion ? 0 : performance.now() + 2400;
    if (fly) this.fitClash();
    this.invalidate();
  }

  // -- view controls --------------------------------------------------------

  setLayer(name, on) {
    this.layers[name] = on;
    this.applyLayers();
  }

  applyLayers() {
    const { a, b, volume, markers, xray } = this.layers;
    if (this.context.a) this.context.a.mesh.visible = a && this.contextMode !== 'off';
    if (this.context.b) this.context.b.mesh.visible = b && this.contextMode !== 'off';
    if (this.markerPoints) this.markerPoints.visible = markers;
    for (const material of [this.materials.focusA, this.materials.focusB]) {
      material.transparent = xray;
      material.opacity = xray ? 0.42 : 1;
      material.depthWrite = !xray;
      material.needsUpdate = true;
    }
    if (this.focusParts) {
      const objects = this.focusParts.objects;
      objects.a.visible = a;
      objects.b.visible = b;
      objects.volume.visible = volume;
      objects.ring.visible = markers;
    }
    this.invalidate();
  }

  setContextMode(mode) {
    this.contextMode = mode;
    this.applyContextMode();
    this.applyLayers();
  }

  applyContextMode() {
    const solid = this.contextMode === 'solid';
    for (const material of [this.materials.contextA, this.materials.contextB]) {
      material.transparent = !solid;
      material.opacity = solid ? 1 : 0.12;
      material.depthWrite = solid;
      material.needsUpdate = true;
    }
  }

  setPull(amount) {
    this.pull = amount;
    this.applyPull();
  }

  applyPull() {
    if (!this.focusParts) return;
    const { objects, direction, reach } = this.focusParts;
    const half = this.pull * reach * 0.5;
    objects.a.position.copy(direction).multiplyScalar(-half);
    objects.b.position.copy(direction).multiplyScalar(half);
    this.invalidate();
  }

  // amount 0 switches the cut off; towards 1 it moves through the clash.
  setCut(amount, axis = this.cutAxis) {
    this.cut = amount;
    this.cutAxis = axis;
    this.applyCut();
  }

  applyCut() {
    const cuttable = [this.materials.contextA, this.materials.contextB,
      this.materials.focusA, this.materials.focusB,
      this.materials.edgeA, this.materials.edgeB];
    const active = this.cut > 0 && this.focusParts;
    if (active) {
      const box = this.focusParts.box;
      const axis = AXES[this.cutAxis];
      const span = box.max[axis] - box.min[axis];
      const margin = Math.max(span * 0.75, 0.15);
      const at = box.max[axis] + margin - this.cut * (span + 2 * margin);
      this.cutPlane.normal.set(0, 0, 0);
      this.cutPlane.normal[axis] = -1;
      this.cutPlane.constant = at;
    }
    for (const material of cuttable) {
      material.clippingPlanes = active ? [this.cutPlane] : [];
      material.needsUpdate = true;
    }
    this.invalidate();
  }

  setOrbit(on) {
    this.controls.autoRotate = on;
    this.invalidate();
  }

  flyTo(target, radius, animate = true) {
    const direction = new THREE.Vector3()
      .subVectors(this.camera.position, this.controls.target);
    if (direction.lengthSq() < 1e-9) direction.copy(HOME_DIRECTION);
    direction.normalize();
    // Looking straight down the up axis leaves orbiting nowhere to go.
    if (Math.abs(direction.dot(UP)) > 0.985) direction.copy(HOME_DIRECTION);
    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    const narrow = Math.min(1, this.camera.aspect);
    const distance = radius / Math.sin(fov / 2) / narrow;
    const position = target.clone().addScaledVector(direction, distance);
    if (!animate || this.reducedMotion) {
      this.camera.position.copy(position);
      this.controls.target.copy(target);
      this.flight = null;
    } else {
      this.flight = {
        start: performance.now(), length: 650,
        fromPosition: this.camera.position.clone(), toPosition: position,
        fromTarget: this.controls.target.clone(), toTarget: target.clone(),
      };
    }
    this.invalidate();
  }

  fitAll(animate = true) {
    const target = this.sceneBox.getCenter(new THREE.Vector3());
    this.flyTo(target, this.sceneRadius * 1.05, animate);
  }

  fitClash(animate = true) {
    if (!this.focusParts) return this.fitAll(animate);
    const clash = this.focused;
    const box = this.focusParts.box;
    const own = box.getSize(new THREE.Vector3()).length() / 2;
    let radius = Math.max(own * 3.2, 0.6);
    const a = this.elements.get(clash.a);
    const b = this.elements.get(clash.b);
    if (a && b && a.box && b.box) {
      const smaller = Math.min(
        a.box.getSize(new THREE.Vector3()).length(),
        b.box.getSize(new THREE.Vector3()).length()) / 2;
      radius = Math.max(radius, Math.min(smaller * 1.15, own * 9 + 1.5));
    }
    return this.flyTo(box.getCenter(new THREE.Vector3()), radius, animate);
  }

  // -- pointer --------------------------------------------------------------

  bindPointer() {
    let down = null;
    this.canvas.addEventListener('pointerdown', (event) => {
      down = { x: event.clientX, y: event.clientY };
    });
    this.canvas.addEventListener('pointerup', (event) => {
      if (!down) return;
      const moved = Math.hypot(event.clientX - down.x, event.clientY - down.y);
      down = null;
      if (moved < 4 && event.button === 0) this.pick(event);
    });
    let pending = false;
    this.canvas.addEventListener('pointermove', (event) => {
      if (pending || event.buttons) return;
      pending = true;
      requestAnimationFrame(() => {
        pending = false;
        const rect = this.canvas.getBoundingClientRect();
        const entry = this.layers.markers
          ? this.markerAt(event.clientX - rect.left, event.clientY - rect.top) : null;
        this.canvas.style.cursor = entry ? 'pointer' : '';
        if (this.callbacks.onHover) {
          this.callbacks.onHover(entry ? entry.n : null, event.clientX, event.clientY);
        }
      });
    });
    this.canvas.addEventListener('pointerleave', () => {
      if (this.callbacks.onHover) this.callbacks.onHover(null, 0, 0);
    });
  }

  pick(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (this.layers.markers) {
      const entry = this.markerAt(x, y);
      if (entry) {
        if (this.callbacks.onPickClash) this.callbacks.onPickClash(entry.n);
        return;
      }
    }
    const pointer = new THREE.Vector2(
      (x / rect.width) * 2 - 1, -(y / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(pointer, this.camera);
    const targets = [];
    for (const side of ['a', 'b']) {
      const part = this.context[side];
      if (part && part.mesh.visible) targets.push(part.mesh);
    }
    if (this.focusParts) {
      for (const name of ['a', 'b']) {
        const group = this.focusParts.objects[name];
        if (group.visible && group.children[0]) targets.push(group.children[0]);
      }
    }
    const hit = this.raycaster.intersectObjects(targets, false)[0];
    let key = null;
    if (hit) {
      key = hit.object.userData.key || this.keyOfFace(hit.object, hit.faceIndex);
    }
    if (this.callbacks.onPickElement) this.callbacks.onPickElement(key);
  }

  keyOfFace(mesh, faceIndex) {
    const part = this.context.a && this.context.a.mesh === mesh
      ? this.context.a : this.context.b;
    if (!part) return null;
    const at = faceIndex * 3;
    let low = 0;
    let high = part.ranges.length - 1;
    while (low <= high) {
      const mid = (low + high) >> 1;
      const range = part.ranges[mid];
      if (at < range.start) high = mid - 1;
      else if (at >= range.start + range.count) low = mid + 1;
      else return range.key;
    }
    return null;
  }

  // -- frame ------------------------------------------------------------------

  resize() {
    const parent = this.canvas.parentElement;
    const width = Math.max(parent.clientWidth, 1);
    const height = Math.max(parent.clientHeight, 1);
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.invalidate();
  }

  invalidate() {
    this.dirty = true;
  }

  loop(now) {
    requestAnimationFrame(this.loop);
    let animating = false;

    if (this.flight) {
      const t = Math.min((now - this.flight.start) / this.flight.length, 1);
      const k = ease(t);
      this.camera.position.lerpVectors(this.flight.fromPosition, this.flight.toPosition, k);
      this.controls.target.lerpVectors(this.flight.fromTarget, this.flight.toTarget, k);
      if (t >= 1) this.flight = null;
      animating = true;
    }

    if (this.focusParts && now < this.pulseUntil) {
      const left = (this.pulseUntil - now) / 2400;
      const beat = 0.5 + 0.5 * Math.sin((1 - left) * Math.PI * 6 - Math.PI / 2);
      this.materials.ring.size = 30 + 16 * beat * left;
      this.materials.volume.opacity = 0.92 - 0.3 * beat * left;
      animating = true;
    } else if (this.materials.ring.size !== 30) {
      this.materials.ring.size = 30;
      this.materials.volume.opacity = 0.92;
      animating = true;
    }

    if (this.controls.autoRotate) animating = true;
    const moved = this.controls.update();
    if (!moved && !animating && !this.dirty) return;
    this.dirty = false;

    const distance = this.camera.position.distanceTo(this.controls.target);
    this.camera.near = Math.max(distance / 250, 0.004);
    this.camera.far = distance + this.sceneRadius * 4 + 200;
    this.camera.updateProjectionMatrix();
    this.renderer.render(this.scene, this.camera);
  }
}
