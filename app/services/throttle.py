"""Limitation de débit en mémoire : au-delà de `limit` événements en `window` secondes pour une même clé
(email, IP, objet…), l'action est refusée temporairement. Mémoire par processus : suffisant pour une petite
association, sans dépendance supplémentaire."""
import threading
import time

_limiters = []


class Limiter:
    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self._events = {}
        self._lock = threading.Lock()
        _limiters.append(self)

    def _recent(self, key, now):
        return [t for t in self._events.get(key, []) if now - t < self.window]

    def blocked(self, keys):
        now = time.monotonic()
        with self._lock:
            return any(len(self._recent(k, now)) >= self.limit for k in keys)

    def hit(self, keys):
        now = time.monotonic()
        with self._lock:
            for k in keys:
                self._events[k] = self._recent(k, now) + [now]

    def clear(self, keys):
        with self._lock:
            for k in keys:
                self._events.pop(k, None)

    def reset(self):
        with self._lock:
            self._events.clear()


def reset_all():
    """Pour les tests : oublie tous les compteurs."""
    for limiter in _limiters:
        limiter.reset()
