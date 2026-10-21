// Tests for js/trip-tool-core.js. Run: node --test js/test/
// Every test states its KIND, any tolerance and why, and what it cannot catch.
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const core = require("../trip-tool-core.js");

const load = (f) => JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", f), "utf8"));
const DATA = load("trips_fixture.json");
const PROFILES = load("trip_profiles_fixture.json");
const clone = (x) => JSON.parse(JSON.stringify(x));
const sumLegs = (route, f) => route.legs.reduce((s, l) => s + l[f], 0);
// What a reader sees in a row: everything except where in the JSON it came from.
const visible = (rows) => rows.map((r) => ({ type: r.type, label: r.label, text: r.text, value: r.cell ? r.cell.value : undefined, legIndex: r.legIndex, key: r.key, profileKey: r.profileKey }));
const col = (view, id) => view.columns.find((c) => c.id === id);

// ---------------------------------------------------------------- getTrip

test("getTrip returns the stored pair for valid codes, case/space-insensitive", () => {
  /* KIND: example. Tolerance: none (exact lookup).
     CANNOT CATCH: wrong data inside the pair (other tests do), or behaviour on the real 2450-pair file. */
  const r = core.getTrip(DATA, " bos", "sfo");
  assert.equal(r.ok, true);
  assert.equal(r.key, "BOS-SFO");
  assert.equal(r.trip, DATA.pairs["BOS-SFO"]);
});

test("getTrip is directional: A-B and B-A are different records", () => {
  /* KIND: property. Tolerance: none.
     CANNOT CATCH: a fixture whose two directions happen to be equal (fixture directions differ in fuel). */
  const ab = core.getTrip(DATA, "BOS", "SFO").trip;
  const ba = core.getTrip(DATA, "SFO", "BOS").trip;
  assert.equal(ab.origin, "BOS");
  assert.equal(ba.origin, "SFO");
  assert.notEqual(ab.hub_and_spoke.fuel_kg_per_passenger, ba.hub_and_spoke.fuel_kg_per_passenger);
});

test("getTrip validation: same airport, unknown code, missing code -> error objects, never throws", () => {
  /* KIND: example (one per error class). Tolerance: none.
     CANNOT CATCH: error wording quality; only the machine-readable code and the codes named in the message. */
  const same = core.getTrip(DATA, "BOS", "BOS");
  assert.deepEqual([same.ok, same.error.code], [false, "same_airport"]);
  const unk = core.getTrip(DATA, "BOS", "XXX");
  assert.deepEqual([unk.ok, unk.error.code], [false, "unknown_airport"]);
  assert.match(unk.error.message, /XXX/);
  assert.doesNotMatch(unk.error.message, /BOS/);
  const both = core.getTrip(DATA, "QQQ", "ZZZ");
  assert.match(both.error.message, /QQQ, ZZZ/);
  for (const bad of [["", "SFO"], [null, "SFO"], ["BOS", undefined]]) {
    const r = core.getTrip(DATA, ...bad);
    assert.deepEqual([r.ok, r.error.code], [false, "missing_airport"]);
  }
});

test("getTrip rejects missing data and an unsupported schema_version", () => {
  /* KIND: example. Tolerance: none.
     CANNOT CATCH: a future schema that keeps version 1 but renames fields (the totals check would flag some of that). */
  assert.equal(core.getTrip(null, "BOS", "SFO").error.code, "bad_data");
  assert.equal(core.getTrip({}, "BOS", "SFO").error.code, "bad_data");
  const v2 = clone(DATA);
  v2.schema_version = 2;
  assert.equal(core.getTrip(v2, "BOS", "SFO").error.code, "bad_schema");
});

test("getTrip reports no_data when a known pair is absent from the file", () => {
  /* KIND: example. Tolerance: none. CANNOT CATCH: nothing beyond the one deleted key. */
  const d = clone(DATA);
  delete d.pairs["BOS-SFO"];
  assert.equal(core.getTrip(d, "BOS", "SFO").error.code, "no_data");
});

test("getTrip: every ordered pair of distinct airports resolves, every same-airport pair errors", () => {
  /* KIND: property (exhaustive over the fixture airports). Tolerance: none.
     CANNOT CATCH: gaps in the real file's 2450 keys (that is NW-28's contract test). */
  const codes = core.listAirports(DATA).map((a) => a.iata);
  for (const o of codes) for (const d of codes) {
    const r = core.getTrip(DATA, o, d);
    if (o === d) assert.equal(r.ok, false);
    else {
      assert.equal(r.ok, true, `${o}-${d}`);
      assert.equal(r.trip.origin, o);
      assert.equal(r.trip.destination, d);
    }
  }
});

