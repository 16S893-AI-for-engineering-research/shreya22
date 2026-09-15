// egg.js
// The easter egg: click (or press Enter/Space on) a plane once it has
// landed, and a lingering "emissions trail" spreads along its route,
// plus a one-line aviation-emissions fact appears near the plane.
// Trails persist on the page until the user navigates away, per spec.

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const PARTICLE_COUNT = 22;
  const SPREAD_DURATION_MS = 3200;

  let facts = [];
  let factIndex = 0;
  const triggered = new Set(); // route indices already popped, so repeat
  // clicks don't restart the animation or duplicate particles.

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

  function showTooltip(pageX, pageY, text) {
    const host = ensureTooltipHost();
    const tip = document.createElement("div");
    tip.className = "emission-tooltip";
    tip.textContent = text;
    tip.style.position = "fixed";
    tip.style.left = pageX + "px";
    tip.style.top = pageY + "px";
    host.appendChild(tip);
    requestAnimationFrame(() => {
      tip.style.opacity = "1";
    });
    setTimeout(() => {
      tip.style.opacity = "0";
      setTimeout(() => tip.remove(), 400);
    }, 4200);
  }

  function spreadTrail(trailLayer, pathEl) {
    const totalLength = pathEl.getTotalLength();

    // A visible haze line following the whole route, fading in and
    // staying (per spec: trails persist until page navigation).
    const haze = el("path", {
      class: "emission-trail",
      d: pathEl.getAttribute("d"),
    });
    trailLayer.appendChild(haze);
    requestAnimationFrame(() => {
      haze.style.transition = `opacity ${SPREAD_DURATION_MS}ms ease`;
      haze.style.opacity = "0.55";
    });

    // Particle puffs distributed along the path, each growing and drifting
    // slightly, then settling as a faint permanent haze cloud.
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const t = i / (PARTICLE_COUNT - 1);
      const dist = t * totalLength;
      const point = pathEl.getPointAtLength(dist);
      const jitterX = (Math.random() - 0.5) * 10;
      const jitterY = (Math.random() - 0.5) * 10;

      const particle = el("circle", {
        class: "emission-particle",
        cx: point.x,
        cy: point.y,
        r: 0.5,
      });
      trailLayer.appendChild(particle);

      const delay = t * SPREAD_DURATION_MS * 0.6 + Math.random() * 200;
      const targetR = 3 + Math.random() * 4;

      setTimeout(() => {
        particle.style.transition = `r 900ms ease-out, cx 900ms ease-out, cy 900ms ease-out, opacity 900ms ease-out`;
        particle.setAttribute("r", String(targetR));
        particle.setAttribute("cx", String(point.x + jitterX));
        particle.setAttribute("cy", String(point.y + jitterY));
        particle.style.opacity = "0.35";
      }, delay);
    }
  }

  function handleTrigger(detail) {
    const { element, pathEl, route, index } = detail;
    if (triggered.has(index)) return;
    triggered.add(index);

    const svg = element.ownerSVGElement;
    const trailLayer = svg.querySelector(".emission-layer");
    spreadTrail(trailLayer, pathEl);

    const rect = element.getBoundingClientRect();
    const text = `${route.from} \u2192 ${route.to}: ${nextFact()}`;
    showTooltip(rect.left + rect.width / 2, rect.top - 10, text);

    element.setAttribute("data-triggered", "true");
  }

  function wirePlane(detail) {
    const { element } = detail;
    element.addEventListener("click", () => handleTrigger(detail));
    element.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        handleTrigger(detail);
      }
    });
  }

  async function init() {
    const container = document.getElementById("map-container");
    if (!container) return; // not on the home page

    await loadFacts();

    // map.js dispatches this event on its <svg> once it exists; we attach
    // the listener as early as possible so we don't miss early flights.
    const watchSvg = () => {
      const svg = container.querySelector("svg.map-svg");
      if (svg) {
        svg.addEventListener("flightmap:planeready", (e) => wirePlane(e.detail));
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
