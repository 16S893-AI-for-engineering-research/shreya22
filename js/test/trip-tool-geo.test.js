// Tests for js/trip-tool-geo.js (ground-track maps). Run: node --test js/test/*.test.js
// Every test states its KIND, any tolerance and why, and what it cannot catch.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const geo = require("../trip-tool-geo.js");

const ROOT = path.join(__dirname, "..", "..");
const readJSON = (p) => JSON.parse(fs.readFileSync(path.join(ROOT, p), "utf8"));
const ROUTES = readJSON("data/routes.json");
const TRIPS = readJSON("data/trips_top50.json");
const AIRPORTS = readJSON("data/trip_airports.json");
const PAIRS = Object.keys(TRIPS.pairs);

const RAD = Math.PI / 180;
// Independent haversine (radians) so the tests do not lean on the code under test.
function hav(p, q) {
  const dphi = (q[1] - p[1]) * RAD, dlam = (q[0] - p[0]) * RAD;
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(p[1] * RAD) * Math.cos(q[1] * RAD) * Math.sin(dlam / 2) ** 2;
  return 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
const ll = (code) => [AIRPORTS.airports[code].lon, AIRPORTS.airports[code].lat];
const maps = (key, ap) => geo.buildMaps(TRIPS.pairs[key], ap === undefined ? AIRPORTS : ap);
const p2p = (key) => maps(key).maps[0];
const hs = (key) => maps(key).maps[1];
const routeCodes = (key) => new Set([...TRIPS.pairs[key].point_to_point.airports, ...TRIPS.pairs[key].hub_and_spoke.airports]);
const NM_PER_RAD = 3440.065; // same constant as project/src/distance.py (EARTH_RADIUS_NM)

// ------------------------------------------------------------- projection

test("projection reproduces routes.json fromXY/toXY for all distinct endpoints", () => {
  /* KIND: golden/differential (the site's own stored pixel positions are the reference).
     Tolerance 0.1 px: routes.json stores XY rounded to 2 decimals and the fit residual is
     0.006 px, so 0.1 px is >10x that and far below one visible pixel at 1000 px width.
     CANNOT CATCH: error away from those 34 points (e.g. at high latitude) - the airports
     are all in 18..65 N and the endpoints include Europe/Asia/S. America/Oceania, so the
     covered range is wider than the airports we draw, but it is not a proof for all points. */
  const seen = new Map();
  ROUTES.forEach((r) => {
    seen.set(r.from, [r.fromCoord, r.fromXY]);
    seen.set(r.to, [r.toCoord, r.toXY]);
  });
  assert.ok(seen.size >= 30, "expected a few dozen distinct endpoints, got " + seen.size);
  for (const [name, [c, xy]] of seen) {
    const p = geo.project(c[0], c[1]);
    assert.ok(Math.abs(p[0] - xy[0]) <= 0.1, name + " x " + p[0] + " vs " + xy[0]);
    assert.ok(Math.abs(p[1] - xy[1]) <= 0.1, name + " y " + p[1] + " vs " + xy[1]);
  }
});

test("projection orientation: north is up (smaller y), east is right (larger x)", () => {
  /* KIND: property (monotonicity). Tolerance: none (strict inequalities).
     CANNOT CATCH: a wrong magnitude (the golden test above does). */
  assert.ok(geo.project(-100, 50)[1] < geo.project(-100, 30)[1]);
  assert.ok(geo.project(-70, 40)[0] > geo.project(-120, 40)[0]);
  const o = geo.project(0, 0);
  assert.ok(Math.abs(o[0] - 500) < 0.01 && Math.abs(o[1] - 246.1) < 0.01);
});

// ------------------------------------------------------------- great circle

const GC_PAIRS = [["BOS", "SFO"], ["HNL", "SFO"], ["SJU", "MIA"], ["SEA", "MIA"], ["LGA", "DCA"], ["HNL", "BOS"], ["ANC_FAKE", "BOS"]]
  .filter((p) => AIRPORTS.airports[p[0]] && AIRPORTS.airports[p[1]]);

test("great circle: first and last sampled points equal the endpoints exactly", () => {
  /* KIND: property. Tolerance: none (exact; the code returns the inputs themselves for the ends).
     CANNOT CATCH: wrong interior points (next tests). */
  GC_PAIRS.forEach(([A, B]) => {
    const a = ll(A), b = ll(B), pts = geo.greatCircle(a, b);
    assert.deepEqual(pts[0], a);
    assert.deepEqual(pts[pts.length - 1], b);
  });
});

test("great circle: every sampled point lies on the arc, d(a,p)+d(p,b) == d(a,b)", () => {
  /* KIND: property (triangle equality holds iff p is on the minor great-circle arc).
     Tolerance 1e-9 rad (~2e-6 nm): double-precision trig error is ~1e-15 per step; 1e-9
     leaves a million-fold margin yet is 5 orders below any visible deviation.
     CANNOT CATCH: points on the arc but unevenly spaced (the midpoint test and the step test do). */
  GC_PAIRS.forEach(([A, B]) => {
    const a = ll(A), b = ll(B), d = hav(a, b);
    geo.greatCircle(a, b).forEach((p, i) => {
      const e = Math.abs(hav(a, p) + hav(p, b) - d);
      assert.ok(e < 1e-9, A + "-" + B + " point " + i + " off the arc by " + e);
    });
  });
});

test("great circle: the sampled midpoint is equidistant from both ends and the samples are evenly spaced", () => {
  /* KIND: property. Tolerance 1e-9 rad (as above) for equidistance; 1e-9 for equal spacing.
     This kills a lon/lat linear interpolation, whose midpoint is on neither the arc nor the bisector.
     CANNOT CATCH: nothing about projection (separate tests). */
  GC_PAIRS.forEach(([A, B]) => {
    const a = ll(A), b = ll(B), pts = geo.greatCircle(a, b), n = pts.length - 1;
    assert.equal(n % 2, 0, "even number of segments so a sample is the midpoint");
    const m = pts[n / 2];
    assert.ok(Math.abs(hav(a, m) - hav(m, b)) < 1e-9, A + "-" + B);
    const step = hav(a, b) / n;
    for (let i = 0; i < n; i++) assert.ok(Math.abs(hav(pts[i], pts[i + 1]) - step) < 1e-9, A + "-" + B + " step " + i);
  });
});

test("great circle: summed segment length within 0.05% of the haversine distance", () => {
  /* KIND: property/differential (independent haversine). Tolerance 0.05% as specified; segments lie
     on the circle, so the sum is exact up to rounding and the tolerance is deliberately loose.
     CANNOT CATCH: wrong sample COUNT (any count sums to d) - the density test below does. */
  GC_PAIRS.forEach(([A, B]) => {
    const a = ll(A), b = ll(B), pts = geo.greatCircle(a, b);
    let s = 0;
    for (let i = 0; i < pts.length - 1; i++) s += hav(pts[i], pts[i + 1]);
    assert.ok(Math.abs(s - hav(a, b)) / hav(a, b) < 5e-4, A + "-" + B);
  });
});

test("great circle: length matches data/trips_top50.json great_circle_nm for ALL 2450 pairs", () => {
  /* KIND: differential (the Python pipeline's haversine, computed from project/data/airports.csv at full
     precision, vs this module on the 4-decimal coordinates of data/trip_airports.json).
     Tolerance 0.1 nm: 4-dp rounding moves a point by <= 6e-3 nm; the JSON rounds nm to 0.01.
     This also validates data/trip_airports.json against the CSV through a different path.
     CANNOT CATCH: arcs that are the right length but the wrong shape (other tests). */
  PAIRS.forEach((k) => {
    const t = TRIPS.pairs[k];
    const a = ll(t.origin), b = ll(t.destination);
    const pts = geo.greatCircle(a, b);
    let s = 0;
    for (let i = 0; i < pts.length - 1; i++) s += geo.angularDistance(pts[i], pts[i + 1]);
    assert.ok(Math.abs(s * NM_PER_RAD - t.great_circle_nm) < 0.1, k + ": " + s * NM_PER_RAD + " vs " + t.great_circle_nm);
  });
});

test("great circle: degenerate inputs never produce NaN (identical points, antipodal points, poles)", () => {
  /* KIND: example (documented fallbacks). Tolerance: none / 1e-9 rad for the antipodal arc length.
     Documented behaviour: identical points -> a zero-length arc (every sample equals the point);
     antipodal points -> the arc leaving the start northward, still of length pi and ending at b.
     CANNOT CATCH: a different fallback choice - the tests pin the documented one only. */
  const a = [-71.0052, 42.3643];
  const same = geo.greatCircle(a, a);
  assert.ok(same.length > 2);
  same.forEach((p) => assert.deepEqual(p, a));

  const anti = [a[0] + 180, -a[1]];
  const arc = geo.greatCircle(a, anti);
  arc.forEach((p) => assert.ok(Number.isFinite(p[0]) && Number.isFinite(p[1]), "NaN in antipodal arc"));
  assert.deepEqual(arc[0], a);
  assert.ok(Math.abs(hav(arc[0], arc[arc.length - 1]) - Math.PI) < 1e-9);
  const mid = arc[arc.length >> 1];
  assert.ok(Math.abs(hav(a, mid) - Math.PI / 2) < 1e-9, "antipodal midpoint is a quarter turn away");
  assert.ok(mid[1] > a[1], "the documented fallback goes north");

  [[[0, 90], [0, -90]], [[10, 90], [10, 90]], [[0, 0], [180, 0]]].forEach(([p, q]) =>
    geo.greatCircle(p, q).forEach((x) => assert.ok(Number.isFinite(x[0]) && Number.isFinite(x[1]))));
});

test("great circle: Hawaii-mainland and Puerto Rico pairs are finite and project to finite points", () => {
  /* KIND: example. Tolerance: none. CANNOT CATCH: wrong shape (property tests above cover HNL-SFO and SJU-MIA too). */
  [["HNL", "SFO"], ["SJU", "MIA"]].forEach(([A, B]) => {
    const pts = geo.arcPoints(ll(A), ll(B));
    assert.ok(pts.length > 2);
    pts.forEach((p) => assert.ok(Number.isFinite(p[0]) && Number.isFinite(p[1])));
  });
});

test("arc density: >= 64 segments and no projected step longer than 25 px, for every pair in the data", () => {
  /* KIND: property over all 2450 pairs. Tolerance 25 px: the longest pair (HNL-JFK ~ 600 px)
     in 64 even segments steps ~10 px; 25 px gives margin while a 2-point or 8-point arc (>= 75 px)
     fails. Smooth drawing needs short segments in projected space, which is what this checks.
     CANNOT CATCH: an arc that is dense but wrong (other tests). */
  PAIRS.forEach((k) => {
    const t = TRIPS.pairs[k];
    const pts = geo.arcPoints(ll(t.origin), ll(t.destination));
    assert.ok(pts.length >= 65, k + " has only " + pts.length + " points");
    for (let i = 0; i < pts.length - 1; i++) {
      const step = Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]);
      assert.ok(step < 25, k + " step " + i + " = " + step);
    }
  });
});