// ---------------------------------------------------------------- airports list

test("listAirports keeps file order and labels each with code and name", () => {
  /* KIND: example + order property. Tolerance: none.
     CANNOT CATCH: that the real file is sorted by departures (contract on NW-28's side). */
  const l = core.listAirports(DATA);
  assert.deepEqual(l.map((a) => a.iata), DATA.airports.map((a) => a.iata));
  for (const a of l) {
    assert.ok(a.label.includes(a.iata));
    assert.ok(a.label.includes(DATA.airports.find((x) => x.iata === a.iata).name));
  }
});

// ---------------------------------------------------------------- formatting

test("fmtNumber / fmtKg / fmtNm: thousands separators and fixed decimals", () => {
  /* KIND: golden (hand-computed strings). Tolerance: none, strings are exact.
     CANNOT CATCH: locale differences, because the formatter deliberately ignores locale. */
  assert.equal(core.fmtNumber(1234567.891, 1), "1,234,567.9");
  assert.equal(core.fmtNumber(999, 0), "999");
  assert.equal(core.fmtNumber(1000, 0), "1,000");
  assert.equal(core.fmtNumber(0, 2), "0.00");
  assert.equal(core.fmtNumber(-1234.5, 1), "-1,234.5");
  assert.equal(core.fmtNumber(-0.0001, 1), "0.0");
  assert.equal(core.fmtKg(23940, 0), "23,940 kg");
  assert.equal(core.fmtKg(110), "110.0 kg");
  assert.equal(core.fmtNm(2504), "2,504 nm");
});

test("formatters turn null/NaN/Infinity/undefined into 'n/a', never 'NaN' or 'null'", () => {
  /* KIND: property over a list of non-finite inputs. Tolerance: none.
     CANNOT CATCH: strings like '1e21' for astronomically large inputs (not in scope for kg/nm/%). */
  for (const bad of [null, undefined, NaN, Infinity, -Infinity, "12"]) {
    for (const f of [core.fmtNumber, core.fmtKg, core.fmtNm, core.fmtPct]) {
      assert.equal(f(bad), "n/a");
    }
  }
});

test("fmtPct: shares in [0,1] -> percent, trailing .0 dropped, tiny positives not shown as 0%", () => {
  /* KIND: golden. Tolerance: none.
     CANNOT CATCH: rounding of values exactly on a .x5 boundary (binary float noise). */
  assert.equal(core.fmtPct(0.625), "62.5%");
  assert.equal(core.fmtPct(0.8), "80%");
  assert.equal(core.fmtPct(0), "0%");
  assert.equal(core.fmtPct(1), "100%");
  assert.equal(core.fmtPct(0.0001), "<0.1%");
});

test("fmtNumber is monotone: a larger input never formats to a smaller number", () => {
  /* KIND: property (grid sweep). Tolerance: none; compared after removing commas.
     CANNOT CATCH: wrong decimals on values off the grid. */
  let prev = -Infinity;
  for (let x = 0; x < 5e6; x += 12345.67) {
    const v = Number(core.fmtNumber(x, 1).replace(/,/g, ""));
    assert.ok(v >= prev);
    prev = v;
  }
});

// ---------------------------------------------------------------- hash / state

test("parseHash/formatHash: deep link round-trips; garbage falls back to BOS-SFO", () => {
  /* KIND: property (round trip) + examples. Tolerance: none.
     CANNOT CATCH: browser behaviour (hashchange events), tested manually. */
  assert.deepEqual(core.parseHash("#bos-sfo"), { o: "BOS", d: "SFO" });
  assert.deepEqual(core.parseHash("#ORD-SFO"), { o: "ORD", d: "SFO" });
  for (const bad of ["", "#", "#BOSSFO", "#BOS-", "#BOS-SFO-X", "#<script>-ABC", null, undefined]) {
    assert.deepEqual(core.parseHash(bad), { o: "BOS", d: "SFO" }, String(bad));
  }
  const s = { o: "SFO", d: "ORD" };
  assert.deepEqual(core.parseHash(core.formatHash(s)), s);
  assert.equal(core.formatHash(s), "#SFO-ORD");
});

test("nextState swap exchanges origin and destination; swapping twice is the identity", () => {
  /* KIND: metamorphic (involution). Tolerance: none.
     CANNOT CATCH: the DOM button not being wired to this action (checked manually in a browser). */
  const s = { o: "BOS", d: "SFO" };
  const sw = core.nextState(s, { type: "swap" });
  assert.deepEqual(sw, { o: "SFO", d: "BOS" });
  assert.deepEqual(core.nextState(sw, { type: "swap" }), s);
  assert.deepEqual(s, { o: "BOS", d: "SFO" }, "input must not be mutated");
});

