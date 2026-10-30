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

// Erreurs des requêtes HTMX : sans ce bandeau, l'action échoue sans rien afficher.
(function () {
  const MESSAGES = {
    400: "La page a expiré : rechargez-la puis recommencez.",
    403: "Vous n'avez pas les droits nécessaires pour cette action.",
    404: "Élément introuvable : il a peut-être été supprimé entre-temps.",
    413: "Fichier trop volumineux.",
    429: "Trop de tentatives : patientez quelques minutes.",
  };
  function show(text) {
    const main = document.querySelector("main");
    if (!main) return;
    let banner = main.querySelector(".htmx-error");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "flash flash-error htmx-error";
      banner.setAttribute("role", "alert");
      main.prepend(banner);
    }
    banner.textContent = text;
  }
  document.addEventListener("htmx:responseError", function (event) {
    const status = event.detail.xhr.status;
    show(MESSAGES[status] || "Une erreur est survenue (" + status + "). Rechargez la page puis réessayez.");
  });
  document.addEventListener("htmx:sendError", function () {
    show("Connexion au serveur impossible : vérifiez votre réseau puis réessayez.");
  });
})();