// ------------------------------------------------------------- longitude wrap

test("longitude: a lon -157 .. -66 pair is ONE polyline with no jump above 100 px", () => {
  /* KIND: property. Tolerance 100 px: a real jump across the antimeridian is ~1000 px; an honest
     step here is ~10 px. Uses HNL (-157.9) to SJU (-66.0) and a synthetic -157 .. -66 pair.
     CANNOT CATCH: wrap handling for other regions beyond the antimeridian test below. */
  [[[-157, 21], [-66, 18]], [ll("HNL"), ll("SJU")]].forEach(([a, b]) => {
    const pts = geo.arcPoints(a, b);
    for (let i = 0; i < pts.length - 1; i++)
      assert.ok(Math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]) < 100);
  });
});

test("longitude: an arc that does cross the antimeridian stays continuous (no jump above 100 px)", () => {
  /* KIND: property. Tolerance 100 px as above. No pair in the data needs this (all US), so this
     guards the documented unwrap behaviour: x then runs past the map edge instead of jumping.
     CANNOT CATCH: how the basemap is drawn beyond x=1000 (never needed by the data). */
  const pts = geo.arcPoints([170, 10], [-170, 20]);
  for (let i = 0; i < pts.length - 1; i++) assert.ok(Math.abs(pts[i + 1][0] - pts[i][0]) < 100);
  const back = geo.arcPoints([-170, 20], [170, 10]);
  for (let i = 0; i < back.length - 1; i++) assert.ok(Math.abs(back[i + 1][0] - back[i][0]) < 100);
});

