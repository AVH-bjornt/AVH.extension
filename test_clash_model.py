# -*- coding: utf-8 -*-
"""
The parts of the Clash Detector that need no Revit: boxes, candidate
pairs, the triangle test and the packing of meshes for the viewer.

The triangle test is the one to distrust. It decides whether two
elements clash when Revit cannot intersect them as solids, and its two
ways of being wrong are both quiet: calling a wall that stands on a
floor a clash, and missing a pipe that goes clean through one.

Run outside Revit:

    python test_clash_model.py
"""

import base64
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "lib"))

from avh_clash import model  # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))


def close(a, b, tolerance=1e-6):
    return abs(a - b) < tolerance


def box_mesh(x0, y0, z0, x1, y1, z1):
    """A closed box as (positions, indices, box), a vertex set per face
    as Revit's Face.Triangulate gives."""
    corners = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
               (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    pos = model.new_floats()
    idx = model.new_indices()
    for face in faces:
        base = len(pos) // 3
        for corner in face:
            pos.extend(corners[corner])
        idx.extend((base, base + 1, base + 2, base, base + 2, base + 3))
    return pos, idx, (x0, y0, z0, x1, y1, z1)


def clash(a, b):
    return model.mesh_clash(a[0], a[1], a[2], b[0], b[1], b[2])


# --------------------------------------------------------------------------
# Boxes
# --------------------------------------------------------------------------

check("bounds: nothing gives nothing", model.bounds([]) is None)
check("bounds: around three points",
      model.bounds([0, 0, 0, 2, -1, 5, 1, 3, -4]) == (0, -1, -4, 2, 3, 5))
check("bounds: from a start offset",
      model.bounds([9, 9, 9, 1, 2, 3, 4, 5, 6], 3) == (1, 2, 3, 4, 5, 6))
check("union: with nothing is itself",
      model.union(None, (0, 0, 0, 1, 1, 1)) == (0, 0, 0, 1, 1, 1))
check("intersection: apart is None",
      model.intersection((0, 0, 0, 1, 1, 1), (2, 0, 0, 3, 1, 1)) is None)
check("intersection: the shared box",
      model.intersection((0, 0, 0, 2, 2, 2), (1, 1, 1, 3, 3, 3))
      == (1, 1, 1, 2, 2, 2))
check("overlap: overlapping boxes",
      model.boxes_overlap((0, 0, 0, 2, 2, 2), (1, 1, 1, 3, 3, 3)))
check("overlap: touching boxes are not overlapping",
      not model.boxes_overlap((0, 0, 0, 1, 1, 1), (0, 0, 1, 1, 1, 2)))
check("overlap: apart on one axis is apart",
      not model.boxes_overlap((0, 0, 0, 1, 1, 1), (0.5, 0.5, 5, 1, 1, 6)))

padded = model.pad((0, 0, 0, 10, 2, 2), 0.5, 0.1)
check("pad: the fraction of the largest dimension wins when larger",
      close(padded[0], -1.0) and close(padded[5], 3.0), repr(padded))
check("pad: the minimum wins on a small box",
      close(model.pad((0, 0, 0, 1, 1, 1), 0.5, 0.1)[0], -0.5))

origin = (100.0, -50.0, 10.0)
feet = (100.0, -50.0, 10.0, 110.0, -40.0, 20.0)
local = model.to_local(feet, origin)
check("to_local: the origin becomes zero, feet become metres",
      close(local[0], 0.0) and close(local[3], 3.048), repr(local))
back = model.to_feet(local, origin)
check("to_feet: undoes to_local",
      all(close(p, q) for p, q in zip(back, feet)), repr(back))


# --------------------------------------------------------------------------
# Candidate pairs
# --------------------------------------------------------------------------

unit = (0, 0, 0, 1, 1, 1)
set_a = [("a1", unit), ("a2", (10, 0, 0, 11, 1, 1))]
set_b = [("b1", (0.5, 0.5, 0.5, 2, 2, 2)), ("b2", (20, 0, 0, 21, 1, 1)),
         ("b3", (10.5, 0, 0, 12, 1, 1))]
pairs = model.candidate_pairs(set_a, set_b)
check("pairs: the two overlapping pairs and no others",
      sorted(pairs) == [("a1", "b1"), ("a2", "b3")], repr(pairs))
check("pairs: a first, b second",
      all(a.startswith("a") and b.startswith("b") for a, b in pairs))
check("pairs: order of the sets does not change what is found",
      sorted(model.candidate_pairs(list(reversed(set_a)),
                                   list(reversed(set_b)))) == sorted(pairs))
check("pairs: touching boxes are not candidates",
      model.candidate_pairs([("a", unit)], [("b", (1, 0, 0, 2, 1, 1))]) == [])
check("pairs: overlap on x only is not a candidate",
      model.candidate_pairs([("a", unit)],
                            [("b", (0.5, 5, 0, 1.5, 6, 1))]) == [])

same = [("x", unit), ("y", (0.5, 0, 0, 1.5, 1, 1)),
        ("z", (0.9, 0, 0, 1.9, 1, 1))]
own = model.candidate_pairs(same, same)
check("pairs: a set against itself never pairs an element with itself",
      all(a != b for a, b in own), repr(own))
check("pairs: and gives each pair once, not once each way",
      len(own) == 3 and
      len(set(tuple(sorted(pair)) for pair in own)) == 3, repr(own))

# against brute force, on a few hundred boxes
import random  # noqa: E402

rng = random.Random(7)


def random_boxes(prefix, count):
    made = []
    for n in range(count):
        x, y, z = rng.uniform(0, 30), rng.uniform(0, 30), rng.uniform(0, 9)
        made.append(("{0}{1}".format(prefix, n),
                     (x, y, z, x + rng.uniform(0.1, 4),
                      y + rng.uniform(0.1, 4), z + rng.uniform(0.1, 3))))
    return made


many_a = random_boxes("a", 150)
many_b = random_boxes("b", 150)
brute = sorted((ka, kb) for ka, ba in many_a for kb, bb in many_b
               if model.boxes_overlap(ba, bb))
check("pairs: the sweep finds exactly what brute force finds",
      sorted(model.candidate_pairs(many_a, many_b)) == brute and
      len(brute) > 50, "{0} pairs".format(len(brute)))


# --------------------------------------------------------------------------
# A segment through a triangle
# --------------------------------------------------------------------------

tri = ((0, 0, 0), (4, 0, 0), (0, 4, 0))
hit = model.pierce((1, 1, -1), (1, 1, 1), *tri)
check("pierce: straight through the middle",
      hit is not None and close(hit[0], 1) and close(hit[2], 0), repr(hit))
check("pierce: a segment that stops on the triangle does not go through",
      model.pierce((1, 1, -1), (1, 1, 0), *tri) is None)
check("pierce: a segment that starts on the triangle does not go through",
      model.pierce((1, 1, 0), (1, 1, 1), *tri) is None)
check("pierce: a segment lying in the plane does not go through",
      model.pierce((0.5, 0.5, 0), (2, 1, 0), *tri) is None)
check("pierce: a segment that misses",
      model.pierce((5, 5, -1), (5, 5, 1), *tri) is None)
check("pierce: a segment that stops short",
      model.pierce((1, 1, -3), (1, 1, -1), *tri) is None)


# --------------------------------------------------------------------------
# Meshes through each other
# --------------------------------------------------------------------------

wall = box_mesh(0, 0, 0, 5, 0.2, 3)
pipe = box_mesh(2, -1, 1, 2.1, 1, 1.1)          # through the wall
found = clash(wall, pipe)
check("mesh: a pipe through a wall clashes", found is not None)
check("mesh: and the clash is where the pipe meets the wall",
      found is not None and close(found["center"][0], 2.05, 0.02) and
      close(found["center"][1], 0.1, 0.02) and
      close(found["center"][2], 1.05, 0.02), repr(found))
check("mesh: its box spans the wall's thickness",
      found is not None and
      close(found["box"][4] - found["box"][1], 0.2, 1e-4), repr(found))
check("mesh: surfaces crossing is not one inside the other",
      found is not None and found["inside"] is False)
check("mesh: the same either way round", clash(pipe, wall) is not None)

floor = box_mesh(-1, -1, -0.25, 6, 2, 0)
check("mesh: a wall standing on a floor does not clash",
      clash(wall, floor) is None)
check("mesh: nor the floor under the wall", clash(floor, wall) is None)
beside = box_mesh(0, 0.2, 0, 5, 0.4, 3)
check("mesh: two walls face to face do not clash",
      clash(wall, beside) is None)
check("mesh: elements apart do not clash",
      clash(wall, box_mesh(0, 5, 0, 5, 5.2, 3)) is None)
near = box_mesh(2, 0.25, 1, 2.1, 1, 1.1)        # stops 50 mm short
check("mesh: a pipe that stops short of the wall does not clash",
      clash(wall, near) is None)
sunk = box_mesh(1, -0.5, 2.9, 1.2, 0.7, 3.5)    # 100 mm into the top
check("mesh: a beam bedded 100 mm into the wall clashes",
      clash(wall, sunk) is not None)

inner = box_mesh(2, 0.05, 1, 2.1, 0.15, 1.1)    # wholly inside the wall
swallowed = clash(wall, inner)
check("mesh: an element wholly inside another clashes",
      swallowed is not None and swallowed["inside"] is True,
      repr(swallowed))
check("mesh: inside is found either way round",
      clash(inner, wall) is not None)
check("mesh: inside the box of an L, outside the L itself, is not inside",
      not model.point_inside((4.5, 4.5, 0.5), *(
          box_mesh(0, 0, 0, 1, 5, 1)[:2] + ((0, 0, 0, 5, 5, 1),))))
check("mesh: an empty mesh never clashes",
      model.mesh_clash(model.new_floats(), model.new_indices(), wall[2],
                       pipe[0], pipe[1], pipe[2]) is None)

points, complete = model.mesh_crossings(
    wall[0], wall[1], pipe[0], pipe[1],
    model.pad(model.intersection(wall[2], pipe[2]), 1e-4), max_points=3)
check("mesh: a search cut short says it is incomplete",
      complete is False and len(points) >= 3)


# --------------------------------------------------------------------------
# Packing for the viewer
# --------------------------------------------------------------------------

sent = []
batch = model.MeshBatch(sent.append, limit=100)
batch.add("e", "host:1", wall[0], wall[1])
check("batch: a mesh under the limit waits", sent == [])
batch.add("e", "host:2", pipe[0], pipe[1])
check("batch: reaching the limit sends", len(sent) == 1)
batch.add("c", 0, model.new_floats(), model.new_indices())
check("batch: an empty mesh is not sent at all", batch.items == [])
batch.add("c", 4, inner[0], inner[1])
batch.flush()
batch.flush()
check("batch: flush sends the rest, once", len(sent) == 2)
check("batch: counts triangles", batch.triangles == 36)

message = sent[0]
check("batch: a mesh message names its type", message["type"] == "mesh")
raw_pos = base64.b64decode(message["pos"])
raw_idx = base64.b64decode(message["idx"])
check("batch: positions are 4 byte floats",
      len(raw_pos) == 4 * (len(wall[0]) + len(pipe[0])))
check("batch: indices are 4 byte unsigned integers",
      len(raw_idx) == 4 * (len(wall[1]) + len(pipe[1])))
second = message["items"][1]
check("batch: an item says where its numbers are",
      second["key"] == "host:2" and second["kind"] == "e" and
      second["v"] == [len(wall[0]), len(pipe[0])] and
      second["i"] == [len(wall[1]), len(pipe[1])], repr(second))
start = second["v"][0] * 4
values = struct.unpack("<3f", raw_pos[start:start + 12])
check("batch: and they decode to the mesh, little endian",
      close(values[0], pipe[0][0]) and close(values[1], pipe[0][1]) and
      close(values[2], pipe[0][2]), repr(values))
first_index = struct.unpack("<I", raw_idx[second["i"][0] * 4:
                                          second["i"][0] * 4 + 4])[0]
check("batch: indices stay relative to their own mesh",
      first_index == pipe[1][0])
check("batch: base64 is text, for JSON",
      isinstance(message["pos"], type(u"")))


# --------------------------------------------------------------------------
# Distance between surfaces, for clearance zones
#
# A soft clash is decided entirely by this number, so every case has an
# answer worked out by hand, and the mesh search is checked against a
# brute force over every pair of triangles.
# --------------------------------------------------------------------------

def near(p, q, tolerance=1e-9):
    return all(abs(x - y) < tolerance for x, y in zip(p, q))


flat = ((0, 0, 0), (4, 0, 0), (0, 4, 0))
check("nearest: above the inside lands straight below",
      near(model.closest_on_triangle((1, 1, 3), *flat), (1, 1, 0)))
check("nearest: beyond a corner is that corner",
      near(model.closest_on_triangle((-2, -3, 1), *flat), (0, 0, 0)))
check("nearest: beyond an edge lands on that edge",
      near(model.closest_on_triangle((2, -5, 0), *flat), (2, 0, 0)))
check("nearest: beyond the long edge lands on it",
      near(model.closest_on_triangle((4, 4, 0), *flat), (2, 2, 0)))
check("nearest: a degenerate triangle does not divide by zero",
      model.closest_on_triangle((1, 1, 1), (0, 0, 0), (0, 0, 0),
                                (0, 0, 0)) == (0, 0, 0))

c1, c2 = model.closest_on_segments((0, 0, 0), (2, 0, 0), (1, -1, 1), (1, 1, 1))
check("segments: crossing at a distance meet in the middle",
      near(c1, (1, 0, 0)) and near(c2, (1, 0, 1)), repr((c1, c2)))
c1, c2 = model.closest_on_segments((0, 0, 0), (2, 0, 0), (0, 1, 0), (2, 1, 0))
check("segments: parallel ones are one apart",
      close(model._squared(c1, c2), 1.0), repr((c1, c2)))
c1, c2 = model.closest_on_segments((0, 0, 0), (1, 0, 0), (3, 0, 0), (5, 0, 0))
check("segments: in line, end to end",
      near(c1, (1, 0, 0)) and near(c2, (3, 0, 0)))
c1, c2 = model.closest_on_segments((0, 0, 0), (0, 0, 0), (1, -1, 0), (1, 1, 0))
check("segments: a point against a segment",
      near(c1, (0, 0, 0)) and near(c2, (1, 0, 0)))

above = ((0, 0, 0.5), (4, 0, 0.5), (0, 4, 0.5))
d, p, q = model.triangle_distance(flat, above)
check("triangles: one 0.5 above the other", close(d, 0.25), repr(d))
crossing = ((1, 1, -1), (1, 1, 1), (2, 1, 0))
check("triangles: crossing ones are zero apart",
      model.triangle_distance(flat, crossing)[0] == 0.0)
skew = ((5, 0, 1), (5, 4, 1), (8, 0, 1))
d, p, q = model.triangle_distance(flat, skew)
check("triangles: edge to edge, across a gap",
      close(d, 1.0 + 1.0), repr((d, p, q)))

# Two boxes with a 30 mm gap between their faces, measured in metres.
wall_m = box_mesh(0, 0, 0, 5, 0.2, 3)
pipe_near = box_mesh(1, 0.23, 1, 4, 0.33, 1.1)
gap = model.mesh_distance(wall_m[0], wall_m[1], wall_m[2],
                          pipe_near[0], pipe_near[1], pipe_near[2], 0.05)
check("gap: 30 mm between faces is found",
      gap is not None and close(gap["distance"], 0.03, 1e-6), repr(gap))
check("gap: the nearest points are on the two faces",
      gap is not None and close(gap["a"][1], 0.2, 1e-6) and
      close(gap["b"][1], 0.23, 1e-6), repr(gap))
check("gap: wider than the limit is no soft clash",
      model.mesh_distance(wall_m[0], wall_m[1], wall_m[2],
                          pipe_near[0], pipe_near[1], pipe_near[2],
                          0.02) is None)
check("gap: exactly the limit is not inside it",
      model.mesh_distance(wall_m[0], wall_m[1], wall_m[2],
                          pipe_near[0], pipe_near[1], pipe_near[2],
                          0.03) is None)
check("gap: the same either way round",
      close(model.mesh_distance(pipe_near[0], pipe_near[1], pipe_near[2],
                                wall_m[0], wall_m[1], wall_m[2],
                                0.05)["distance"], 0.03, 1e-6))
resting = box_mesh(1, 0.2, 1, 4, 0.3, 1.1)
check("gap: touching is zero",
      close(model.mesh_distance(wall_m[0], wall_m[1], wall_m[2],
                                resting[0], resting[1], resting[2],
                                0.05)["distance"], 0.0, 1e-9))
corner = box_mesh(5.02, 0.21, 3.02, 6, 1, 4)    # off the far top corner
corner_gap = model.mesh_distance(wall_m[0], wall_m[1], wall_m[2],
                                 corner[0], corner[1], corner[2], 0.05)
check("gap: corner to corner is the diagonal",
      corner_gap is not None and
      close(corner_gap["distance"], (0.02 ** 2 + 0.01 ** 2 + 0.02 ** 2) ** 0.5,
            1e-6), repr(corner_gap))
check("gap: an empty mesh has no gap",
      model.mesh_distance(model.new_floats(), model.new_indices(), wall_m[2],
                          pipe_near[0], pipe_near[1], pipe_near[2],
                          0.05) is None)


def brute_gap(a, b):
    best = None
    tris = lambda mesh: [tuple((mesh[0][i * 3], mesh[0][i * 3 + 1],
                                mesh[0][i * 3 + 2])
                               for i in mesh[1][n:n + 3])
                         for n in range(0, len(mesh[1]), 3)]
    for ta in tris(a):
        for tb in tris(b):
            d = model.triangle_distance(ta, tb)[0]
            if best is None or d < best:
                best = d
    return best ** 0.5


rng = random.Random(11)
agree = 0
for trial in range(40):
    x, y, z = rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)
    first = box_mesh(0, 0, 0, rng.uniform(0.2, 1), rng.uniform(0.2, 1),
                     rng.uniform(0.2, 1))
    second = box_mesh(x + 1.05, y, z, x + 1.05 + rng.uniform(0.1, 0.8),
                      y + rng.uniform(0.1, 0.8), z + rng.uniform(0.1, 0.8))
    expected = brute_gap(first, second)
    got = model.mesh_distance(first[0], first[1], first[2],
                              second[0], second[1], second[2], 10.0)
    if got is not None and close(got["distance"], expected, 1e-7):
        agree += 1
