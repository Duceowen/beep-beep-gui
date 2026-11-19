import os
import sys
import textwrap
import threading
import time

from logutil import log
from beep_beep_config import config
from i18n import tl
import cooldown_timer as cooldown_state

ALERT_COLOR = "#13ec39"
ALERT_TTL = 3
ALERT_SIZE = "huge"
ALERT_X = 640
ALERT_Y = 192
ID_PREFIX = "beepbeep-connect-"
BATCH_ID = f"{ID_PREFIX}batch"
PLUGIN_NAME = "BeepBeep"

# The cooldown uses its own id prefix and its own Modern Overlay group, so the
# CMDR can position it independently of the connection announcements.
COOLDOWN_ID_PREFIX = "beepbeep-cooldown-"
COOLDOWN_ID = f"{COOLDOWN_ID_PREFIX}timer"
COOLDOWN_GROUP_NAME = "BeepBeep Cooldown"
COOLDOWN_TICK_SECONDS = 1.0
COOLDOWN_TTL = 2

# PvP kill/death banners get their own group so the CMDR can place them
# independently of arrivals and the cooldown banner.
PVP_ID_PREFIX = "beepbeep-kill-"
PVP_GROUP_NAME = "BeepBeep Kills"
PVP_TTL_SECONDS = 12
PVP_MAX_ACTIVE = 5
PVP_STACK_OFFSET_Y = 80
PVP_LINE_STEP = 28
PVP_WRAP_WIDTH = 40
PVP_KILL_COLOR = "#ff8c00"
PVP_DEATH_COLOR = "#ff3b3b"
PVP_SIZE = "large"
PVP_TICK_SECONDS = 1.0

WHOIS_ID_PREFIX = "beepbeep-whois-"
WHOIS_ID = f"{WHOIS_ID_PREFIX}result"
WHOIS_GROUP_NAME = "BeepBeep WhoIs"
WHOIS_TTL = 5

_lock = threading.Lock()
_initialised = False
_send = None
_define_group = None
_group_ready = False
_cooldown_group_ready = False
_pvp_group_ready = False
_whois_group_ready = False
_missing_logged = False

_cooldown_lock = threading.Lock()
_cooldown_timer = None
_cooldown_generation = 0

_pvp_lock = threading.Lock()
_pvp_entries = []
_pvp_seq = 0
_pvp_timer = None
_pvp_generation = 0

# Active announcement lines keyed by per-message id. Every announce call
# prunes expired lines and re-sends the survivors as ONE multi-line payload
# so Modern Overlay stacks them vertically instead of overlapping at x/y.
_active_lock = threading.Lock()
_active = {}

# Every send bumps the generation and arms a timer. If nothing newer is sent
# before the timer fires, the batch is cleared explicitly instead of relying
# on the overlay to age it out.
_generation = 0
_clear_timer = None


def _import_edmcoverlay():
    try:
        from EDMCOverlay import edmcoverlay
        return edmcoverlay
    except Exception:
        pass

    try:
        plugins_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        overlay_dir = os.path.join(plugins_dir, "ModernOverlay")
        if os.path.isdir(overlay_dir) and overlay_dir not in sys.path:
            sys.path.append(overlay_dir)
        from EDMCOverlay import edmcoverlay
        return edmcoverlay
    except Exception:
        pass

    try:
        from edmcoverlay import edmcoverlay
        return edmcoverlay
    except Exception:
        return None


def _ensure_imports() -> bool:
    global _initialised, _send, _define_group, _missing_logged
    if _initialised:
        return True

    with _lock:
        if _initialised:
            return True

        edmcoverlay = _import_edmcoverlay()
        if edmcoverlay is None:
            if not _missing_logged:
                _missing_logged = True
                log.info("Modern Overlay not found; connection announcements unavailable")
            return False

        send = getattr(edmcoverlay, "send_overlay_message", None)
        if send is None:
            return False

        _send = send
        try:
            from overlay_plugin import overlay_api
            _define_group = getattr(overlay_api, "define_plugin_group", None)
        except Exception:
            _define_group = None

        _initialised = True
        log.info("Modern Overlay connection announcements enabled")
        return True


def _ensure_group() -> None:
    global _group_ready
    if _group_ready or _define_group is None:
        return

    try:
        _define_group(
            plugin_name=PLUGIN_NAME,
            plugin_matching_prefixes=["beepbeep-"],
            plugin_group_name=tl("BeepBeep Connections"),
            plugin_group_prefixes=[ID_PREFIX],
            plugin_group_anchor="center",
            plugin_group_offset_x=0,
            plugin_group_offset_y=0,
            payload_justification="center",
            controller_preview_box_mode="last",
        )
        _group_ready = True
        log.info("Registered BeepBeep overlay group with Modern Overlay")
    except Exception as exc:
        log.info("Could not register BeepBeep overlay group yet: %s", exc)