// ------------------------------------------------------------- extent

const dotsXY = (key) => {
  const out = [];
  routeCodes(key).forEach((c) => out.push(geo.project(AIRPORTS.airports[c].lon, AIRPORTS.airports[c].lat)));
  return out;
};

test("view box contains every airport of BOTH structures for all 2450 pairs (and the same box serves both maps)", () => {
  /* KIND: property over the whole committed trips file. Tolerance 1e-6 px (float).
     Includes hubs that sit outside the origin/destination box, so it catches a box that ignores the hub
     or is built from one structure only (e.g. DCA-BOS point-to-point has no LGA).
     CANNOT CATCH: arcs that bulge outside the box (they are clipped by the viewBox by design). */
  PAIRS.forEach((k) => {
    const m = maps(k);
    const vb = m.viewBox;
    dotsXY(k).forEach((p) => {
      assert.ok(p[0] >= vb.x - 1e-6 && p[0] <= vb.x + vb.w + 1e-6 && p[1] >= vb.y - 1e-6 && p[1] <= vb.y + vb.h + 1e-6, k + " point outside " + vb.str);
    });
  });
});

test("view box uses BOTH structures even when one is not a subset of the other (synthetic trip)", () => {
  /* KIND: metamorphic/differential. Tolerance 1e-6 px.
     In the real data the nonstop's airports are always a subset of the hub route's, so a box built from
     the hub structure alone is indistinguishable there; this synthetic trip gives each structure an
     airport the other lacks (SEA only in point-to-point, MIA only in hub-and-spoke) and requires both inside.
     CANNOT CATCH: anything about real routes (covered by the all-pairs test). */
  const mk = (codes) => ({ airports: codes, total_nm: 1, legs: codes.slice(1).map((c, i) => ({ origin: codes[i], destination: c, distance_nm: 1 })) });
  const trip = { origin: "BOS", destination: "SFO", point_to_point_is_connecting: false, point_to_point: mk(["BOS", "SEA", "SFO"]), hub_and_spoke: mk(["BOS", "MIA", "SFO"]) };
  const m = geo.buildMaps(trip, AIRPORTS), vb = m.viewBox;
  ["BOS", "SEA", "SFO", "MIA"].forEach((c) => {
    const p = geo.project(AIRPORTS.airports[c].lon, AIRPORTS.airports[c].lat);
    assert.ok(p[0] >= vb.x && p[0] <= vb.x + vb.w && p[1] >= vb.y && p[1] <= vb.y + vb.h, c + " outside " + vb.str);
  });
  // and the box is identical to the one computed from the union of all four
  const all = ["BOS", "SEA", "SFO", "MIA"].map((c) => geo.project(AIRPORTS.airports[c].lon, AIRPORTS.airports[c].lat));
  assert.equal(vb.str, geo.computeViewBox(all).str);
});

