"""Client minimal de l'API en lecture seule de l'Inventaire."""
import requests


class ApiError(Exception):
    """Erreur renvoyée par l'API (jeton refusé, droit manquant, introuvable…), message en français."""


class InventaireClient:
    def __init__(self, base_url, token, session=None, timeout=15):
        self.base_url = base_url.rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url += "/api/v1"
        self.token, self.timeout = token, timeout
        self.session = session or requests.Session()

    def get(self, path, **params):
        """GET sur l'API ; les paramètres None ou vides sont omis. Renvoie le JSON, ou lève ApiError."""
        params = {k: v for k, v in params.items() if v not in (None, "", False)}
        try:
            response = self.session.get(self.base_url + path, params=params, timeout=self.timeout,
                                        headers={"Authorization": f"Bearer {self.token}", "Accept": "application/json"})
        except requests.RequestException as e:
            raise ApiError(f"Inventaire injoignable à l'adresse {self.base_url} ({type(e).__name__}).") from e
        try:
            data = response.json()
        except ValueError:
            raise ApiError(f"Réponse inattendue de l'Inventaire (HTTP {response.status_code}).") from None
        if response.status_code >= 400:
            error = data.get("error", {}) if isinstance(data, dict) else {}
            raise ApiError(error.get("message") or f"Erreur HTTP {response.status_code}.")
        return data