def _ensure_cooldown_group() -> None:
    global _cooldown_group_ready
    if _cooldown_group_ready or _define_group is None:
        return

    try:
        _define_group(
            plugin_name=PLUGIN_NAME,
            plugin_matching_prefixes=["beepbeep-"],
            plugin_group_name=tl(COOLDOWN_GROUP_NAME),
            plugin_group_prefixes=[COOLDOWN_ID_PREFIX],
            plugin_group_anchor="top",
            plugin_group_offset_x=0,
            plugin_group_offset_y=60,
            payload_justification="center",
            controller_preview_box_mode="last",
        )
        _cooldown_group_ready = True
        log.info("Registered BeepBeep cooldown overlay group")
    except Exception as exc:
        log.info("Could not register BeepBeep cooldown overlay group yet: %s", exc)


def _ensure_pvp_group() -> None:
    global _pvp_group_ready
    if _pvp_group_ready or _define_group is None:
        return

    try:
        _define_group(
            plugin_name=PLUGIN_NAME,
            plugin_matching_prefixes=["beepbeep-"],
            plugin_group_name=tl(PVP_GROUP_NAME),
            plugin_group_prefixes=[PVP_ID_PREFIX],
            plugin_group_anchor="center",
            plugin_group_offset_x=0,
            plugin_group_offset_y=0,
            payload_justification="center",
            controller_preview_box_mode="last",
        )
        _pvp_group_ready = True
        log.info("Registered BeepBeep PvP kill overlay group")
    except Exception as exc:
        log.info("Could not register BeepBeep PvP kill overlay group yet: %s", exc)


def _ensure_whois_group() -> None:
    global _whois_group_ready
    if _whois_group_ready or _define_group is None:
        return

    try:
        _define_group(
            plugin_name=PLUGIN_NAME,
            plugin_matching_prefixes=["beepbeep-"],
            plugin_group_name=tl(WHOIS_GROUP_NAME),
            plugin_group_prefixes=[WHOIS_ID_PREFIX],
            plugin_group_anchor="center",
            plugin_group_offset_x=0,
            plugin_group_offset_y=120,
            payload_justification="center",
            controller_preview_box_mode="last",
        )
        _whois_group_ready = True
        log.info("Registered BeepBeep WhoIs overlay group")
    except Exception as exc:
        log.info("Could not register BeepBeep WhoIs overlay group yet: %s", exc)


def _format_line(entry) -> str:
    raw_name = str(entry.get("name") or "").strip()
    if not raw_name or raw_name.lower() == "unknown":
        return tl("A commander has jumped in")
    return tl("Commander {NAME} has jumped in").format(NAME=raw_name)


def _message_id(entry) -> str:
    cmdr_id = str(entry.get("commander_id") or "").strip()
    if cmdr_id:
        return f"{ID_PREFIX}{cmdr_id}"
    raw_name = str(entry.get("name") or "").strip()
    if raw_name and raw_name.lower() != "unknown":
        slug = "".join(ch if ch.isalnum() else "_" for ch in raw_name.lower())
        return f"{ID_PREFIX}{slug}"
    return f"{ID_PREFIX}unknown"


def _send_message(item_id: str, text: str) -> None:
    payload = {
        "event": "LegacyOverlay",
        "type": "message",
        "id": item_id,
        "plugin": PLUGIN_NAME,
        "text": text,
        "color": ALERT_COLOR,
        "size": ALERT_SIZE,
        "x": ALERT_X,
        "y": ALERT_Y,
        "ttl": ALERT_TTL,
    }
    try:
        _send(payload)
        log.info("Overlay announce sent: %s", text)
    except Exception:
        log.exception("Failed to send overlay connection announcement")


def _clear_payload(item_id: str) -> dict:
    return {
        "event": "LegacyOverlay",
        "type": "message",
        "id": item_id,
        "plugin": PLUGIN_NAME,
        "text": "",
        "ttl": 0,
    }


def _send_clear() -> None:
    """Send a LegacyOverlay clear (ttl 0 with the same id) for the batch."""
    if _send is None:
        return
    try:
        _send(_clear_payload(BATCH_ID))
    except Exception:
        log.exception("Failed to clear overlay connection announcement")


def _send_cooldown(text: str, ttl: int) -> None:
    if _send is None:
        return
    try:
        _send({
            "event": "LegacyOverlay",
            "type": "message",
            "id": COOLDOWN_ID,
            "plugin": PLUGIN_NAME,
            "text": text,
            "color": ALERT_COLOR,
            "size": ALERT_SIZE,
            "x": ALERT_X,
            "y": ALERT_Y,
            "ttl": ttl,
        })
    except Exception:
        log.exception("Failed to send overlay cooldown")


