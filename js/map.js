// map.js
// Renders a world map SVG and animates ONE aircraft that flies through the
// 36 illustrative routes in sequence, one flight at a time. Only one plane
// is ever visible on the map. map.js dispatches a "flightmap:planeready"
// event each time the aircraft completes a flight; the easter egg logic
// (trail + tooltip) lives separately in egg.js and listens for that event.

(function () {
  const VIEWBOX_W = 1000;
  const VIEWBOX_H = 500;
  const GAP_BETWEEN_FLIGHTS_MS = 700; // pause at destination before next takeoff
  const FLIGHT_DURATION_MS = 4200; // slower, more readable flight speed

  const SVG_NS = "http://www.w3.org/2000/svg";

  function el(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      node.setAttribute(k, v);
    }
    return node;
  }

  // Quadratic bezier control point that "bows" the line, matching the
  // approved Style A mockup curvature.
  function arcControlPoint([x1, y1], [x2, y2], bow = 0.18) {
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const nx = -dy;
    const ny = dx;
    const len = Math.sqrt(nx * nx + ny * ny) || 1;
    const cx = mx + (nx / len) * len * bow;
    const cy = my + (ny / len) * len * bow - Math.abs(dy) * 0.05;
    return [cx, cy];
  }

  function pathD(from, to) {
    const [cx, cy] = arcControlPoint(from, to);
    return `M ${from[0]},${from[1]} Q ${cx},${cy} ${to[0]},${to[1]}`;
  }

  // A proper aircraft glyph (Material Symbols "flight" icon path), which
  // points "up" (+y is down in SVG, so "up" = -y) in its native
  // orientation. We bake in a +90deg rotation here so that the icon's
  // zero-rotation state points along +x, matching the atan2-based
  // direction-of-travel angle used in animatePlane.
  const PLANE_ICON_PATH =
    "M340-80v-60l80-60v-220L80-320v-80l340-200v-220q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820v220l340 200v80L540-420v220l80 60v60l-140-40-140 40Z";

  function buildPlaneIcon() {
    const g = el("g", { class: "plane-icon-group" });
    const inner = el("g", {
      transform: "rotate(90) scale(0.018) translate(-480,-480)",
    });
    const body = el("path", {
      class: "plane-icon",
      d: PLANE_ICON_PATH,
    });
    inner.appendChild(body);
    g.appendChild(inner);
    return g;
  }

  async function loadText(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to load " + url);
    return res.text();
  }

  async function loadJSON(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error("Failed to load " + url);
    return res.json();
  }

  function shuffle(array) {
    const copy = array.slice();
    for (let i = copy.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [copy[i], copy[j]] = [copy[j], copy[i]];
    }
    return copy;
  }

  function animatePlane(planeGroup, pathEl, onDone) {
    const totalLength = pathEl.getTotalLength();
    const start = performance.now();

    function frame(now) {
      const t = Math.min(1, (now - start) / FLIGHT_DURATION_MS);
      const dist = t * totalLength;
      const point = pathEl.getPointAtLength(dist);
      const lookAheadDist = Math.min(totalLength, dist + 1);
      const lookAhead = pathEl.getPointAtLength(lookAheadDist);
      const angle =
        (Math.atan2(lookAhead.y - point.y, lookAhead.x - point.x) * 180) /
        Math.PI;

      planeGroup.setAttribute(
        "transform",
        `translate(${point.x},${point.y}) rotate(${angle})`
      );

      if (t < 1) {
        requestAnimationFrame(frame);
      } else {
        onDone();
      }
    }

    requestAnimationFrame(frame);
  }

  function flyRoute(svg, planeGroup, route, index, onDone) {
    const from = route.fromXY;
    const to = route.toXY;
    const d = pathD(from, to);

    const routeLayer = svg.querySelector(".route-layer");

    const group = el("g", { class: "route", "data-index": index });
    const line = el("path", { class: "route-line", d });
    group.appendChild(line);

    const dotFrom = el("circle", {
      class: "city-dot",
      cx: from[0],
      cy: from[1],
      r: 2.4,
    });
    const dotTo = el("circle", {
      class: "city-dot",
      cx: to[0],
      cy: to[1],
      r: 2.4,
    });
    group.appendChild(dotFrom);
    group.appendChild(dotTo);
    routeLayer.appendChild(group);

    // Draw-in reveal via stroke-dasharray/dashoffset.
    const totalLength = line.getTotalLength();
    line.style.strokeDasharray = String(totalLength);
    line.style.strokeDashoffset = String(totalLength);
    line.style.transition = `stroke-dashoffset ${FLIGHT_DURATION_MS}ms linear`;
    // Force a reflow before triggering the transition.
    // eslint-disable-next-line no-unused-expressions
    line.getBoundingClientRect();
    requestAnimationFrame(() => {
      line.style.strokeDashoffset = "0";
    });

    planeGroup.setAttribute("transform", `translate(${from[0]},${from[1]})`);
    planeGroup.setAttribute(
      "aria-label",
      `Flight ${route.from} to ${route.to}`
    );
    planeGroup.dataset.ready = "false";

    animatePlane(planeGroup, line, () => {
      planeGroup.dataset.ready = "true";
      svg.dispatchEvent(
        new CustomEvent("flightmap:planeready", {
          detail: { element: planeGroup, pathEl: line, routeGroup: group, route, index },
        })
      );
      onDone(group, line);
    });
  }

  async function init() {
    const container = document.getElementById("map-container");
    if (!container) return; // not on the home page

    const [worldPath, routesRaw] = await Promise.all([
      loadText("data/world-outline.path.txt"),
      loadJSON("data/routes.json"),
    ]);

    const routes = shuffle(routesRaw);

    const svg = el("svg", {
      viewBox: `0 0 ${VIEWBOX_W} ${VIEWBOX_H}`,
      class: "map-svg",
      role: "img",
      "aria-label":
        "Animated world map showing a single aircraft flying illustrative routes between continents",
    });

    const land = el("path", { class: "land", d: worldPath });
    svg.appendChild(land);

    // Route lines/dots live in their own layer, kept separate from the
    // emissions layer so route fade-out (tweak #2) never touches trails.
    const routeLayer = el("g", { class: "route-layer" });
    svg.appendChild(routeLayer);

    // A dedicated layer the easter egg draws into, kept separate so trail
    // effects never interfere with route/plane hit-testing.
    const trailLayer = el("g", { class: "emission-layer" });
    svg.appendChild(trailLayer);

    // Single persistent aircraft, created once and reused across flights.
    const planeGroup = el("g", {
      class: "plane",
      tabindex: "0",
      role: "button",
    });
    planeGroup.appendChild(buildPlaneIcon());
    planeGroup.appendChild(el("circle", { class: "plane-hit", cx: 0, cy: 0, r: 10 }));
    svg.appendChild(planeGroup);

    container.appendChild(svg);
    container.dataset.svgReady = "true";

    let i = 0;
    function next() {
      const route = routes[i % routes.length];
      flyRoute(svg, planeGroup, route, i, (routeGroup, lineEl) => {
        // Fade the route line out shortly after arrival, per tweak #2 —
        // the path disappears but any emissions trail (drawn separately
        // in the trail layer by egg.js) remains untouched.
        setTimeout(() => {
          routeGroup.style.transition = "opacity 1.2s ease";
          routeGroup.style.opacity = "0";
          setTimeout(() => routeGroup.remove(), 1300);
        }, 900);

        i += 1;
        setTimeout(next, GAP_BETWEEN_FLIGHTS_MS);
      });
    }
    next();

    // Expose for egg.js / debugging
    window.FlightMap = { svg, trailLayer, routeLayer, planeGroup, routes };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
