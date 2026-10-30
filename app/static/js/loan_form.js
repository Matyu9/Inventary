// Formulaire de prêt : ajout / retrait de lignes de matériel, puis vérification de disponibilité (HTMX).
(function () {
  const form = document.getElementById("loan-form");
  const lines = document.getElementById("selected-lines");
  const empty = document.getElementById("no-lines");
  const template = document.getElementById("line-template");

  function changed() {
    empty.hidden = lines.children.length > 0;
    htmx.trigger(form, "lines-changed");
  }

  // Entrée dans la recherche de matériel ne doit pas envoyer la demande.
  form.querySelector('input[name="q"]').addEventListener("keydown", function (event) {
    if (event.key === "Enter") event.preventDefault();
  });

  document.addEventListener("click", function (event) {
    const add = event.target.closest("[data-add-line]");
    if (add) {
      const key = add.dataset.kind + "-" + add.dataset.id;
      if (!lines.querySelector('[data-line="' + key + '"]')) {
        const row = template.content.firstElementChild.cloneNode(true);
        row.dataset.line = key;
        const hidden = row.querySelector("input");
        hidden.name = add.dataset.kind === "kit" ? "kit_ids" : "item_ids";
        hidden.value = add.dataset.id;
        row.querySelector("span").textContent = add.dataset.label;
        if (add.dataset.quantity === "true") {
          const qty = document.createElement("input");
          Object.assign(qty, { type: "number", name: "qty_" + add.dataset.id, value: 1, min: 1, className: "qty" });
          qty.setAttribute("aria-label", "Quantité");
          row.insertBefore(qty, row.querySelector("button"));
        }
        lines.appendChild(row);
        changed();
      }
      return;
    }
    const remove = event.target.closest("[data-remove-line]");
    if (remove && lines.contains(remove)) {
      remove.closest(".line").remove();
      changed();
    }
  });
})();