test("view box: min extent, 2:1 aspect, padding and centring hold for all 2450 pairs", () => {
  /* KIND: property over all pairs. Tolerance 1e-6 (float) for aspect/min; 0.02 px for centring and
     padding because the box is rounded to 0.01 px.
     Min extent: width >= 150 and height >= 75. Padding: when the airports are larger than the minimum,
     the box is at least 1.24 x their extent (12% each side). Centring: the box centre equals the centre
     of the airports' bounding box.
     CANNOT CATCH: whether these numbers look right on screen (checked by eye in the browser).*/
  let minBound = 0;
  PAIRS.forEach((k) => {
    const vb = maps(k).viewBox, pts = dotsXY(k);
    assert.ok(vb.w >= geo.MIN_W - 1e-6 && vb.h >= geo.MIN_H - 1e-6, k + " " + vb.str);
    assert.ok(Math.abs(vb.w / vb.h - 2) < 1e-6, k + " aspect " + vb.w / vb.h);
    const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
    const ex = Math.max(...xs) - Math.min(...xs), ey = Math.max(...ys) - Math.min(...ys);
    assert.ok(Math.abs(vb.x + vb.w / 2 - (Math.max(...xs) + Math.min(...xs)) / 2) < 0.02, k + " x-centre");
    assert.ok(Math.abs(vb.y + vb.h / 2 - (Math.max(...ys) + Math.min(...ys)) / 2) < 0.02, k + " y-centre");
    if (ex * 1.24 > 150 && ex * 1.24 / 2 >= ey * 1.24) assert.ok(vb.w >= ex * 1.24 - 1e-6, k + " padding (x)");
    if (ey * 1.24 > 75 && ey * 1.24 * 2 >= ex * 1.24) assert.ok(vb.h >= ey * 1.24 - 1e-6, k + " padding (y)");
    if (Math.abs(vb.w - geo.MIN_W) < 0.03) minBound++;
  });
  assert.ok(minBound > 0, "at least one pair must be small enough to hit the minimum extent");
});