test("nextState: origin/destination changes touch only their own field; swap shows the opposite-direction trip", () => {
  /* KIND: example + metamorphic (swap -> reversed pair record). Tolerance: none.
     CANNOT CATCH: UI-level focus handling. */
  const s = { o: "BOS", d: "SFO" };
  assert.deepEqual(core.nextState(s, { type: "origin", value: "ord" }), { o: "ORD", d: "SFO" });
  assert.deepEqual(core.nextState(s, { type: "destination", value: "ord" }), { o: "BOS", d: "ORD" });
  assert.deepEqual(core.nextState(s, { type: "hash", value: "#SFO-BOS" }), { o: "SFO", d: "BOS" });
  const sw = core.nextState(s, { type: "swap" });
  assert.equal(core.buildView(DATA, sw.o, sw.d).key, "SFO-BOS");
});

// ---------------------------------------------------------------- summary & totals

test("summary: trip totals per structure equal the sum of the legs' own per-passenger values", () => {
  /* KIND: property over all fixture pairs, both structures. Tolerance: 1e-6 relative + 0.0005 per addend,
     because the JSON rounds each number to 3 decimals so sum-of-rounded differs from rounded-sum.
     CANNOT CATCH: legs that are wrong in the same way as the total (then they would still agree). */
  for (const key of Object.keys(DATA.pairs)) {
    const [o, d] = key.split("-");
    const view = core.buildView(DATA, o, d);
    const sm = core.summary(view);
    assert.deepEqual(sm.map((s) => s.id), ["point_to_point", "hub_and_spoke"]);
    for (const s of sm) {
      const route = DATA.pairs[key][s.id];
      const n = route.legs.length;
      for (const [cellName, f] of [["fuelPerPassenger", "fuel_kg_per_passenger"], ["noxPerPassenger", "nox_kg_per_passenger"]]) {
        const tol = 1e-6 * Math.abs(s[cellName].value) + 0.0005 * (n + 1);
        assert.ok(Math.abs(s[cellName].value - sumLegs(route, f)) <= tol, `${key} ${s.id} ${f}`);
      }
      assert.ok(Math.abs(s.distance.value - sumLegs(route, "distance_nm")) <= 0.0005 * (n + 1));
    }
  }
});

test("summary reads the stored per-passenger totals (no recomputation in JS)", () => {
  /* KIND: differential/metamorphic: change the stored total by a visible amount while keeping the legs, and the
     displayed total must follow the stored value, not the legs. Tolerance: exact equality.
     CANNOT CATCH: a JS computation that is coincidentally equal to the stored value. */
  const d = clone(DATA);
  d.pairs["BOS-SFO"].hub_and_spoke.fuel_kg_per_passenger += 7;
  d.pairs["BOS-SFO"].hub_and_spoke.nox_kg_per_passenger += 0.5;
  const sm = core.summary(core.buildView(d, "BOS", "SFO"));
  assert.equal(sm[1].fuelPerPassenger.value, d.pairs["BOS-SFO"].hub_and_spoke.fuel_kg_per_passenger);
  assert.equal(sm[1].noxPerPassenger.value, d.pairs["BOS-SFO"].hub_and_spoke.nox_kg_per_passenger);
});

test("per-passenger values shown are the JSON's, even when they disagree with per_flight / passengers_per_flight", () => {
  /* KIND: differential: doctor one leg's fuel_kg_per_passenger so that JS recomputation (per_flight / passengers)
     would give a different number; the display must still show the stored field. Tolerance: exact.
     CANNOT CATCH: JS that recomputes only for display text but keeps the .value from JSON. */
  const d = clone(DATA);
  const leg = d.pairs["BOS-SFO"].hub_and_spoke.legs[0];
  leg.fuel_kg_per_passenger = 999.123;
  const rows = core.renderRows(core.buildView(d, "BOS", "SFO"))[1].rows;
  const r = rows.find((x) => x.type === "metric" && x.key === "fuelPerPassenger" && x.legIndex === 0);
  assert.equal(r.cell.value, 999.123);
  assert.match(r.text, /999\.1 kg/);
});