check("gap: the search agrees with brute force on 40 random pairs",
      agree == 40, "{0} of 40".format(agree))

cut = model.mesh_distance(wall_m[0], wall_m[1], wall_m[2],
                          pipe_near[0], pipe_near[1], pipe_near[2], 0.05,
                          max_tests=1)
check("gap: a search cut short says so",
      cut is None or cut["complete"] is False, repr(cut))


# --------------------------------------------------------------------------
# JSON for the page
#
# 2.44.1 used json.dumps, which on IronPython raises on any non-ASCII
# text: the list of models never reached the page because a category
# name had an o with an umlaut in it. CPython's json has no such
# problem, so these checks cannot reproduce that failure. What they can
# prove is that model.to_json never leaves a non-ASCII character in its
# output, which is what keeps IronPython's string handling out of it,
# and that the page reads back exactly what was sent.
# --------------------------------------------------------------------------

import json  # noqa: E402

NAMES = [u"R\u00f6r", u"G\u00f3lf", u"H\u00e6\u00f0", u"\u00deak",
         u"L\u00c1GSPENNUR\u00ddMI", u"m\u00b2", u"\u6728\u6750",
         u"rocket \U0001F680", u"a \"quoted\" \\ path",
         u"line\nbreak\ttab", u"\x01\x1f control", u"\u2028 separator",
         u""]

