// trip-tool.js
// DOM wiring for project-trip-tool.html. All logic and all numbers come from
// TripToolCore (js/trip-tool-core.js); this file only builds elements from the
// rows it returns. Text is always set with textContent (no innerHTML), so data
// can never inject markup.

(function () {
  "use strict";
  var Core = window.TripToolCore;
  var TRIPS_URL = "data/trips_top50.json";
  var PROFILES_URL = "data/trip_profiles.json";

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
    })
    .catch(fail);
})();