test("trip total per-flight fuel/NOx are sums of the leg values (the only arithmetic the module does)", () => {
  /* KIND: property over all pairs. Tolerance: 1e-9 relative (pure float addition of the same numbers).
     CANNOT CATCH: wrong leg values. */
  for (const key of Object.keys(DATA.pairs)) {
    const [o, d] = key.split("-");
    const v = core.buildView(DATA, o, d);
    for (const c of v.columns) {
      const route = DATA.pairs[key][c.id];
      const want = sumLegs(route, "fuel_kg_per_flight");
      assert.ok(Math.abs(c.totals.fuelPerFlight.value - want) <= 1e-9 * want);
      const wantN = sumLegs(route, "nox_kg_per_flight");
      assert.ok(Math.abs(c.totals.noxPerFlight.value - wantN) <= 1e-9 * wantN);
    }
  }
});

// ---------------------------------------------------------------- totals-mismatch warning

test("clean fixture produces no warnings for any pair", () => {
  /* KIND: property (negative control for the warning tests). Tolerance: as in checkRouteTotals.
     CANNOT CATCH: a checker that never fires (the doctored tests do). */
  for (const key of Object.keys(DATA.pairs)) {
    const [o, d] = key.split("-");
    assert.deepEqual(core.buildView(DATA, o, d).warnings, [], key);
  }
});

test("doctored route total (fuel, NOx, distance, unmapped max) fires a visible warning naming the field", () => {
  /* KIND: example/mutation-style. Tolerance: the doctoring adds 0.01 or more, far above the 1e-6 relative + 0.0005
     rounding allowance, so a hit is unambiguous.
     CANNOT CATCH: doctoring smaller than the rounding allowance (by design not flagged). */
  for (const [field, delta] of [["fuel_kg_per_passenger", 0.01], ["nox_kg_per_passenger", 0.01], ["total_nm", 1], ["unmapped_share_max", 0.2]]) {
    const d = clone(DATA);
    d.pairs["BOS-SFO"].hub_and_spoke[field] += delta;
    const v = core.buildView(d, "BOS", "SFO");
    assert.equal(v.ok, true);
    assert.equal(v.warnings.length, 1, field);
    assert.match(v.warnings[0], new RegExp(field));
    assert.match(v.warnings[0], /hub-and-spoke/);
  }
});

test("rounding-sized differences (0.0004 per leg) do NOT warn: the tolerance matches 3-decimal JSON rounding", () => {
  /* KIND: boundary example. Tolerance: 0.0005 per addend is half a unit in the 3rd decimal.
     CANNOT CATCH: whether 3 decimals is the right rounding for the real file (NW-28's contract). */
  const d = clone(DATA);
  d.pairs["BOS-SFO"].hub_and_spoke.fuel_kg_per_passenger += 0.0004;
  assert.deepEqual(core.buildView(d, "BOS", "SFO").warnings, []);
});

test("a doctored P2P total warns on the point-to-point side, and the page can see it in view.warnings", () => {
  /* KIND: example. Tolerance: as above. CANNOT CATCH: the page failing to display view.warnings (manual browser check). */
  const d = clone(DATA);
  d.pairs["BOS-SFO"].point_to_point.fuel_kg_per_passenger += 5;
  const w = core.buildView(d, "BOS", "SFO").warnings;
  assert.equal(w.length, 1);
  assert.match(w[0], /point-to-point/);
});

// ---------------------------------------------------------------- ADR 0007

test("ADR 0007 connecting case: banner present and both columns' rows are identical", () => {
  /* KIND: property/metamorphic (P2P rows == H&S rows when no observed nonstop; compared on what is visible:
     type, label, text, value, i.e. all but the JSON source path, which necessarily names its own column). Tolerance: none.
     CANNOT CATCH: a data file that is itself non-identical (checkTrip flags that, tested below). */
  const connecting = Object.keys(DATA.pairs).filter((k) => DATA.pairs[k].point_to_point_is_connecting);
  assert.ok(connecting.length >= 1, "fixture must contain a connecting pair");
  for (const key of connecting) {
    const [o, d] = key.split("-");
    const v = core.buildView(DATA, o, d);
    assert.equal(v.connecting, true);
    assert.match(v.banner, /No observed nonstop for this pair, so under ADR 0007 point-to-point is the same connecting trip/);
    const [p2p, hs] = core.renderRows(v);
    assert.deepEqual(visible(p2p.rows), visible(hs.rows));
    assert.equal(p2p.kind, "connecting");
    assert.ok(p2p.rows.some((r) => r.type === "hub"));
  }
});