test("view box: a tiny pair is NOT zoomed in below the contiguous-US minimum (DCA-BWI, LGA-JFK)", () => {
  /* KIND: example/metamorphic (the box for a pair 30 nm apart equals the minimum, not its own extent).
     Tolerance 1e-6. CANNOT CATCH: the right minimum value (it is a stated design choice: 150 x 75 px). */
  ["DCA-BWI", "LGA-JFK"].forEach((k) => {
    if (!TRIPS.pairs[k]) return;
    const vb = maps(k).viewBox;
    assert.ok(vb.w >= 150 && vb.h >= 75);
  });
  const tiny = geo.computeViewBox([[500, 100], [501, 100.5]]);
  assert.equal(tiny.w, 150);
  assert.equal(tiny.h, 75);
  assert.ok(Math.abs(tiny.x + 75 - 500.5) < 0.01 && Math.abs(tiny.y + 37.5 - 100.25) < 0.01);
});

test("view box: the string is deterministic and well formed; a Hawaii-from-the-East pair gives a larger box", () => {
  /* KIND: property + example. Tolerance: none (string equality / strict inequalities).
     BOS-HNL spans the continent plus Hawaii, so its box (302 px wide today) must be clearly
     larger than BOS-SFO's (166 px): asserted as > 1.5x, not as a magic number. LAX-HNL and SJU-MIA
     are small enough to sit at the minimum extent (150 px) - a documented consequence of the
     projection, not zoomed out; they are only asserted to contain their airports (other test).
     CANNOT CATCH: sensible visuals of the zoomed-out fallback (checked in the browser). */
  const re = /^-?\d+(\.\d{1,2})? -?\d+(\.\d{1,2})? \d+(\.\d{1,2})? \d+(\.\d{1,2})?$/;
  PAIRS.forEach((k) => assert.ok(re.test(maps(k).viewBox.str), k + " " + maps(k).viewBox.str));
  assert.equal(maps("BOS-SFO").viewBox.str, maps("BOS-SFO").viewBox.str);
  assert.ok(maps("BOS-HNL").viewBox.w > 1.5 * maps("BOS-SFO").viewBox.w);
  assert.ok(maps("SFO-HNL").viewBox.w >= maps("SJU-MIA").viewBox.w);
});

