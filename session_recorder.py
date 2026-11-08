"""Append-only session log for debugging arrival and departure decisions.

Writes JSONL under the plugin's recordings folder. It never changes beep
decisions and never raises to its caller. Chat message text is not stored.
"""
import importlib.util
import json
import os
import threading
import time

from logutil import log

# About 50 MB for every session file together. Oldest files are removed first.
MAX_RECORDING_BYTES = 50 * 1024 * 1024

_JOURNAL_STRINGS = {
    "Location": ("StarSystem", "Body", "BodyType", "StationName"),
    "FSDJump": ("StarSystem", "Body"),
    "StartJump": ("JumpType", "StarSystem"),
    "SupercruiseEntry": ("StarSystem",),
    "SupercruiseExit": ("StarSystem", "Body", "BodyType"),
    "ApproachBody": ("StarSystem", "Body"),
    "LeaveBody": ("StarSystem", "Body"),
    "Docked": ("StarSystem", "StationName", "StationType"),
    "Undocked": ("StarSystem", "StationName"),
    "Touchdown": ("StarSystem", "Body"),
    "Liftoff": ("StarSystem", "Body"),
    "Embark": ("StarSystem", "Body"),
    "Disembark": ("StarSystem", "Body"),
    "LaunchSRV": ("StarSystem",),
    "DockSRV": ("StarSystem",),
    "LaunchFighter": ("StarSystem",),
    "LoadGame": ("Commander", "StarSystem"),
    "StartUp": ("StarSystem",),
    "Died": ("KillerName",),
    "PVPKill": ("Victim", "Victim_Localised"),
    "Resurrect": ("Option",),
    "Resurrected": ("Option",),
    "Shutdown": (),
    "ShutDown": (),
    "WingLeave": (),
    "Music": ("MusicTrack",),
}

_CHAT_CHANNELS = frozenset(("local", "player"))


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".%03dZ" % int((time.time() % 1) * 1000)


