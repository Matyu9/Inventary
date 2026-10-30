// Menu latéral (tiroir sur mobile) et service worker.
(function () {
  const body = document.body;
  function setNav(open) {
    body.classList.toggle("nav-open", open);
    const scrim = document.querySelector(".scrim");
    if (scrim) scrim.hidden = !open;
    document.querySelectorAll("[data-open-nav]").forEach((b) => b.setAttribute("aria-expanded", String(open)));
  }
  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-open-nav]")) setNav(!body.classList.contains("nav-open"));
    else if (event.target.closest("[data-close-nav]") || event.target.closest(".sidebar a")) setNav(false);
  });
  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") setNav(false);
  });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(function () {});
})();