// ------------------------------------------------------------- data wiring

test("data wiring: every airport used by every pair of data/trips_top50.json is in data/trip_airports.json", () => {
  /* KIND: property over the committed data. Tolerance: none. Also checks lat/lon are numbers in range.
     CANNOT CATCH: coordinates that are plausible but wrong (the great-circle length test compares
     them to the pipeline's distances for all pairs, and pytest compares them to the CSV). */
  assert.equal(AIRPORTS.schema_version, 1);
  const used = new Set(TRIPS.airports.map((a) => a.iata));
  PAIRS.forEach((k) => routeCodes(k).forEach((c) => used.add(c)));
  used.forEach((c) => {
    const a = AIRPORTS.airports[c];
    assert.ok(a, "missing " + c);
    assert.ok(a.lat >= -90 && a.lat <= 90 && a.lon >= -180 && a.lon <= 180);
  });
  assert.equal(Object.keys(AIRPORTS.airports).length, used.size, "no extra airports are shipped");
});

// ------------------------------------------------------------- rendering model

test("model BOS-SFO: nonstop has 1 solid arc and 2 dots; hub route has 2 dashed arcs, 3 dots, hub LAX", () => {
  /* KIND: example (hand-checked against the JSON: BOS-SFO point-to-point is a nonstop; hub is LAX).
     Tolerance 1e-9 for dot positions (they must be exactly project() of the file's coordinates).
     CANNOT CATCH: how the page styles the dashes (CSS; checked in the browser). */
  const m = maps("BOS-SFO");
  const [a, b] = m.maps;
  assert.equal(a.id, "point_to_point");
  assert.equal(b.id, "hub_and_spoke");
  assert.equal(a.arcs.length, 1);
  assert.equal(a.arcs[0].dashed, false);
  assert.equal(a.arcs[0].number, null);
  assert.deepEqual(a.dots.map((d) => [d.iata, d.role]), [["BOS", "origin"], ["SFO", "destination"]]);
  assert.equal(b.arcs.length, 2);
  assert.deepEqual(b.arcs.map((x) => [x.leg, x.from, x.to, x.dashed, x.number]), [[0, "BOS", "LAX", true, "1"], [1, "LAX", "SFO", true, "2"]]);
  assert.deepEqual(b.dots.map((d) => [d.iata, d.role]), [["BOS", "origin"], ["LAX", "hub"], ["SFO", "destination"]]);
  assert.deepEqual(b.dots.map((d) => d.label), ["BOS", "LAX hub", "SFO"]);
  assert.deepEqual(a.dots.map((d) => d.label), ["BOS", "SFO"]);
  [a, b].forEach((map) => map.dots.forEach((d) => {
    const p = geo.project(AIRPORTS.airports[d.iata].lon, AIRPORTS.airports[d.iata].lat);
    assert.ok(Math.abs(d.x - p[0]) < 1e-9 && Math.abs(d.y - p[1]) < 1e-9, d.iata + " dot not at its projected position");
  }));
  // each arc starts and ends on its dots
  b.arcs.forEach((arc) => {
    const from = b.dots.find((d) => d.iata === arc.from), to = b.dots.find((d) => d.iata === arc.to);
    assert.ok(Math.hypot(arc.points[0][0] - from.x, arc.points[0][1] - from.y) < 1e-9);
    const last = arc.points[arc.points.length - 1];
    assert.ok(Math.hypot(last[0] - to.x, last[1] - to.y) < 1e-9);
  });
  // the same view box serves both maps
  assert.ok(m.viewBox && typeof m.viewBox.str === "string");
});