def _plugin_version():
    """The version in this plugin's version.py.

    EDMC puts every plugin on sys.path, so ``import version`` can be another
    plugin's file. Read this folder's version.py by path.
    """
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.py")
        spec = importlib.util.spec_from_file_location("_beep_beep_plugin_version", path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        value = getattr(module, "__version__", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    except Exception:
        return None
    return None


def _text(value):
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _copy_strings(entry, keys):
    found = {}
    for key in keys:
        text = _text(entry.get(key))
        if text is not None:
            found[key] = text
    return found


def compact_journal(entry):
    """A small journal record, or None when this line is not recorded.

    ReceiveText keeps the speaker and the channel only. The message text is
    left out. ShipTargeted is kept only for a player ship.
    """
    if not isinstance(entry, dict):
        return None
    event = entry.get("event")
    if not isinstance(event, str) or not event:
        return None
    timestamp = _text(entry.get("timestamp"))
    base = {"event": event}
    if timestamp is not None:
        base["timestamp"] = timestamp

    if event in _JOURNAL_STRINGS:
        base.update(_copy_strings(entry, _JOURNAL_STRINGS[event]))
        return base

    if event == "Interdiction":
        base.update(_copy_strings(entry, ("Interdicted",)))
        if isinstance(entry.get("Success"), bool):
            base["Success"] = entry["Success"]
        if isinstance(entry.get("IsPlayer"), bool):
            base["IsPlayer"] = entry["IsPlayer"]
        return base

    if event == "Interdicted":
        base.update(_copy_strings(entry, ("Interdictor",)))
        if isinstance(entry.get("Submitted"), bool):
            base["Submitted"] = entry["Submitted"]
        if isinstance(entry.get("IsPlayer"), bool):
            base["IsPlayer"] = entry["IsPlayer"]
        return base

    if event == "EscapeInterdiction":
        base.update(_copy_strings(entry, ("Interdictor",)))
        if isinstance(entry.get("IsPlayer"), bool):
            base["IsPlayer"] = entry["IsPlayer"]
        return base

    if event == "WingAdd":
        base.update(_copy_strings(entry, ("Name",)))
        return base

    if event == "WingJoin":
        others = entry.get("Others")
        if isinstance(others, list):
            names = [name.strip() for name in others if isinstance(name, str) and name.strip()]
            if names:
                base["Others"] = names
        base.update(_copy_strings(entry, ("Name",)))
        return base

    if event == "ReceiveText":
        channel = _text(entry.get("Channel"))
        if channel is None or channel.lower() not in _CHAT_CHANNELS:
            return None
        name = _text(entry.get("From_Localised")) or _text(entry.get("From"))
        base["channel"] = channel.lower()
        if name is not None:
            base["name"] = name
        return base

    if event == "ShipTargeted":
        try:
            from commander_names import journal_player_names
            names = [name for kind, name in journal_player_names(entry) if kind == "scan"]
        except Exception:
            return None
        if not names:
            return None
        base["name"] = names[0]
        base.update(_copy_strings(entry, ("Ship", "Ship_Localised")))
        if isinstance(entry.get("TargetLocked"), bool):
            base["TargetLocked"] = entry["TargetLocked"]
        stage = entry.get("ScanStage")
        if isinstance(stage, int) and not isinstance(stage, bool):
            base["ScanStage"] = stage
        return base

    return None


class SessionRecorder:
    """One JSONL file per plugin process, capped across the recordings folder."""

    def __init__(self, plugin_dir, max_total_bytes=MAX_RECORDING_BYTES):
        self._plugin_dir = plugin_dir if callable(plugin_dir) else (lambda: plugin_dir)
        self.max_total_bytes = int(max_total_bytes)
        # None follows the setting. Tests set True or False directly.
        self.enabled_override = None
        self._lock = threading.Lock()
        self._fh = None
        self._path = None
        self._epochs = {}
        self._failed = False

    def directory(self) -> str:
        return os.path.join(self._plugin_dir(), "recordings")

    def _enabled(self) -> bool:
        if self.enabled_override is not None:
            return bool(self.enabled_override)
        try:
            import beep_beep_config
            return bool(beep_beep_config.config.get_config("record_session", True))
        except Exception:
            return True

    def record(self, kind, fields=None) -> None:
        """Append one record. Never raises."""
        try:
            if not self._enabled():
                return
            with self._lock:
                self._write_unlocked(kind, fields or {})
        except Exception:
            self._note_failure()

    def record_history(self, rows, file_mtime) -> None:
        """Record one CommanderHistory snapshot and the diff from the last one."""
        try:
            if not self._enabled():
                return
            clean = []
            current = {}
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    cid = row.get("id")
                    if cid is None:
                        continue
                    cid = str(cid)
                    epoch = row.get("epoch")
                    if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
                        epoch = None
                    flags = row.get("interactions")
                    if not isinstance(flags, list):
                        flags = []
                    flags = [flag for flag in flags if isinstance(flag, str)]
                    item = {"id": cid, "epoch": epoch, "interactions": flags}
                    name = row.get("name")
                    if isinstance(name, str) and name.strip():
                        item["name"] = name.strip()
                    clean.append(item)
                    current[cid] = epoch
            with self._lock:
                previous = self._epochs
                diff = {
                    "new": sorted(cid for cid in current if cid not in previous),
                    "epoch_changed": sorted(
                        cid for cid in current
                        if cid in previous and previous[cid] != current[cid]
                    ),
                    "disappeared": sorted(cid for cid in previous if cid not in current),
                }
                fields = {"file_mtime": file_mtime, "rows": clean, "diff": diff}
                if self._write_unlocked("history", fields):
                    self._epochs = current
        except Exception:
            self._note_failure()

    def record_journal(self, entry) -> None:
        """Record one journal line when it is on the debugging list."""
        try:
            fields = compact_journal(entry)
            if fields is not None:
                self.record("journal", fields)
        except Exception:
            self._note_failure()

    def close(self) -> None:
        """Flush and close the session file. Never raises."""
        try:
            with self._lock:
                self._close_file()
        except Exception:
            self._note_failure()

    def _close_file(self) -> None:
        handle = self._fh
        self._fh = None
        if handle is not None:
            handle.flush()
            handle.close()

    def __del__(self):
        try:
            self._close_file()
        except Exception:
            pass

    def _header_line(self) -> str:
        header = {"kind": "session", "wall": _utc_now(), "version": _plugin_version()}
        return json.dumps(header, ensure_ascii=False, separators=(",", ":")) + "\n"

    def _write_unlocked(self, kind, fields) -> bool:
        payload = {"kind": kind, "wall": _utc_now()}
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key not in ("kind", "wall"):
                    payload[key] = value
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        extra = len(line.encode("utf-8"))
        header = None
        if self._fh is None:
            header = self._header_line()
            extra += len(header.encode("utf-8"))
        if not self._room_for(extra):
            return False
        if not self._ensure_file(header):
            return False
        self._fh.write(line)
        return True

    def _ensure_file(self, header: str | None = None) -> bool:
        if self._fh is not None:
            return True
        folder = self.directory()
        os.makedirs(folder, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
        name = "session-%s.jsonl" % stamp
        path = os.path.join(folder, name)
        suffix = 2
        while os.path.exists(path):
            name = "session-%s-%d.jsonl" % (stamp, suffix)
            path = os.path.join(folder, name)
            suffix += 1
        self._path = path
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        self._fh.write(header if header is not None else self._header_line())
        return True

    def _room_for(self, extra: int) -> bool:
        """Drop the oldest session files until this write fits. Never deletes the open file."""
        if extra > self.max_total_bytes:
            return False
        files = self._session_files()
        total = sum(size for _path, size in files)
        active = self._path
        for path, size in files:
            if total + extra <= self.max_total_bytes:
                break
            if active is not None and os.path.abspath(path) == os.path.abspath(active):
                continue
            try:
                os.remove(path)
            except OSError:
                continue
            total -= size
        return total + extra <= self.max_total_bytes

    def _session_files(self):
        folder = self.directory()
        found = []
        try:
            names = os.listdir(folder)
        except OSError:
            return found
        for name in sorted(names):
            if not name.endswith(".jsonl"):
                continue
            path = os.path.join(folder, name)
            try:
                found.append((path, os.path.getsize(path)))
            except OSError:
                continue
        return found

    def _note_failure(self) -> None:
        if self._failed:
            return
        self._failed = True
        try:
            log.exception("Session recorder could not write a line")
        except Exception:
            pass
