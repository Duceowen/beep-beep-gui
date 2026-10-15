import os
import sys
import threading

from logutil import log
from beep_beep_config import config

ALERT_COLOR = "#13ec39"
ALERT_TTL = 3
ALERT_SIZE = "huge"
ALERT_X = 640
ALERT_Y = 192
ID_PREFIX = "beepbeep-connect-"
PLUGIN_NAME = "BeepBeep"
GROUP_NAME = "BeepBeep Connections"

_lock = threading.Lock()
_initialised = False
_send = None
_define_group = None
_group_ready = False
_missing_logged = False


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
            plugin_group_name=GROUP_NAME,
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


def _format_line(entry) -> str:
    raw_name = str(entry.get("name") or "").strip()
    if not raw_name or raw_name.lower() == "unknown":
        return "A commander has jumped in"
    return f"Commander {raw_name} has jumped in"


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


def announce_connected(entries) -> None:
    if not entries:
        return
    if not config.get_config("overlay_announce", False):
        return
    if not _ensure_imports():
        return

    _ensure_group()

    for entry in entries:
        _send_message(_message_id(entry), _format_line(entry))
