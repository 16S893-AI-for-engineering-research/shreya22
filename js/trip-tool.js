// trip-tool.js
// DOM wiring for project-trip-tool.html. All logic and all numbers come from
// TripToolCore (js/trip-tool-core.js); this file only builds elements from the
// rows it returns. Text is always set with textContent (no innerHTML), so data
// can never inject markup.

(function () {
  "use strict";
  var Core = window.TripToolCore;
  var Geo = window.TripToolGeo;
  var TRIPS_URL = "data/trips_top50.json";
  var PROFILES_URL = "data/trip_profiles.json";
  var AIRPORTS_URL = "data/trip_airports.json";
  var OUTLINE_URL = "data/world-outline.path.txt";

  var el = {
    origin: document.getElementById("origin"),
    destination: document.getElementById("destination"),
    swap: document.getElementById("swap"),
    status: document.getElementById("status"),
    result: document.getElementById("result"),
    help: document.getElementById("fetch-help"),
    source: document.getElementById("source"),
  };
  var trips = null;
  var profiles = null;
  // Map inputs: undefined = still loading, null = failed to load.
  var airports;
  var outlineD;
  var outlineNode = null;
  var mapsRenderedWidth = 0;
  var state = Core.parseHash(window.location.hash);

  function h(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function setStatus(msg, isError) {
    el.status.textContent = msg || "";
    el.status.className = "status-msg" + (isError ? " status-msg--error" : "");
  }

  // ---------- altitude profile (inline SVG) ----------

  var NS = "http://www.w3.org/2000/svg";
  function s(tag, attrs, text) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) { e.setAttribute(k, attrs[k]); });
    if (text != null) e.textContent = text;
    return e;
  }

  function profileFigure(row, colId, legTitle) {
    var fig = h("figure", "profile");
    fig.style.margin = "8px 0 0";
    var prof = row.profileKey && profiles && profiles.profiles ? profiles.profiles[row.profileKey] : null;
    var W = 420, H = 220;
    var g = prof ? Core.profileGeometry(prof, W, H) : null;
    if (!g) {
      var why = !profiles ? "Profile file not loaded." :
        !row.profileKey ? "No OpenAP aircraft on this leg, so no trajectory." :
        "No profile found for " + row.profileKey + ".";
      fig.appendChild(h("p", "profile-missing", "Altitude profile unavailable. " + why));
      return fig;
    }
    var svg = s("svg", { viewBox: "0 0 " + W + " " + H, role: "img",
      "aria-label": "Altitude in feet against distance in nautical miles for " + legTitle +
        ", idealized OpenAP trajectory, aircraft " + row.profileKey.split("-")[0] });
    svg.appendChild(s("title", {}, "Altitude vs distance, " + legTitle));
    g.yTicks.forEach(function (t) {
      svg.appendChild(s("line", { class: "grid", x1: g.plot.left, x2: g.plot.right, y1: t.y, y2: t.y }));
      svg.appendChild(s("text", { x: g.plot.left - 5, y: t.y + 3, "text-anchor": "end" }, Core.fmtNumber(t.value, 0)));
    });
    g.xTicks.forEach(function (t) {
      svg.appendChild(s("line", { class: "ax", x1: t.x, x2: t.x, y1: g.plot.bottom, y2: g.plot.bottom + 4 }));
      svg.appendChild(s("text", { x: t.x, y: g.plot.bottom + 15, "text-anchor": "middle" }, Core.fmtNumber(t.value, 0)));
    });
    svg.appendChild(s("path", { class: "ax", d: "M" + g.plot.left + " " + g.plot.top + " V" + g.plot.bottom + " H" + g.plot.right }));
    svg.appendChild(s("path", { class: "curve " + (colId === "point_to_point" ? "curve--p2p" : "curve--hs"), d: g.d }));
    svg.appendChild(s("text", { x: (g.plot.left + g.plot.right) / 2, y: H - 4, "text-anchor": "middle" }, "Distance flown (nm)"));
    svg.appendChild(s("text", { x: 11, y: (g.plot.top + g.plot.bottom) / 2, "text-anchor": "middle",
      transform: "rotate(-90 11 " + (g.plot.top + g.plot.bottom) / 2 + ")" }, "Altitude (ft)"));
    fig.appendChild(svg);
    fig.appendChild(h("figcaption", null, "Idealized OpenAP trajectory (" + row.profileKey.split("-")[0].toUpperCase() +
      ", top aircraft type on this leg; 80% MTOW, no wind). Not the flown track."));
    return fig;
  }

  // ---------- ground-track maps (geometry in TripToolGeo) ----------

  var LEGEND_NOTE = "Ground tracks are idealized great circles; real routings differ.";

  // Width one map will have, in CSS px: two columns above 720 px, one below,
  // minus the figure's padding and border (26 px) - see css/trip-tool.css. Used
  // only to size dots and text, so a few px of error is harmless.
  var FIG_CHROME_PX = 26;
  function mapWidthPx() {
    var w = el.result.clientWidth || 520;
    return (window.innerWidth <= 720 ? w : (w - 16) / 2) - FIG_CHROME_PX;
  }

  function basemap() {
    if (!outlineNode) {
      outlineNode = s("path", { class: "gt-land", d: outlineD, "vector-effect": "non-scaling-stroke" });
    }
    return outlineNode.cloneNode(false);
  }

  function polyline(points, cls, dashed) {
    var p = s("path", { class: cls + (dashed ? " gt-leg--dashed" : " gt-leg--solid"), d: Geo.pathD(points), fill: "none",
      "vector-effect": "non-scaling-stroke" });
    return p;
  }

  function drawDot(d, u, cls) {
    var g = s("g", { class: "gt-dot gt-dot--" + d.role });
    if (d.role === "origin") {
      g.appendChild(s("circle", { cx: d.x, cy: d.y, r: 5 * u, class: "gt-fill " + cls, "vector-effect": "non-scaling-stroke" }));
    } else if (d.role === "destination") {
      g.appendChild(s("rect", { x: d.x - 5 * u, y: d.y - 5 * u, width: 10 * u, height: 10 * u, class: "gt-fill " + cls, "vector-effect": "non-scaling-stroke" }));
    } else {
      g.appendChild(s("circle", { cx: d.x, cy: d.y, r: 8 * u, class: "gt-ring " + cls, "vector-effect": "non-scaling-stroke" }));
      g.appendChild(s("circle", { cx: d.x, cy: d.y, r: 3 * u, class: "gt-fill " + cls, "vector-effect": "non-scaling-stroke" }));
    }
    return g;
  }

  var mapIdSeq = 0;
  function mapSvg(m, vbStr, vb, u) {
    var id = "gt" + (mapIdSeq++);
    var isP2P = m.id === "point_to_point";
    var cls = isP2P ? "gt--p2p" : "gt--hs";
    var svg = s("svg", { viewBox: vbStr, role: "img", "aria-labelledby": id + "-t " + id + "-d", class: "gt-svg " + cls,
      preserveAspectRatio: "xMidYMid meet" });
    svg.appendChild(s("title", { id: id + "-t" }, m.title));
    svg.appendChild(s("desc", { id: id + "-d" }, m.desc));
    svg.setAttribute("font-size", Geo.LABEL_FONT_PX * u);
    if (outlineD) svg.appendChild(basemap());
    if (!m.ok) return svg;
    // legs first, then number badges, then dots and labels on top
    m.arcs.forEach(function (a) { svg.appendChild(polyline(a.points, "gt-leg " + cls, a.dashed)); });
    m.arcs.forEach(function (a) {
      if (a.number == null) return;
      var g = s("g", { class: "gt-badge" });
      g.appendChild(s("circle", { cx: a.mid[0], cy: a.mid[1], r: 6.5 * u, class: "gt-badge-bg " + cls, "vector-effect": "non-scaling-stroke" }));
      g.appendChild(s("text", { x: a.mid[0], y: a.mid[1] + 3.6 * u, "text-anchor": "middle", class: "gt-badge-num" }, a.number));
      svg.appendChild(g);
    });
    m.dots.forEach(function (d) {
      svg.appendChild(drawDot(d, u, cls));
      svg.appendChild(s("text", { x: d.x + d.labelDx * u, y: d.y + d.labelDy * u, "text-anchor": d.labelAnchor,
        class: "gt-label" + (d.role === "hub" ? " gt-label--hub" : ""), "stroke-width": 3 * u }, d.label));
    });
    return svg;
  }

  function mapUnavailable(msg) {
    var p = h("p", "gt-error", msg);
    p.setAttribute("role", "status");
    return p;
  }

  function legendSvgSample(kind) {
    // tiny 26 x 12 px inline sample of a line / dot style for the legend
    var svg = s("svg", { width: 26, height: 14, viewBox: "0 0 26 14", "aria-hidden": "true", focusable: "false", class: "gt-sample" });
    if (kind === "solid" || kind === "dashed") {
      svg.appendChild(s("line", { x1: 1, y1: 7, x2: 25, y2: 7, class: "gt-leg gt--ink " + (kind === "dashed" ? "gt-leg--dashed" : "gt-leg--solid") }));
    } else if (kind === "origin") {
      svg.appendChild(s("circle", { cx: 13, cy: 7, r: 5, class: "gt-fill gt--ink" }));
    } else if (kind === "destination") {
      svg.appendChild(s("rect", { x: 8, y: 2, width: 10, height: 10, class: "gt-fill gt--ink" }));
    } else {
      svg.appendChild(s("circle", { cx: 13, cy: 7, r: 6, class: "gt-ring gt--ink" }));
      svg.appendChild(s("circle", { cx: 13, cy: 7, r: 2.2, class: "gt-fill gt--ink" }));
    }
    return svg;
  }

  function legend() {
    var ul = h("ul", "gt-legend");
    ul.setAttribute("aria-label", "Map legend");
    [["solid", "nonstop leg (solid line)"], ["dashed", "leg of a connecting trip (dashed line, numbered 1, 2)"],
     ["origin", "origin (filled circle)"], ["destination", "destination (filled square)"],
     ["hub", "hub / connection airport (large ringed dot, labelled \u201chub\u201d)"]].forEach(function (x) {
      var li = h("li");
      li.appendChild(legendSvgSample(x[0]));
      li.appendChild(document.createTextNode(" " + x[1]));
      ul.appendChild(li);
    });
    return ul;
  }

  function renderMaps(trip) {
    var sec = h("section", "ground-tracks");
    sec.setAttribute("aria-labelledby", "gt-title");
    var title = h("h3", "gt-title", "Ground tracks");
    title.id = "gt-title";
    sec.appendChild(title);
    var grid = h("div", "gt-grid");
    var widthPx = mapWidthPx();
    mapsRenderedWidth = widthPx;
    var model = airports ? Geo.buildMaps(trip, airports, { widthPx: widthPx }) : null;
    var fallbackModel = model || Geo.buildMaps(trip, { airports: {} }, { widthPx: widthPx });
    fallbackModel.maps.forEach(function (m) {
      var fig = h("figure", "gt-fig " + (m.id === "point_to_point" ? "gt-fig--p2p" : "gt-fig--hs"));
      fig.appendChild(h("h4", "gt-fig-title", m.heading));
      if (airports === undefined) {
        fig.appendChild(mapUnavailable("Loading map data\u2026"));
      } else if (airports === null) {
        fig.appendChild(mapUnavailable("Map unavailable: " + AIRPORTS_URL + " could not be loaded."));
      } else if (!m.ok) {
        fig.appendChild(mapUnavailable(m.error));
      } else {
        fig.appendChild(mapSvg(m, fallbackModel.viewBox.str, fallbackModel.viewBox, fallbackModel.unit));
      }
      var cap = h("figcaption");
      var lines = h("ul", "gt-caption");
      m.caption.forEach(function (c) { lines.appendChild(h("li", null, c)); });
      cap.appendChild(lines);
      if (m.note) cap.appendChild(h("p", "gt-note", m.note));
      fig.appendChild(cap);
      grid.appendChild(fig);
    });
    sec.appendChild(grid);
    sec.appendChild(legend());
    sec.appendChild(h("p", "gt-ideal", LEGEND_NOTE));
    return sec;
  }

  // ---------- columns ----------

  function renderColumn(col, view) {
    var isP2P = col.id === "point_to_point";
    var sec = h("section", "compare__col " + (isP2P ? "compare__col--p2p" : "compare__col--hs"));
    sec.setAttribute("aria-labelledby", "h-" + col.id);
    var title = h("h3", null, col.heading);
    title.id = "h-" + col.id;
    sec.appendChild(title);
    if (view.connecting) {
      sec.appendChild(h("p", "compare__same", "Same connecting trip as the " + (isP2P ? "hub-and-spoke" : "point-to-point") + " column (ADR 0007)."));
    }

    var legList = null, dl = null, legTitle = "";
    var totals = null;
    col.rows.forEach(function (r) {
      switch (r.type) {
        case "route":
          sec.appendChild(h("p", "route-line", r.text));
          break;
        case "hub":
          sec.appendChild(h("p", "hub-line", r.label + ": " + r.text + "."));
          break;
        case "candidate":
          if (!dl) {
            var cl = h("dl"); cl.style.margin = "0 0 8px"; cl.className = "cands";
            cl.style.display = "grid"; cl.style.gridTemplateColumns = "1fr auto"; cl.style.fontSize = "13px";
            sec.appendChild(cl);
            dl = cl;
          }
          dl.appendChild(h("dt", null, r.label));
          dl.appendChild(h("dd", null, r.text)).style.margin = "0";
          break;
        case "leg":
          dl = null;
          legList = h("div", "leg");
          legTitle = r.text;
          legList.appendChild(h("h4", null, "Leg " + (r.legIndex + 1) + ": " + r.text));
          dl = h("dl");
          legList.appendChild(dl);
          sec.appendChild(legList);
          break;
        case "metric":
        case "mix": {
          if (r.type === "mix" && /^Aircraft:/.test(r.label) && !dl.dataset.mix) {
            dl.dataset.mix = "1";
            dl.appendChild(h("div", "mix-title", "Aircraft mix (share of departures)"));
          }
          var key = /per passenger/.test(r.label) ? " key-metric" : "";
          var wrap = h("div", key.trim());
          wrap.style.display = "contents";
          if (key) { wrap.className = "key-metric"; }
          wrap.appendChild(h("dt", null, r.label.replace(/^Aircraft: /, "")));
          wrap.appendChild(h("dd", null, r.text));
          dl.appendChild(wrap);
          break;
        }
        case "profile":
          legList.appendChild(profileFigure(r, col.id, legTitle));
          break;
        case "totals":
          totals = h("div", "totals");
          totals.appendChild(h("h4", null, "Trip total (" + r.text + ")"));
          dl = h("dl");
          totals.appendChild(dl);
          sec.appendChild(totals);
          break;
        case "total": {
          var w = h("div", /per passenger/.test(r.label) ? "key-metric" : "");
          w.style.display = "contents";
          w.appendChild(h("dt", null, r.label));
          w.appendChild(h("dd", null, r.text));
          dl.appendChild(w);
          break;
        }
      }
    });
    return sec;
  }

  function renderSummary(view, rowsCols) {
    var sm = Core.summary(view);
    var t = h("table", "summary-table");
    t.appendChild(h("caption", null, "Summary per passenger, " + view.origin + " to " + view.destination));
    var head = h("tr");
    ["Structure", "Route", "Distance", "Fuel per passenger", "NOx per passenger"].forEach(function (x) {
      var th = h("th", null, x); th.scope = "col"; head.appendChild(th);
    });
    var thead = h("thead"); thead.appendChild(head); t.appendChild(thead);
    var tb = h("tbody");
    sm.forEach(function (r) {
      var tr = h("tr");
      var th = h("th", null, r.heading); th.scope = "row"; tr.appendChild(th);
      tr.appendChild(h("td", null, r.route));
      tr.appendChild(h("td", null, r.distance.text));
      tr.appendChild(h("td", null, r.fuelPerPassenger.text));
      tr.appendChild(h("td", null, r.noxPerPassenger.text));
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    return t;
  }

  function render() {
    el.result.textContent = "";
    if (!trips) return;
    var view = Core.buildView(trips, state.o, state.d);
    if (!view.ok) {
      setStatus(view.error.message, true);
      el.result.setAttribute("aria-busy", "false");
      return;
    }
    setStatus("");
    var frag = document.createDocumentFragment();
    frag.appendChild(h("h2", "trip-title", view.origin + " \u2192 " + view.destination));
    frag.appendChild(h("p", "trip-context",
      "Great-circle distance " + view.context.greatCircle.text + ". DB1B passengers on this directed pair in the quarter: " +
      view.context.db1bPassengers.text + (view.context.db1bNonstopShare.value != null
        ? "; " + view.context.db1bNonstopShare.text + " of them flew nonstop." : ".")));
    if (view.warnings.length) {
      var wb = h("div", "warn-box");
      wb.setAttribute("role", "alert");
      wb.appendChild(h("span", null, "the stored totals disagree with the legs, so treat the numbers below with caution."));
      var ul = h("ul");
      view.warnings.forEach(function (w) { ul.appendChild(h("li", null, w)); });
      wb.appendChild(ul);
      frag.appendChild(wb);
    }
    if (view.banner) frag.appendChild(h("div", "banner", view.banner));
    if (view.caution) {
      var cb = h("div", "caution-box", view.caution);
      cb.setAttribute("role", "note");
      frag.appendChild(cb);
    }
    var scroller = h("div", "summary-scroll");
    scroller.tabIndex = 0;
    scroller.setAttribute("role", "region");
    scroller.setAttribute("aria-label", "Summary table (scrolls sideways on narrow screens)");
    scroller.appendChild(renderSummary(view));
    frag.appendChild(scroller);
    frag.appendChild(renderMaps(trips.pairs[view.key]));
    var grid = h("div", "compare");
    Core.renderRows(view).forEach(function (col) { grid.appendChild(renderColumn(col, view)); });
    frag.appendChild(grid);
    if (view.messages.length) {
      var box = h("div", "card");
      box.appendChild(h("h3", null, "Notes for this pair"));
      var ml = h("ul"); ml.style.margin = "0";
      view.messages.forEach(function (m) { ml.appendChild(h("li", null, m)); });
      box.appendChild(ml);
      frag.appendChild(box);
    }
    el.result.appendChild(frag);
    el.result.setAttribute("aria-busy", "false");
    document.title = "Trip tool " + view.key + " \u2014 Shreya Sharma";
  }

  // ---------- controls ----------

  function syncSelects() {
    el.origin.value = state.o;
    el.destination.value = state.d;
  }

  function go(action) {
    state = Core.nextState(state, action);
    var hash = Core.formatHash(state);
    if (window.location.hash !== hash) window.location.hash = hash; // hashchange re-renders
    else render();
  }

  function fillSelect(sel, airports) {
    sel.textContent = "";
    airports.forEach(function (a) {
      var o = document.createElement("option");
      o.value = a.iata;
      o.textContent = a.label;
      sel.appendChild(o);
    });
  }

  function showSource(src) {
    el.source.textContent = "";
    el.source.appendChild(h("strong", null, "Source: "));
    var short = function (x) { return String(x).slice(0, 12); };
    el.source.appendChild(document.createTextNode(
      "BTS DB1B Market and T-100 Segment, " + src.quarter +
      " (DB1B sha256 " + short(src.db1b_sha256) + "\u2026; T-100 sha256 " + (src.t100_sha256 || []).map(short).join(", ") +
      "\u2026; airports sha256 " + short(src.airports_sha256) + "\u2026); OpenAP " + src.openap_version +
      "; nonstop = at least " + src.nonstop_min_departures + " T-100 departures in the quarter. " +
      (src.notes || []).join(" ")));
  }

  function enable(airports) {
    fillSelect(el.origin, airports);
    fillSelect(el.destination, airports);
    [el.origin, el.destination, el.swap].forEach(function (c) { c.disabled = false; });
    el.origin.addEventListener("change", function () { go({ type: "origin", value: el.origin.value }); });
    el.destination.addEventListener("change", function () { go({ type: "destination", value: el.destination.value }); });
    el.swap.addEventListener("click", function () { go({ type: "swap" }); });
    window.addEventListener("hashchange", function () {
      state = Core.nextState(state, { type: "hash", value: window.location.hash });
      syncSelects();
      render();
    });
  }

  function fail(err) {
    el.result.setAttribute("aria-busy", "false");
    if (window.location.protocol === "file:") {
      setStatus("Cannot load the trip data from a file:// page. Run \u2018python3 -m http.server\u2019 from the repository root and open this page through http://localhost:8000.", true);
      el.help.hidden = false;
    } else {
      setStatus("Could not load the trip data (" + (err && err.message ? err.message : err) + "). Try reloading; if it persists the data files may not be published yet.", true);
    }
  }

  function getJSON(url) {
    return fetch(url).then(function (r) {
      if (!r.ok) throw new Error(url + " returned HTTP " + r.status);
      return r.json();
    });
  }

  window.addEventListener("resize", function () {
    if (trips && Math.abs(mapWidthPx() - mapsRenderedWidth) > 8) render();
  });

  getJSON(TRIPS_URL)
    .then(function (data) {
      var v = Core.validateData(data);
      if (!v.ok) throw new Error(v.error.message);
      trips = data;
      enable(Core.listAirports(data));
      showSource(data.source);
      syncSelects();
      setStatus("");
      render();
      // Profiles are optional: the comparison works without the plots.
      getJSON(PROFILES_URL).then(function (p) { profiles = p; render(); }, function () { render(); });
      // Maps are optional too: a failure is shown in place of the maps, never as a crash.
      getJSON(AIRPORTS_URL).then(function (a) { airports = a; render(); }, function () { airports = null; render(); });
      fetch(OUTLINE_URL).then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.text();
      }).then(function (t) { outlineD = t.trim(); outlineNode = null; render(); }, function () { outlineD = null; });
    })
    .catch(fail);
})();