test("non-connecting pairs have no banner and differ between columns", () => {
  /* KIND: example/negative control for the ADR test. Tolerance: none.
     CANNOT CATCH: a pair that is nonstop but where both structures happen to coincide in the real data. */
  const v = core.buildView(DATA, "BOS", "SFO");
  assert.equal(v.connecting, false);
  assert.equal(v.banner, null);
  const [p2p, hs] = core.renderRows(v);
  assert.equal(p2p.kind, "nonstop");
  assert.notDeepEqual(visible(p2p.rows), visible(hs.rows));
  assert.deepEqual(col(v, "point_to_point").airports, ["BOS", "SFO"]);
  assert.deepEqual(col(v, "hub_and_spoke").airports, ["BOS", "ORD", "SFO"]);
  assert.equal(p2p.rows.find((r) => r.type === "hub").text.startsWith("None"), true);
});

test("columns are in the order point-to-point (left) then hub-and-spoke (right), and carry their own data", () => {
  /* KIND: example. Tolerance: none. Guards against swapped columns: the nonstop route has 1 leg, H&S has 2.
     CANNOT CATCH: CSS reordering columns visually (manual check). */
  const v = core.buildView(DATA, "BOS", "SFO");
  assert.deepEqual(v.columns.map((c) => c.id), ["point_to_point", "hub_and_spoke"]);
  assert.deepEqual(v.columns.map((c) => c.legs.length), [1, 2]);
  assert.deepEqual(v.columns.map((c) => c.heading), ["Point-to-point", "Hub-and-spoke"]);
  const rows = core.renderRows(v);
  assert.deepEqual(rows.map((c) => c.id), ["point_to_point", "hub_and_spoke"]);
  assert.equal(rows[0].rows.find((r) => r.type === "total" && r.key === "fuelPerPassenger").cell.value,
    DATA.pairs["BOS-SFO"].point_to_point.fuel_kg_per_passenger);
  assert.equal(rows[1].rows.find((r) => r.type === "total" && r.key === "fuelPerPassenger").cell.value,
    DATA.pairs["BOS-SFO"].hub_and_spoke.fuel_kg_per_passenger);
});

test("checkTrip flags an ADR 0007 violation and contradictory flags in the data", () => {
  /* KIND: example (doctored data). Tolerance: none.
     CANNOT CATCH: other kinds of inconsistent data. */
  const d = clone(DATA);
  d.pairs["ORD-SFO"].point_to_point.legs[0].fuel_kg_per_flight += 1;
  const w = core.buildView(d, "ORD", "SFO").warnings;
  assert.ok(w.some((x) => /ADR 0007/.test(x)));
  const e = clone(DATA);
  e.pairs["BOS-SFO"].nonstop_observed = false;
  assert.ok(core.buildView(e, "BOS", "SFO").warnings.some((x) => /contradict/.test(x)));
});

test("hub rule is explained in plain words, with share for db1b_most_used and none for the fallback", () => {
  /* KIND: example (one per rule). Tolerance: none.
     CANNOT CATCH: wording quality beyond key phrases. */
  const a = col(core.buildView(DATA, "BOS", "SFO"), "hub_and_spoke").hub;
  assert.equal(a.iata, "ORD");
  assert.match(a.ruleText, /most-used connecting airport.*DB1B/);
  assert.equal(a.share.text, "62.5%");
  const b = col(core.buildView(DATA, "ORD", "SFO"), "hub_and_spoke").hub;
  assert.match(b.ruleText, /fallback/);
  assert.equal(b.share.text, "n/a");
  const rows = core.renderRows(core.buildView(DATA, "ORD", "SFO"))[1].rows;
  assert.doesNotMatch(rows.find((r) => r.type === "hub").text, /connecting passengers used it/);
});

// ---------------------------------------------------------------- aircraft mix

test("aircraft mix: top 3 + 'other' shares add up to the full mix; unmapped share shown separately", () => {
  /* KIND: property (conservation of share) over all legs. Tolerance: 1e-9 (sums of the same floats).
     CANNOT CATCH: a mix that does not sum to 1 in the data itself. */
  for (const key of Object.keys(DATA.pairs)) {
    const [o, d] = key.split("-");
    for (const c of core.buildView(DATA, o, d).columns) {
      c.legs.forEach((leg, i) => {
        const raw = DATA.pairs[key][c.id].legs[i];
        assert.ok(leg.mixTop.length <= 3);
        const shown = leg.mixTop.reduce((s, m) => s + m.share.value, 0) + (leg.mixOther ? leg.mixOther.value : 0);
        const all = raw.aircraft_mix.reduce((s, m) => s + m.share, 0);
        assert.ok(Math.abs(shown - all) <= 1e-9, `${key} ${c.id} leg ${i}`);
        assert.equal(leg.mixOther === null, raw.aircraft_mix.length <= 3);
        assert.equal(leg.unmappedShare.value, raw.unmapped_share);
      });
    }
  }
});

