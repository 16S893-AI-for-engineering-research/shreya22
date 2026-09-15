// egg.js
// The easter egg: click (or press Enter/Space on) the aircraft once it has
// landed, and a lingering "emissions trail" spreads along its just-flown
// route, plus a one-line aviation-emissions fact appears near the plane.
//
// The aircraft is a single persistent element that's reused across all
// flights (see map.js), so this file tracks "the flight that just landed"
// via the flightmap:planeready event rather than binding a new listener
// per route.

(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const PARTICLE_COUNT = 34;
  const SPREAD_DURATION_MS = 7000; // slow, gradual spread rather than a quick puff

  let facts = [];
  let factIndex = 0;
  const triggered = new Set(); // flight indices already popped, so repeat
  // clicks on the same landed flight don't restart or duplicate its trail.
  let currentFlight = null; // { element, pathEl, route, index }
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
    // Tweak: the previous fact bubble disappears as soon as a new one
    // is triggered, instead of both being visible at once.
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

    // Particle puffs distributed along the path. Each starts small, dark,
    // and tight to the path (fresh, concentrated emissions), then slowly
    // grows and drifts outward while lightening — mimicking a contrail /
    // emissions plume dispersing over time. Growth uses several staged
    // steps rather than one CSS transition so the "spreading" reads as
    // continuous and slow rather than a single jump.
    for (let i = 0; i < PARTICLE_COUNT; i++) {
      const t = i / (PARTICLE_COUNT - 1);
      const dist = t * totalLength;
      const point = pathEl.getPointAtLength(dist);
      const driftAngleBase = Math.random() * Math.PI * 2;
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
        const stages = 5;
        for (let s = 1; s <= stages; s++) {
          setTimeout(() => {
            const progress = s / stages;
            const radius = 0.6 + progress * (3.5 + Math.random() * 3);
            const driftDist = progress * (6 + Math.random() * 10);
            const dx = Math.cos(driftAngleBase) * driftDist;
            const dy = Math.sin(driftAngleBase) * driftDist;
            particle.style.transition =
              "r 1400ms ease-out, cx 1400ms ease-out, cy 1400ms ease-out, opacity 1400ms ease-out, fill 1400ms ease-out";
            particle.setAttribute("r", String(radius));
            particle.setAttribute("cx", String(point.x + dx));
            particle.setAttribute("cy", String(point.y + dy));
            // Lighten and fade as it disperses outward.
            particle.style.opacity = String(0.85 - progress * 0.55);
            particle.style.fill = progress < 0.5 ? "#3a3a3a" : "#b91c1c";
          }, s * (SPREAD_DURATION_MS / stages));
        }
      }, startDelay);
    }
  }

  function handleTrigger() {
    if (!currentFlight) return;
    const { element, pathEl, route, index } = currentFlight;
    if (element.dataset.ready !== "true") return; // still mid-flight; ignore

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

    let wired = false;
    const watchSvg = () => {
      const svg = container.querySelector("svg.map-svg");
      if (svg) {
        svg.addEventListener("flightmap:planeready", (e) => {
          currentFlight = e.detail;
          if (!wired) {
            // The aircraft element is created once and reused, so we only
            // need to bind the click/keyboard handlers a single time.
            const plane = e.detail.element;
            plane.addEventListener("click", handleTrigger);
            plane.addEventListener("keydown", (evt) => {
              if (evt.key === "Enter" || evt.key === " ") {
                evt.preventDefault();
                handleTrigger();
              }
            });
            wired = true;
          }
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
