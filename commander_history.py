import datetime
import json
import os
import platform
import re
import shutil
import threading
import time
from typing import Callable, Dict, NotRequired, TypedDict
from logutil import log
from beep_beep_config import config
from location import location
from commander_names import (
    UnknownNameResolver,
    journal_player_names,
    backfill_seen_record,
    candidate_unknown_ids,
    close_seen_time,
    is_unknown_name,
    name_key,
    note_seen_time,
    parse_seen_time,
    utc_now,
    visit_is_new,
)

_HISTORY_TAIL = (
    "Frontier Developments",
    "Elite Dangerous",
    "CommanderHistory",
)
_ELITE_APP_ID = "359320"
_STEAM_LIBRARY_PATH = re.compile(r'"path"\s+"([^"]+)"')
_KILL_RESET_SUPPRESS_SECONDS = 60.0
_LOAD_BATCH_SECONDS = 15.0
_REANNOUNCE_SECONDS = 60.0
_BACKUP_KEEP = 10
_SAVE_INTERVAL_SECONDS = 5.0


def _atomic_write_json(path: str, data) -> None:
    """Write to a temp file, then swap it in, so a crash never leaves a half-written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    os.replace(tmp, path)
_PRESENCE_QUIET_SECONDS = 600.0
_KILLS_BACKFILL_MARKER = "kill_tally_v2.txt"


def _join_parts(parts: list[str]) -> str:
    if not parts:
        return ""
    if parts[0] == "":
        return "/" + "/".join(parts[1:])
    # os.path.join("C:", "Users") is drive-relative ("C:Users"), not "C:\Users".
    if len(parts[0]) == 2 and parts[0][1] == ":":
        return parts[0] + "\\" + "\\".join(parts[1:])
    return os.path.join(*parts)


def history_dir_from_journal_dir(journal_dir: str) -> str | None:
    """Map an EDMC journal folder to the Wine/Windows CommanderHistory folder.

    Journals live in Saved Games. CommanderHistory lives in AppData\\Local
    for the same Windows user, including inside a Proton prefix.
    """
    expanded = os.path.expanduser(journal_dir.strip())
    if not expanded:
        return None
    parts = expanded.replace("\\", "/").split("/")
    lowered = [part.lower() for part in parts]
    user_root = None
    if "saved games" in lowered:
        user_root = parts[: lowered.index("saved games")]
    else:
        for index, part in enumerate(lowered):
            if part == "users" and index > 0 and lowered[index - 1] == "drive_c" and index + 1 < len(parts):
                user_root = parts[: index + 2]
                break
    if not user_root:
        return None
    return _join_parts(user_root + ["AppData", "Local", *_HISTORY_TAIL])


def edmc_journal_dir() -> str | None:
    try:
        from config import config as edmc_config
    except ImportError:
        return None
    raw = None
    getter = getattr(edmc_config, "get_str", None)
    if callable(getter):
        try:
            raw = getter("journaldir")
        except Exception:
            raw = None
    if not raw:
        raw = getattr(edmc_config, "default_journal_dir", None)
    if not raw:
        return None
    expanded = os.path.expanduser(str(raw))
    return expanded or None


_JOURNAL_FILE = re.compile(r"^Journal\..+\.log$")
_WING_ON_EVENTS = frozenset(("WingJoin", "WingAdd"))
# Leaving a wing also happens when the game quits or starts a new session.
_WING_OFF_EVENTS = frozenset(("WingLeave", "Shutdown", "ShutDown", "LoadGame", "StartUp"))
_JOURNAL_TAIL_BYTES = 4 * 1024 * 1024


_SESSION_START_EVENTS = frozenset(("LoadGame", "StartUp"))
_SESSION_END_EVENTS = frozenset(("Shutdown", "ShutDown"))


def _journal_time(value) -> datetime.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def read_session_state(journal_dir: str | None = None) -> tuple[bool | None, datetime.datetime | None]:
    """Read the wing state and game session start from the newest journal.

    EDMC does not replay old journal lines, so a plugin started mid-session
    never sees the WingJoin or the LoadGame. Returns (in_wing, session_start):
    - in_wing: True/False from the last wing event, None if there is none.
    - session_start: UTC time of the last LoadGame/StartUp, or None when the
      game is not running (a Shutdown came after it, or none was found).
    """
    journal_dir = journal_dir if journal_dir is not None else edmc_journal_dir()
    if not journal_dir or not os.path.isdir(journal_dir):
        return None, None
    try:
        names = [name for name in os.listdir(journal_dir) if _JOURNAL_FILE.match(name)]
    except OSError:
        return None, None
    if not names:
        return None, None
    # Journal names embed the session start time, so the newest sorts last.
    path = os.path.join(journal_dir, max(names))
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _JOURNAL_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return None, None

    in_wing = None
    session_start = None
    wing_known = False
    session_known = False
    for raw in reversed(tail.splitlines()):
        if b'"event"' not in raw:
            continue
        try:
            entry = json.loads(raw)
            event = entry.get("event")
        except (ValueError, AttributeError):
            continue
        if not wing_known:
            if event in _WING_ON_EVENTS:
                in_wing, wing_known = True, True
            elif event in _WING_OFF_EVENTS:
                in_wing, wing_known = False, True
        if not session_known:
            if event in _SESSION_END_EVENTS:
                session_known = True
            elif event in _SESSION_START_EVENTS:
                session_start = _journal_time(entry.get("timestamp"))
                session_known = True
        if wing_known and session_known:
            break
    return in_wing, session_start


def platform_default_history_dir(system: str | None = None, home: str | None = None) -> str:
    system = system or platform.system()
    if system == "Windows":
        base_dir = os.getenv("LOCALAPPDATA", "")
    elif system == "Darwin":
        base_dir = os.path.expanduser("~/Library/Application Support")
    elif home is not None:
        base_dir = os.path.join(home, ".local", "share")
    else:
        base_dir = os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    return os.path.join(base_dir, *_HISTORY_TAIL)


def steam_library_roots(home: str | None = None) -> list[str]:
    home = home or os.path.expanduser("~")
    # Linux Mint installs Steam from the Ubuntu/Debian package. That package
    # uses ~/.steam/debian-installation, usually with ~/.steam/steam linked to
    # it. The Valve .deb and the path Tom reported use ~/.local/share/Steam.
    seeds = [
        os.path.join(home, ".steam", "debian-installation"),
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", "data", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".local", "share", "Steam"),
    ]
    roots: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if not path:
            return
        expanded = os.path.expanduser(path)
        try:
            real = os.path.realpath(expanded)
        except OSError:
            real = expanded
        key = os.path.normcase(os.path.normpath(real))
        if key in seen:
            return
        seen.add(key)
        roots.append(real)

    for seed in seeds:
        if os.path.isdir(seed):
            add(seed)
        vdf = os.path.join(seed, "steamapps", "libraryfolders.vdf")
        if not os.path.isfile(vdf):
            continue
        try:
            with open(vdf, encoding="utf-8", errors="replace") as handle:
                text = handle.read()
        except OSError:
            continue
        for match in _STEAM_LIBRARY_PATH.finditer(text):
            add(match.group(1).replace("\\\\", "\\"))
    return roots


def proton_history_dirs(home: str | None = None) -> list[str]:
    found: list[str] = []
    tail = os.path.join("AppData", "Local", *_HISTORY_TAIL)
    for root in steam_library_roots(home):
        # Mint's ext4 filesystem is case-sensitive. Current Steam uses
        # steamapps; older installs used SteamApps.
        for steamapps_name in ("steamapps", "SteamApps"):
            users_root = os.path.join(
                root, steamapps_name, "compatdata", _ELITE_APP_ID, "pfx", "drive_c", "users"
            )
            if not os.path.isdir(users_root):
                continue
            try:
                names = os.listdir(users_root)
            except OSError:
                continue
            for name in names:
                found.append(os.path.join(users_root, name, tail))
    return found


def commander_history_candidates(journal_dir: str | None = None, system: str | None = None, home: str | None = None) -> list[str]:
    journal_dir = journal_dir if journal_dir is not None else edmc_journal_dir()
    ordered: list[str] = []
    if journal_dir:
        derived = history_dir_from_journal_dir(journal_dir)
        if derived:
            ordered.append(derived)
        ordered.append(os.path.join(os.path.expanduser(journal_dir), "CommanderHistory"))
    if (system or platform.system()) != "Windows":
        ordered.extend(proton_history_dirs(home))
    ordered.append(platform_default_history_dir(system, home))

    unique: list[str] = []
    seen: set[str] = set()
    for path in ordered:
        if not path:
            continue
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(os.path.normpath(path))
    return unique


def _directory_has_history(path: str) -> bool:
    try:
        with os.scandir(path) as entries:
            return any(
                entry.is_file() and CommanderHistoryManager.is_cmdr_history_file(entry.name)
                for entry in entries
            )
    except OSError:
        return False


class CommanderEntry(TypedDict):
    commander_id: str
    name: str
    sound: str
    last_seen: str
    seen_count: int
    first_seen: NotRequired[str]
    encounters: NotRequired[list]

CommanderDict = Dict[str, CommanderEntry]  

class CommanderHistoryManager:
    def __init__(self):
        self.plugin_dir = os.path.dirname(__file__)
        self.seen_data: CommanderDict = {}
        self.commander_history_dir = platform_default_history_dir()
        self._resolved_history_dir: str | None = None
        self._fallback_history_dir: str | None = None
        self._next_history_search = 0.0
        self._missing_history_logged = False
        self.json_file_path = os.path.join(self.plugin_dir, "seen_commanders.json")
        self.last_modified_timestamp: datetime.datetime = datetime.datetime.min
        self._gui_listener: Callable[[dict], None] | None = None
        self._sound_listener: Callable[[dict], None] | None = None
        self._overlay_listener: Callable[[list], None] | None = None        
        self.worker_thread: threading.Thread | None = None
        self.worker_stop_event = threading.Event()
        self.changed = False
        self.last_data: dict | None = None
        self.data_received = False
        self._trigger = False
        self._reset_timer: threading.Timer | None = None
        self._lock = threading.Lock()    
        self.last_interactions: dict[str, set[str]] = {}
        self._kill_reset_until: dict[str, float] = {}
        self._names = UnknownNameResolver()
        location.on_instance_reset = self.forget_pending_names
        self._session_start: datetime.datetime | None = None
        self._last_jump: datetime.datetime | None = None
        self._presence_saved: set[str] | None = None
        self._kills_lock = threading.Lock()
        self._tick = 0
        self._batch_until = 0.0
        self._announced_at: dict[str, float] = {}
        self._departed: set[str] = set()
        self._left_at: dict[str, float] = {}
        self._last_signal: dict[str, float] = {}
        self._seen_dirty = False
        self._seen_saved_at = 0.0
        self._live_kills: dict[str, int] = {}
        self.kill_tally_path = os.path.join(self.plugin_dir, "kill_tally.json")
        self._kill_tally: dict[str, int] = self._read_kill_tally()

    def set_session_start(
        self,
        session_start: datetime.datetime | None,
        last_jump: datetime.datetime | None = None,
    ) -> None:
        """UTC time the game session began (None if not running) and the last jump."""
        self._session_start = session_start
        self._last_jump = last_jump

    def _log_presence_changes(self) -> None:
        """Log who joins or leaves the instance, so presence can be checked in the log."""
        present = {str(cid) for cid, inst in location.instance.items() if isinstance(inst, dict) and inst.get("here")}
        previous = self._presence_saved or set()
        if present == previous:
            return
        jumping = location.jump_ts is not None and (time.time() - location.jump_ts <= 60)
        for cid in sorted(previous - present):
            # Any drop outside a jump counts as leaving, whatever the cause. A quick
            # re-add afterwards is a stale snapshot, not an arrival.
            if not jumping:
                self._left_at[cid] = time.monotonic()
        for cid in sorted(present - previous) + sorted(previous - present):
            info = self.seen_data.get(cid)
            name = info.get("name", cid) if isinstance(info, dict) else cid
            log.info("Presence %s: %s", "added" if cid in present else "removed", name)
        self._presence_saved = present

    def _should_announce(self, cmdr_id: str, now: float) -> bool:
        """Announce an arrival unless they were announced recently and have not left since.

        A leave or a kill counts as leaving, so a quick return after a kill is still announced.
        """
        left = self._left_at.get(cmdr_id)
        if left is not None and now - left < _REANNOUNCE_SECONDS:
            # Left by interdiction, high wake or similar and back within the window.
            # A return this soon is a stale re-add, not a new arrival.
            return False
        last = self._announced_at.get(cmdr_id)
        if last is None or cmdr_id in self._departed or now - last >= _REANNOUNCE_SECONDS:
            self._announced_at[cmdr_id] = now
            self._departed.discard(cmdr_id)
            return True
        return False

    def _touch(self, cmdr_id) -> None:
        """Note that the game or the journal just showed this commander."""
        self._last_signal[str(cmdr_id)] = time.monotonic()

    def _touch_names(self, names) -> None:
        keys = {name_key(name) for name in names if isinstance(name, str) and name.strip()}
        if not keys:
            return
        for cid, info in self.seen_data.items():
            if isinstance(info, dict) and isinstance(info.get("name"), str) and name_key(info["name"]) in keys:
                self._touch(cid)

    def _expire_quiet_presence(self) -> None:
        """Drop anyone not seen for a while. The game gives no leave signal for some
        departures, so silence is the fallback. A later return is announced again."""
        now = time.monotonic()
        for cid, inst in list(location.instance.items()):
            if not (isinstance(inst, dict) and inst.get("here")):
                continue
            last = self._last_signal.get(cid)
            if last is None:
                self._last_signal[cid] = now
                continue
            if now - last >= _PRESENCE_QUIET_SECONDS:
                inst["here"] = False
                self._departed.add(cid)
                info = self.seen_data.get(cid)
                name = info.get("name", cid) if isinstance(info, dict) else cid
                log.info("No signal from %s for %d minutes; no longer marked here", name, _PRESENCE_QUIET_SECONDS // 60)

    def clear_presence(self) -> None:
        """Forget everyone in the instance, e.g. after the main menu or a game load."""
        self._batch_until = time.monotonic() + _LOAD_BATCH_SECONDS
        self._announced_at.clear()
        self._departed.clear()
        self._left_at.clear()
        self._last_signal.clear()
        location.instance.clear()

    def note_jump(self, timestamp) -> None:
        """A jump starts a new instance, so presence from the old one no longer applies."""
        jump_at = _journal_time(timestamp)
        if jump_at is None:
            return
        self._last_jump = jump_at
        self._announced_at.clear()
        self._departed.clear()
        self._left_at.clear()
        self._last_signal.clear()

    def is_shown_present(self, cmdr_id) -> bool:
        """Present for display: in the instance and heard within the quiet window."""
        if not self.is_present(cmdr_id):
            return False
        last = self._last_signal.get(str(cmdr_id))
        if last is None:
            return True
        return time.monotonic() - last < _PRESENCE_QUIET_SECONDS

    def is_present(self, cmdr_id) -> bool:
        """True while the commander is recorded as in your current instance."""
        inst = location.instance.get(str(cmdr_id))
        return bool(isinstance(inst, dict) and inst.get("here"))

    @property
    def wing_notify(self) -> bool:
        return config.get_config("wing_notify", False)
    
    @property
    def beep_on_leave(self) -> bool:
        return config.get_config("beep_on_leave", False)

    @property
    def autofill_on_kill(self) -> bool:
        return bool(config.get_config("autofill_on_kill", True))    

    @staticmethod
    def is_cmdr_history_file(name: str) -> bool:
        return re.match(r"^Commander\d*\.cmdrHistory$", name) is not None

    def subscribe_gui(self, cb: Callable[[dict], None]):
        self._gui_listener = cb
    
    def subscribe_sound(self, cb: Callable[[dict], None]):
        self._sound_listener = cb
    
    def subscribe_overlay(self, cb: Callable[[list], None]):
        self._overlay_listener = cb
    
    def load_seen_commanders(self):
        if not os.path.isfile(self.json_file_path) or os.path.getsize(self.json_file_path) == 0:
            self.seen_data = {}
            return
    
        try:
            with open(self.json_file_path, "r", encoding="utf-8") as f:
                self.seen_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            log.exception("Failed to load seen_commanders.json, trying the newest backup")
            self.seen_data = self._load_latest_backup() or {}
                
        changed = False
        for cmdr_id, entry in self.seen_data.items():
            if not isinstance(entry, dict):
                continue
            if "sound" in entry:
                entry["sound"] = os.path.splitext(entry["sound"])[0].lower()
            try:
                entry["seen_count"] = int(entry.get("seen_count", 1) or 1)
            except (TypeError, ValueError):
                entry["seen_count"] = 1
            if backfill_seen_record(entry):
                changed = True
        if changed:
            self.save_seen_commanders()

    @property
    def backup_dir(self) -> str:
        return os.path.join(self.plugin_dir, "backups")

    def _backup_names(self) -> list[str]:
        try:
            return sorted(
                name for name in os.listdir(self.backup_dir)
                if name.startswith("seen_commanders-") and name.endswith(".json")
            )
        except OSError:
            return []

    def backup_seen_commanders(self) -> None:
        """Copy the saved file aside at startup and keep only the newest few copies."""
        if not os.path.isfile(self.json_file_path) or os.path.getsize(self.json_file_path) == 0:
            return
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
            shutil.copy2(self.json_file_path, os.path.join(self.backup_dir, f"seen_commanders-{stamp}.json"))
            for old in self._backup_names()[:-_BACKUP_KEEP]:
                os.remove(os.path.join(self.backup_dir, old))
        except OSError:
            log.exception("Failed to back up seen_commanders.json")

    def _load_latest_backup(self):
        for name in reversed(self._backup_names()):
            try:
                with open(os.path.join(self.backup_dir, name), "r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict):
                log.error("Restored seen commanders from backup %s", name)
                return data
        return None

    def save_seen_commanders(self):
        """Mark the data as changed. The worker writes it out at most every few seconds."""
        self._seen_dirty = True

    def flush_seen_commanders(self, force: bool = False) -> None:
        if not self._seen_dirty or self.json_file_path is None:
            return
        if not force and time.monotonic() - self._seen_saved_at < _SAVE_INTERVAL_SECONDS:
            return
        try:
            _atomic_write_json(self.json_file_path, self.seen_data)
        except (OSError, TypeError, RuntimeError):
            log.exception("Failed to save seen_commanders.json")
            return
        self._seen_dirty = False
        self._seen_saved_at = time.monotonic()
            
    def resolve_commander_history_dir(self) -> str | None:
        if self._resolved_history_dir and os.path.isdir(self._resolved_history_dir):
            return self._resolved_history_dir

        now = time.monotonic()
        if now < self._next_history_search:
            if self._fallback_history_dir and os.path.isdir(self._fallback_history_dir):
                return self._fallback_history_dir
            return None

        candidates = commander_history_candidates()
        existing = [path for path in candidates if os.path.isdir(path)]
        with_files = next((path for path in existing if _directory_has_history(path)), None)
        chosen = with_files or (existing[0] if existing else None)
        if chosen:
            self.commander_history_dir = chosen
            self._missing_history_logged = False
            if with_files:
                self._resolved_history_dir = chosen
                self._fallback_history_dir = None
                log.info("Using CommanderHistory at %s", chosen)
            else:
                self._fallback_history_dir = chosen
                self._next_history_search = now + 30
            return chosen

        self._fallback_history_dir = None
        self._next_history_search = now + 30
        if not self._missing_history_logged:
            log.error(
                "CommanderHistory folder was not found. EDMC journals are at %s. Looked in: %s",
                edmc_journal_dir() or "(not set)",
                "; ".join(candidates) or "(none)",
            )
            self._missing_history_logged = True
        return None

    def aggregate_most_recent_commanders(self, first_run=False):
        self.changed = False
        history_dir = self.resolve_commander_history_dir()
        if not history_dir:
            return None
        try:
            abs_paths = [
                e.path
                for e in os.scandir(history_dir)
                if e.is_file() and self.is_cmdr_history_file(e.name)
            ]
        except (FileNotFoundError, PermissionError, OSError) as err:
            self._resolved_history_dir = None
            self._next_history_search = time.monotonic() + 30
            if not self._missing_history_logged:
                log.error("Failed to list CommanderHistory at %s: %s", history_dir, err)
                self._missing_history_logged = True
            return None

    
        if not abs_paths:
            return None
    
        file_mtimes = {f: os.path.getmtime(f) for f in abs_paths if os.path.getsize(f) > 0}
    
        latest_timestamp = self.last_modified_timestamp
    
        if first_run:
            files_to_process = abs_paths
            combined_data = {}
        
            for file_path in files_to_process:
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, json.JSONDecodeError):
                    continue
        
                combined_data.update(data)
        
                file_mtime_dt = datetime.datetime.fromtimestamp(file_mtimes[file_path])
                if not latest_timestamp or file_mtime_dt > latest_timestamp:
                    latest_timestamp = file_mtime_dt
        
            if not combined_data:
                return None
        
            data_to_return = combined_data
    
        else:
            files_to_process = [
                f for f, mtime in file_mtimes.items()
                if not self.last_modified_timestamp
                   or (lambda ts: ts is not None and mtime > ts)(getattr(self.last_modified_timestamp, "timestamp", lambda: None)())
            ]
            if not files_to_process:
                return None
    
            newest_file = max(files_to_process, key=lambda f: file_mtimes[f])
    
            try:
                with open(newest_file, "r", encoding="utf-8") as f:
                    data_to_return = json.load(f)
            except (OSError, json.JSONDecodeError):
                return None

    
            latest_timestamp = datetime.datetime.fromtimestamp(file_mtimes[newest_file])
    
        if self.last_data != data_to_return:
            self.changed = True
            self.last_data = data_to_return
    
        if not self.changed:
            return None
    
        if self._trigger:
            self.data_received = True
    
        self.last_modified_timestamp = latest_timestamp
        return data_to_return

    def aggregated_commanders_load(self):
        data = self.aggregate_most_recent_commanders(True)
        if data:
            entries = data.get("Interactions", [])
            frontier_epoch = datetime.datetime(1601, 1, 1)
        
            for entry in entries:
                interactions = entry.get("Interactions", [])
                if "Met" not in interactions:
                    continue
        
                cmdr_id = str(entry["CommanderID"])
                
                
                ts = frontier_epoch + datetime.timedelta(seconds=entry["Epoch"])
    
                existing = self.seen_data.get(cmdr_id)
                if existing:
                    last_seen_existing = datetime.datetime.fromisoformat(existing["last_seen"])
                    if ts <= last_seen_existing:
                        continue
        
          
                info: CommanderEntry = {
                    "commander_id": cmdr_id,
                    "name": existing.get("name", "unknown") if existing else "unknown",
                    "sound": existing.get("sound", "neutral") if existing else "neutral",
                    "last_seen": ts.isoformat(),
                    "seen_count": (existing.get("seen_count", 0) if existing else 0) + 1,
                    "kills": int(existing.get("kills", 0)) if existing else 0,
                }
        
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
             
            self.save_seen_commanders()
    


    
    
    def aggregated_commanders(self):
        if location.system is None:
            # Wait for the game's StartUp. Arrivals read before it are wiped by the reset.
            return
        data = self.aggregate_most_recent_commanders(False)
    
        if not data:
            return
    
        entries = data.get("Interactions", [])
    
        frontier_epoch = datetime.datetime(1601, 1, 1)
    
        beeps_to_play = []
        connected_entries: list[CommanderEntry] = []
        changed_entries: list[CommanderEntry] = []
        killed_unknown_ids = []
        new_wing_unknown_ids = []
    
        now_ts = time.time()
        jump_recent = location.jump_ts is not None and (now_ts - location.jump_ts <= 60)
        wing_recent = location.wing_join is not None and (now_ts - location.wing_join <= 60)
        interdiction_recent = location.interdiction_ts is not None and (now_ts - location.interdiction_ts <= 60)
        pvp_kill_recent = location.pvp_kill_ts is not None and (now_ts - location.pvp_kill_ts <= 60)
        in_batch = time.monotonic() < self._batch_until

    
        log.info(
            "aggregated_commanders: context system=%s state=%s wing=%s jump_recent=%s wing_recent=%s",
            location.system,
            location.state,
            location.wing,
            jump_recent,
            wing_recent
        )
    
        for entry in entries:
            interactions = entry.get("Interactions", [])
    
            if "Met" not in interactions:
                continue
    
            cmdr_id = str(entry["CommanderID"])
            ts = frontier_epoch + datetime.timedelta(seconds=entry["Epoch"])
    
            existing = self.seen_data.get(cmdr_id)
    
            if existing:
                last_seen_existing = datetime.datetime.fromisoformat(existing["last_seen"])
                if ts <= last_seen_existing:
                    continue
                
                
            self._touch(cmdr_id)
            current_flags = set(interactions)
            previous_flags = self.last_interactions.get(cmdr_id, set())
            
            killed_now = "Killed" in current_flags and "Killed" not in previous_flags

    
            is_wing = "WingMember" in interactions
    
            info: CommanderEntry = {
                "commander_id": cmdr_id,
                "name": existing.get("name", "unknown") if existing else "unknown",
                "sound": existing.get("sound", "neutral") if existing else "neutral",
                "last_seen": ts.isoformat(),
                "seen_count": (existing.get("seen_count", 0) if existing else 0) + 1,
                "kills": int(existing.get("kills", 0)) if existing else 0,
            }

            if killed_now and is_unknown_name(info.get("name")):
                killed_unknown_ids.append(cmdr_id)
            if is_wing and "WingMember" not in previous_flags and is_unknown_name(info.get("name")):
                new_wing_unknown_ids.append(cmdr_id)
 
            inst = location.get_instance().get(cmdr_id)
    
            beep_this_commander = True            

            suppress_until = self._kill_reset_until.get(cmdr_id)
            if suppress_until is not None:
                if now_ts < suppress_until:
                    # Frontier updates the killed commander's Epoch again
                    # after the kill. Keep them out of the instance until the
                    # update settles, so the kill itself is not announced.
                    location.forget(cmdr_id)
                    self.last_interactions[cmdr_id] = current_flags
                    self._remember_sighting(info, existing)
                    self.seen_data[cmdr_id] = info
                    changed_entries.append(info)
                    continue
                self._kill_reset_until.pop(cmdr_id, None)

            kill_reset = (
                killed_now
                and "Killed" in current_flags
                and (inst is not None or cmdr_id in location.jump_backup)
            )
            if kill_reset:
                # A kill ends the visit only when this commander first gains
                # the Killed flag. A recent PvP kill of someone else must not
                # reset commanders whose Killed flag is already on record.
                # Clear every presence record so the next sighting is a fresh
                # arrival, and consume the kill marker so the return itself is
                # not mistaken for another kill. This runs before jump
                # suppression: a victim who followed you through a jump is
                # still dead and can come back.
                location.forget(cmdr_id)
                location.pvp_kill_ts = None
                location.pvp_kill_victim = None
                self._departed.add(cmdr_id)
                beep_this_commander = False
                self._kill_reset_until[cmdr_id] = now_ts + _KILL_RESET_SUPPRESS_SECONDS
                self.last_interactions[cmdr_id] = current_flags
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                changed_entries.append(info)
                log.info(
                    "Kill reset for %s; next sighting will be announced",
                    info.get("name") or cmdr_id,
                )
                continue

            if jump_recent and cmdr_id in location.jump_backup:
                prev = location.jump_backup[cmdr_id]
                if prev.get("here", False):
                    # CommanderHistory bumps Epoch for people still listed when
                    # you jump. They did not jump in. Keep the new Epoch so the
                    # same refresh is not announced after the jump window.
                    if existing is not None:
                        info["seen_count"] = existing.get("seen_count", 1)
                    self._remember_sighting(info, existing)
                    self.seen_data[cmdr_id] = info
                    self.last_interactions[cmdr_id] = current_flags
                    changed_entries.append(info)
                    log.info(
                        "Not announcing %s; they were already here when you jumped",
                        info.get("name") or cmdr_id,
                    )
                    continue
    
            if is_wing and wing_recent:
                beep_this_commander = False
                continue

            if inst:
                if pvp_kill_recent:
                    inst["here"] = True
                    beep_this_commander = False
                       
                    if killed_now and cmdr_id in self.last_interactions:
                        self.last_interactions[cmdr_id].discard("Killed")
                
                    self.last_interactions[cmdr_id] = current_flags
                    self._remember_sighting(info, existing)
                    self.seen_data[cmdr_id] = info
                    changed_entries.append(info)
                    continue

                                
                
                was_here = inst.get("here", False)
            
                if was_here and interdiction_recent:
                    # An interdiction can carry someone who was already here with you
                    # into the new instance. They are still present: no alert, no departure.
                    beep_this_commander = False
                    if existing is not None:
                        info["seen_count"] = existing.get("seen_count", 1)
                elif was_here:
                    # A new record for someone already marked here means they left.
                    # Jumping starts a new instance, which clears presence, so no
                    # update arrives for people who are still in the instance.
                    # Mark them gone and stay silent for this update. Departure
                    # sounds still follow the beep-on-leave setting.
                    inst["here"] = False
                    location.departed.append(cmdr_id)
                    self._departed.add(cmdr_id)
                    self._left_at[cmdr_id] = time.monotonic()
                    beep_this_commander = False
                    if existing is not None:
                        info["seen_count"] = existing.get("seen_count", 1)
                else:
                    inst["here"] = True
                    # Right after a game load, the game rewrites everyone already here.
                    beep_this_commander = not in_batch and self._should_announce(cmdr_id, time.monotonic())

                inst["state"] = location.state
                inst["system"] = location.system



            else:
                location.add_instance(
                    cmdr_id,
                    state=location.state,
                    system=location.system
                )
    
                inst = location.get_instance()[cmdr_id]
                inst["here"] = True
                beep_this_commander = not in_batch and self._should_announce(cmdr_id, time.monotonic())
    
    
            if not location.wing:
                allow_beep = True
            else:
                if is_wing:
                    allow_beep = self.wing_notify
                else:
                    allow_beep = True
                  
    
            # The overlay follows the same rule as the sound, so a wing member
            # hidden by the wing setting is not shown on screen either.
            if allow_beep and beep_this_commander:
                connected_entries.append(info)
                beeps_to_play.append(info)
   
            self._remember_sighting(info, existing)
            self.seen_data[cmdr_id] = info
            changed_entries.append(info)
                            
            self.last_interactions[cmdr_id] = current_flags
    
        stored = set(self.seen_data)
        self._resolve_unknown_names(
            [cid for cid in killed_unknown_ids if cid in stored],
            [cid for cid in new_wing_unknown_ids if cid in stored],
        )

        if self._kill_reset_until:
            self._kill_reset_until = {
                cid: until
                for cid, until in self._kill_reset_until.items()
                if until > now_ts
            }

        if not changed_entries:
            return

        # Several arrivals at once with no jump or interdiction behind them are a
        # stale presence snapshot (for example after a state change), not real arrivals.
        if len(beeps_to_play) > 1 and not jump_recent and not interdiction_recent:
            log.info(
                "Ignoring %d bulk arrivals with no jump: %s",
                len(beeps_to_play),
                ", ".join(str(info.get("name", "unknown")) for info in beeps_to_play),
            )
            beeps_to_play = []
            connected_entries = []
    
        if beeps_to_play and self._sound_listener:
            self._sound_listener(beeps_to_play)
    
        if self._gui_listener and changed_entries:
            self._gui_listener(changed_entries)

        if connected_entries and self._overlay_listener:
            self._overlay_listener(connected_entries)
    
    
    
    
        location.jump_backup = {}
        location.jump_ts = None
        location.wing_join = None
        location.interdiction_ts = None

        


        self.save_seen_commanders()

    def _remember_sighting(self, info: dict, existing) -> None:
        seen_at = info.get("last_seen")
        previous_last = existing.get("last_seen") if isinstance(existing, dict) else None
        note_seen_time(
            info,
            seen_at,
            existing if isinstance(existing, dict) else None,
            visit_is_new(previous_last, seen_at),
        )

    def note_journal_name(self, entry: dict) -> None:
        if not isinstance(entry, dict) or not journal_player_names(entry):
            # Most journal lines name no one. Skip them before the costly lookups.
            return
        self._touch_names([name for _kind, name in journal_player_names(entry)])
        try:
            self._names.note_journal(
                entry,
                time.time(),
                set(self._unknown_ids_here()),
                self._known_name_keys(),
                allow_kill=self.autofill_on_kill,
            )
        except Exception:
            log.exception("Failed to note a commander name from the journal")

    def forget_pending_names(self) -> None:
        self._names.clear()

    def _unknown_ids_here(self) -> list[str]:
        ids = []
        for cmdr_id, inst in list(location.get_instance().items()):
            if not isinstance(inst, dict) or not inst.get("here"):
                continue
            info = self.seen_data.get(str(cmdr_id))
            name = info.get("name") if isinstance(info, dict) else None
            if not isinstance(info, dict) or is_unknown_name(name):
                ids.append(str(cmdr_id))
        return ids

    def _known_name_keys(self) -> set[str]:
        keys = set()
        for info in self.seen_data.values():
            if not isinstance(info, dict):
                continue
            name = info.get("name")
            if isinstance(name, str) and not is_unknown_name(name):
                keys.add(name_key(name))
        return keys

    def _resolve_unknown_names(self, killed_unknown_ids=None, new_wing_unknown_ids=None) -> None:
        unknown_ids, historical = candidate_unknown_ids(
            self._unknown_ids_here(),
            self.seen_data,
            self._names.link_time(),
        )
        try:
            assignments = self._names.resolve(
                unknown_ids,
                time.time(),
                self._known_name_keys(),
                killed_unknown_ids=killed_unknown_ids,
                new_wing_unknown_ids=new_wing_unknown_ids,
                allow_kill=self.autofill_on_kill,
                historical=historical,
            )
        except Exception:
            log.exception("Failed to resolve an unknown commander name")
            return
        if not assignments:
            return

        known = self._known_name_keys()
        updated = []
        for cmdr_id, name in assignments:
            key = name_key(name)
            if key in known:
                continue
            info = self.seen_data.get(cmdr_id)
            if not isinstance(info, dict) or not is_unknown_name(info.get("name")):
                continue
            info["name"] = name
            known.add(key)
            updated.append(info)
            log.info("Named commander %s from the journal as %s", cmdr_id, name)
        if not updated:
            return
        self.save_seen_commanders()
        if self._gui_listener:
            try:
                self._gui_listener(updated)
            except Exception:
                log.exception("Failed to show a resolved commander name")

    def apply_spelling_updates(self, planned) -> list[tuple[str, str, str]]:
        """Write journal spellings onto saved rows. Unknown names are not filled."""
        applied = []
        updated = []
        for cmdr_id, old_name, new_name in planned or []:
            info = self.seen_data.get(str(cmdr_id))
            if not isinstance(info, dict) or info.get("name") != old_name:
                continue
            if not isinstance(new_name, str) or is_unknown_name(new_name):
                continue
            if name_key(new_name) != name_key(old_name):
                continue
            info["name"] = new_name
            applied.append((str(cmdr_id), old_name, new_name))
            updated.append(info)
        if not updated:
            return []
        self.save_seen_commanders()
        log.info("Updated %s commander spellings from journals", len(updated))
        if self._gui_listener:
            try:
                self._gui_listener(updated)
            except Exception:
                log.exception("Failed to show an updated commander spelling")
        return applied

    def _wing_muted(self, cmdr_id) -> bool:
        """True for a wing member while you are in a wing and wing alerts are off."""
        if not location.wing or self.wing_notify:
            return False
        return "WingMember" in self.last_interactions.get(str(cmdr_id), set())

    def _process_departures(self):
        departed = location.departed
        if not departed:
            return

        location.departed = []

        closed_at = utc_now()
        closed = False
        for cmdr_id in departed:
            info = self.seen_data.get(cmdr_id)
            if isinstance(info, dict) and close_seen_time(info, closed_at):
                closed = True
        if closed:
            self.save_seen_commanders()

        if not self.beep_on_leave:
            return

        now_ts = time.time()
        jump_recent = location.jump_ts is not None and (now_ts - location.jump_ts <= 60)
        if jump_recent:
            return

        infos = [
            self.seen_data[cmdr_id]
            for cmdr_id in departed
            if cmdr_id in self.seen_data and not self._wing_muted(cmdr_id)
        ]
        if infos and self._sound_listener:
            self._sound_listener(infos)
            log.info(
                "BeepBeep: departure sound for %s",
                ", ".join(info.get("name", "unknown") for info in infos),
            )

    def record_kill(self, victim) -> None:
        """Count one PvP kill by victim name. Rows pick up the count when they have that name."""
        if not isinstance(victim, str) or not victim.strip():
            return
        key = name_key(victim)
        with self._kills_lock:
            self._live_kills[key] = self._live_kills.get(key, 0) + 1
            self._kill_tally[key] = self._kill_tally.get(key, 0) + 1
            self._save_kill_tally()

    def _read_kill_tally(self) -> dict[str, int]:
        try:
            with open(self.kill_tally_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def _save_kill_tally(self) -> None:
        try:
            _atomic_write_json(self.kill_tally_path, self._kill_tally)
        except OSError:
            log.exception("Failed to save kill tally")

    def _apply_kill_tally(self) -> None:
        """Give each row the kill count for its name. Runs on the worker thread.

        A name is credited to the most recently seen row with that name, so a
        kill still counts after a row is named later.
        """
        with self._kills_lock:
            tally = dict(self._kill_tally)
        best: dict[str, str] = {}
        for cid, info in self.seen_data.items():
            if not isinstance(info, dict):
                continue
            name = info.get("name")
            if not isinstance(name, str) or is_unknown_name(name):
                continue
            key = name_key(name)
            current = best.get(key)
            if current is None or str(info.get("last_seen", "")) > str(self.seen_data[current].get("last_seen", "")):
                best[key] = cid
        changed = []
        for cid, info in self.seen_data.items():
            if not isinstance(info, dict):
                continue
            value = 0
            name = info.get("name")
            if isinstance(name, str) and not is_unknown_name(name):
                key = name_key(name)
                if best.get(key) == cid:
                    value = tally.get(key, 0)
            if info.get("kills", 0) != value:
                info["kills"] = value
                changed.append(info)
        if not changed:
            return
        self.save_seen_commanders()
        if self._gui_listener:
            try:
                self._gui_listener(changed)
            except Exception:
                log.exception("Failed to show kill counts")
    def _journal_kill_counts(self, before: datetime.datetime) -> dict[str, int]:
        """Count PvP kills in the journals written before this session, keyed by name."""
        counts: dict[str, int] = {}
        journal_dir = edmc_journal_dir()
        if not journal_dir or not os.path.isdir(journal_dir):
            return counts
        try:
            names = [name for name in os.listdir(journal_dir) if _JOURNAL_FILE.match(name)]
        except OSError:
            return counts
        for name in names:
            try:
                with open(os.path.join(journal_dir, name), "rb") as handle:
                    for raw in handle:
                        if b'"PVPKill"' not in raw:
                            continue
                        try:
                            entry = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(entry, dict) or entry.get("event") != "PVPKill":
                            continue
                        when = _journal_time(entry.get("timestamp"))
                        victim = entry.get("Victim")
                        if when is None or when >= before or not isinstance(victim, str):
                            continue
                        key = name_key(victim)
                        if key:
                            counts[key] = counts.get(key, 0) + 1
            except OSError:
                continue
        return counts

    def start_kill_backfill(self) -> None:
        """Once per install: rebuild the kill tally from every journal before this session."""
        marker = os.path.join(self.plugin_dir, _KILLS_BACKFILL_MARKER)
        if os.path.exists(marker):
            return
        started = utc_now()

        def run() -> None:
            base = self._journal_kill_counts(started)
            with self._kills_lock:
                for key in set(base) | set(self._live_kills):
                    self._kill_tally[key] = base.get(key, 0) + self._live_kills.get(key, 0)
                self._save_kill_tally()
            try:
                with open(marker, "w", encoding="utf-8") as handle:
                    handle.write(started.isoformat())
            except OSError:
                log.exception("Failed to save the kill backfill marker")
            log.info("Kill tally rebuilt from journals: %d names", len(base))

        threading.Thread(target=run, daemon=True, name="KillBackfill").start()
    def trigger(self):
        with self._lock:
            if self._reset_timer and self._reset_timer.is_alive():
                self._reset_timer.cancel()
            
            self._reset_timer = threading.Timer(5.0, self._check_reset)
            self._reset_timer.start()
            self._trigger = True

    def _check_reset(self):
        with self._lock:
            if not self.data_received:
                location._record_departures()
                location.instance = {}

            self.data_received = False
            self._trigger = False

    def start_worker(self):
        if self.worker_thread and self.worker_thread.is_alive():
            return

        log.info("Starting Beepbeep worker!")
        self.worker_stop_event.clear()
        self.worker_thread = threading.Thread(target=self.worker_loop, daemon=True, name="CommanderHistoryWorker")
        self.worker_thread.start()

    def stop_worker(self):
        self.worker_stop_event.set()
        if self.worker_thread:
            self.worker_thread.join(timeout=3)
            log.info("Beepbeep worker stopped.")
        self.flush_seen_commanders(force=True)

    def worker_loop(self):
        while not self.worker_stop_event.is_set():
            try:
                if self.worker_stop_event.wait(1):
                    break
                self._process_departures()
                self.aggregated_commanders()
                self._resolve_unknown_names()
                self._tick += 1
                if self._tick % 5 == 0:
                    self._apply_kill_tally()
                self._expire_quiet_presence()
                self.flush_seen_commanders()
                self._log_presence_changes()
            except Exception:
                log.exception("Exception in CommanderHistoryManager worker loop, continuing")

history_inst = CommanderHistoryManager()