for name in NAMES:
    text = model.to_json({"name": name})
    check(u"json: {0!r} comes out as plain ASCII".format(name),
          all(ord(character) < 128 for character in text), text)
    check(u"json: {0!r} reads back as itself".format(name),
          json.loads(text) == {"name": name}, text)

check("json: a name with an umlaut is escaped, not passed through",
      model.to_json(u"R\u00f6r") == u'"R\\u00f6r"', model.to_json(u"R\u00f6r"))

init_like = {
    "type": "init", "version": u"2.44.2", "model": u"H\u00fas.rvt",
    "view": {"name": u"{3D}", "usable": True, "is3d": True,
             "sectionBox": False},
    "sources": [{"key": u"host", "label": u"H\u00fas.rvt", "kind": u"host",
                 "cats": [{"id": -2008044, "name": u"R\u00f6r", "count": 3},
                          {"id": -2000011, "name": u"Veggir", "count": 12}]}],
}
check("json: an init with umlauts in it round trips whole",
      json.loads(model.to_json(init_like)) == init_like)

numbers = {"int": -2000011, "big": 2 ** 40, "zero": 0, "float": 0.1,
           "whole_float": 3.0, "negative": -1.5e-7, "nan": float("nan"),
           "inf": float("inf"), "none": None, "yes": True, "no": False}
