"""Tiny persistent cache of last-known device fields, keyed by TV IP.

Used so `/state` can still report which TV it is (model/name) when the TV is
fully powered off and REST no longer answers. Stored as JSON in TOKEN_DIR, which
is a mounted volume, so it survives container rebuilds. Writes are atomic
(temp + rename); a lock guards in-process concurrency (gunicorn threads).
"""
import json
import os
import threading

_LOCK = threading.Lock()
_FILENAME = "device-cache.json"


def _path(token_dir: str) -> str:
    return os.path.join(token_dir, _FILENAME)


def _load(token_dir: str) -> dict:
    try:
        with open(_path(token_dir)) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def remember(ip: str, fields: dict, token_dir: str) -> None:
    """Persist the non-null `fields` for `ip`, merging over any existing entry."""
    clean = {k: v for k, v in fields.items() if v is not None}
    if not clean:
        return
    with _LOCK:
        cache = _load(token_dir)
        cache[ip] = {**cache.get(ip, {}), **clean}
        try:
            os.makedirs(token_dir, exist_ok=True)
            tmp = _path(token_dir) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cache, f)
            os.replace(tmp, _path(token_dir))
        except OSError:
            # Caching is best-effort; a read-only cache dir must never break the
            # endpoint that called us.
            pass


def recall(ip: str, token_dir: str) -> dict:
    """Return the last-known fields for `ip` (empty dict if none)."""
    with _LOCK:
        return dict(_load(token_dir).get(ip, {}))
