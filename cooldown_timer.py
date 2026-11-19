"""State for the FSD cooldown indicator.

The journal gives no cooldown duration, so the indicator only shows while the
game's own Status.json FSD cooldown flag is set. No number is guessed.
"""
import threading
import time

from commander_names import name_key

DEFAULT_SECONDS = 60.0
# Status.json Flags bit 18: the game's own FSD cooldown flag for your ship.
FSD_COOLDOWN_FLAG = 1 << 18
# A status edge only counts as an interdiction cooldown when a fresh event set
# the context. Ordinary supercruise exits also set the FSD cooldown bit.
CONTEXT_MAX_AGE_SECONDS = 15.0
# Safety net if a Status.json flag edge is missed.
MAX_ACTIVE_SECONDS = 90.0

_lock = threading.RLock()
_status_active = False
_active = False
_started_at = 0.0
_target_name = None
_target_id = None
_seconds = 0.0


def seconds_for_event(event, entry):
    """Cooldown length implied by an interdiction journal event.

    An outgoing Interdiction, win or lose, carries no Submitted flag and the
    game applies the 60 s cooldown. Incoming Interdicted has an explicit flag.
    """
    if not isinstance(entry, dict):
        return DEFAULT_SECONDS
    if event == "Interdicted":
        return 10.0 if entry.get("Submitted") is True else DEFAULT_SECONDS
    if event == "Interdiction":
        return DEFAULT_SECONDS
    return DEFAULT_SECONDS


def fresh_context(at, used, now=None, max_age=CONTEXT_MAX_AGE_SECONDS):
    """True only for a recent, unconsumed interdiction context."""
    if used or not at:
        return False
    moment = time.monotonic() if now is None else now
    return 0 <= moment - at <= max_age


def note_status_flags(flags):
    """Return 'start' or 'end' on an FSD-cooldown flag edge, else None.

    Status.json is polled by EDMC about once a second. Only the edge is useful:
    the flag has no duration or remaining-time value.
    """
    global _status_active
    cooling = bool(int(flags or 0) & FSD_COOLDOWN_FLAG)
    if cooling and not _status_active:
        _status_active = True
        return "start"
    if not cooling and _status_active:
        _status_active = False
        return "end"
    return None


def start(target_name=None, target_id=None, seconds=DEFAULT_SECONDS, now=None):
    global _target_name, _target_id, _seconds, _active, _started_at
    with _lock:
        _active = True
        _started_at = time.monotonic() if now is None else now
        _seconds = max(0.0, float(seconds))
        _target_name = target_name if isinstance(target_name, str) and target_name.strip() else None
        _target_id = str(target_id) if target_id is not None else None


def is_active(now=None):
    moment = time.monotonic() if now is None else now
    with _lock:
        return _active and 0 <= moment - _started_at <= MAX_ACTIVE_SECONDS


def is_full_window():
    """True for a 60 s timer. A late submit clears this one, not the 10 s one."""
    with _lock:
        return _seconds > 10.0


def stop():
    global _target_name, _target_id, _seconds, _active
    with _lock:
        _active = False
        _target_name = None
        _target_id = None
        _seconds = 0.0


def stop_if_target(name=None, cmdr_id=None):
    """Stop only when the kill matches the commander the timer is tracking."""
    global _target_name, _target_id, _seconds, _active
    with _lock:
        if not _active:
            return False
        if cmdr_id is not None and _target_id is not None and str(cmdr_id) == str(_target_id):
            _target_name = _target_id = None
            _seconds = 0.0
            _active = False
            return True
        if name and _target_name and name_key(name) == name_key(_target_name):
            _target_name = _target_id = None
            _seconds = 0.0
            _active = False
            return True
        return False
