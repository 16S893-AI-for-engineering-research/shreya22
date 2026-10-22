// trip-tool-geo.js
// Pure geometry for the trip tool's "Ground tracks" maps (project-trip-tool.html).
// No DOM access. Works as a browser global (window.TripToolGeo) and via
// require() in node (js/test/trip-tool-geo.test.js).
//
// Coordinate conventions: a geographic point is [lon, lat] in DEGREES (the same
// order as data/routes.json "fromCoord"). A projected point is [x, y] in the
// site's 1000 x 500 map space (the space of data/world-outline.path.txt).
//
// Everything the page draws is computed here (projection, great-circle arcs,
// the shared view box, dots, labels, titles, captions); js/trip-tool.js only
// turns the returned model into SVG elements.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TripToolGeo = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  // ---------- projection: Natural Earth I, calibrated against data/routes.json ----------
  // Do not recalibrate: these constants reproduce the 34 distinct endpoints of
  // data/routes.json to 0.006 px (test: "projection reproduces routes.json").
  var SX = 178.50007536566645, TX = 499.99988889175825;
  var SY = -178.50003758680518, TY = 246.10383577942946;
  var RAD = Math.PI / 180;

  function project(lon, lat) {
    var lam = lon * RAD, phi = lat * RAD;
    var p2 = phi * phi, p4 = p2 * p2;
    var x = SX * lam * (0.8707 - 0.131979 * p2 + p4 * (-0.013791 + p4 * (0.003971 * p2 - 0.001529 * p4))) + TX;
    var y = SY * phi * (1.007226 + p2 * (0.015085 + p4 * (-0.044475 + 0.028874 * p2 - 0.005916 * p4))) + TY;
    return [x, y];
  }

  // ---------- great circle ----------

  // Number of straight segments per arc (so SEGMENTS + 1 sampled points). Even,
  // so the middle sample is exactly the arc midpoint.
  var SEGMENTS = 64;

  function toVec(p) {
    var lon = p[0] * RAD, lat = p[1] * RAD, c = Math.cos(lat);
    return [c * Math.cos(lon), c * Math.sin(lon), Math.sin(lat)];
  }
  function fromVec(v) {
    var n = Math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]);
    return [Math.atan2(v[1], v[0]) / RAD, Math.asin(Math.max(-1, Math.min(1, v[2] / n))) / RAD];
  }
  function dot(a, b) { return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]; }
  function cross(a, b) { return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]; }
  function norm(a) { return Math.sqrt(dot(a, a)); }

  // Angular distance in radians between two [lon, lat] points (atan2 form, stable
  // for both tiny and near-antipodal separations).
  function angularDistance(p, q) {
    var a = toVec(p), b = toVec(q);
    return Math.atan2(norm(cross(a, b)), dot(a, b));
  }

  // Shift lon by multiples of 360 so it is within 180 degrees of `prev`: the
  // polyline stays continuous across the antimeridian (its projected x then
  // runs past the 0..1000 map edge instead of jumping across the map).
  function unwrap(lon, prev) {
    while (lon - prev > 180) lon -= 360;
    while (lon - prev < -180) lon += 360;
    return lon;
  }

  // n + 1 [lon, lat] points along the great circle from a to b (spherical
  // interpolation). The first point is exactly `a` and the last exactly `b`
  // (b's longitude shifted by a multiple of 360 if the arc crosses the
  // antimeridian). Documented degenerate cases, never NaN:
  //   * a == b (within 1e-12 rad): every point is a (a zero-length arc).
  //   * a and b antipodal (within 1e-9 rad of pi): the great circle is not
  //     unique; the arc is the one that leaves a toward the north (via the
  //     pole side of a's meridian; for a pole itself, along lon 0).
  function greatCircle(a, b, n) {
    n = n || SEGMENTS;
    var pts = [], i;
    var va = toVec(a), vb = toVec(b);
    var d = Math.atan2(norm(cross(va, vb)), dot(va, vb));
    if (!(d > 1e-12)) {
      for (i = 0; i <= n; i++) pts.push([a[0], a[1]]);
      return pts;
    }
    var antipodal = d > Math.PI - 1e-9;
    var u = null;
    if (antipodal) {
      // unit vector perpendicular to a, pointing north (toward +z) along a's meridian
      var up = [0, 0, 1];
      var east = cross(up, va);
      if (norm(east) < 1e-9) east = [0, 1, 0];
      u = cross(va, east);
      var un = norm(u);
      u = [u[0] / un, u[1] / un, u[2] / un];
      if (u[2] < 0) u = [-u[0], -u[1], -u[2]];
    }
    var prevLon = a[0];
    for (i = 0; i <= n; i++) {
      var t = i / n, p;
      if (i === 0) p = [a[0], a[1]];
      else if (i === n) p = [unwrap(b[0], prevLon), b[1]];
      else {
        var v;
        if (antipodal) {
          var ang = t * Math.PI;
          v = [va[0] * Math.cos(ang) + u[0] * Math.sin(ang), va[1] * Math.cos(ang) + u[1] * Math.sin(ang), va[2] * Math.cos(ang) + u[2] * Math.sin(ang)];
        } else {
          var s = Math.sin(d), w1 = Math.sin((1 - t) * d) / s, w2 = Math.sin(t * d) / s;
          v = [w1 * va[0] + w2 * vb[0], w1 * va[1] + w2 * vb[1], w1 * va[2] + w2 * vb[2]];
        }
        p = fromVec(v);
        p[0] = unwrap(p[0], prevLon);
      }
      prevLon = p[0];
      pts.push(p);
    }
    return pts;
  }

  // Projected polyline of the great circle a -> b.
  function arcPoints(a, b, n) {
    return greatCircle(a, b, n).map(function (p) { return project(p[0], p[1]); });
  }

  // ---------- shared view box ----------

  var PAD = 0.12;            // padding on each side, as a fraction of the airports' extent
  var MIN_W = 150;           // min box width  (roughly the contiguous US in the 1000 x 500 space)
  var MIN_H = 75;            // min box height
  var ASPECT = 2;            // width / height of every map
  var REF_W = 520;           // default rendered width of one map in CSS px (used to size dots/labels)

  function round2(x) { return Math.round(x * 100) / 100; }

  // Smallest box with aspect 2:1 that holds the points plus 12% padding on each
  // side, is at least MIN_W x MIN_H, and is centred on the points' bounding box.
  // pts: array of [x, y]. Returns {x, y, w, h, str} (numbers rounded to 0.01;
  // w is a multiple of 0.02 so that h = w / 2 is exact).
  function computeViewBox(pts) {
    if (!pts.length) return { x: 0, y: 0, w: 1000, h: 500, str: "0 0 1000 500" };
    var x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    pts.forEach(function (p) {
      if (p[0] < x0) x0 = p[0];
      if (p[0] > x1) x1 = p[0];
      if (p[1] < y0) y0 = p[1];
      if (p[1] > y1) y1 = p[1];
    });
    var cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
    var w = Math.max((x1 - x0) * (1 + 2 * PAD), MIN_W);
    var h = Math.max((y1 - y0) * (1 + 2 * PAD), MIN_H);
    if (w / h < ASPECT) w = h * ASPECT; else h = w / ASPECT;
    w = Math.ceil(w * 50) / 50;       // round UP so padding is never reduced
    h = w / ASPECT;
    var x = round2(cx - w / 2), y = round2(cy - h / 2);
    return { x: x, y: y, w: w, h: h, str: x + " " + y + " " + w + " " + h };
  }

  // ---------- formatting ----------

  function nm(x) { return String(Math.round(x)); }

  // ---------- rendering model ----------

  var STRUCTURES = [
    { id: "point_to_point", heading: "Point-to-point", short: "Point-to-point" },
    { id: "hub_and_spoke", heading: "Hub-and-spoke", short: "Hub-and-spoke" },
  ];
  var LABEL_FONT_PX = 11, LABEL_CHAR_PX = 6.8, LABEL_H_PX = 13;
  // Candidate label placements, tried in order: [anchor, dx px, dy px (baseline)].
  var LABEL_SLOTS = [["start", 9, -8], ["start", 9, 15], ["end", -9, -8], ["end", -9, 15]];

  function routeCoords(route, airports) {
    var missing = [], coords = {};
    route.airports.forEach(function (code) {
      var a = airports && airports.airports ? airports.airports[code] : null;
      if (!a || typeof a.lat !== "number" || typeof a.lon !== "number") { if (missing.indexOf(code) < 0) missing.push(code); }
      else coords[code] = [a.lon, a.lat];
    });
    return { coords: coords, missing: missing };
  }

  function roleOf(route, i) {
    if (i === 0) return "origin";
    if (i === route.airports.length - 1) return "destination";
    return "hub";
  }

  function rectsOverlap(a, b) {
    return a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
  }

  // Greedy label placement for one map (3 dots at most). u = user units per CSS
  // pixel (view box width / rendered width). Rectangles are estimates: width =
  // characters x 6.8 px, height 13 px. Keeps labels off the other dots, off each other and inside the box.
  function placeLabels(dots, vb, u) {
    var taken = dots.map(function (d) {
      var r = (d.role === "hub" ? 10 : 6) * u;
      return [d.x - r, d.y - r, d.x + r, d.y + r];
    });
    var placed = [];
    dots.forEach(function (d, i) {
      var wpx = d.label.length * LABEL_CHAR_PX;
      var best = null, bestScore = Infinity;
      LABEL_SLOTS.forEach(function (slot) {
        var x0 = slot[0] === "start" ? d.x + slot[1] * u : d.x + slot[1] * u - wpx * u;
        var rect = [x0, d.y + (slot[2] - LABEL_H_PX + 2) * u, x0 + wpx * u, d.y + (slot[2] + 3) * u];
        var score = 0;
        taken.forEach(function (t, j) { if (j !== i && rectsOverlap(rect, t)) score += 1; });
        placed.forEach(function (t) { if (rectsOverlap(rect, t)) score += 1; });
        if (rect[0] < vb.x || rect[2] > vb.x + vb.w || rect[1] < vb.y || rect[3] > vb.y + vb.h) score += 1;
        if (score < bestScore) { bestScore = score; best = { slot: slot, rect: rect }; }
      });
      d.labelAnchor = best.slot[0];
      d.labelDx = best.slot[1];
      d.labelDy = best.slot[2];
      placed.push(best.rect);
    });
  }

  function legNm(leg) { return nm(leg.distance_nm); }

  // One structure's map. vb is the shared view box.
  function mapModel(structure, trip, route, airports, vb, u, connectingNote) {
    var rc = routeCoords(route, airports);
    var m = { id: structure.id, heading: structure.heading, ok: true, error: null,
      arcs: [], dots: [], title: "", desc: "", caption: [], note: connectingNote || null };
    var multi = route.legs.length > 1;
    var od = trip.origin + " to " + trip.destination;
    if (rc.missing.length) {
      m.ok = false;
      m.error = "Map unavailable: no coordinates for " + rc.missing.join(", ") + " in data/trip_airports.json.";
    } else {
      route.legs.forEach(function (leg, i) {
        var pts = arcPoints(rc.coords[leg.origin], rc.coords[leg.destination]);
        var midPt = pts[Math.floor(pts.length / 2)];
        m.arcs.push({ leg: i, from: leg.origin, to: leg.destination, points: pts,
          dashed: multi, number: multi ? String(i + 1) : null, mid: [midPt[0], midPt[1]] });
      });
      route.airports.forEach(function (code, i) {
        var role = roleOf(route, i);
        var xy = project(rc.coords[code][0], rc.coords[code][1]);
        m.dots.push({ iata: code, role: role, x: xy[0], y: xy[1],
          label: role === "hub" ? code + " hub" : code });
      });
      placeLabels(m.dots, vb, u);
    }
    // Words for assistive technology and the visible caption (always, even if the map failed).
    var hubCode = multi ? route.airports[1] : null;
    if (multi) {
      m.title = structure.short + ": " + od + " via " + hubCode + " hub, " + route.legs.length + " legs, " +
        nm(route.total_nm) + " nautical miles in total";
      m.desc = structure.short + " ground tracks (idealized great circles). " +
        route.legs.map(function (l, i) { return "Leg " + (i + 1) + ": " + l.origin + " to " + l.destination + ", " + legNm(l) + " nautical miles"; }).join(". ") +
        ". The connection is at the hub, " + hubCode + ".";
      m.caption = route.legs.map(function (l, i) { return "Leg " + (i + 1) + ": " + l.origin + " \u2192 " + l.destination + ", " + legNm(l) + " nm"; });
      m.caption.push("Total " + nm(route.total_nm) + " nm via " + hubCode + " (hub)");
    } else {
      m.title = structure.short + ": nonstop " + od + ", " + nm(route.total_nm) + " nautical miles";
      m.desc = structure.short + " ground track (idealized great circle): one nonstop leg from " + trip.origin + " to " + trip.destination + ", " + nm(route.total_nm) + " nautical miles.";
      m.caption = ["Nonstop " + trip.origin + " \u2192 " + trip.destination + ", " + nm(route.total_nm) + " nm"];
    }
    return m;
  }

  // Everything the page needs to draw both maps for one trip record.
  // airports = parsed data/trip_airports.json (or null if it failed to load).
  // Returns {viewBox, unit (user units per CSS px), connecting, maps: [point_to_point, hub_and_spoke]}. A map
  // whose airports lack coordinates has ok:false and an `error` message (the
  // other map and the view box, computed from the airports that are known, are
  // unaffected).
  // opts.widthPx = rendered width of one map in CSS px (default REF_W); it only
  // affects `unit` and the label placement, never the view box.
  function buildMaps(trip, airports, opts) {
    var widthPx = opts && opts.widthPx > 0 ? opts.widthPx : REF_W;
    var xy = [];
    STRUCTURES.forEach(function (s) {
      var route = trip[s.id];
      var rc = routeCoords(route, airports);
      Object.keys(rc.coords).forEach(function (c) { xy.push(project(rc.coords[c][0], rc.coords[c][1])); });
    });
    var vb = computeViewBox(xy);
    var connecting = !!trip.point_to_point_is_connecting;
    var maps = STRUCTURES.map(function (s) {
      var note = connecting && s.id === "point_to_point"
        ? "No nonstop was observed for this pair, so this is the same connecting trip as the hub-and-spoke map (ADR 0007)."
        : null;
      return mapModel(s, trip, trip[s.id], airports, vb, vb.w / widthPx, note);
    });
    return { viewBox: vb, unit: vb.w / widthPx, connecting: connecting, maps: maps };
  }

  // SVG path "d" for a projected polyline (2 decimals).
  function pathD(points) {
    return points.map(function (p, i) { return (i ? "L" : "M") + round2(p[0]) + "," + round2(p[1]); }).join("");
  }

  return {
    SEGMENTS: SEGMENTS, PAD: PAD, MIN_W: MIN_W, MIN_H: MIN_H, ASPECT: ASPECT, REF_W: REF_W,
    LABEL_FONT_PX: LABEL_FONT_PX,
    project: project, greatCircle: greatCircle, arcPoints: arcPoints, angularDistance: angularDistance,
    computeViewBox: computeViewBox, buildMaps: buildMaps, pathD: pathD,
  };
});
