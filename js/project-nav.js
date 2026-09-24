// project-nav.js
// Injects the sub-navigation shared by all Project sub-pages. The Project
// section is a small deck of pages; the proposal is the landing page.

(function () {
  const SUB_LINKS = [
    { href: "project.html", label: "Proposal" },
    { href: "project-background.html", label: "Background" },
    { href: "project-methods.html", label: "Methods" },
    { href: "project-data.html", label: "Data" },
    { href: "project-progress.html", label: "Progress" },
  ];

  function currentPage() {
    const path = window.location.pathname.split("/").pop();
    return path === "" ? "index.html" : path;
  }

  function build() {
    const current = currentPage();
    const wrap = document.createElement("nav");
    wrap.className = "sub-nav";
    wrap.setAttribute("aria-label", "Project sections");

    const inner = document.createElement("div");
    inner.className = "sub-nav__inner";

    SUB_LINKS.forEach(({ href, label }) => {
      const a = document.createElement("a");
      a.href = href;
      a.textContent = label;
      if (href === current) {
        a.className = "active";
        a.setAttribute("aria-current", "page");
      }
      inner.appendChild(a);
    });

    wrap.appendChild(inner);
    return wrap;
  }

  function mount() {
    const slot = document.getElementById("project-subnav");
    if (slot) slot.replaceWith(build());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