test("aircraft types with no OpenAP model are labelled as excluded from fuel in the rows", () => {
  /* KIND: example. Tolerance: none. CANNOT CATCH: labelling of types hidden inside 'other'. */
  const rows = core.renderRows(core.buildView(DATA, "ORD", "SFO"))[0].rows;
  const dash = rows.find((r) => r.type === "mix" && /Dash 8/.test(r.label));
  assert.ok(dash, "Dash 8 should be in the top 3 of ORD-SFO");
  assert.match(dash.label, /no OpenAP model, excluded from fuel/);
});

// ---------------------------------------------------------------- traceability

test("every rendered numeric cell maps to a JSON field (or a sum of JSON fields) with matching value and text", () => {
  /* KIND: property (walks every row of every pair, both columns). Tolerance: 1e-9 relative for sums, exact otherwise.
     CANNOT CATCH: a number typed directly into the HTML/DOM code outside renderRows (the page test below greps for that). */
  let seen = 0;
  for (const key of Object.keys(DATA.pairs)) {
    const [o, d] = key.split("-");
    const view = core.buildView(DATA, o, d);
    const cols = core.renderRows(view);
    for (const c of cols) for (const r of c.rows) {
      if (!r.cell) continue;
      seen++;
      const cell = r.cell;
      assert.ok(cell.path || cell.paths, `${key} ${c.id} ${r.label} has no source path`);
      if (cell.path) {
        const v = core.resolvePath(DATA, cell.path);
        assert.notEqual(v, undefined, `${key} ${cell.path.join(".")} does not exist`);
        assert.equal(cell.value, v);
      } else {
        const s = cell.paths.reduce((a, p) => {
          const v = core.resolvePath(DATA, p);
          assert.equal(typeof v, "number", `${key} ${p.join(".")}`);
          return a + v;
        }, 0);
        assert.ok(Math.abs(cell.value - s) <= 1e-9 * Math.max(1, Math.abs(s)));
      }
      assert.equal(typeof r.text, "string");
      assert.doesNotMatch(r.text, /NaN|undefined|null/);
    }
    // the view itself: all cells anywhere in it are sourced
    for (const cell of core.collectCells(view)) {
      assert.ok(cell.path || cell.paths);
    }
  }
  assert.ok(seen > 200, "walk must have visited many cells, saw " + seen);
});

test("every JSON number on a displayed leg is reachable from the rendered rows (nothing silently dropped)", () => {
  /* KIND: property (coverage of the contract). Tolerance: none.
     CANNOT CATCH: fields deliberately not shown: range_km, range_ok, bts_code (shown only as a flag/not at all), profile_key (drawn, not printed). */
  const shown = new Set();
  const view = core.buildView(DATA, "BOS", "SFO");
  for (const cell of core.collectCells(view)) {
    if (cell.path) shown.add(cell.path.join("."));
    else cell.paths.forEach((p) => shown.add(p.join(".")));
  }
  const need = ["distance_nm", "t100_departures", "seats_per_departure", "load_factor", "passengers_per_flight",
    "unmapped_share", "fuel_kg_per_flight", "nox_kg_per_flight", "fuel_kg_per_passenger", "nox_kg_per_passenger"];
  for (const id of ["point_to_point", "hub_and_spoke"]) {
    DATA.pairs["BOS-SFO"][id].legs.forEach((_, i) => {
      for (const f of need) assert.ok(shown.has(`pairs.BOS-SFO.${id}.legs.${i}.${f}`), `${id} leg ${i} ${f}`);
    });
  }
});

test("rendered cell text for a known leg matches hand-computed strings (golden)", () => {
  /* KIND: golden. Hand arithmetic for BOS-ORD in the fixture: 150 seats x 0.8 = 120 pax; 6000 kg / 120 = 50 kg/pax;
     60 kg NOx / 120 = 0.5 kg/pax. Tolerance: exact strings.
     CANNOT CATCH: errors in the fixture's own arithmetic that equally affect expected values. */
  const leg = col(core.buildView(DATA, "BOS", "SFO"), "hub_and_spoke").legs[0];
  assert.equal(leg.title, "BOS \u2192 ORD");
  assert.equal(leg.passengersPerFlight.text, "120.0");
  assert.equal(leg.fuelPerFlight.text, "6,000 kg");
  assert.equal(leg.fuelPerPassenger.text, "50.0 kg");
  assert.equal(leg.noxPerPassenger.text, "0.500 kg");
  assert.equal(leg.loadFactor.text, "80%");
});

// ---------------------------------------------------------------- profiles / geometry

