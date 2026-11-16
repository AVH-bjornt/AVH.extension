// A stand in for Revit, loaded only when the page is opened in an
// ordinary browser (no WebView2 host). It answers the same messages
// with a small made up building, so the viewer can be worked on and
// checked without Revit. Nothing here runs inside Revit.

function encode(array) {
  const bytes = new Uint8Array(array.buffer);
  let text = '';
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}

// Each face gets its own vertices, as Revit's Face.Triangulate gives.
function box(x0, y0, z0, x1, y1, z1) {
  const corners = [
    [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
    [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1]];
  const faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4],
    [1, 2, 6, 5], [2, 3, 7, 6], [3, 0, 4, 7]];
  const pos = [];
  const idx = [];
  for (const face of faces) {
    const base = pos.length / 3;
    for (const corner of face) pos.push(...corners[corner]);
    idx.push(base, base + 1, base + 2, base, base + 2, base + 3);
  }
  return { pos, idx, box: [x0, y0, z0, x1, y1, z1] };
}

// A cylinder along x (axis 0) or y (axis 1).
function pipe(axis, from, to, u, z, radius, sides = 20) {
  const pos = [];
  const idx = [];
  const point = (along, angle) => {
    const p = u + Math.cos(angle) * radius;
    const q = z + Math.sin(angle) * radius;
    return axis === 0 ? [along, p, q] : [p, along, q];
  };
  for (let n = 0; n <= sides; n++) {
    const angle = (n / sides) * Math.PI * 2;
    pos.push(...point(from, angle), ...point(to, angle));
  }
  for (let n = 0; n < sides; n++) {
    const a = n * 2;
    idx.push(a, a + 1, a + 3, a, a + 3, a + 2);
  }
  for (const end of [from, to]) {
    const base = pos.length / 3;
    pos.push(...(axis === 0 ? [end, u, z] : [u, end, z]));
    for (let n = 0; n <= sides; n++) pos.push(...point(end, (n / sides) * Math.PI * 2));
    for (let n = 0; n < sides; n++) idx.push(base, base + 1 + n, base + 2 + n);
  }
  const low = axis === 0 ? [from, u - radius, z - radius] : [u - radius, from, z - radius];
  const high = axis === 0 ? [to, u + radius, z + radius] : [u + radius, to, z + radius];
  return { pos, idx, box: [...low, ...high] };
}

function building() {
  const elements = [];
  const add = (src, id, cat, name, set, mesh, shape) => {
    elements.push({ key: `${src}:${id}`, src, id, cat, name, set, mesh, shape });
  };
  let id = 310400;
  for (let floor = 0; floor < 3; floor++) {
    const z = floor * 3.4;
    add('host', id++, 'Floors', 'Steypt plata 250', 1, box(-12, -8, z - 0.25, 12, 8, z));
    for (const y of [-8, 0, 7.8]) {
      add('host', id++, 'Walls', 'Steyptur veggur 200', 1, box(-12, y, z, 12, y + 0.2, z + 3.15));
    }
    for (const x of [-12, -4, 4, 11.8]) {
      add('host', id++, 'Walls', 'Steyptur veggur 200', 1, box(x, -8, z, x + 0.2, 8, z + 3.15));
    }
    for (const x of [-8, 0, 8]) {
      add('host', id++, 'Structural Columns', 'Sula 400x400', 1,
        box(x - 0.2, -4.2, z, x + 0.2, -3.8, z + 3.15));
    }
    // Services from the linked model, run through the structure.
    const top = z + 2.75;
    add('link:901', 770100 + floor * 10, 'Ducts', 'Rectangular Duct 600x300', 2,
      box(-11, -4.3, top - 0.15, 11, -3.7, top + 0.15), { kind: 'box' });
    add('link:901', 770101 + floor * 10, 'Pipes', 'Pipe DN100', 2,
      pipe(0, -11.5, 11.5, 3.2, top - 0.3, 0.057), { kind: 'pipe', axis: 0, u: 3.2, z: top - 0.3, r: 0.057 });
    add('link:901', 770102 + floor * 10, 'Pipes', 'Pipe DN50', 2,
      pipe(1, -7.5, 7.5, -6.5, top - 0.5, 0.03), { kind: 'pipe', axis: 1, u: -6.5, z: top - 0.5, r: 0.03 });
    add('link:901', 770103 + floor * 10, 'Cable Trays', 'Cable Tray 300x60', 2,
      box(6.5, -7.5, top + 0.2, 6.8, 7.5, top + 0.26), { kind: 'box' });
  }
  add('link:901', 770900, 'Pipes', 'Riser DN150', 2, box(9.6, 5.1, -0.4, 9.76, 5.26, 10.2), { kind: 'box' });
  // A structural link: one beam per floor, across the ducts.
  for (let floor = 0; floor < 3; floor++) {
    const z = floor * 3.4 + 2.55;
    add('link:902', 880100 + floor, 'Structural Framing', 'HEA 300', 2,
      box(-2.15, -7.9, z, -1.85, 7.9, z + 0.29), { kind: 'box' });
  }
  return elements;
}

