// Page de scan : caméra (HTTPS ou localhost), photo d'un QR (fonctionne aussi en HTTP), ou saisie du code.
(function () {
  const root = document.getElementById("scanner");
  if (!root) return;
  const lookupUrl = root.dataset.lookupUrl;
  const status = document.getElementById("scan-status");
  let handled = false;

  function go(text) {
    if (handled) return;
    handled = true;
    // Un QR de l'appli pointe vers /q/<jeton> : on reste sur l'origine courante.
    const match = text.match(/\/q\/([0-9a-f]{32})(?:[/?#]|$)/);
    window.location.href = match ? "/q/" + match[1] : lookupUrl + "?code=" + encodeURIComponent(text);
  }

  const startBtn = document.getElementById("scan-start");
  if (!window.isSecureContext || !navigator.mediaDevices) {
    startBtn.hidden = true;
    status.textContent = "La caméra en direct nécessite HTTPS. Utilisez « Prendre une photo du QR » ou saisissez le code.";
  }

  let scanner = null;
  startBtn.addEventListener("click", function () {
    scanner = scanner || new Html5Qrcode("scan-reader");
    startBtn.disabled = true;
    status.textContent = "Visez le QR code…";
    scanner.start({ facingMode: "environment" }, { fps: 10, qrbox: { width: 250, height: 250 } }, go)
      .catch(function (err) {
        startBtn.disabled = false;
        status.textContent = "Impossible d'accéder à la caméra : " + err;
      });
  });

  document.getElementById("scan-file").addEventListener("change", function (event) {
    const file = event.target.files[0];
    if (!file) return;
    scanner = scanner || new Html5Qrcode("scan-reader");
    status.textContent = "Lecture de la photo…";
    scanner.scanFile(file, false).then(go).catch(function () {
      status.textContent = "Aucun QR code reconnu sur cette photo. Réessayez plus près et bien net.";
    });
  });
})();