test("every leg's profile_key exists in the profiles file; every profile is referenced by some leg", () => {
  /* KIND: property (referential integrity between the two data files). Tolerance: none.
     CANNOT CATCH: integrity of the real files unless this test is also pointed at them (the page shows a note for a missing key). */
  const used = new Set();
  for (const p of Object.values(DATA.pairs)) for (const id of ["point_to_point", "hub_and_spoke"])
    for (const l of p[id].legs) if (l.profile_key) used.add(l.profile_key);
  for (const k of used) assert.ok(PROFILES.profiles[k], k);
  for (const k of Object.keys(PROFILES.profiles)) assert.ok(used.has(k), "unreferenced profile " + k);
});

test("profiles honour the contract: <=120 points, equal-length arrays, integer t_s/alt_ft, 1-decimal dist_nm, monotone time/distance", () => {
  /* KIND: property over all fixture profiles. Tolerance: 1e-9 for the 1-decimal check (float representation).
     CANNOT CATCH: physical plausibility of the trajectory. */
  for (const [k, p] of Object.entries(PROFILES.profiles)) {
    assert.ok(p.t_s.length <= 120 && p.t_s.length >= 2, k);
    assert.equal(p.alt_ft.length, p.t_s.length);
    assert.equal(p.dist_nm.length, p.t_s.length);
    p.t_s.forEach((t, i) => {
      assert.ok(Number.isInteger(t) && Number.isInteger(p.alt_ft[i]));
      assert.ok(Math.abs(p.dist_nm[i] * 10 - Math.round(p.dist_nm[i] * 10)) < 1e-9);
      if (i) assert.ok(t >= p.t_s[i - 1] && p.dist_nm[i] >= p.dist_nm[i - 1]);
    });
  }
});

test("profileGeometry: points stay inside the plot box, higher altitude -> smaller y, ticks cover the data", () => {
  /* KIND: property. Tolerance: 0.1 px (coordinates are rounded to 1 decimal).
     CANNOT CATCH: how the SVG actually looks (manual check in a browser). */
  const p = PROFILES.profiles["b738-2700"];
  const g = core.profileGeometry(p, 400, 200);
  const pts = g.d.split(/(?=[ML])/).map((s) => s.slice(1).split(" ").map(Number));
  assert.equal(pts.length, p.dist_nm.length);
  for (const [x, y] of pts) {
    assert.ok(x >= g.plot.left - 0.1 && x <= g.plot.right + 0.1);
    assert.ok(y >= g.plot.top - 0.1 && y <= g.plot.bottom + 0.1);
  }
  const iMax = p.alt_ft.indexOf(Math.max(...p.alt_ft));
  assert.equal(Math.min(...pts.map((q) => q[1])), pts[iMax][1]);
  assert.ok(g.xTicks[g.xTicks.length - 1].value >= Math.max(...p.dist_nm));
  assert.ok(g.yTicks[g.yTicks.length - 1].value >= Math.max(...p.alt_ft));
  assert.equal(g.xTicks[0].value, 0);
  assert.equal(core.profileGeometry({ dist_nm: [1], alt_ft: [1] }, 400, 200), null);
  assert.equal(core.profileGeometry(undefined, 400, 200), null);
});

// ---------------------------------------------------------------- page wiring (static checks)

test("project-nav.js has Trip tool right after Prototype and leaves the other labels alone", () => {
  /* KIND: example on source text. Tolerance: none.
     CANNOT CATCH: the nav rendering (manual browser check). */
  const src = fs.readFileSync(path.join(__dirname, "..", "project-nav.js"), "utf8");
  const labels = [...src.matchAll(/label:\s*"([^"]+)"/g)].map((m) => m[1]);
  const i = labels.indexOf("Prototype");
  assert.equal(labels[i + 1], "Trip tool");
  assert.match(src, /href:\s*"project-trip-tool\.html",\s*label:\s*"Trip tool"/);
  assert.equal(labels.length, 8);
});

test("trip-tool.js contains no arithmetic on fuel/NOx fields (display reads core output only)", () => {
  /* KIND: static source check. Tolerance: none. It looks for the JSON field names of per-flight/passenger quantities
     in the DOM file, which should never touch raw data.
     CANNOT CATCH: arithmetic hidden behind other names; a code-review item. */
  const src = fs.readFileSync(path.join(__dirname, "..", "trip-tool.js"), "utf8");
  assert.doesNotMatch(src, /fuel_kg|nox_kg|passengers_per_flight|seats_per_departure|load_factor/);
});

