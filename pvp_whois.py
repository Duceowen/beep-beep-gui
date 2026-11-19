"""Rate-limited gankers.org whois lookups for the PvP overlay.

Commander scans repeat constantly, so results are cached per commander and
global API usage is capped. A 429 response pauses every lookup for a while.
"""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from logutil import log

DEFAULT_BACKEND = "https://api.gankers.org"
CACHE_TTL_SECONDS = 15 * 60
MIN_INTERVAL_SECONDS = 3.0
MAX_CALLS_PER_MINUTE = 10
WINDOW_SECONDS = 60.0
RATE_LIMIT_PAUSE_SECONDS = 5 * 60

_lock = threading.RLock()
_cache = {}
_last_call = 0.0
_calls = []
_pause_until = 0.0
_inflight = set()


def _backend():
    try:
        from config import config as edmc_config
        get_str = getattr(edmc_config, "get_str", None)
        if get_str is not None:
            value = get_str("edmc_pvp_bot.api_backend")
            if isinstance(value, str) and value.strip():
                return value.strip().rstrip("/")
    except Exception:
        pass
    return DEFAULT_BACKEND


def _throttled(now):
    global _last_call, _pause_until
    if now < _pause_until:
        return True
    if _last_call and now - _last_call < MIN_INTERVAL_SECONDS:
        return True
    _calls[:] = [stamp for stamp in _calls if now - stamp < WINDOW_SECONDS]
    if len(_calls) >= MAX_CALLS_PER_MINUTE:
        return True
    _last_call = now
    _calls.append(now)
    return False


def _fetch(name):
    url = f"{_backend()}/elite/cmdrs/{urllib.parse.quote(name, safe='')}"
    request = urllib.request.Request(url, headers={"User-Agent": "BeepBeep/whois"})
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                return "error", {"name": name}
            payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                return "error", {"name": name}
            return "ok", {
                "name": str(payload.get("cmdr_name") or name),
                "kills": int(payload.get("kills") or 0),
                "deaths": int(payload.get("deaths") or 0),
                "combat_logger": bool(payload.get("is_combat_logger")),
            }
    except urllib.error.HTTPError as err:
        global _pause_until
        if err.code == 404:
            return "unknown", {"name": name}
        if err.code == 429:
            retry = err.headers.get("Retry-After") if err.headers else None
            try:
                pause = min(max(int(retry), 1), 3600) if retry else RATE_LIMIT_PAUSE_SECONDS
            except (TypeError, ValueError):
                pause = RATE_LIMIT_PAUSE_SECONDS
            _pause_until = time.time() + pause
            log.info("Whois rate limited; pausing %s s", pause)
            return "error", {"name": name}
        return "error", {"name": name}
    except Exception:
        return "error", {"name": name}


def cached(name):
    key = str(name or "").strip().casefold()
    if not key:
        return None
    with _lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stamp, result = entry
        if time.time() - stamp >= CACHE_TTL_SECONDS:
            _cache.pop(key, None)
            return None
        return result


def request(name, callback):
    """Use a cached result or start one throttled background lookup."""
    key = str(name or "").strip()
    if not key:
        return
    folded = key.casefold()
    result = cached(key)
    if result is not None:
        callback(*result)
        return
    with _lock:
        if folded in _inflight:
            return
        if _throttled(time.time()):
            return
        _inflight.add(folded)

    def worker():
        try:
            kind, data = _fetch(key)
            if kind in ("ok", "unknown"):
                with _lock:
                    _cache[folded] = (time.time(), (kind, data))
            callback(kind, data)
        except Exception:
            log.exception("Whois lookup failed")
        finally:
            with _lock:
                _inflight.discard(folded)

    threading.Thread(target=worker, name="beepbeep-whois", daemon=True).start()


def reset_for_tests():
    global _last_call, _pause_until
    with _lock:
        _cache.clear()
        _calls.clear()
        _inflight.clear()
        _last_call = 0.0
        _pause_until = 0.0
