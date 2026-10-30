// Scan continu pendant un inventaire : chaque QR lu est envoyé au serveur (HTMX), sans quitter la page.
(function () {
  const form = document.getElementById("audit-scan-form");
  const button = document.getElementById("audit-camera");
  const status = document.getElementById("scan-status");
  if (!form || !button) return;
  if (!window.isSecureContext || !navigator.mediaDevices) {
    button.hidden = true;
    status.textContent = "La caméra nécessite HTTPS : saisissez les codes ou utilisez une douchette.";
    return;
  }
  const recent = new Map(); // évite d'envoyer 10 fois le même QR resté devant la caméra
  let scanner = null;
  button.addEventListener("click", function () {
    if (scanner) {
      scanner.stop().then(function () { scanner = null; button.textContent = "Scanner avec la caméra"; });
      return;
    }
    scanner = new Html5Qrcode("scan-reader");
    scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 250, height: 250 } }, function (text) {
      const last = recent.get(text);
      if (last && Date.now() - last < 4000) return;
      recent.set(text, Date.now());
      form.code.value = text;
      htmx.trigger(form, "submit");
      if (navigator.vibrate) navigator.vibrate(80);
    }).then(function () {
      button.textContent = "Arrêter la caméra";
    }).catch(function (err) {
      scanner = null;
      status.textContent = "Caméra indisponible : " + err;
    });
  });
})();