test("project-trip-tool.html has labelled selects, a swap button, an aria-live result region and the permanent caveats", () => {
  /* KIND: example on page source. Tolerance: none.
     CANNOT CATCH: actual accessibility behaviour (manual check) or contrast (checked by hand against css variables). */
  const html = fs.readFileSync(path.join(__dirname, "..", "..", "project-trip-tool.html"), "utf8");
  assert.match(html, /<label[^>]+for="origin"/);
  assert.match(html, /<label[^>]+for="destination"/);
  assert.match(html, /<select[^>]+id="origin"/);
  assert.match(html, /<select[^>]+id="destination"/);
  assert.match(html, /<button[^>]+id="swap"/);
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /population-weighted exposure/i);
  assert.match(html, /ADR 0003/);
  assert.match(html, /2019 Q1/);
  assert.match(html, /80% MTOW/);
  assert.match(html, /no wind/i);
  assert.match(html, /not (?:a )?finding|not findings/i);
  assert.match(html, /seats × load factor|seats &times; load factor/i);
  assert.match(html, /python3 -m http\.server/);
  assert.match(html, /<script src="js\/trip-tool-core\.js">/);
  assert.match(html, /<script src="js\/trip-tool\.js">/);
});


test("total_nm differences at the 2-decimal rounding size (0.01 over 2 legs) do NOT warn; 0.05 does", () => {
  /* KIND: boundary example, from the real top-50 file (e.g. AUS-BOS hub-and-spoke stored 1526.13 vs legs 1526.12).
     Tolerance: half a unit of the 2-decimal nm rounding (0.005) per addend, per docs/trip-schema.md.
     CANNOT CATCH: a real distance error smaller than ~0.015 nm (harmless at this precision). */
  const d = clone(DATA);
  const r = d.pairs["BOS-SFO"].hub_and_spoke;
  r.total_nm = r.legs.reduce((s, l) => s + l.distance_nm, 0) + 0.01;
  assert.deepEqual(core.buildView(d, "BOS", "SFO").warnings, []);
  r.total_nm += 0.04;
  assert.equal(core.buildView(d, "BOS", "SFO").warnings.length, 1);
});

// ---------------------------------------------------------------- comparability caution

test("caution: a structure with a leg above 20% unmapped makes the comparison flagged, naming the structure and leg share", () => {
  /* KIND: example + boundary. Tolerance: threshold is exactly 0.20 (a stated choice, not derived: above it the
     fuel of that leg is understated by at least a fifth because unmapped aircraft are excluded, not rescaled).
     CANNOT CATCH: whether 0.20 is the right threshold; the real file has 661 of 1,943 observed-nonstop pairs above it. */
  const d = clone(DATA);
  d.pairs["BOS-SFO"].hub_and_spoke.unmapped_share_max = 0.2;
  let v = core.buildView(d, "BOS", "SFO");
  assert.equal(v.caution, null, "exactly 0.20 is not above the threshold");
  d.pairs["BOS-SFO"].hub_and_spoke.unmapped_share_max = 0.2001;
  v = core.buildView(d, "BOS", "SFO");
  assert.ok(v.caution, "above 0.20 must flag");
  assert.match(v.caution, /hub-and-spoke/);
  assert.match(v.caution, /20\.0%|20%/);
  assert.match(v.caution, /not comparable|not a like-for-like/i);
  assert.doesNotMatch(v.caution, /point-to-point/);
});

test("caution: both structures above the threshold are both named; the fixture default has no caution", () => {
  /* KIND: example/property. Tolerance: none. CANNOT CATCH: wording quality. */
  const d = clone(DATA);
  d.pairs["BOS-SFO"].point_to_point.unmapped_share_max = 0.5;
  d.pairs["BOS-SFO"].hub_and_spoke.unmapped_share_max = 0.3;
  const v = core.buildView(d, "BOS", "SFO");
  assert.match(v.caution, /point-to-point/);
  assert.match(v.caution, /hub-and-spoke/);
  for (const key of Object.keys(DATA.pairs)) {
    const [o, dd] = key.split("-");
    assert.equal(core.buildView(DATA, o, dd).caution, null, key);
  }
});

test("caution reads the stored unmapped_share_max (no recomputation) and is independent of fuel values", () => {
  /* KIND: metamorphic. Changing fuel numbers must not change whether the caution shows. Tolerance: none.
     CANNOT CATCH: a stored unmapped_share_max that is itself wrong (checkTrip compares it with the legs). */
  const d = clone(DATA);
  d.pairs["BOS-SFO"].hub_and_spoke.fuel_kg_per_passenger += 50;
  d.pairs["BOS-SFO"].hub_and_spoke.legs.forEach((l) => { l.fuel_kg_per_passenger += 25; });
  assert.equal(core.buildView(d, "BOS", "SFO").caution, null);
});
