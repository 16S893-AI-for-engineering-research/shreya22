// egg.js
// The easter egg: click (or press Enter/Space on) the aircraft, or click
// anywhere along its currently-flying route line, and a lingering
// "emissions trail" spreads along that route, plus a one-line
// aviation-emissions fact appears near the plane.
//
// map.js keeps window.FlightMap.currentFlight up to date with whichever
// flight is in progress. Rather than binding a listener per plane/route
// element (which risks missing the very first flight if egg.js attaches
// a moment late), we bind ONE delegated click listener on the whole SVG
// and just read the live currentFlight value at click-time.

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const PARTICLE_COUNT = 18;
  const SPREAD_DURATION_MS = 7000; // slow, gradual spread rather than a quick puff

  let facts = [];
  let factIndex = 0;
  const triggered = new Set(); // flight indices already popped, so repeat
  // clicks on the same flight don't restart or duplicate its trail.
  let currentTooltip = null; // only one fact bubble on screen at a time

  function el(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      node.setAttribute(k, v);
    }
    return node;
  }

  async function loadFacts() {
    try {
      const res = await fetch("data/emissions-facts.json");
      facts = await res.json();
    } catch (err) {
      facts = ["Aviation is a meaningful and growing source of global CO\u2082 emissions."];
    }
  }

  function nextFact() {
    if (facts.length === 0) return "";
    const fact = facts[factIndex % facts.length];
    factIndex += 1;
    return fact;
  }

  function ensureTooltipHost() {
    let host = document.getElementById("emission-tooltip-host");
    if (!host) {
      host = document.createElement("div");
      host.id = "emission-tooltip-host";
      host.style.position = "relative";
      document.body.appendChild(host);
    }
    return host;
  }

  function dismissTooltip() {
    if (!currentTooltip) return;
    const tip = currentTooltip;
    currentTooltip = null;
    tip.style.opacity = "0";
    setTimeout(() => tip.remove(), 250);
  }

  function showTooltip(pageX, pageY, text) {
    // The previous fact bubble disappears as soon as a new one is
    // triggered, instead of letting both stack on screen.
    dismissTooltip();

    const host = ensureTooltipHost();
    const tip = document.createElement("div");
    tip.className = "emission-tooltip";
    tip.textContent = text;
    tip.style.position = "fixed";
    tip.style.left = pageX + "px";
    tip.style.top = pageY + "px";
    host.appendChild(tip);
    currentTooltip = tip;

    requestAnimationFrame(() => {
      if (currentTooltip === tip) tip.style.opacity = "1";
    });
  }

  // Simple, cheap "puff" mark along the path -- a small circle that grows
  // and fades slowly. Kept intentionally simple rather than a fluid/blob
  // shader effect: the visual payoff wasn't worth the added complexity.
  function spreadTrail(trailLayer, pathEl) {
    const totalLength = pathEl.getTotalLength();

    // A haze line following the whole route, fading in and staying
    // (trails persist until page navigation, per spec).
    const haze = el("path", {
      class: "emission-trail",
      d: pathEl.getAttribute("d"),
    });
    trailLayer.appendChild(haze);
    requestAnimationFrame(() => {
      haze.style.transition = `opacity ${SPREAD_DURATION_MS}ms ease`;
      haze.style.opacity = "0.5";
    });

    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const t = i / (PARTICLE_COUNT - 1);
      const dist = t * totalLength;
      const point = pathEl.getPointAtLength(dist);
      const driftAngle = Math.random() * Math.PI * 2;
      const startDelay = t * SPREAD_DURATION_MS * 0.4 + Math.random() * 300;

      const particle = el("circle", {
        class: "emission-particle",
        cx: point.x,
        cy: point.y,
        r: 0.6,
      });
      particle.style.opacity = "0.9"; // starts dark/concentrated
      particle.style.fill = "#3a3a3a"; // dark soot-like color at origin
      trailLayer.appendChild(particle);

      setTimeout(() => {
        const radius = 3 + Math.random() * 3;
        const driftDist = 8 + Math.random() * 8;
        particle.style.transition =
          "r 2000ms ease-out, cx 2000ms ease-out, cy 2000ms ease-out, opacity 2000ms ease-out, fill 2000ms ease-out";
        particle.setAttribute("r", String(radius));
        particle.setAttribute("cx", String(point.x + Math.cos(driftAngle) * driftDist));
        particle.setAttribute("cy", String(point.y + Math.sin(driftAngle) * driftDist));
        particle.style.opacity = "0.4";
        particle.style.fill = "#b91c1c";
      }, startDelay);
    }
  }

  function handleTrigger(flight) {
    if (!flight) return;
    const { element, pathEl, route, index } = flight;

    if (!triggered.has(index)) {
      triggered.add(index);
      const svg = element.ownerSVGElement;
      const trailLayer = svg.querySelector(".emission-layer");
      spreadTrail(trailLayer, pathEl);
    }

    const rect = element.getBoundingClientRect();
    const text = `${route.from} \u2192 ${route.to}: ${nextFact()}`;
    showTooltip(rect.left + rect.width / 2, rect.top - 10, text);
  }

  function init() {
    const container = document.getElementById("map-container");
    if (!container) return; // not on the home page

    loadFacts();

    // Single delegated listener: catches clicks on the plane AND clicks
    // anywhere along the current route's line-hit path (see map.js),
    // since both are descendants of the SVG. No per-element wiring, no
    // risk of missing the first flight.
    const watchSvg = () => {
      const svg = container.querySelector("svg.map-svg");
      if (svg) {
        svg.addEventListener("click", (evt) => {
          const target = evt.target;
          const isPlane = target.closest(".plane");
          const isRouteLine = target.classList.contains("route-line-hit");
          if (!isPlane && !isRouteLine) return;
          handleTrigger(window.FlightMap && window.FlightMap.currentFlight);
        });
        svg.addEventListener("keydown", (evt) => {
          if (evt.key !== "Enter" && evt.key !== " ") return;
          if (!evt.target.closest(".plane")) return;
          evt.preventDefault();
          handleTrigger(window.FlightMap && window.FlightMap.currentFlight);
        });
      } else {
        requestAnimationFrame(watchSvg);
      }
    };
    watchSvg();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
