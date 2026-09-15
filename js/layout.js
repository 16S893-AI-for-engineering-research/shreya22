// layout.js
// Injects a shared navigation bar and footer into every page, and marks
// the current page's nav link as active. Keeping this in one place means
// the nav only needs to be edited once, not once per HTML file.

(function () {
  const NAV_LINKS = [
    { href: "index.html", label: "Home" },
    { href: "about.html", label: "About" },
    { href: "project.html", label: "Project" },
    { href: "devlog.html", label: "Dev Log" },
  ];

  function currentPage() {
    const path = window.location.pathname.split("/").pop();
    return path === "" ? "index.html" : path;
  }

  function buildNav() {
    const current = currentPage();
    const nav = document.createElement("nav");
    nav.className = "site-nav";

    const brand = document.createElement("a");
    brand.href = "index.html";
    brand.className = "site-nav__brand";
    brand.textContent = "\u2708 Shreya Sharma";
    nav.appendChild(brand);

    const links = document.createElement("div");
    links.className = "site-nav__links";
    NAV_LINKS.forEach(({ href, label }) => {
      const a = document.createElement("a");
      a.href = href;
      a.textContent = label;
      if (href === current) {
        a.className = "active";
        a.setAttribute("aria-current", "page");
      }
      links.appendChild(a);
    });
    nav.appendChild(links);

    return nav;
  }

  function buildFooter() {
    const footer = document.createElement("footer");
    footer.className = "site-footer";
    footer.innerHTML =
      "<p>Built for 16.S893 &middot; Sustainable Aviation Portfolio &middot; " +
      new Date().getFullYear() +
      "</p>";
    return footer;
  }

  function mount() {
    const header = document.getElementById("site-header");
    const footer = document.getElementById("site-footer");
    if (header) header.replaceWith(buildNav());
    if (footer) footer.replaceWith(buildFooter());
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