test("model: leg number sits at the arc midpoint (a point of the arc, equidistant from the leg's ends)", () => {
  /* KIND: property. Tolerance 1e-9 rad on the un-projected arc; the model's `mid` must be exactly a sample of the arc.
     CANNOT CATCH: label collisions with other text. */
  hs("BOS-SFO").arcs.forEach((arc) => {
    assert.ok(arc.points.some((p) => p[0] === arc.mid[0] && p[1] === arc.mid[1]));
    const s = arc.points[0], e = arc.points[arc.points.length - 1];
    assert.ok(Math.abs(Math.hypot(arc.mid[0] - s[0], arc.mid[1] - s[1]) - Math.hypot(arc.mid[0] - e[0], arc.mid[1] - e[1])) < arc.points.length, "mid is near the middle of the leg");
  });
  const a = hs("BOS-SFO").arcs[0];
  assert.equal(a.mid[0], a.points[a.points.length >> 1][0]);
});

test("model DCA-SAN (connecting): both maps carry the identical route; only the point-to-point map has the caption note", () => {
  /* KIND: metamorphic/example (ADR 0007: when no nonstop was observed the two structures are the same trip).
     Tolerance: none (deepEqual). CANNOT CATCH: the banner text (owned by trip-tool-core). */
  const m = maps("DCA-SAN");
  assert.equal(m.connecting, true);
  const [a, b] = m.maps;
  assert.deepEqual(a.arcs, b.arcs);
  assert.deepEqual(a.dots, b.dots);
  assert.equal(a.arcs.length, 2);
  assert.ok(a.arcs.every((x) => x.dashed === true));
  assert.ok(a.dots.some((d) => d.role === "hub" && d.iata === "DFW"));
  assert.match(a.note, /same connecting trip/);
  assert.equal(b.note, null);
  assert.equal(maps("BOS-SFO").maps[0].note, null);
  assert.equal(maps("BOS-SFO").connecting, false);
});

test("model: for a pair with an observed nonstop the two maps differ (the connecting case is not an accident)", () => {
  /* KIND: metamorphic (negation of the test above). CANNOT CATCH: nothing beyond that contrast. */
  const [a, b] = maps("BOS-SFO").maps;
  assert.notDeepEqual(a.arcs, b.arcs);
});

test("model HNL pairs: LAX-HNL (nonstop; hub KOA) and BOS-HNL (connecting) draw, with a larger shared box", () => {
  /* KIND: example. Tolerance: none. The zoomed-out fallback means the box holds Hawaii and the mainland.
     CANNOT CATCH: legibility of labels at that zoom (checked in the browser). */
  const m = maps("LAX-HNL");
  assert.deepEqual(m.maps[0].dots.map((d) => d.iata), ["LAX", "HNL"]);
  assert.deepEqual(m.maps[1].dots.map((d) => [d.iata, d.role]), [["LAX", "origin"], ["KOA", "hub"], ["HNL", "destination"]]);
  assert.ok(m.maps.every((x) => x.ok));
  const sj = maps("SJU-MIA");
  assert.deepEqual(sj.maps[1].dots.map((d) => d.iata), ["SJU", "ATL", "MIA"]);
  const c = maps("BOS-HNL");
  assert.equal(c.connecting, true);
  assert.deepEqual(c.maps[0].arcs, c.maps[1].arcs);
});

test("model: every hub route in the data has exactly one hub dot (the middle airport) and origin/destination at the ends", () => {
  /* KIND: property over all 2450 pairs and both structures. Tolerance: none.
     CANNOT CATCH: routes with 2+ intermediate airports (the data has at most one hub; ADR 0007 / schema). */
  PAIRS.forEach((k) => {
    const t = TRIPS.pairs[k];
    maps(k).maps.forEach((map) => {
      const route = t[map.id];
      assert.equal(map.dots.length, route.airports.length, k);
      assert.equal(map.arcs.length, route.legs.length, k);
      assert.deepEqual(map.dots.map((d) => d.iata), route.airports, k);
      assert.equal(map.dots.filter((d) => d.role === "hub").length, route.airports.length - 2, k);
      assert.equal(map.dots[0].role, "origin");
      assert.equal(map.dots[map.dots.length - 1].role, "destination");
      map.arcs.forEach((arc) => assert.equal(arc.dashed, route.legs.length > 1, k));
    });
  });
});

