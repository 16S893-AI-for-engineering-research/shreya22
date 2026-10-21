// trip-tool-core.js
// Pure logic for the interactive trip tool (project-trip-tool.html).
// No DOM access. Works as a browser global (window.TripToolCore) and via
// require() in node (js/test/trip-tool-core.test.js).
//
// Rule: this module never computes fuel or NOx. It reads the per-flight and
// per-passenger numbers from data/trips_top50.json and only (a) sums legs for
// trip totals that the JSON does not carry and (b) checks that the JSON's own
// route totals equal the sum of its legs. Every number handed to the page is a
// "cell" {value, text, path | paths} so a test can trace it to a JSON field.

(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.TripToolCore = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  var SCHEMA_VERSION = 1;
  var DEFAULT_PAIR = { o: "BOS", d: "SFO" };
  var ADR7_BANNER =
    "No observed nonstop for this pair, so under ADR 0007 point-to-point is the same connecting trip.";
  // Route totals are rounded independently of their legs (docs/trip-schema.md:
  // nm to 2 decimals, per-passenger values to 4), so a sum of rounded legs can
  // differ from the rounded total by up to half a unit in the last decimal per
  // addend. The allowance is per field: half a unit of that field's rounding.
  var REL_TOL = 1e-6;
  var ROUND_ABS = 0.0005;            // per-passenger fields (4 dp) and shares (5 dp)
  var ROUND_ABS_BY_FIELD = { total_nm: 0.005 };   // nm is rounded to 2 dp
  // Above this share of departures with no OpenAP model, a leg's fuel is understated
  // (those flights are excluded, not rescaled), so fuel per passenger is not
  // comparable between structures. A stated choice, not a derived value.
  var CAUTION_UNMAPPED = 0.2;

  // ---------- formatting ----------

  function isNum(x) {
    return typeof x === "number" && isFinite(x);
  }

  function groupThousands(intStr) {
    return intStr.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  // Number with thousands separators and fixed decimals; "n/a" if not finite.
  function fmtNumber(x, digits) {
    if (!isNum(x)) return "n/a";
    var d = digits == null ? 0 : digits;
    var s = Math.abs(x).toFixed(d);
    var parts = s.split(".");
    var out = groupThousands(parts[0]) + (parts[1] ? "." + parts[1] : "");
    // do not print "-0.0"
    return x < 0 && Number(s) !== 0 ? "-" + out : out;
  }

  function fmtKg(x, digits) {
    var t = fmtNumber(x, digits == null ? 1 : digits);
    return t === "n/a" ? t : t + " kg";
  }

  function fmtNm(x) {
    var t = fmtNumber(x, 0);
    return t === "n/a" ? t : t + " nm";
  }

  // share in [0,1] -> "62.5%" (one decimal, trailing ".0" dropped)
  function fmtPct(share) {
    if (!isNum(share)) return "n/a";
    var p = share * 100;
    if (p > 0 && p < 0.05) return "<0.1%";
    var s = p.toFixed(1);
    if (s.slice(-2) === ".0") s = s.slice(0, -2);
    return s + "%";
  }

  // ---------- data access ----------

  function validateData(data) {
    if (!data || typeof data !== "object" || !data.pairs || !Array.isArray(data.airports)) {
      return { ok: false, error: { code: "bad_data", message: "Trip data file is missing or malformed." } };
    }
    if (data.schema_version !== SCHEMA_VERSION) {
      return {
        ok: false,
        error: {
          code: "bad_schema",
          message: "Unsupported trip data schema_version " + data.schema_version + " (expected " + SCHEMA_VERSION + ").",
        },
      };
    }
    return { ok: true };
  }

  // Airports for the dropdowns, in the file's order (busiest first).
  function listAirports(data) {
    return data.airports.map(function (a) {
      return { iata: a.iata, name: a.name, label: a.iata + " \u2014 " + a.name };
    });
  }

  function norm(code) {
    return typeof code === "string" ? code.trim().toUpperCase() : "";
  }

  // -> {ok:true, key, trip} | {ok:false, error:{code, message}}
  function getTrip(data, origin, destination) {
    var v = validateData(data);
    if (!v.ok) return v;
    var o = norm(origin);
    var d = norm(destination);
    if (!o || !d) {
      return { ok: false, error: { code: "missing_airport", message: "Choose both an origin and a destination." } };
    }
    var known = {};
    data.airports.forEach(function (a) {
      known[a.iata] = true;
    });
    var unknown = [o, d].filter(function (c) {
      return !known[c];
    });
    if (unknown.length) {
      return {
        ok: false,
        error: {
          code: "unknown_airport",
          message: "Unknown airport code: " + unknown.join(", ") + ". Only the 50 busiest airports are covered.",
        },
      };
    }
    if (o === d) {
      return { ok: false, error: { code: "same_airport", message: "Origin and destination must be different airports." } };
    }
    var key = o + "-" + d;
    var trip = data.pairs[key];
    if (!trip) {
      return { ok: false, error: { code: "no_data", message: "No data for " + key + " in the trip file." } };
    }
    return { ok: true, key: key, trip: trip };
  }

  function resolvePath(data, path) {
    var cur = data;
    for (var i = 0; i < path.length; i++) {
      if (cur == null) return undefined;
      cur = cur[path[i]];
    }
    return cur;
  }

  // ---------- URL hash and page state ----------

  // "#BOS-SFO" -> {o:"BOS", d:"SFO"}; anything unparsable -> default pair.
  function parseHash(hash) {
    var m = /^#?([A-Za-z]{3})-([A-Za-z]{3})$/.exec(typeof hash === "string" ? hash.trim() : "");
    if (!m) return { o: DEFAULT_PAIR.o, d: DEFAULT_PAIR.d };
    return { o: m[1].toUpperCase(), d: m[2].toUpperCase() };
  }

  function formatHash(state) {
    return "#" + state.o + "-" + state.d;
  }

  // Pure state transitions for the controls. Never mutates its argument.
  function nextState(state, action) {
    switch (action.type) {
      case "origin":
        return { o: norm(action.value), d: state.d };
      case "destination":
        return { o: state.o, d: norm(action.value) };
      case "swap":
        return { o: state.d, d: state.o };
      case "hash":
        return parseHash(action.value);
      default:
        return { o: state.o, d: state.d };
    }
  }

  // ---------- cells (every displayed number) ----------

  function cell(value, text, path) {
    return { value: value, text: text, path: path };
  }

  function sumCell(values, text, paths) {
    return { value: values.reduce(function (a, b) { return a + b; }, 0), text: text, paths: paths, derived: "sum" };
  }

  function cellFrom(data, path, fmt) {
    var v = resolvePath(data, path);
    return cell(v, fmt(v), path);
  }

  // ---------- consistency checks ----------

  function closeEnough(a, b, nAddends, field) {
    var half = ROUND_ABS_BY_FIELD[field] || ROUND_ABS;
    var tol = REL_TOL * Math.max(Math.abs(a), Math.abs(b)) + half * (nAddends + 1);
    return Math.abs(a - b) <= tol;
  }

  // Warnings (strings) if a route's stored totals differ from the sum of its legs.
  function checkRouteTotals(route, label) {
    var warnings = [];
    var legs = route.legs || [];
    [
      ["total_nm", "distance_nm"],
      ["fuel_kg_per_passenger", "fuel_kg_per_passenger"],
      ["nox_kg_per_passenger", "nox_kg_per_passenger"],
    ].forEach(function (pair) {
      var total = route[pair[0]];
      var sum = legs.reduce(function (s, l) { return s + l[pair[1]]; }, 0);
      if (!isNum(total) || !isNum(sum) || !closeEnough(total, sum, legs.length, pair[0])) {
        warnings.push(
          label + ": stored " + pair[0] + " (" + total + ") does not equal the sum of its legs (" +
            (isNum(sum) ? Number(sum.toFixed(6)) : sum) + ")."
        );
      }
    });
    var maxUnmapped = legs.reduce(function (m, l) { return Math.max(m, l.unmapped_share); }, 0);
    if (!isNum(route.unmapped_share_max) || Math.abs(route.unmapped_share_max - maxUnmapped) > ROUND_ABS) {
      warnings.push(label + ": stored unmapped_share_max (" + route.unmapped_share_max + ") is not the maximum over its legs (" + maxUnmapped + ").");
    }
    return warnings;
  }

  function deepEqual(a, b) {
    return JSON.stringify(a) === JSON.stringify(b);
  }

  function checkTrip(trip, key) {
    var w = [];
    w = w.concat(checkRouteTotals(trip.point_to_point, key + " point-to-point"));
    w = w.concat(checkRouteTotals(trip.hub_and_spoke, key + " hub-and-spoke"));
    if (trip.point_to_point_is_connecting === trip.nonstop_observed) {
      w.push(key + ": point_to_point_is_connecting and nonstop_observed contradict each other.");
    }
    if (trip.point_to_point_is_connecting && !deepEqual(trip.point_to_point, trip.hub_and_spoke)) {
      w.push(key + ": ADR 0007 requires the point-to-point trip to be identical to hub-and-spoke when there is no observed nonstop, but the data differ.");
    }
    return w;
  }

  // ---------- view model ----------

  function hubDescription(hub) {
    if (hub.rule === "db1b_most_used") {
      return "the most-used connecting airport for this pair in DB1B";
    }
    if (hub.rule === "fallback_shortest_top10_hub") {
      return "chosen by the fallback rule (no usable DB1B connecting itineraries): the shortest route through one of the ten busiest airports";
    }
    return "chosen by an unrecognised rule (" + hub.rule + ")";
  }

  function legView(data, base, leg, index) {
    var p = function (field) { return base.concat(["legs", index, field]); };
    var mixBase = base.concat(["legs", index, "aircraft_mix"]);
    var mix = leg.aircraft_mix || [];
    var top = mix.slice(0, 3).map(function (m, i) {
      return {
        name: m.name,
        openap: m.openap,
        rangeOk: m.range_ok,
        share: cellFrom(data, mixBase.concat([i, "share"]), fmtPct),
      };
    });
    var rest = mix.slice(3);
    var other = null;
    if (rest.length) {
      var restPaths = rest.map(function (m, i) { return mixBase.concat([i + 3, "share"]); });
      var restVals = rest.map(function (m) { return m.share; });
      var s = restVals.reduce(function (a, b) { return a + b; }, 0);
      other = sumCell(restVals, fmtPct(s), restPaths);
      other.count = rest.length;
    }
    return {
      title: leg.origin + " \u2192 " + leg.destination,
      origin: leg.origin,
      destination: leg.destination,
      distance: cellFrom(data, p("distance_nm"), fmtNm),
      departures: cellFrom(data, p("t100_departures"), function (v) { return fmtNumber(v, 0); }),
      seats: cellFrom(data, p("seats_per_departure"), function (v) { return fmtNumber(v, 0); }),
      loadFactor: cellFrom(data, p("load_factor"), fmtPct),
      passengersPerFlight: cellFrom(data, p("passengers_per_flight"), function (v) { return fmtNumber(v, 1); }),
      mixTop: top,
      mixOther: other,
      unmappedShare: cellFrom(data, p("unmapped_share"), fmtPct),
      fuelPerFlight: cellFrom(data, p("fuel_kg_per_flight"), function (v) { return fmtKg(v, 0); }),
      noxPerFlight: cellFrom(data, p("nox_kg_per_flight"), function (v) { return fmtKg(v, 2); }),
      fuelPerPassenger: cellFrom(data, p("fuel_kg_per_passenger"), function (v) { return fmtKg(v, 1); }),
      noxPerPassenger: cellFrom(data, p("nox_kg_per_passenger"), function (v) { return fmtKg(v, 3); }),
      profileKey: leg.profile_key,
    };
  }

  function hubView(data, key, trip, connectingOrHub) {
    var hub = trip.hub;
    var base = ["pairs", key, "hub"];
    var cands = (hub.candidates || []).map(function (c, i) {
      return { iata: c.iata, share: cellFrom(data, base.concat(["candidates", i, "share"]), fmtPct) };
    });
    return {
      iata: hub.iata,
      rule: hub.rule,
      ruleText: hubDescription(hub),
      share: cellFrom(data, base.concat(["share_of_connecting_passengers"]), fmtPct),
      candidates: cands,
    };
  }

  function columnView(data, key, trip, which) {
    var route = trip[which];
    var base = ["pairs", key, which];
    var legs = route.legs.map(function (l, i) { return legView(data, base, l, i); });
    var perFlightFuel = route.legs.map(function (l) { return l.fuel_kg_per_flight; });
    var perFlightNox = route.legs.map(function (l) { return l.nox_kg_per_flight; });
    var legPath = function (i, f) { return base.concat(["legs", i, f]); };
    var isP2P = which === "point_to_point";
    var nonstop = isP2P && !trip.point_to_point_is_connecting;
    return {
      id: which,
      heading: isP2P ? "Point-to-point" : "Hub-and-spoke",
      kind: nonstop ? "nonstop" : "connecting",
      airports: route.airports.slice(),
      totalNm: cellFrom(data, base.concat(["total_nm"]), fmtNm),
      hub: nonstop ? null : hubView(data, key, trip),
      legs: legs,
      totals: {
        fuelPerFlight: sumCell(perFlightFuel, fmtKg(perFlightFuel.reduce(function (a, b) { return a + b; }, 0), 0),
          route.legs.map(function (l, i) { return legPath(i, "fuel_kg_per_flight"); })),
        noxPerFlight: sumCell(perFlightNox, fmtKg(perFlightNox.reduce(function (a, b) { return a + b; }, 0), 2),
          route.legs.map(function (l, i) { return legPath(i, "nox_kg_per_flight"); })),
        fuelPerPassenger: cellFrom(data, base.concat(["fuel_kg_per_passenger"]), function (v) { return fmtKg(v, 1); }),
        noxPerPassenger: cellFrom(data, base.concat(["nox_kg_per_passenger"]), function (v) { return fmtKg(v, 3); }),
        unmappedShareMax: cellFrom(data, base.concat(["unmapped_share_max"]), fmtPct),
      },
    };
  }

  // Everything the page renders for one pair. {ok:false,error} if getTrip fails.
  // A caution string (or null) when either structure has a leg whose unmapped share
  // exceeds CAUTION_UNMAPPED. Reads the stored unmapped_share_max; computes nothing.
  function cautionFor(trip) {
    var parts = [];
    [["point_to_point", "point-to-point"], ["hub_and_spoke", "hub-and-spoke"]].forEach(function (p) {
      var u = trip[p[0]] && trip[p[0]].unmapped_share_max;
      if (isNum(u) && u > CAUTION_UNMAPPED) {
        parts.push(p[1] + " has a leg where " + fmtPct(u) + " of departures have no OpenAP model");
      }
    });
    if (!parts.length) return null;
    return "Not a like-for-like comparison: " + parts.join(", and ") +
      ". Those flights are excluded from fuel, not estimated, so that structure's fuel per passenger is understated " +
      "and the two columns are not comparable.";
  }

  function buildView(data, origin, destination) {
    var got = getTrip(data, origin, destination);
    if (!got.ok) return got;
    var key = got.key;
    var trip = got.trip;
    var connecting = !!trip.point_to_point_is_connecting;
    var columns = [columnView(data, key, trip, "point_to_point"), columnView(data, key, trip, "hub_and_spoke")];
    var base = ["pairs", key];
    return {
      ok: true,
      key: key,
      origin: trip.origin,
      destination: trip.destination,
      connecting: connecting,
      banner: connecting ? ADR7_BANNER : null,
      context: {
        greatCircle: cellFrom(data, base.concat(["great_circle_nm"]), fmtNm),
        db1bPassengers: cellFrom(data, base.concat(["db1b_passengers"]), function (v) { return fmtNumber(v, 0); }),
        db1bNonstopShare: cellFrom(data, base.concat(["db1b_nonstop_share"]), fmtPct),
      },
      columns: columns,
      caution: cautionFor(trip),
      messages: (trip.messages || []).slice(),
      warnings: checkTrip(trip, key),
    };
  }

  // ---------- rows to render ----------

  function row(type, label, text, cellObj, extra) {
    var r = { type: type, label: label, text: text == null ? (cellObj ? cellObj.text : "") : text, cell: cellObj || null };
    if (extra) Object.keys(extra).forEach(function (k) { r[k] = extra[k]; });
    return r;
  }

  function hubText(hub) {
    var t = hub.iata + " \u2014 " + hub.ruleText;
    if (hub.share.value != null) t += "; " + hub.share.text + " of connecting passengers used it";
    return t;
  }

  // Flat, ordered rows for one column. The page renders these and nothing else.
  function columnRows(col) {
    var rows = [];
    rows.push(row("route", "Route", col.airports.join(" \u2192 ") + " (" + col.totalNm.text + ")", col.totalNm));
    if (col.kind === "nonstop") {
      rows.push(row("hub", "Hub", "None: nonstop flight, observed in T-100", null));
    } else if (col.hub) {
      rows.push(row("hub", "Hub", hubText(col.hub), col.hub.share.value != null ? col.hub.share : null));
      col.hub.candidates.forEach(function (c) {
        rows.push(row("candidate", "Candidate hub " + c.iata + " (share of connecting passengers)", null, c.share));
      });
    }
    col.legs.forEach(function (leg, i) {
      rows.push(row("leg", "Leg " + (i + 1), leg.title, null, { legIndex: i }));
      rows.push(row("metric", "Distance", null, leg.distance, { legIndex: i }));
      rows.push(row("metric", "T-100 departures (2019 Q1)", null, leg.departures, { legIndex: i }));
      rows.push(row("metric", "Seats per departure", null, leg.seats, { legIndex: i }));
      rows.push(row("metric", "Load factor", null, leg.loadFactor, { legIndex: i }));
      rows.push(row("metric", "Passengers per flight", null, leg.passengersPerFlight, { legIndex: i }));
      leg.mixTop.forEach(function (m) {
        var tag = m.openap ? "" : " (no OpenAP model, excluded from fuel)";
        rows.push(row("mix", "Aircraft: " + m.name + tag, null, m.share, { legIndex: i }));
      });
      if (leg.mixOther) {
        rows.push(row("mix", "Aircraft: other (" + leg.mixOther.count + " more types)", null, leg.mixOther, { legIndex: i }));
      }
      rows.push(row("metric", "Departures with no OpenAP model", null, leg.unmappedShare, { legIndex: i }));
      rows.push(row("metric", "Fuel per flight", null, leg.fuelPerFlight, { legIndex: i }));
      rows.push(row("metric", "NOx per flight", null, leg.noxPerFlight, { legIndex: i }));
      rows.push(row("metric", "Fuel per passenger", null, leg.fuelPerPassenger, { legIndex: i, key: "fuelPerPassenger" }));
      rows.push(row("metric", "NOx per passenger", null, leg.noxPerPassenger, { legIndex: i, key: "noxPerPassenger" }));
      rows.push(row("profile", "Altitude profile", null, null, { legIndex: i, profileKey: leg.profileKey }));
    });
    rows.push(row("totals", "Trip total", col.legs.length === 1 ? "single leg" : col.legs.length + " legs", null));
    rows.push(row("total", "Fuel per flight, all legs", null, col.totals.fuelPerFlight));
    rows.push(row("total", "NOx per flight, all legs", null, col.totals.noxPerFlight));
    rows.push(row("total", "Fuel per passenger", null, col.totals.fuelPerPassenger, { key: "fuelPerPassenger" }));
    rows.push(row("total", "NOx per passenger", null, col.totals.noxPerPassenger, { key: "noxPerPassenger" }));
    rows.push(row("total", "Largest unmapped share on any leg", null, col.totals.unmappedShareMax));
    return rows;
  }

  // Short comparison of the two structures: the trip-level numbers only.
  function summary(view) {
    return view.columns.map(function (col) {
      return {
        id: col.id,
        heading: col.heading,
        route: col.airports.join(" \u2192 "),
        distance: col.totalNm,
        fuelPerPassenger: col.totals.fuelPerPassenger,
        noxPerPassenger: col.totals.noxPerPassenger,
      };
    });
  }

  // {columns:[{id, heading, rows}]} in display order (left: point-to-point).
  function renderRows(view) {
    return view.columns.map(function (col) {
      return { id: col.id, heading: col.heading, kind: col.kind, rows: columnRows(col) };
    });
  }

  // All cells in a view (anything carrying path or paths), for traceability tests.
  function collectCells(node, out) {
    out = out || [];
    if (Array.isArray(node)) {
      node.forEach(function (n) { collectCells(n, out); });
    } else if (node && typeof node === "object") {
      if (node.path || node.paths) out.push(node);
      else Object.keys(node).forEach(function (k) { collectCells(node[k], out); });
    }
    return out;
  }

  // ---------- altitude profile geometry (pure; the page draws it) ----------

  function niceStep(max, targetTicks) {
    var raw = max / targetTicks;
    var mag = Math.pow(10, Math.floor(Math.log10(raw)));
    var f = raw / mag;
    var nice = f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10;
    return nice * mag;
  }

  function ticks(max, targetTicks) {
    if (!(max > 0)) return [0];
    var step = niceStep(max, targetTicks || 5);
    var out = [];
    for (var v = 0; v <= max + step * 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
    if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
    return out;
  }

  // profile {dist_nm[], alt_ft[]} -> pixel geometry inside a w x h box with margins.
  function profileGeometry(profile, w, h, margin) {
    if (!profile || !profile.dist_nm || profile.dist_nm.length < 2 || profile.dist_nm.length !== profile.alt_ft.length) {
      return null;
    }
    var m = margin || { left: 52, right: 12, top: 10, bottom: 38 };
    var xt = ticks(Math.max.apply(null, profile.dist_nm), 5);
    var yt = ticks(Math.max.apply(null, profile.alt_ft), 4);
    var xMax = xt[xt.length - 1];
    var yMax = yt[yt.length - 1];
    var iw = w - m.left - m.right;
    var ih = h - m.top - m.bottom;
    var X = function (nm) { return m.left + (nm / xMax) * iw; };
    var Y = function (ft) { return m.top + ih - (ft / yMax) * ih; };
    var pts = profile.dist_nm.map(function (nm, i) { return [X(nm), Y(profile.alt_ft[i])]; });
    var r = function (n) { return Math.round(n * 10) / 10; };
    return {
      d: pts.map(function (p, i) { return (i ? "L" : "M") + r(p[0]) + " " + r(p[1]); }).join(" "),
      xTicks: xt.map(function (v) { return { value: v, x: r(X(v)) }; }),
      yTicks: yt.map(function (v) { return { value: v, y: r(Y(v)) }; }),
      plot: { left: m.left, right: w - m.right, top: m.top, bottom: m.top + ih },
    };
  }

  return {
    SCHEMA_VERSION: SCHEMA_VERSION,
    DEFAULT_PAIR: DEFAULT_PAIR,
    ADR7_BANNER: ADR7_BANNER,
    fmtNumber: fmtNumber,
    fmtKg: fmtKg,
    fmtNm: fmtNm,
    fmtPct: fmtPct,
    validateData: validateData,
    listAirports: listAirports,
    getTrip: getTrip,
    resolvePath: resolvePath,
    parseHash: parseHash,
    formatHash: formatHash,
    nextState: nextState,
    checkRouteTotals: checkRouteTotals,
    checkTrip: checkTrip,
    buildView: buildView,
    renderRows: renderRows,
    summary: summary,
    collectCells: collectCells,
    ticks: ticks,
    profileGeometry: profileGeometry,
  };
});
