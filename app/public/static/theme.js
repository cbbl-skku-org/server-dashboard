// Apply the remembered light/dark choice before the page paints.
(function () {
  try {
    var t = localStorage.getItem("cbbl-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* storage blocked: keep dark */ }
})();
