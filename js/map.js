// map.js
// Renders a world map SVG and animates 36 illustrative flight routes,
// revealing them one after another. Each landed plane becomes clickable —
// map.js dispatches a "flightmap:planeready" event; the easter egg logic
// (trail + tooltip) lives separately in egg.js and listens for that event.

(function () {
  const VIEWBOX_W = 1000;
  const VIEWBOX_H = 500;
  const REVEAL_INTERVAL_MS = 900; // gap between successive flights starting
  const FLIGHT_DURATION_MS = 1600; // how long a single flight takes to draw + fly

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

  function buildPlaneIcon() {
    // Simple chevron/plane glyph, points along +x by default.
    const g = el("g", { class: "plane-icon-group" });
    const body = el("path", {
      class: "plane-icon",
      d: "M -7,0 L 5,0 M 5,0 L 1,-4 M 5,0 L 1,4 M -7,0 L -3,-2 M -7,0 L -3,2",
    });
    g.appendChild(body);
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

  function revealRoute(svg, route, index) {
    const from = route.fromXY;
    const to = route.toXY;
    const d = pathD(from, to);

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

    svg.appendChild(group);

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

    // Plane that flies along the same path, then rests at the destination.
    const planeGroup = el("g", {
      class: "plane",
      transform: `translate(${from[0]},${from[1]})`,
      tabindex: "0",
      role: "button",
      "aria-label": `Flight ${route.from} to ${route.to}`,
    });
    planeGroup.appendChild(buildPlaneIcon());
    // Invisible larger hit target for easier clicking/tapping.
    planeGroup.appendChild(
      el("circle", { class: "plane-hit", cx: 0, cy: 0, r: 9 })
    );
    group.appendChild(planeGroup);

    animatePlane(planeGroup, line, () => {
      planeGroup.setAttribute("data-ready", "true");
      svg.dispatchEvent(
        new CustomEvent("flightmap:planeready", {
          detail: { element: planeGroup, pathEl: line, route, index },
        })
      );
    });
  }

  async function init() {
    const container = document.getElementById("map-container");
    if (!container) return; // not on the home page

    const [worldPath, routes] = await Promise.all([
      loadText("data/world-outline.path.txt"),
      loadJSON("data/routes.json"),
    ]);

    const svg = el("svg", {
      viewBox: `0 0 ${VIEWBOX_W} ${VIEWBOX_H}`,
      class: "map-svg",
      role: "img",
      "aria-label":
        "Animated world map showing illustrative flight routes between continents",
    });

    const land = el("path", { class: "land", d: worldPath });
    svg.appendChild(land);

    // A dedicated layer the easter egg draws into, kept separate so trail
    // effects never interfere with route/plane hit-testing.
    const trailLayer = el("g", { class: "emission-layer" });
    svg.appendChild(trailLayer);
    svg.setAttribute("data-trail-layer", "true");

    container.appendChild(svg);
    container.dataset.svgReady = "true";

    routes.forEach((route, index) => {
      setTimeout(() => revealRoute(svg, route, index), index * REVEAL_INTERVAL_MS);
    });

    // Expose for egg.js
    window.FlightMap = { svg, trailLayer, routes };
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