def _clear_cooldown_message() -> None:
    if _send is None:
        return
    try:
        _send(_clear_payload(COOLDOWN_ID))
    except Exception:
        log.exception("Failed to clear overlay cooldown")


def _expire_batch(generation: int) -> None:
    with _active_lock:
        if generation != _generation:
            return
        _active.clear()
    _send_clear()


def announce_name_conflict(name: str, count: int) -> None:
    if not config.get_config("overlay_announce", False):
        return
    if not _ensure_imports():
        return
    _ensure_group()
    _send_message(
        f"{ID_PREFIX}name-conflict",
        tl("'{NAME}' matches {COUNT} commanders here").format(NAME=name, COUNT=count),
    )


def clear_connected() -> None:
    """Remove any connection announcement still on screen (jump, shutdown, stop)."""
    global _generation, _clear_timer
    with _active_lock:
        _generation += 1
        _active.clear()
        if _clear_timer is not None:
            _clear_timer.cancel()
            _clear_timer = None
    if _initialised:
        _send_clear()


def announce_connected(entries) -> None:
    global _generation, _clear_timer
    if not entries:
        return
    if not config.get_config("overlay_announce", False):
        return
    if not _ensure_imports():
        return

    _ensure_group()

    now = time.monotonic()
    with _active_lock:
        expired = [key for key, item in _active.items() if item["expires"] <= now]
        for key in expired:
            del _active[key]

        for entry in entries:
            item_id = _message_id(entry)
            _active[item_id] = {
                "line": _format_line(entry),
                "expires": now + ALERT_TTL,
            }

        # Oldest first, newest last, so the newest line sits at the bottom
        # of the multi-line block.
        text = "\n".join(item["line"] for item in _active.values())

        if not text:
            return

        _generation += 1
        generation = _generation
        if _clear_timer is not None:
            _clear_timer.cancel()
        _clear_timer = threading.Timer(ALERT_TTL + 0.5, _expire_batch, args=(generation,))
        _clear_timer.daemon = True
        _clear_timer.start()

    _send_message(BATCH_ID, text)


def _cooldown_tick(generation: int) -> None:
    global _cooldown_timer
    with _cooldown_lock:
        if generation != _cooldown_generation:
            return
    if not cooldown_state.is_active():
        stop_cooldown()
        return
    _send_cooldown(tl("FSD cooldown"), COOLDOWN_TTL)
    with _cooldown_lock:
        if generation != _cooldown_generation:
            return
        _cooldown_timer = threading.Timer(COOLDOWN_TICK_SECONDS, _cooldown_tick, args=(generation,))
        _cooldown_timer.daemon = True
        _cooldown_timer.start()


def start_cooldown() -> None:
    """Show a status-only FSD cooldown banner until the flag clears."""
    global _cooldown_generation, _cooldown_timer
    if not config.get_config("cooldown_timer", True):
        return
    if not config.get_config("cooldown_overlay", True):
        return
    if not config.get_config("overlay_announce", False):
        return
    if not _ensure_imports():
        return
    _ensure_cooldown_group()
    with _cooldown_lock:
        _cooldown_generation += 1
        generation = _cooldown_generation
        if _cooldown_timer is not None:
            _cooldown_timer.cancel()
            _cooldown_timer = None
    log.info("Overlay cooldown banner started")
    _cooldown_tick(generation)


def stop_cooldown() -> None:
    """Cancel the ticker and remove the cooldown banner from the overlay."""
    global _cooldown_generation, _cooldown_timer
    with _cooldown_lock:
        _cooldown_generation += 1
        if _cooldown_timer is not None:
            _cooldown_timer.cancel()
            _cooldown_timer = None
    if _initialised:
        _clear_cooldown_message()
        log.info("Overlay cooldown banner cleared")


def _pvp_redraw(generation: int) -> None:
    global _pvp_timer
    with _pvp_lock:
        if generation != _pvp_generation:
            return
        now = time.time()
        _pvp_entries[:] = [entry for entry in _pvp_entries if entry["expire"] > now]
        entries = list(_pvp_entries)
    if not entries:
        with _pvp_lock:
            _pvp_timer = None
        return
    cursor_y = ALERT_Y + PVP_STACK_OFFSET_Y
    for entry in entries:
        try:
            _send({
                "event": "LegacyOverlay",
                "type": "message",
                "id": entry["id"],
                "plugin": PLUGIN_NAME,
                "text": entry["text"],
                "color": entry["color"],
                "size": PVP_SIZE,
                "x": ALERT_X,
                "y": cursor_y,
                "ttl": max(1, round(entry["expire"] - time.time())),
            })
        except Exception:
            log.exception("Failed to send PvP kill overlay")
        cursor_y += PVP_LINE_STEP
    with _pvp_lock:
        if generation != _pvp_generation:
            return
        _pvp_timer = threading.Timer(PVP_TICK_SECONDS, _pvp_redraw, args=(generation,))
        _pvp_timer.daemon = True
        _pvp_timer.start()


