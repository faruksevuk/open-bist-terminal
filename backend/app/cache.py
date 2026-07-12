"""In-process TTL cache — Redis yerine (tek-süreç masaüstü app; open-source, sıfır servis).

Backend tek uvicorn süreci + in-process APScheduler olarak çalışır; dolayısıyla modül-düzeyi
bir cache API istekleri ile scheduler job'ları arasında PAYLAŞILIR ve tutarlıdır. Network-ağır,
sık çağrılan değerler (fx, canlı fiyat) için; config gibi ucuz DB okumaları cache'lenmez
(config_store doğrudan DB okur — cross-process staleness olmasın).

Thread-safe: API thread'leri + scheduler thread aynı cache'e dokunur → basit Lock.
`time.monotonic()` kullanılır (duvar-saati sıçramalarından etkilenmez).
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    """Küçük anahtar→(değer, son-kullanma) sözlüğü. get None dönerse çağıran yeniden üretir."""

    def __init__(self) -> None:
        self._d: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            expires, value = item
            if expires < now:
                self._d.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any, ttl: float) -> None:
        with self._lock:
            self._d[key] = (time.monotonic() + float(ttl), value)

    def delete(self, key: str) -> None:
        with self._lock:
            self._d.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