back = json.loads(model.to_json(numbers))
check("json: numbers keep their value",
      back["int"] == -2000011 and back["big"] == 2 ** 40 and
      back["zero"] == 0 and back["float"] == 0.1 and
      back["whole_float"] == 3.0 and back["negative"] == -1.5e-7, repr(back))
check("json: True and False are not written as 1 and 0",
      back["yes"] is True and back["no"] is False and back["none"] is None)
check("json: NaN and infinity become null, which JSON can carry",
      back["nan"] is None and back["inf"] is None)
check("json: tuples are arrays",
      json.loads(model.to_json({"c": (1.5, -2, 0)})) == {"c": [1.5, -2, 0]})


class NetInt64(object):
    """Stands in for System.Int64, which is not a Python int on
    IronPython but converts to one."""

    def __init__(self, value):
        self.value = value

    def __float__(self):
        return float(self.value)


class Opaque(object):
    def __str__(self):
        return "opaque"


check("json: a .NET integer is written as a number",
      model.to_json([NetInt64(310405)]) == u"[310405]")
check("json: anything else is written as its text",
      json.loads(model.to_json([Opaque()])) == [u"opaque"])

geometry = model.b64_text(model.new_floats())
batch_message = []
packed = model.MeshBatch(batch_message.append)
packed.add("e", u"host:1", wall[0], wall[1])
packed.flush()
check("json: a mesh message survives the trip",
      json.loads(model.to_json(batch_message[0])) == batch_message[0])


# --------------------------------------------------------------------------
# The check itself must be able to fail.
# --------------------------------------------------------------------------

probe = []
results, kept = probe, results
check("self-check", False)
results = kept
if probe[0][1] is not False:
    results.append(("self-check: a false check is recorded as false",
                    False, ""))

# Failure details can hold the accented names under test, which a
# Windows console cannot print; escape them rather than crash before
# the total.
try:
    sys.stdout.reconfigure(errors="backslashreplace")
except AttributeError:
    pass

print("Clash Detector: model")
print("=" * 70)
failed = 0
for name, ok, detail in results:
    if not ok:
        failed += 1
        print("  [FAIL] {0}".format(name))
        if detail:
            print("         {0}".format(detail))
print("=" * 70)
print("{0} checks, {1} passed, {2} failed".format(
    len(results), len(results) - failed, failed))
sys.exit(1 if failed else 0)
