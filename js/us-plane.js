// us-plane.js
// A small looping animation for the Fig 1 US demand map on the proposal
// page: one plane, flying between a handful of the plotted airports, on
// repeat. Deliberately tiny and self-contained -- no shared state with
// js/map.js (the home page globe), just borrows the same visual language.

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // A short loop through a few of the airport dots already drawn in the
  // Fig 1 markup (see project.html), reusing their coordinates so the
  // plane visibly starts/ends on real airport bubbles.
  const STOPS = [
    [425, 233], // ATL
    [526, 154], // JFK
    [305, 241], // DFW
    [103, 230], // LAX
    [392, 139], // ORD
    [425, 233], // back to ATL
  ];

  const SPEED_PX_PER_SEC = 70;
  const PAUSE_MS = 500;

  function el(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      node.setAttribute(k, v);
    }
    return node;
  }

  function arc([x1, y1], [x2, y2], bow = 0.16) {
    const mx = (x1 + x2) / 2;
    const my = (y1 + y2) / 2;
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len = Math.sqrt(dx * dx + dy * dy) || 1;
    const nx = -dy / len;
    const ny = dx / len;
    const cx = mx + nx * len * bow;
    const cy = my + ny * len * bow;
    return [cx, cy];
  }

  // Reuse the Material "flight" glyph from js/map.js so the two planes
  // on the site look identical.
  const PLANE_ICON_PATH =
    "M340-80v-60l80-60v-220L80-320v-80l340-200v-220q0-25 17.5-42.5T480-880q25 0 42.5 17.5T540-820v220l340 200v80L540-420v220l80 60v60l-140-40-140 40Z";

  function buildPlane() {
    const g = el("g", { class: "us-plane-group" });
    const inner = el("g", {
      transform: "rotate(90) scale(0.011) translate(-480,-480)",
    });
    inner.appendChild(el("path", { class: "us-plane-icon", d: PLANE_ICON_PATH }));
    g.appendChild(inner);
    return g;
  }

  function animate(planeGroup, pathEl, durationMs, onDone) {
    const total = pathEl.getTotalLength();
    const start = performance.now();

    function frame(now) {
      const t = Math.min(1, (now - start) / durationMs);
      const dist = t * total;
      const p = pathEl.getPointAtLength(dist);
      const ahead = pathEl.getPointAtLength(Math.min(total, dist + 1));
      const angle = (Math.atan2(ahead.y - p.y, ahead.x - p.x) * 180) / Math.PI;
      planeGroup.setAttribute("transform", `translate(${p.x},${p.y}) rotate(${angle})`);
      if (t < 1) {
        requestAnimationFrame(frame);
      } else {
        onDone();
      }
    }
    requestAnimationFrame(frame);
  }

  function init() {
    const svg = document.getElementById("fig1-us-map");
    if (!svg) return;

    const layer = el("g", { class: "us-plane-layer" });
    svg.appendChild(layer);

    const plane = buildPlane();
    layer.appendChild(plane);

    let i = 0;

    function next() {
      const from = STOPS[i % (STOPS.length - 1)];
      const to = STOPS[(i + 1) % STOPS.length];
      const [cx, cy] = arc(from, to);
      const d = `M ${from[0]},${from[1]} Q ${cx},${cy} ${to[0]},${to[1]}`;

      const path = el("path", { class: "us-plane-route", d });
      layer.insertBefore(path, plane);

      const len = path.getTotalLength();
      const duration = (len / SPEED_PX_PER_SEC) * 1000;

      path.style.strokeDasharray = String(len);
      path.style.strokeDashoffset = String(len);
      path.style.transition = `stroke-dashoffset ${duration}ms linear`;
      path.getBoundingClientRect();
      requestAnimationFrame(() => {
        path.style.strokeDashoffset = "0";
      });

      plane.setAttribute("transform", `translate(${from[0]},${from[1]})`);

      animate(plane, path, duration, () => {
        i += 1;
        setTimeout(() => {
          path.style.transition = "opacity 0.8s ease";
          path.style.opacity = "0";
          setTimeout(() => path.remove(), 850);
        }, 300);
        setTimeout(next, PAUSE_MS);
      });
    }

    next();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