def ensure_pvp_group() -> None:
    """Register the kill group at startup so it can be positioned in !ovr."""
    if not _ensure_imports():
        return
    _ensure_pvp_group()


def announce_pvp_kill(kind, name, rank=None, ship=None, system=None) -> None:
    """Show a stacked kill or death banner from journal data only."""
    global _pvp_seq, _pvp_generation, _pvp_timer
    if not config.get_config("pvp_overlay", False):
        return
    if not isinstance(name, str) or not name.strip():
        return
    if not _ensure_imports():
        return
    _ensure_pvp_group()

    details = " - ".join(
        str(part).strip() for part in (ship, system)
        if isinstance(part, str) and part.strip()
    )
    template = (
        "KILL: CMDR {NAME} ({RANK}) - {DETAILS}"
        if kind == "kill"
        else "DEATH: CMDR {NAME} ({RANK}) - {DETAILS}"
    )
    text = tl(template).format(
        NAME=name.strip(),
        RANK=str(rank) if rank is not None else "?",
        DETAILS=details,
    ).rstrip(" -")
    text = textwrap.fill(
        " ".join(text.split()),
        width=PVP_WRAP_WIDTH,
        break_long_words=False,
        break_on_hyphens=False,
    )
    color = PVP_KILL_COLOR if kind == "kill" else PVP_DEATH_COLOR

    with _pvp_lock:
        _pvp_generation += 1
        generation = _pvp_generation
        if _pvp_timer is not None:
            _pvp_timer.cancel()
            _pvp_timer = None
        _pvp_seq += 1
        _pvp_entries.insert(0, {
            "id": f"{PVP_ID_PREFIX}{_pvp_seq}",
            "text": text,
            "color": color,
            "expire": time.time() + PVP_TTL_SECONDS,
        })
        overflow = _pvp_entries[PVP_MAX_ACTIVE:]
        del _pvp_entries[PVP_MAX_ACTIVE:]
    for entry in overflow:
        try:
            _send(_clear_payload(entry["id"]))
        except Exception:
            log.exception("Failed to clear an old PvP kill overlay")
    _pvp_redraw(generation)


def clear_pvp_kills() -> None:
    """Remove all kill/death banners (shutdown or manual reset)."""
    global _pvp_generation, _pvp_timer
    with _pvp_lock:
        _pvp_generation += 1
        if _pvp_timer is not None:
            _pvp_timer.cancel()
            _pvp_timer = None
        entries = list(_pvp_entries)
        _pvp_entries.clear()
    if _send is None:
        return
    for entry in entries:
        try:
            _send(_clear_payload(entry["id"]))
        except Exception:
            log.exception("Failed to clear PvP kill overlay")


def ensure_whois_group() -> None:
    """Register the WhoIs group at startup so it can be positioned in !ovr."""
    if not _ensure_imports():
        return
    _ensure_whois_group()


def announce_pvp_whois(kind, name, kills=0, deaths=0, combat_logger=False) -> None:
    """Show a short whois banner from a cached, rate-limited lookup."""
    if not config.get_config("pvp_overlay", False):
        return
    if not isinstance(name, str) or not name.strip():
        return
    if not _ensure_imports():
        return
    _ensure_whois_group()

    name = name.strip()
    if kind == "unknown":
        text = tl("CMDR {NAME} not known to Bot").format(NAME=name)
    else:
        combat = tl(" <COMBAT LOGGER>") if combat_logger else ""
        text = tl("CMDR {NAME} @ {KILLS} K / {DEATHS} D{COMBAT}").format(
            NAME=name, KILLS=kills, DEATHS=deaths, COMBAT=combat
        )
    try:
        _send({
            "event": "LegacyOverlay",
            "type": "message",
            "id": WHOIS_ID,
            "plugin": PLUGIN_NAME,
            "text": text,
            "color": ALERT_COLOR,
            "size": "large",
            "x": ALERT_X,
            "y": ALERT_Y + 120,
            "ttl": WHOIS_TTL,
        })
    except Exception:
        log.exception("Failed to send PvP WhoIs overlay")


def clear_pvp_whois() -> None:
    if _send is None:
        return
    try:
        _send(_clear_payload(WHOIS_ID))
    except Exception:
        log.exception("Failed to clear PvP WhoIs overlay")