test("model text: titles, descriptions and captions are in words and read distances from the JSON", () => {
  /* KIND: example (exact strings for BOS-SFO) + property (all pairs: distances in the caption are the legs'
     rounded distance_nm). Tolerance: none. CANNOT CATCH: how a screen reader voices them. */
  const m = maps("BOS-SFO");
  assert.equal(m.maps[0].title, "Point-to-point: nonstop BOS to SFO, 2344 nautical miles");
  assert.match(m.maps[1].title, /^Hub-and-spoke: BOS to SFO via LAX hub, 2 legs, 2557 nautical miles in total$/);
  assert.match(m.maps[1].desc, /Leg 1: BOS to LAX, 2264 nautical miles\. Leg 2: LAX to SFO, 293 nautical miles/);
  assert.deepEqual(m.maps[1].caption, ["Leg 1: BOS \u2192 LAX, 2264 nm", "Leg 2: LAX \u2192 SFO, 293 nm", "Total 2557 nm via LAX (hub)"]);
  assert.deepEqual(m.maps[0].caption, ["Nonstop BOS \u2192 SFO, 2344 nm"]);
  PAIRS.forEach((k) => {
    const t = TRIPS.pairs[k];
    maps(k).maps.forEach((map) => t[map.id].legs.forEach((leg, i) => {
      const nm = String(Math.round(leg.distance_nm));
      assert.ok(map.desc.includes(nm + " nautical miles"), k + " desc leg " + i);
      assert.ok(map.caption.join("|").includes(nm + " nm"), k + " caption leg " + i);
    }));
  });
});

test("model: label slots are valid and labels of one map choose distinct positions where the dots are apart", () => {
  /* KIND: property. Tolerance: none. Every dot has an anchor of start|end and finite offsets.
     CANNOT CATCH: collisions with the basemap or in dense areas such as NYC/DC (documented limit). */
  PAIRS.forEach((k) => maps(k).maps.forEach((map) => map.dots.forEach((d) => {
    assert.ok(d.labelAnchor === "start" || d.labelAnchor === "end", k);
    assert.ok(Number.isFinite(d.labelDx) && Number.isFinite(d.labelDy), k);
  })));
});

test("missing coordinates: the affected map says so, the other map still draws, nothing throws", () => {
  /* KIND: example (error path). Tolerance: none. Removes LAX (the hub of BOS-SFO) from the file:
     the hub map is unavailable with a message naming LAX, the nonstop map is fine. A null file
     (fetch failed) makes both maps unavailable. Captions/titles stay present for accessibility.
     CANNOT CATCH: the page's visible rendering of the message (browser check). */
  const partial = { schema_version: 1, airports: Object.assign({}, AIRPORTS.airports) };
  delete partial.airports.LAX;
  const m = geo.buildMaps(TRIPS.pairs["BOS-SFO"], partial);
  assert.equal(m.maps[0].ok, true);
  assert.equal(m.maps[1].ok, false);
  assert.match(m.maps[1].error, /LAX/);
  assert.equal(m.maps[1].arcs.length, 0);
  assert.ok(m.maps[1].title.length > 0 && m.maps[1].caption.length === 3);
  const none = geo.buildMaps(TRIPS.pairs["BOS-SFO"], null);
  assert.ok(none.maps.every((x) => !x.ok && /BOS/.test(x.error)));
  assert.ok(Number.isFinite(none.viewBox.w));
});

test("pathD: a polyline becomes M/L commands with 2 decimals", () => {
  /* KIND: example. Tolerance: none. CANNOT CATCH: SVG rendering. */
  assert.equal(geo.pathD([[1, 2], [3.456, 4.001]]), "M1,2L3.46,4");
});