const CATEGORY_IDS = { Floors: -2000032, Walls: -2000011, 'Structural Columns': -2001330,
  Ducts: -2008000, Pipes: -2008044, 'Cable Trays': -2008130, 'Structural Framing': -2001320 };
const catId = (name) => CATEGORY_IDS[name];
const GROUPS = { Floors: 'Architecture', Walls: 'Architecture', 'Structural Columns': 'Structure',
  'Structural Framing': 'Structure', Ducts: 'MEP - Mechanical', Pipes: 'MEP - Plumbing',
  'Cable Trays': 'MEP - Electrical' };

function overlap(p, q) {
  const o = [Math.max(p[0], q[0]), Math.max(p[1], q[1]), Math.max(p[2], q[2]),
    Math.min(p[3], q[3]), Math.min(p[4], q[4]), Math.min(p[5], q[5])];
  return (o[0] < o[3] - 1e-5 && o[1] < o[4] - 1e-5 && o[2] < o[5] - 1e-5) ? o : null;
}

// Open the page with ?noinit to see what happens when Revit never
// sends the list of models.
const SILENT = new URLSearchParams(location.search).has('noinit');

export function connect(onMessage) {
  const send = (message, delay = 0) => setTimeout(() => onMessage(message), delay);
  const elements = building();
  const count = (src) => {
    const cats = new Map();
    for (const one of elements) {
      if (one.src !== src) continue;
      cats.set(one.cat, (cats.get(one.cat) || 0) + 1);
    }
    return [...cats].map(([name, n]) => ({ id: catId(name), name, count: n, group: GROUPS[name] }));
  };
  // The selection a refresh pretends Revit has: two walls on the ground floor.
  const selected = new Set([310401, 310402]);
  const makeInit = (withSelection) => ({
    type: 'init', version: 'demo', model: 'Demo model (not Revit)',
    view: { name: '{3D}', usable: true, is3d: true, sectionBox: false },
    sources: [
      ...(withSelection ? [{ key: 'selection', label: 'Current selection', kind: 'selection',
        cats: [{ id: catId('Walls'), name: 'Walls', count: selected.size, group: GROUPS.Walls }] }] : []),
      { key: 'host', label: 'A-Building.rvt', kind: 'host', cats: count('host') },
      { key: 'link:901', label: 'M-Services.rvt', kind: 'link', cats: count('link:901') },
      { key: 'link:902', label: 'S-Structure.rvt', kind: 'link', cats: count('link:902') },
    ],
    groups: ['Architecture', 'Interior', 'Structure', 'MEP - Mechanical', 'MEP - Plumbing',
      'MEP - Electrical', 'Site', 'Other'],
  });

  // Which elements a set takes, as the service decides it.
  function members(entries) {
    const found = new Set();
    for (const entry of entries || []) {
      const cats = new Set(entry.cats);
      for (const one of elements) {
        const from = entry.source === 'selection'
          ? one.src === 'host' && selected.has(one.id) : one.src === entry.source;
        if (from && cats.has(catId(one.cat))) found.add(one.key);
      }
    }
    return found;
  }

  function run(config) {
    const inA = members(config.a);
    const inB = members(config.b);
    for (const one of elements) one.set = (inA.has(one.key) ? 1 : 0) | (inB.has(one.key) ? 2 : 0);
    const setA = elements.filter((one) => one.set & 1);
    const setB = elements.filter((one) => one.set & 2);
    if (!setA.length || !setB.length) {
      const tally = (entries, n) => ({ entries: (entries || []).length,
        categories: (entries || []).reduce((sum, e) => sum + e.cats.length, 0),
        looked: elements.length, matched: n, nobox: 0, clipped: 0, unknown: [] });
      send({ type: 'result', clashes: [], elements: [], levels: [],
        stats: { a: setA.length, b: setB.length, pairs: 0, tested: 0, clipped: 0, failed: 0,
          unreadable: 0, cancelled: false, contextCut: 0, seconds: 0.1 },
        empty: setA.length ? 'B' : 'A',
        why: { scope: config.scope, a: tally(config.a, setA.length), b: tally(config.b, setB.length) } }, 200);
      return;
    }
    const clashes = [];
    const meshes = [];
    const seen = new Set();
    for (const a of setA) {
      for (const b of setB) {
        if (a === b || seen.has(`${b.key}|${a.key}`)) continue;
        seen.add(`${a.key}|${b.key}`);
        const shared = overlap(a.mesh.box, b.mesh.box);
        if (!shared) continue;
        const n = clashes.length;
        const size = [shared[3] - shared[0], shared[4] - shared[1], shared[5] - shared[2]];
        // Every seventh one stands in for a mesh only (approximate) clash.
        const exact = n % 7 !== 5;
        let volume = size[0] * size[1] * size[2];
        if (exact) {
          const shape = b.shape || { kind: 'box' };
          let mesh;
          if (shape.kind === 'pipe') {
            const along = shape.axis;
            mesh = pipe(along, shared[along], shared[along + 3], shape.u, shape.z, shape.r);
            volume = Math.PI * shape.r * shape.r * size[along];
          } else {
            mesh = box(...shared);
          }
          meshes.push({ kind: 'c', key: n, mesh });
        }
        clashes.push({
          n, a: a.key, b: b.key, vol: exact ? volume : null,
          depth: exact ? Math.min(...size) : null,
          c: [(shared[0] + shared[3]) / 2, (shared[1] + shared[4]) / 2, (shared[2] + shared[5]) / 2],
          box: shared, exact, partial: false });
      }
    }
    for (const one of elements) meshes.push({ kind: 'e', key: one.key, mesh: one.mesh });

    let delay = 0;
    const stages = ['Collecting elements', 'Finding candidate pairs', 'Testing pairs', 'Sending geometry'];
    stages.forEach((stage, at) => {
      for (let step = 0; step < 5; step++) {
        send({ type: 'progress', stage, done: at === 2 ? step * 60 : null, total: at === 2 ? 300 : null }, delay);
        delay += 70;
      }
    });
    for (let at = 0; at < meshes.length; at += 12) {
      const part = meshes.slice(at, at + 12);
      const pos = [];
      const idx = [];
      const items = part.map(({ kind, key, mesh }) => {
        const item = { kind, key, v: [pos.length, mesh.pos.length], i: [idx.length, mesh.idx.length] };
        pos.push(...mesh.pos);
        idx.push(...mesh.idx);
        return item;
      });
      send({ type: 'mesh', items, pos: encode(new Float32Array(pos)), idx: encode(new Uint32Array(idx)) }, delay);
    }
    send({
      type: 'result', clashes,
      elements: elements.map(({ key, src, id, cat, name, set }) => ({ key, src, id, cat, name, set })),
      levels: [{ name: '00 Jarðhæð', z: 0 }, { name: '01 Hæð', z: 3.4 }, { name: '02 Hæð', z: 6.8 }],
      stats: { a: setA.length, b: setB.length, pairs: 300, tested: 300, clipped: 0, failed: 0,
        unreadable: 0, cancelled: false, contextCut: 0, seconds: 1.4 },
    }, delay + 60);
  }

  return (message) => {
    if (message.type === 'ready') {
      if (!SILENT) send(makeInit(false), 30);
    } else if (message.type === 'refresh') {
      send({ ...makeInit(Boolean(message.useSelection)), refreshed: true,
        useSelection: Boolean(message.useSelection) }, 150);
    } else if (message.type === 'log') {
      console.warn('logged to pyRevit:', message.message);
    } else if (message.type === 'run') run(message.config);
    else if (message.type === 'select') send({ type: 'notice', kind: 'ok', text: 'Demo: this would select the pair in Revit.' });
    else if (message.type === 'sectionbox') send({ type: 'notice', kind: 'ok', text: 'Demo: this would set the section box in Revit.' });
  };
}
