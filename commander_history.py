import datetime
import json
import os
import platform
import re
import shutil
import threading
import time
import unicodedata
from typing import Callable, Dict, NotRequired, TypedDict
from logutil import log
from beep_beep_config import config
from session_recorder import SessionRecorder
from instance_machine import (
    HISTORY_POLL_SECONDS,
    INTERDICTION_SUBMIT_COOLDOWN,
    INTERDICTION_SUCCESS_COOLDOWN,
    InstanceMachine,
)

class Location:
    def __init__(self):
        self.state = None
        self.prev_state = None
        self.system = None
        self.prev_system = None
        self.instance = {}
        self.wing = False
        self.jump_ts = None
        self.wing_join = None
        # name_key -> wall time when WingJoin/WingAdd/WingInvite named them.
        # CommanderHistory often lists wingmates without WingMember on the first tick.
        self.wing_join_names: dict[str, float] = {}
        self.interdiction_ts = None
        self.interdiction_target = None
        # Set on supercruise drop-in after a successful interdiction: target name and drop time.
        self.interdiction_drop_target = None
        self.interdiction_drop_ts = None
        self.interdiction_complete = False
        self.jump_backup = {}
        self._jump_company = {}
        self._jump_company_pending = False
        # Set when you leave the instance for supercruise in the same system. Until
        # you drop back into normal space, the instance is empty and updates wait.
        self.instance_reset_pending = False
        # Commanders in the instance when supercruise was entered (see leave_instance).
        self.leave_snapshot: set[str] = set()
        # Commanders you killed or who escaped while they were in the instance. A death
        # after that still counts them as left behind, so their re-list is not an arrival.
        self.kill_departed: set[str] = set()
        # True after a death: the snapshot silences re-listed people until a jump or load.
        self.death_snapshot = False
        # Commanders first heard while in supercruise. They are dropped when you drop back out.
        self.supercruise_arrivals: set[str] = set()
        # Commanders dropped at a supercruise drop-out, with the drop time. Updates from them are
        # ignored for a short grace period, so the immediate re-list does not bring them back.
        self.supercruise_dropped: dict[str, float] = {}
        # When you last dropped out of supercruise. A freshly killed or escaped
        # commander can be re-listed by the game at that moment; it is not a return.
        self.last_drop_ts = None
        self.pvp_kill_ts = None
        self.pvp_kill_victim = None
        self.departed = []
        self.on_instance_reset = None

    def _record_departures(self):
        for cmdr_id, info in self.instance.items():
            if info.get("here", False):
                self.departed.append(cmdr_id)

    def set_wing(self, in_wing: bool):
        self.wing = in_wing

    def set(self, state=None, system=None, event=None):
        if event == "SupercruiseExit":
            # Each drop-out starts fresh. Only a successful interdiction sets a target.
            self.interdiction_drop_target = None
            self.interdiction_drop_ts = None
            if self.interdiction_target:
                # The interdiction has finished and you are dropping back out. The window starts here.
                self.interdiction_drop_target = self.interdiction_target
                self.interdiction_drop_ts = time.time()
                self.interdiction_target = None

        # StartJump calls set() before jump(). Dropping the instance here
        # used to leave jump() snapshotting an empty dict, so commanders who
        # were already here were announced again when you left.
        self._jump_company_pending = False
        # A known system changing is a jump. Going from no system to the first one (plugin start) is not, so restored presence stays.
        if state != self.state and system != self.system and self.system is not None:
            self._record_departures()
            if self.instance or not self._jump_company:
                # An empty instance here means it was already left for supercruise,
                # so keep the snapshot taken then.
                self._jump_company = {
                    cmdr_id: dict(info) if isinstance(info, dict) else info
                    for cmdr_id, info in self.instance.items()
                }
            self._jump_company_pending = True
            self.instance = {}
            self._fire_instance_reset()

        self.prev_system = self.system
        self.prev_state = self.state
        self.state = state
        self.system = system
        
    def get(self):
        return self.state, self.system

    def get_instance(self):
        return self.instance
    
    def add_instance(self, id_, state=None, system=None):
        if state is None:
            state = -1
        if system is None:
            system = "Unknown"
    
        here = (system == self.system and state == self.state)
    
        self.instance[id_] = {
            "state": state,
            "system": system,
            "here": here
        }
         
    def leave_instance(self, record_departures: bool = True, after_death: bool = False):
        """Entering supercruise in the same system takes you out of the instance.

        Everyone in it is cleared now. The leave snapshot remembers who was
        already here, so a later re-list of those commanders is not a new
        arrival. A commander who was not in that snapshot still counts as one.
        A later jump still uses this snapshot as its jump backup if the
        instance is empty by then.
        """
        if record_departures:
            self._record_departures()
        if self.instance:
            self._jump_company = {
                cmdr_id: dict(info) if isinstance(info, dict) else info
                for cmdr_id, info in self.instance.items()
            }
        # The worker can hear a new commander between StartJump and the
        # SupercruiseEntry event. StartJump already sets state to supercruise,
        # and a refresh of someone already here copies that state onto their
        # row. Only a commander who first became present in supercruise is a
        # drop-out arrival. Someone who was already here stayed behind.
        late_arrivals = set()
        if self.state == 1:
            late_arrivals = {
                str(cid)
                for cid, info in self.instance.items()
                if isinstance(info, dict) and info.get("here") and info.get("arrived_in_supercruise")
            }
        # Late arrivals were first heard during the jump, so they are not members who stayed behind.
        # Only people marked here — stale instance rows must not silence later SC arrivals.
        self.leave_snapshot = {
            str(cid)
            for cid, info in self.instance.items()
            if isinstance(info, dict) and info.get("here")
        } - late_arrivals
        if after_death:
            # Killed or escaped commanders were in this instance, so the death leaves them behind too.
            self.leave_snapshot |= self.kill_departed
        self.instance.clear()
        self._jump_company_pending = False
        self.death_snapshot = False
        self.instance_reset_pending = True
        self.supercruise_arrivals = late_arrivals
        self._fire_instance_reset()

    def clear_snapshot(self) -> None:
        """Forget the visit snapshot and the supercruise arrivals together."""
        self.leave_snapshot = set()
        self.kill_departed = set()
        self.death_snapshot = False
        self.supercruise_arrivals = set()
        self.supercruise_dropped = {}
        self.last_drop_ts = None

    def jump(self):
        if self.prev_system != self.system:
            self.jump_ts = time.time()
            # A jump supersedes a pending supercruise drop-out.
            self.instance_reset_pending = False
            self.clear_snapshot()
            self._record_departures()
            if self.instance:
                self.jump_backup = {
                    cmdr_id: dict(info) if isinstance(info, dict) else info
                    for cmdr_id, info in self.instance.items()
                }
            elif self._jump_company_pending or self._jump_company:
                self.jump_backup = self._jump_company
            else:
                self.jump_backup = {}
            self._jump_company_pending = False
            self._jump_company = {}
            self.instance.clear()
            self._fire_instance_reset()

        else:
            self.jump_ts = None
            self.jump_backup = {}
            self._jump_company_pending = False
            self._jump_company = {}
            
    def wing_changed(self):
        self.wing_join = time.time()

    def note_wing_mates(self, names) -> None:
        """Remember wingmate display names for a short CommanderHistory silence window."""
        now = time.time()
        self.wing_join = now
        for name in names:
            if not isinstance(name, str):
                continue
            # name_key is imported later in this module; looked up at call time.
            key = name_key(name) if name.strip() else ""
            if key:
                self.wing_join_names[key] = now

    def clear_wing_join_names(self) -> None:
        self.wing_join_names.clear()

    def interdiction(self, target=None):
        self.interdiction_ts = time.time()
        self.interdiction_target = target if isinstance(target, str) else None
        
    def pvp_kill(self, victim):
        self.pvp_kill_ts = time.time()
        self.pvp_kill_victim = victim

    def forget(self, cmdr_id):
        self.instance.pop(cmdr_id, None)
        self.jump_backup.pop(cmdr_id, None)
        self._jump_company.pop(cmdr_id, None)

    def _fire_instance_reset(self):
        callback = self.on_instance_reset
        if callback is not None:
            callback()
        
        
location = Location()

from commander_names import (
    UnknownNameResolver,
    journal_player_names,
    backfill_seen_record,
    candidate_unknown_ids,
    close_seen_time,
    is_unknown_name,
    name_key,
    confusable_key,
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
_KILL_RESET_SUPPRESS_SECONDS = 30.0
_LOAD_BATCH_SECONDS = 12.0
_RESTART_BATCH_SECONDS = 60.0
_REANNOUNCE_SECONDS = 60.0
_BACKUP_KEEP = 10
_SAVE_INTERVAL_SECONDS = 5.0


def _atomic_write_json(path: str, data) -> None:
    """Write to a temp file, then swap it in, so a crash never leaves a half-written file."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, separators=(",", ":"))
    os.replace(tmp, path)
_PRESENCE_QUIET_SECONDS = 180.0
# Still on Now, but dimmed, once this long has passed with no new evidence.
_QUIET_DISPLAY_SECONDS = 60.0
_LEAVE_MEMORY_SECONDS = 600.0
_WING_JOIN_SILENCE_SECONDS = 20.0
_INTERDICTION_WINDOW_SECONDS = 60.0
_SUPERCRUISE_DROP_GRACE_SECONDS = 12.0
# A killed or escaped commander can be re-listed by the game just after a
# supercruise drop. Only silence that re-list close to the drop, so a genuine
# return later in the visit is still announced.
_KILL_DROP_GUARD_SECONDS = 5.0
_KILL_RELIST_GUARD_SECONDS = 600.0
_RESTORE_WINDOW_SECONDS = 600.0
_BEEP_MARKS_MAX_BYTES = 1_000_000
_BEEP_MARKS_NAME = "beep_marks.jsonl"
_KILLS_BACKFILL_MARKER = "kill_tally_v2.txt"
_AUTO_BACKUP_NAME = re.compile(r"^seen_commanders-\d{8}-\d{6}\.json$")
_MANUAL_BACKUP_NAME = re.compile(r"^seen_commanders-manual-.*\.json$")
_IMPORT_EPOCH = "1970-01-01T00:00:00"
# A shared list is untrusted. These caps keep a huge or crowded file off the UI thread.
_IMPORT_MAX_BYTES = 5 * 1024 * 1024
_IMPORT_MAX_ENTRIES = 20_000
_NAME_MAX_CHARS = 64
_ID_MAX_DIGITS = 32
# Cc is control characters (including newlines). Cf is format characters,
# including bidi overrides and zero-width marks that can disguise a name.
_HIDDEN_CATEGORIES = ("Cc", "Cf")


def backup_list_kind(name: str) -> str:
    """Kind shown beside a file in the restore list."""
    if isinstance(name, str) and name.startswith("seen_commanders-manual-before-import-"):
        return "Before import"
    if isinstance(name, str) and name.startswith("seen_commanders-manual-"):
        return "Manual"
    return "Startup"


def _has_hidden_chars(text: str) -> bool:
    return any(unicodedata.category(char) in _HIDDEN_CATEGORIES for char in text)


def _without_hidden_chars(text: str) -> str:
    return "".join(char for char in text if unicodedata.category(char) not in _HIDDEN_CATEGORIES)


def _decimal_commander_id(value) -> str | None:
    """Commander ids from the game are decimal digit strings. Nothing else is trusted."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    if not text or len(text) > _ID_MAX_DIGITS:
        return None
    if not text.isascii() or not text.isdigit():
        return None
    return text


def _classify_import_name(value) -> tuple[str, str | None]:
    """Classify a name from a shared file.

    'ok' is safe to store. 'skip' is empty or unknown, which imports already
    ignore. 'bad' must not be trusted: the wrong length, or hidden characters.
    """
    if not isinstance(value, str):
        if value is None:
            return "skip", None
        return "bad", None
    text = value.strip()
    if is_unknown_name(text):
        return "skip", None
    if len(text) > _NAME_MAX_CHARS or _has_hidden_chars(text):
        return "bad", None
    return "ok", text


def _planned_import_name(item) -> tuple[str, str] | None:
    if not isinstance(item, dict):
        return None
    cmdr_id = _decimal_commander_id(item.get("id"))
    status, name = _classify_import_name(item.get("name"))
    if cmdr_id is None or status != "ok" or name is None:
        return None
    return cmdr_id, name


def _comparable_last_seen(existing) -> datetime.datetime | None:
    """Naive timestamp for the sighting check, or None when it cannot be read.

    None means the sighting is not skipped. An unreadable value must not raise
    and must not hide the commander.
    """
    if not isinstance(existing, dict):
        return None
    raw = existing.get("last_seen")
    if isinstance(raw, datetime.datetime):
        parsed = raw
    elif isinstance(raw, str):
        try:
            parsed = datetime.datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return parsed


def _safe_entry_int(existing, key: str, default: int = 0) -> int:
    if not isinstance(existing, dict):
        return default
    try:
        return int(existing.get(key, default))
    except (TypeError, ValueError):
        return default


def _safe_entry_text(existing, key: str, default: str) -> str:
    if not isinstance(existing, dict):
        return default
    value = existing.get(key, default)
    if isinstance(value, str):
        return value
    return default


def _import_last_seen(_value=None) -> str:
    """Do not take timestamps from a shared file.

    Aggregation ignores a sighting that is not newer than the stored last_seen.
    A future time in an imported file would hide that commander permanently.
    Imported rows are stored at the epoch so the next real sighting is recorded.
    """
    return _IMPORT_EPOCH


def _read_seen_json(path: str, *, max_bytes: int | None = None, max_entries: int | None = None):
    """Read one seen-commanders file. Raises ValueError when it must not be used."""
    try:
        size = os.path.getsize(path)
    except OSError:
        raise
    if max_bytes is not None and size > max_bytes:
        raise ValueError("That file is larger than 5 MB, so it was not read.")
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except RecursionError:
        raise ValueError("That file is nested too deeply to read.") from None
    except json.JSONDecodeError:
        raise ValueError("That file is not valid JSON.") from None
    except UnicodeError:
        raise ValueError("That file could not be read as text.") from None
    except MemoryError:
        raise ValueError("That file is too large to read.") from None
    if not isinstance(data, dict):
        raise ValueError("That file is not a seen commanders list.")
    if max_entries is not None and len(data) > max_entries:
        raise ValueError("That file has more than 20,000 entries, so it was not imported.")
    return data


def describe_import_plan(plan: dict) -> str:
    """Plain counts for the import preview. Does not read or write a file."""
    if not isinstance(plan, dict):
        return ""

    def count(key: str) -> int:
        try:
            return int(plan.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    added = plan.get("added") if isinstance(plan.get("added"), list) else []
    filled = plan.get("filled") if isinstance(plan.get("filled"), list) else []
    return "\n".join((
        "New names: %d" % len(added),
        "Changed names: %d" % len(filled),
        "Kept names you already have: %d" % count("kept"),
        "Skipped (no name): %d" % count("skipped"),
        "Invalid rows: %d" % count("invalid"),
    ))


def import_plan_lines(plan: dict, limit: int = 40) -> list[str]:
    """Preview lines for names an import would add or change."""
    if not isinstance(plan, dict):
        return []
    lines = []
    shown = 0
    groups = (
        ("New    ", plan.get("added")),
        ("Change ", plan.get("filled")),
    )
    total = 0
    for _prefix, items in groups:
        if isinstance(items, list):
            total += len(items)
    for prefix, items in groups:
        if not isinstance(items, list):
            continue
        for item in items:
            if shown >= limit:
                break
            if not isinstance(item, dict):
                continue
            lines.append("%s%s    %s" % (prefix, item.get("id", ""), item.get("name", "")))
            shown += 1
    extra = total - shown
    if extra > 0:
        lines.append("... and %d more" % extra)
    return lines


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


_SYSTEM_EVENTS = frozenset(("Location", "FSDJump", "SupercruiseEntry", "SupercruiseExit", "CarrierJump", "LoadGame", "StartUp", "Docked"))
_SUPERCRUISE_STATE_EVENTS = frozenset(("SupercruiseEntry", "SupercruiseExit", "StartJump", "FSDJump", "CarrierJump", "Location", "StartUp", "LoadGame", "Shutdown", "ShutDown"))


def journal_in_supercruise(journal_dir: str | None = None) -> bool:
    """True when the newest journal's last supercruise-related event is SupercruiseEntry.

    The game's listing can reach the plugin a second before the journal entry is handled,
    so this lets a listing that arrives during the entry be treated as still in the instance."""
    journal_dir = journal_dir if journal_dir is not None else edmc_journal_dir()
    if not journal_dir or not os.path.isdir(journal_dir):
        return False
    try:
        names = [name for name in os.listdir(journal_dir) if _JOURNAL_FILE.match(name)]
    except OSError:
        return False
    if not names:
        return False
    path = os.path.join(journal_dir, max(names))
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _JOURNAL_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return False
    for raw in reversed(tail.splitlines()):
        if b'"event"' not in raw:
            continue
        try:
            event = json.loads(raw).get("event")
        except (ValueError, AttributeError):
            continue
        if event in _SUPERCRUISE_STATE_EVENTS:
            return event == "SupercruiseEntry"
    return False


def _last_location_entry(journal_dir: str | None = None) -> dict | None:
    """The newest journal entry that says where you are, or None if there is none."""
    journal_dir = journal_dir if journal_dir is not None else edmc_journal_dir()
    if not journal_dir or not os.path.isdir(journal_dir):
        return None
    try:
        names = [name for name in os.listdir(journal_dir) if _JOURNAL_FILE.match(name)]
    except OSError:
        return None
    if not names:
        return None
    path = os.path.join(journal_dir, max(names))
    try:
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _JOURNAL_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return None
    for raw in reversed(tail.splitlines()):
        if b'"StarSystem"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, AttributeError):
            continue
        if entry.get("event") in _SYSTEM_EVENTS and isinstance(entry.get("StarSystem"), str):
            return entry
    return None


def read_current_system(journal_dir: str | None = None) -> str | None:
    """The star system the newest journal says you are in, or None if it is not known yet."""
    entry = _last_location_entry(journal_dir)
    return entry["StarSystem"] if entry else None


def read_in_supercruise(journal_dir: str | None = None) -> bool:
    """True if the newest journal events put you in supercruise: a supercruise jump has started
    or the entry is written, and no exit, location or FSD jump has come after it."""
    journal_dir = journal_dir if journal_dir is not None else edmc_journal_dir()
    if not journal_dir or not os.path.isdir(journal_dir):
        return False
    try:
        names = [name for name in os.listdir(journal_dir) if _JOURNAL_FILE.match(name)]
        if not names:
            return False
        path = os.path.join(journal_dir, max(names))
        with open(path, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - _JOURNAL_TAIL_BYTES))
            tail = handle.read()
    except OSError:
        return False
    for raw in reversed(tail.splitlines()):
        if b'"event"' not in raw:
            continue
        try:
            entry = json.loads(raw)
        except (ValueError, AttributeError):
            continue
        event = entry.get("event")
        if event == "SupercruiseEntry":
            return True
        if event == "StartJump" and entry.get("JumpType") == "Supercruise":
            return True
        if event in ("SupercruiseExit", "Location", "FSDJump", "CarrierJump", "StartUp", "LoadGame"):
            return False
    return False



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

def add_alias(info, old_name, new_name) -> None:
    """Remember a name a commander had before it was changed, so the commander is still found by it."""
    if not isinstance(info, dict) or not isinstance(old_name, str) or is_unknown_name(old_name):
        return
    if name_key(old_name) == name_key(str(new_name)):
        return
    aliases = info.setdefault("aliases", [])
    if old_name not in aliases:
        aliases.append(old_name)

def _one_edit(left: str, right: str) -> bool:
    """True when two casefolded names differ by one insertion or one substitution."""
    if left == right or abs(len(left) - len(right)) > 1:
        return False
    if len(left) > len(right):
        left, right = right, left
    if len(left) == len(right):
        return sum(a != b for a, b in zip(left, right)) == 1
    skipped = 0
    index = 0
    for char in right:
        if index < len(left) and left[index] == char:
            index += 1
        else:
            skipped += 1
            if skipped > 1:
                return False
    return index == len(left)


def row_has_name(info, key) -> bool:
    """True if the row was ever known by this name (current name or an earlier one)."""
    if not isinstance(info, dict):
        return False
    names = [info.get("name")] + list(info.get("aliases", []) or [])
    return any(isinstance(n, str) and name_key(n) == key for n in names)


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
        self._names = UnknownNameResolver()
        location.on_instance_reset = self.forget_pending_names
        self._session_start: datetime.datetime | None = None
        self._last_jump: datetime.datetime | None = None
        self._presence_saved: set[str] | None = None
        self._kills_lock = threading.Lock()
        self._tick = 0
        self._batch_until = 0.0
        self._announced_at: dict[str, float] = {}
        # cid -> last heard (monotonic), or None: in this visit but not heard recently.
        self._visit: dict[str, float | None] = {}
        self._instance_dirty = False
        self._instance_saved_at = 0.0
        self._skip_start_event = False
        self._resume_supercruise = False
        self.instance_seen_path = os.path.join(self.plugin_dir, "instance_seen.json")
        # Per-commander removal bookkeeping: left_at (monotonic), suppress_until (wall),
        # departed (re-announce allowed), kill_removed (one-shot: removal was a kill or escape).
        self._gone: dict[str, dict] = {}
        self._seen_dirty = False
        self._seen_saved_at = 0.0
        # Held by backup, restore and import so they never interleave with a save.
        self._data_lock = threading.RLock()
        self._live_kills: dict[str, int] = {}
        self.kill_tally_path = os.path.join(self.plugin_dir, "kill_tally.json")
        self._kill_tally: dict[str, int] = self._read_kill_tally()
        # Last journal event seen by the plugin (for BEEPMARK / debug_snapshot).
        self._last_journal_event: str | None = None
        self._last_journal_ts: str | None = None
        self._marks_path = os.path.join(self.plugin_dir, _BEEP_MARKS_NAME)
        self._marks_lock = threading.Lock()
        # Reads plugin_dir at write time, so a test can point the folder elsewhere.
        self.recorder = SessionRecorder(lambda: self.plugin_dir)
        self.machine = InstanceMachine()
        self._agg_lock = threading.RLock()

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
        # Leaving for supercruise clears the instance too, so it is not a departure.
        jumping = jumping or location.instance_reset_pending
        for cid in sorted(previous - present):
            # Any drop outside a jump counts as leaving, whatever the cause. A quick
            # re-add afterwards is a stale snapshot, not an arrival.
            rec = self._gone.setdefault(cid, {})
            if rec.pop("kill_removed", False):
                # A commander we killed or who escaped is not a leave, so their return is announced.
                pass
            elif cid in location.leave_snapshot:
                # Removed because you entered supercruise: not a departure, so the drop-in can announce them.
                pass
            elif not jumping:
                rec["left_at"] = time.monotonic()
        if location.state == 1:
            location.supercruise_arrivals.update(present - previous)
        for cid in sorted(present - previous) + sorted(previous - present):
            info = self.seen_data.get(cid)
            name = info.get("name", cid) if isinstance(info, dict) else cid
            log.info("Presence %s: %s (id %s)", "added" if cid in present else "removed", name, cid)
        self._presence_saved = present
        self._instance_dirty = True
        try:
            self.recorder.record("presence", {
                "added": [self._named_id(cid) for cid in sorted(present - previous)],
                "removed": [self._named_id(cid) for cid in sorted(previous - present)],
            })
        except Exception:
            pass

    def _is_relist_silenced(self, cmdr_id: str, now_ts: float) -> bool:
        """True when an update is a re-list of people you have already left behind, not an arrival.

        Silent for anyone still on the leave snapshot (supercruise, the drop
        back into normal space, and a death), and for a short grace after a
        supercruise drop-out, which covers the immediate re-list of someone
        who was only heard in supercruise.
        """
        # The snapshot is who you left behind. It stays in force after the drop:
        # CommanderHistory bumps Met for recent contacts whether or not they
        # entered the new instance. A jump or a load clears the snapshot.
        # While you are still in supercruise the same list covers the re-list.
        if cmdr_id in location.leave_snapshot or self.machine.suppresses(cmdr_id):
            return True
        dropped_at = location.supercruise_dropped.get(cmdr_id)
        if dropped_at is not None and now_ts - dropped_at < _SUPERCRUISE_DROP_GRACE_SECONDS:
            return True
        rec = self._gone.get(cmdr_id)
        if rec is not None:
            removed_at = rec.get("removed_at")
            if removed_at is not None:
                if now_ts - removed_at > _KILL_RELIST_GUARD_SECONDS:
                    rec.pop("removed_at", None)
                elif location.last_drop_ts is not None and now_ts - location.last_drop_ts < _KILL_DROP_GUARD_SECONDS:
                    return True
        return False
    def _decide_new_arrival(self, cmdr_id: str, in_batch: bool, name=None) -> bool:
        """The one decision for a commander who is new to the instance: beep or stay quiet.

        Quiet during the post-load batch, wing-join name window, and for anyone whose
        announcement rules say no.
        """
        if in_batch:
            return self._beep_decision(cmdr_id, False, "load_batch")
        if self._wing_join_name_silent(name if name is not None else self._name_for(cmdr_id)):
            return self._beep_decision(cmdr_id, False, "wing_join")
        if location.state != 1 and journal_in_supercruise():
            # The game's listing beat the SupercruiseEntry event. They are still in the instance you are leaving.
            log.info("Not announcing %s (id %s): listed as you entered supercruise", self._name_for(cmdr_id), cmdr_id)
            return self._beep_decision(cmdr_id, False, "sc_entry_list")
        return self._should_announce(cmdr_id, time.monotonic())

    def _is_interdicted_drop_in(self, cmdr_id: str) -> bool:
        """True for the successful interdiction target, back in normal space within the window."""
        target = location.interdiction_drop_target
        ts = location.interdiction_drop_ts
        if not target or ts is None or location.state != 0:
            return False
        if time.time() - ts > _INTERDICTION_WINDOW_SECONDS:
            return False
        info = self.seen_data.get(str(cmdr_id))
        return isinstance(info, dict) and row_has_name(info, name_key(target))
    def _should_announce(self, cmdr_id: str, now: float) -> bool:
        """Announce once per instance. A later history refresh is not another arrival.

        A commander marked departed (they left, or the kill window has passed and
        they are back) can be announced again. Elapsed time alone never turns a
        refresh into an arrival.
        """
        rec = self._gone.get(cmdr_id, {})
        left = rec.get("left_at")
        if left is not None and now - left < _REANNOUNCE_SECONDS and not rec.get("departed"):
            log.info("Not announcing %s (id %s): left %.0f s ago, inside the re-add window", self._name_for(cmdr_id), cmdr_id, now - left)
            return self._beep_decision(cmdr_id, False, "reannounce_window")
        if str(cmdr_id) in self.machine.announced and not rec.get("departed"):
            return self._beep_decision(cmdr_id, False, "refresh")
        if rec.get("departed"):
            self._announced_at[cmdr_id] = now
            self.machine.killed.discard(str(cmdr_id))
            self.machine.note_announced(cmdr_id)
            rec["departed"] = False
            rec.pop("removed_at", None)
            return self._beep_decision(cmdr_id, True, "return_after_leave")
        if self._announced_at.get(cmdr_id) is not None:
            return self._beep_decision(cmdr_id, False, "refresh")
        self._announced_at[cmdr_id] = now
        self.machine.note_announced(cmdr_id)
        return self._beep_decision(cmdr_id, True, "new_arrival")

    def _name_for(self, cmdr_id) -> str:
        info = self.seen_data.get(str(cmdr_id))
        return str(info.get("name", cmdr_id)) if isinstance(info, dict) else str(cmdr_id)

    def _known_name(self, cmdr_id):
        """Saved display name, or None. Does not create or edit a row."""
        info = self.seen_data.get(str(cmdr_id))
        if not isinstance(info, dict):
            return None
        name = info.get("name")
        if not isinstance(name, str) or is_unknown_name(name):
            return None
        return name

    def _named_id(self, cmdr_id) -> dict:
        item = {"id": str(cmdr_id)}
        name = self._known_name(cmdr_id)
        if name:
            item["name"] = name
        return item

    def _history_rows(self, data) -> list:
        """CommanderHistory rows for the recorder. Read-only."""
        rows = []
        entries = data.get("Interactions") if isinstance(data, dict) else None
        if not isinstance(entries, list):
            return rows
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("CommanderID") is None:
                continue
            flags = entry.get("Interactions")
            if not isinstance(flags, list):
                flags = []
            epoch = entry.get("Epoch")
            if isinstance(epoch, bool) or not isinstance(epoch, (int, float)):
                epoch = None
            row = {
                "id": str(entry["CommanderID"]),
                "epoch": epoch,
                "interactions": [flag for flag in flags if isinstance(flag, str)],
            }
            name = self._known_name(row["id"])
            if name:
                row["name"] = name
            rows.append(row)
        return rows

    def _history_mtime_value(self):
        ts = self.last_modified_timestamp
        try:
            if ts is None or ts == datetime.datetime.min:
                return None
            return round(ts.timestamp(), 3)
        except (OverflowError, OSError, ValueError, AttributeError):
            return None

    def _record_history_snapshot(self, data) -> None:
        try:
            self.recorder.record_history(self._history_rows(data), self._history_mtime_value())
        except Exception:
            pass

    def note_last_journal(self, event, timestamp=None) -> None:
        """Remember the latest journal event name for troubleshooting snapshots."""
        if isinstance(event, str) and event:
            self._last_journal_event = event
            self._last_journal_ts = timestamp if isinstance(timestamp, str) else None

    def _beep_decision(self, cmdr_id, beep: bool, reason: str) -> bool:
        """Log one greppable announce/skip line and return whether to beep."""
        log.info(
            "BEEPDECISION %s reason=%s cmdr=%s id=%s",
            "beep" if beep else "silent",
            reason,
            self._name_for(cmdr_id),
            cmdr_id,
        )
        try:
            fields = {
                "beep": bool(beep),
                "reason": reason,
                "id": str(cmdr_id),
            }
            name = self._known_name(cmdr_id)
            if name:
                fields["name"] = name
            if beep:
                info = self.seen_data.get(str(cmdr_id))
                sound = info.get("sound") if isinstance(info, dict) else None
                if isinstance(sound, str) and sound:
                    fields["sound"] = sound
            self.recorder.record("decision", fields)
        except Exception:
            pass
        return beep

    def _wing_join_name_silent(self, name) -> bool:
        """True when this display name was in a recent WingJoin/WingAdd/WingInvite.

        Unnamed/unknown rows are never silenced here — an interdiction unknown must
        still beep. Matching uses name_key so casing and spacing variants align.
        """
        if not isinstance(name, str) or is_unknown_name(name):
            return False
        key = name_key(name)
        now = time.time()
        at = location.wing_join_names.get(key)
        if at is None:
            at = location.wing_join_names.get(name.strip().casefold())
        if at is None:
            return False
        if now - at > _WING_JOIN_SILENCE_SECONDS:
            location.wing_join_names.pop(key, None)
            location.wing_join_names.pop(name.strip().casefold(), None)
            return False
        return True

    def note_wing_mates(self, names) -> None:
        """Record wingmate names from journal WingJoin/WingAdd/WingInvite for silence."""
        location.note_wing_mates(names)

    def debug_snapshot(self, cmdr_id) -> dict:
        """Read-only presence/announce snapshot for troubleshooting. Does not mutate state."""
        cid = str(cmdr_id)
        now_mono = time.monotonic()
        now_wall = time.time()
        info = self.seen_data.get(cid)
        name = str(info.get("name", cid)) if isinstance(info, dict) else cid
        inst = location.instance.get(cid)
        here = bool(isinstance(inst, dict) and inst.get("here"))
        shown_now = self.is_shown_present(cid)
        visit = self._visit.get(cid)
        last_signal_age = None if visit is None else round(now_mono - visit, 3)
        announced = self._announced_at.get(cid)
        last_announced_age = None if announced is None else round(now_mono - announced, 3)
        rec = self._gone.get(cid, {})
        left = rec.get("left_at")
        left_at_age = None if left is None else round(now_mono - left, 3)
        return {
            "cmdr": name,
            "id": cid,
            "utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "here": here,
            "shown_now": shown_now,
            "shown_now_flag": (inst.get("shown_now") if isinstance(inst, dict) else None),
            "last_signal_age": last_signal_age,
            "last_announced_age": last_announced_age,
            "left_at_age": left_at_age,
            "departed": bool(rec.get("departed")),
            "quiet_left": bool(rec.get("quiet_left")),
            "visit": "heard" if visit is not None else ("listed" if cid in self._visit else "absent"),
            "system": location.system,
            "state": location.state,
            "instance_reset_pending": bool(location.instance_reset_pending),
            "last_journal_event": self._last_journal_event,
            "last_journal_ts": self._last_journal_ts,
            "mono": round(now_mono, 3),
            "wall": round(now_wall, 3),
        }

    @staticmethod
    def format_beep_mark_line(kind: str, snap: dict) -> str:
        """One INFO-friendly mark line from a debug_snapshot dict."""
        def q(value):
            s = str(value).replace("\\", "\\\\").replace('"', '\\"')
            return '"' + s + '"'

        parts = [
            "BEEPMARK kind=%s" % kind,
            "cmdr=%s" % q(snap.get("cmdr")),
            "id=%s" % snap.get("id"),
            "utc=%s" % snap.get("utc"),
            "here=%s" % snap.get("here"),
            "shown_now=%s" % snap.get("shown_now"),
            "last_signal_age=%ss" % snap.get("last_signal_age"),
            "last_announced_age=%ss" % snap.get("last_announced_age"),
            "left_at_age=%s" % snap.get("left_at_age"),
            "visit=%s" % snap.get("visit"),
            "system=%s" % q(snap.get("system")),
            "state=%s" % snap.get("state"),
            "last_journal_event=%s" % snap.get("last_journal_event"),
        ]
        return " ".join(parts)

    def record_beep_mark(self, kind: str, cmdr_id) -> dict:
        """Log a BEEPMARK line and append JSON to beep_marks.jsonl. Never touches seen_commanders.json."""
        snap = self.debug_snapshot(cmdr_id)
        line = self.format_beep_mark_line(kind, snap)
        log.info("%s", line)
        record = {"kind": kind, **snap}
        try:
            payload = json.dumps(record, ensure_ascii=False) + "\n"
            with self._marks_lock:
                path = self._marks_path
                try:
                    size = os.path.getsize(path)
                except OSError:
                    size = 0
                if size + len(payload.encode("utf-8")) > _BEEP_MARKS_MAX_BYTES:
                    # Rotate: keep the newer half of the file.
                    try:
                        with open(path, "rb") as handle:
                            data = handle.read()
                        keep = data[len(data) // 2 :]
                        cut = keep.find(b"\n")
                        if cut >= 0:
                            keep = keep[cut + 1 :]
                        with open(path, "wb") as handle:
                            handle.write(keep)
                    except OSError:
                        log.exception("Failed to rotate beep_marks.jsonl")
                with open(path, "a", encoding="utf-8") as handle:
                    handle.write(payload)
        except OSError:
            log.exception("Failed to append beep_marks.jsonl")
        try:
            self.recorder.record("mark", {
                "mark": kind,
                "id": str(cmdr_id),
                "name": snap.get("cmdr"),
            })
        except Exception:
            pass
        return snap

    def _forget_quiet_left(self) -> None:
        """Scrub leftover quiet_left flags from older builds (no longer written)."""
        for rec in self._gone.values():
            rec.pop("quiet_left", None)

    def _clear_departures(self) -> None:
        """Forget leave timers and departure marks. Suppression windows are kept."""
        for rec in self._gone.values():
            rec.pop("left_at", None)
            rec.pop("departed", None)

    def _heard_clear(self) -> None:
        """Nobody counts as heard recently. The visit list itself is kept."""
        for cid in self._visit:
            self._visit[cid] = None

    def _touch(self, cmdr_id) -> None:
        """Note that the game or the journal just showed this commander.

        Updates last-signal time used by the Now display, and turns shown_now back on
        if they are still in the instance.
        """
        cid = str(cmdr_id)
        self._visit[cid] = time.monotonic()
        self._instance_dirty = True
        inst = location.instance.get(cid)
        if isinstance(inst, dict) and inst.get("here"):
            was_quiet = inst.get("quiet_display") is True
            inst["shown_now"] = True
            inst["quiet_display"] = False
            if was_quiet:
                self._record_quiet_transition(cid, False)

    def _touch_names(self, names) -> None:
        keys = {name_key(name) for name in names if isinstance(name, str) and name.strip()}
        if not keys:
            return
        for cid, info in self.seen_data.items():
            if isinstance(info, dict) and any(row_has_name(info, k) for k in keys):
                self._touch(cid)

    def _expire_quiet_presence(self) -> None:
        """Fade the Now display for anyone not heard recently.

        Does NOT clear instance "here" and does NOT touch announce bookkeeping.
        Beep presence stays until a real leave (jump, supercruise leave, death/load
        clear, kill/escape). A CommanderHistory refresh while still here uses the
        was_here path and stays silent; Now comes back via _touch → shown_now.
        """
        now = time.monotonic()
        for cid, inst in list(location.instance.items()):
            if not (isinstance(inst, dict) and inst.get("here")):
                continue
            last = self._visit.get(cid)
            if not isinstance(last, (int, float)):
                # A missing time is not an open-ended Now. Manual names used to
                # stay highlighted because is_shown_present treated None as fresh
                # and this loop rewrote None to "now" on every pass.
                if inst.get("shown_now") is not False:
                    inst["shown_now"] = False
                    inst["quiet_display"] = False
                continue
            age = now - last
            if age >= _PRESENCE_QUIET_SECONDS:
                # Display-only. Log once when Now fades, not on every worker poll.
                was_shown = inst.get("shown_now")
                if was_shown is False:
                    continue
                inst["shown_now"] = False
                inst["quiet_display"] = False
                if was_shown is not False:
                    info = self.seen_data.get(cid)
                    name = info.get("name", cid) if isinstance(info, dict) else cid
                    log.info(
                        "No signal from %s for %d minutes; cleared Now display only (still present for beeps)",
                        name,
                        _PRESENCE_QUIET_SECONDS // 60,
                    )
                continue
            if age >= _QUIET_DISPLAY_SECONDS:
                if inst.get("quiet_display") is not True:
                    inst["quiet_display"] = True
                    self._record_quiet_transition(cid, True, age)
            elif inst.get("quiet_display") is True:
                inst["quiet_display"] = False
                self._record_quiet_transition(cid, False)

    def clear_presence(self, timestamp=None) -> None:
        """Forget everyone in the instance, e.g. after the main menu or a game load.

        EDMC can hand the event to plugins a few seconds after the game wrote it, so the
        batch window is counted from the journal timestamp, not from delivery."""
        if self._skip_start_event:
            # EDMC sends a synthesized StartUp when it starts while the game runs. The restored list is still valid.
            self._skip_start_event = False
            return
        if _journal_time(timestamp) is not None and _journal_time(timestamp) == self._session_start:
            # The same game session (EDMC restarted): keep the list. The game re-lists everyone
            # already here, so stay quiet for a short window from now, not from the old journal time.
            self._batch_until = time.monotonic() + _RESTART_BATCH_SECONDS
            return
        # A real load or menu return is a new session: the next history list is
        # everyone there, announced as they are read. A same-session EDMC restart
        # took the quiet path above and does not reach this.
        self._batch_until = 0.0
        self.machine.relog()
        location.instance_reset_pending = False
        self._announced_at.clear()
        self._clear_departures()
        self._heard_clear()
        location.instance.clear()
        location.clear_snapshot()
        self._forget_quiet_left()
        if _journal_time(timestamp) != self._session_start:
            self._visit.clear()
        self._instance_dirty = True

    def note_jump(self, timestamp) -> None:
        """A jump starts a new instance, so presence from the old one no longer applies."""
        jump_at = _journal_time(timestamp)
        if jump_at is None:
            return
        self._visit.clear()
        self._forget_quiet_left()
        self._last_jump = jump_at
        self._instance_dirty = True
        self.flush_instance_seen(force=True)
        self._announced_at.clear()
        self._clear_departures()
        self._heard_clear()

    def note_instance_reset(self) -> None:
        """Entering supercruise left the instance: clear leave/announce state for a fresh visit."""
        self._forget_quiet_left()
        self._announced_at.clear()
        self._clear_departures()
        self._heard_clear()
        fresh = self.machine.commit(set(location.leave_snapshot))
        self._emit_fresh(fresh)

    def note_transition_pending(self) -> None:
        """StartJump: hold history rows until the jump actually completes."""
        here = [
            cid for cid, inst in location.instance.items()
            if isinstance(inst, dict) and inst.get("here")
        ]
        self.machine.note_pending(here)

    def poll_history_now(self) -> None:
        """Apply a CommanderHistory file that has already changed. No extra wait."""
        try:
            self.aggregated_commanders()
        except Exception:
            log.exception("Immediate history read failed")

    def _emit_fresh(self, ids) -> None:
        for cid in ids or []:
            try:
                self._emit_arrival(str(cid), "new_arrival")
            except Exception:
                log.exception("Failed to announce a staged arrival")

    def _mark_present(self, cmdr_id) -> None:
        cid = str(cmdr_id)
        inst = location.instance.get(cid)
        if not isinstance(inst, dict):
            location.add_instance(cid, state=location.state, system=location.system)
            inst = location.instance[cid]
        inst["here"] = True
        inst["shown_now"] = True
        inst["state"] = location.state
        inst["system"] = location.system
        self._touch(cid)

    def _emit_arrival(self, cmdr_id, reason: str) -> None:
        """Beep now. Does not create a seen_commanders row."""
        cid = str(cmdr_id)
        self._announced_at[cid] = time.monotonic()
        self.machine.note_announced(cid)
        self._mark_present(cid)
        if not self._beep_decision(cid, True, reason):
            return
        if self._wing_muted(cid):
            self._beep_decision(cid, False, "wing_muted")
            return
        info = self.seen_data.get(cid)
        if not isinstance(info, dict):
            info = {
                "commander_id": cid,
                "name": self._known_name(cid) or self._name_for(cid),
                "sound": "neutral",
            }
        if self._sound_listener:
            self._sound_listener([info])

    def _one_edit_match(self, name):
        """One saved commander whose name is a single-character misspelling.

        Used for journal spellings such as COILMILLOWOLF and Colmillowolf.
        Short names are left alone, and two equally close commanders are not merged.
        """
        target = name_key(name)
        if len(target) < 8:
            return None
        found = []
        for cid, info in self.seen_data.items():
            if not isinstance(info, dict):
                continue
            names = [info.get("name")]
            aliases = info.get("aliases")
            if isinstance(aliases, list):
                names.extend(aliases)
            for candidate in names:
                if not isinstance(candidate, str) or is_unknown_name(candidate):
                    continue
                if _one_edit(target, name_key(candidate)):
                    found.append(str(cid))
                    break
        unique = set(found)
        if len(unique) == 1:
            return found[0]
        return None

    def resolve_commander(self, name):
        """Map a journal name onto one saved id. Read-only aside from the name index."""
        if not isinstance(name, str) or is_unknown_name(name):
            return None
        found = self.machine.resolve(name)
        if found is not None:
            return found
        key = name_key(name)
        conf = confusable_key(name)
        exact = []
        like = []
        for cid, info in self.seen_data.items():
            if not isinstance(info, dict):
                continue
            names = [info.get("name")]
            aliases = info.get("aliases")
            if isinstance(aliases, list):
                names.extend(aliases)
            for candidate in names:
                if not isinstance(candidate, str) or is_unknown_name(candidate):
                    continue
                if name_key(candidate) == key:
                    exact.append(str(cid))
                elif confusable_key(candidate) == conf:
                    like.append(str(cid))
        chosen = None
        if len(set(exact)) == 1:
            chosen = exact[0]
        elif not exact and len(set(like)) == 1:
            chosen = like[0]
        if chosen is None:
            chosen = self._one_edit_match(name)
        if chosen is not None:
            self.machine.index_name(chosen, name)
        return chosen

    def announce_evidence(self, kind: str, name, cooldown: float | None = None) -> None:
        """Announce from a journal line. Does not wait for CommanderHistory."""
        try:
            if not isinstance(name, str) or is_unknown_name(name):
                return
            if self.machine.pending and kind != "interdiction":
                self.machine.hold_evidence(name, kind)
                cid = self.resolve_commander(name)
                if cid:
                    self.machine.index_name(cid, name)
                return
            cid = self.resolve_commander(name)
            if cid is None:
                self.machine.hold_evidence(name, kind)
                if self.machine.name_was_announced(name):
                    self._refresh_resolved_evidence(name)
                    return
                # An unnamed contact already announced in this instance is the
                # same person until every such contact has a journal name.
                # Several unknowns are not written back as a guessed mapping.
                outcome = self.machine.claim_unresolved_name(name, self._announced_unknowns())
                if outcome == "absorb":
                    self._refresh_resolved_evidence(name)
                    self._beep_decision(name, False, "unresolved_contact")
                    return
                self._emit_named(name, kind)
                return
            if cooldown:
                self.machine.note_cooldown(cid, cooldown)
            self._touch(cid)
            beep, reason = self.machine.confirm(cid, kind)
            if beep:
                self._emit_arrival(cid, reason)
            else:
                self._mark_present(cid)
                self._beep_decision(cid, False, reason)
        except Exception:
            log.exception("Journal evidence could not be announced")

    def _emit_named(self, name: str, reason: str) -> None:
        """Beep for a journal name that has no history id yet. Does not write the seen list."""
        self._beep_decision(name, True, reason)
        info = {"commander_id": name, "name": name, "sound": "neutral"}
        if self._sound_listener:
            self._sound_listener([info])

    def _promote_known_name(self, cmdr_id, name) -> bool:
        """If journal evidence was waiting on this name, announce that id now."""
        try:
            result = self.machine.bind_name(cmdr_id, name)
        except Exception:
            return False
        if not result:
            return False
        beep, reason = result
        if beep:
            self._emit_arrival(str(cmdr_id), reason)
        else:
            self._mark_present(cmdr_id)
            self._beep_decision(cmdr_id, False, reason)
        return True

    def is_shown_present(self, cmdr_id) -> bool:
        """Now-group / overlay display only — independent of beep announce decisions.

        Must still be in the instance (`here`), and either shown_now is not faded or
        they were heard within the quiet window (_visit). Old rows without shown_now
        use the visit timestamp only (compatible with restored instance_seen.json).
        """
        if not self.is_present(cmdr_id):
            return False
        cid = str(cmdr_id)
        inst = location.instance.get(cid)
        if isinstance(inst, dict) and inst.get("shown_now") is False:
            return False
        last = self._visit.get(cid)
        if not isinstance(last, (int, float)):
            return False
        return time.monotonic() - last < _PRESENCE_QUIET_SECONDS

    def now_display_state(self, cmdr_id) -> str:
        """'now', 'quiet', or 'off'. Quiet stays on Now until the 180 s fade."""
        if not self.is_shown_present(cmdr_id):
            return "off"
        last = self._visit.get(str(cmdr_id))
        if not isinstance(last, (int, float)):
            return "off"
        if time.monotonic() - last >= _QUIET_DISPLAY_SECONDS:
            return "quiet"
        return "now"

    def now_label(self, cmdr_id):
        """Text for the Last Encounter cell while this commander is on Now."""
        state = self.now_display_state(cmdr_id)
        if state == "off":
            return None
        if state == "quiet":
            last = self._visit.get(str(cmdr_id))
            age = max(0, int(time.monotonic() - last))
            if age >= 60:
                return "last heard %dm ago" % (age // 60)
            return "last heard %ds ago" % age
        return "Now"

    def session_name(self, cmdr_id):
        """Journal name claimed for an unnamed contact. Not written to the seen file."""
        info = self.seen_data.get(str(cmdr_id))
        saved = info.get("name") if isinstance(info, dict) else None
        if isinstance(saved, str) and not is_unknown_name(saved):
            return None
        name = self.machine.display_names.get(str(cmdr_id))
        if isinstance(name, str) and name.strip() and not is_unknown_name(name):
            return name.strip()
        return None

    def note_manual_name(self, cmdr_id) -> None:
        """A typed name starts the Now clock. It does not refresh one already running.

        The Seen Commanders editor (double-click, Save) and Update spelling call
        this. Previously a commander who was `here` with no visit time stayed on
        Now forever: is_shown_present treated a missing time as fresh, and the
        quiet pass rewrote that missing time to "now" instead of letting it age.
        """
        cid = str(cmdr_id)
        inst = location.instance.get(cid)
        if not (isinstance(inst, dict) and inst.get("here")):
            return
        if isinstance(self._visit.get(cid), (int, float)):
            return
        inst["shown_now"] = True
        inst["quiet_display"] = False
        self._visit[cid] = time.monotonic()
        self._instance_dirty = True

    def _refresh_resolved_evidence(self, name) -> None:
        """A scan, local chat line, or interdiction refreshes the resolved id's Now row."""
        try:
            cid = self.resolve_commander(name)
        except Exception:
            return
        if not cid:
            return
        self.machine.remember_display_name(cid, name)
        self._touch(cid)

    def _record_quiet_transition(self, cmdr_id, quiet: bool, age: float | None = None) -> None:
        try:
            info = self.seen_data.get(str(cmdr_id))
            name = self.session_name(cmdr_id)
            if not name and isinstance(info, dict):
                saved = info.get("name")
                if isinstance(saved, str):
                    name = saved
            fields = {"id": str(cmdr_id), "quiet": bool(quiet)}
            if name:
                fields["name"] = name
            if age is not None:
                fields["ago_s"] = int(age)
            self.recorder.record("quiet" if quiet else "unquiet", fields)
        except Exception:
            pass

    def instance_ids(self) -> list[str]:
        """Commander ids currently marked here. For the Now list only."""
        return [
            str(cid) for cid, inst in location.instance.items()
            if isinstance(inst, dict) and inst.get("here")
        ]

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
            self.seen_data.clear()
            return

        try:
            data = _read_seen_json(self.json_file_path)
            cleaned, hardened = self._validated_seen_data(data)
        except (OSError, ValueError, RecursionError) as err:
            log.exception("Failed to load seen_commanders.json, trying the newest backup")
            log.error(
                "Could not load seen_commanders.json (%s). That file was not applied.",
                err,
            )
            cleaned = self._load_latest_backup()
            if cleaned is None:
                log.error("No usable backup was found. The current seen commanders list was left unchanged.")
                return
            hardened = False

        # Validate fully before clear(), so a bad row cannot wipe the list.
        with self._data_lock:
            self.seen_data.clear()
            self.seen_data.update(cleaned)
            if hardened or self._normalize_seen_entries():
                self.save_seen_commanders()

    def _validated_seen_data(self, data) -> tuple[dict, bool]:
        """Copy a parsed list into safe rows. The caller's list is not touched.

        Raises ValueError when the file is not a commander list, or when it has
        rows and none of them are usable. One bad row is dropped, not fatal.
        """
        if not isinstance(data, dict):
            raise ValueError("That file is not a seen commanders list.")
        cleaned = {}
        dropped = 0
        hardened = False
        for key, entry in data.items():
            if not isinstance(entry, dict):
                dropped += 1
                continue
            row = dict(entry)
            cmdr_id = _decimal_commander_id(key) or _decimal_commander_id(row.get("commander_id"))
            if cmdr_id is None or cmdr_id in cleaned:
                dropped += 1
                continue
            if row.get("commander_id") != cmdr_id:
                row["commander_id"] = cmdr_id
                hardened = True
            if self._normalize_loaded_fields(row):
                hardened = True
            cleaned[cmdr_id] = row
        if dropped:
            hardened = True
            log.warning(
                "Skipped %d invalid seen commander entr%s",
                dropped,
                "y" if dropped == 1 else "ies",
            )
        if data and not cleaned:
            raise ValueError("That file has no valid commander entries.")
        return cleaned, hardened

    def _normalize_loaded_fields(self, entry: dict) -> bool:
        """Make one row safe to store and aggregate. Returns True when a value changed."""
        changed = False
        if "sound" in entry:
            sound = entry["sound"]
            if isinstance(sound, str):
                cleaned_sound = os.path.splitext(sound)[0].lower()
            else:
                cleaned_sound = "neutral"
            if sound != cleaned_sound:
                entry["sound"] = cleaned_sound
                changed = True

        raw_count = entry.get("seen_count", 1)
        if raw_count is None:
            raw_count = 1
        try:
            seen_count = int(raw_count)
        except (TypeError, ValueError):
            seen_count = 1
        if entry.get("seen_count") != seen_count:
            entry["seen_count"] = seen_count
            changed = True

        if "kills" in entry:
            try:
                kills = int(entry["kills"])
            except (TypeError, ValueError):
                kills = 0
            if entry["kills"] != kills:
                entry["kills"] = kills
                changed = True

        name = entry.get("name")
        if isinstance(name, str):
            visible = _without_hidden_chars(name)
            if visible != name:
                entry["name"] = visible if visible.strip() else "unknown"
                changed = True
        elif "name" in entry:
            entry["name"] = "unknown"
            changed = True

        if "aliases" in entry:
            raw_aliases = entry["aliases"]
            if isinstance(raw_aliases, list):
                aliases = [
                    alias for alias in raw_aliases
                    if isinstance(alias, str) and alias.strip() and not _has_hidden_chars(alias)
                ]
            else:
                aliases = []
            if aliases != raw_aliases:
                entry["aliases"] = aliases
                changed = True

        stable_seen = self._stable_last_seen(entry.get("last_seen"))
        if entry.get("last_seen") != stable_seen:
            entry["last_seen"] = stable_seen
            changed = True
        return changed

    @staticmethod
    def _stable_last_seen(value) -> str:
        """Keep a naive timestamp string aggregation can compare, or the epoch."""
        if isinstance(value, str):
            try:
                parsed = datetime.datetime.fromisoformat(value)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.tzinfo is None:
                return value
        parsed = parse_seen_time(value)
        if parsed is None:
            return _IMPORT_EPOCH
        return parsed.isoformat()

    def _normalize_seen_entries(self) -> bool:
        """Clean up loaded or restored rows in place. Returns True if anything changed."""
        changed = False
        cleaned = {}
        for key, entry in list(self.seen_data.items()):
            if not isinstance(entry, dict):
                changed = True
                continue
            if self._normalize_loaded_fields(entry):
                changed = True
            if backfill_seen_record(entry):
                changed = True
            cmdr_id = _decimal_commander_id(key) or _decimal_commander_id(entry.get("commander_id"))
            if cmdr_id is None:
                changed = True
                continue
            if entry.get("commander_id") != cmdr_id:
                entry["commander_id"] = cmdr_id
                changed = True
            cleaned[cmdr_id] = entry
        replaced = (
            len(cleaned) != len(self.seen_data)
            or any(self.seen_data.get(cid) is not row for cid, row in cleaned.items())
        )
        if replaced:
            self.seen_data.clear()
            self.seen_data.update(cleaned)
            changed = True
        return changed

    @property
    def backup_dir(self) -> str:
        return os.path.join(self.plugin_dir, "backups")

    def _backup_names(self) -> list[str]:
        """Startup copies only. These are the ones the automatic pruning may delete."""
        try:
            return sorted(
                name for name in os.listdir(self.backup_dir)
                if _AUTO_BACKUP_NAME.match(name)
            )
        except OSError:
            return []

    def list_backups(self) -> list[str]:
        """Every backup file, newest first. Used by the restore list."""
        try:
            names = [
                name for name in os.listdir(self.backup_dir)
                if _AUTO_BACKUP_NAME.match(name) or _MANUAL_BACKUP_NAME.match(name)
            ]
        except OSError:
            return []

        def mtime(name):
            try:
                return os.path.getmtime(os.path.join(self.backup_dir, name))
            except OSError:
                return 0.0

        return sorted(names, key=mtime, reverse=True)

    def _backup_path(self, name: str) -> str:
        if not isinstance(name, str) or os.path.basename(name) != name or name not in self.list_backups():
            raise ValueError("Not a known backup file: %r" % (name,))
        return os.path.join(self.backup_dir, name)

    def backup_seen_commanders_now(self, label: str = "") -> str | None:
        """Write the list in memory to a new manual backup file. Returns its path."""
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"seen_commanders-manual-{label}-{stamp}.json" if label else f"seen_commanders-manual-{stamp}.json"
        try:
            os.makedirs(self.backup_dir, exist_ok=True)
            path = os.path.join(self.backup_dir, name)
            with self._data_lock:
                _atomic_write_json(path, self.seen_data)
            log.info("Seen commanders backed up to %s", name)
            return path
        except (OSError, TypeError):
            log.exception("Failed to write seen commanders backup")
            return None

    def restore_backup(self, name: str) -> int:
        """Replace the current list with a backup. The current list is backed up first."""
        path = self._backup_path(name)
        try:
            data = _read_seen_json(path)
            cleaned, _hardened = self._validated_seen_data(data)
        except (OSError, ValueError, RecursionError) as err:
            log.error(
                "Restore of %s was refused (%s). The current seen commanders list was left unchanged.",
                name,
                err,
            )
            if isinstance(err, OSError):
                raise
            raise ValueError(str(err)) from None
        with self._data_lock:
            if not self.backup_seen_commanders_now("before-restore"):
                raise OSError("Could not back up the current list, so nothing was restored.")
            # Validate finished above. clear() only runs once the new list is safe.
            self.seen_data.clear()
            self.seen_data.update(cleaned)
            self._normalize_seen_entries()
            self._seen_dirty = True
            self.flush_seen_commanders(force=True)
        log.info("Restored seen commanders from %s (%d entries)", name, len(cleaned))
        return len(cleaned)

    def plan_commander_import(self, path: str) -> dict:
        """Describe what an import would change. Does not write a backup or the list.

        Only ids and names are considered. Timestamps in the file are ignored,
        so a future last_seen cannot be part of the plan.
        """
        try:
            data = _read_seen_json(
                path,
                max_bytes=_IMPORT_MAX_BYTES,
                max_entries=_IMPORT_MAX_ENTRIES,
            )
        except (OSError, ValueError, RecursionError) as err:
            log.error("Refusing to import commander names (%s). Nothing was written.", err)
            if isinstance(err, ValueError):
                raise
            raise ValueError(str(err)) from err

        added = []
        filled = []
        kept = 0
        skipped = 0
        invalid = 0
        seen_ids = set()
        with self._data_lock:
            for key, entry in data.items():
                if not isinstance(entry, dict):
                    invalid += 1
                    continue
                cmdr_id = _decimal_commander_id(entry.get("commander_id", key))
                if cmdr_id is None or cmdr_id in seen_ids:
                    invalid += 1
                    continue
                seen_ids.add(cmdr_id)
                status, name = _classify_import_name(entry.get("name"))
                if status == "skip":
                    skipped += 1
                    continue
                if status != "ok":
                    invalid += 1
                    continue
                existing = self.seen_data.get(cmdr_id)
                if isinstance(existing, dict) and not is_unknown_name(existing.get("name")):
                    kept += 1
                    continue
                item = {"id": cmdr_id, "name": name}
                if isinstance(existing, dict):
                    filled.append(item)
                else:
                    added.append(item)
        return {
            "added": added,
            "filled": filled,
            "kept": kept,
            "skipped": skipped,
            "invalid": invalid,
            "total": len(data),
            "path": path,
        }

    def apply_commander_import(self, plan: dict) -> dict:
        """Write a confirmed import. Timestamps from the shared file are never used."""
        if not isinstance(plan, dict):
            raise ValueError("That import plan is not valid.")
        added = []
        filled = []
        for item in plan.get("added") or []:
            pair = _planned_import_name(item)
            if pair is not None:
                added.append(pair)
        for item in plan.get("filled") or []:
            pair = _planned_import_name(item)
            if pair is not None:
                filled.append(pair)

        def plan_count(key: str) -> int:
            try:
                return int(plan.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        counts = {
            "added": 0,
            "filled": 0,
            "kept": plan_count("kept"),
            "skipped": plan_count("skipped"),
            "invalid": plan_count("invalid"),
            "total": plan_count("total"),
            "backup": None,
        }
        if not added and not filled:
            log.info("Import plan has no names to write")
            return counts

        backup = self.backup_seen_commanders_now("before-import")
        if backup is None:
            raise OSError("Could not back up the current list, so nothing was imported.")
        counts["backup"] = backup

        with self._data_lock:
            for cmdr_id, name in filled:
                existing = self.seen_data.get(cmdr_id)
                if isinstance(existing, dict) and is_unknown_name(existing.get("name")):
                    existing["name"] = name
                    counts["filled"] += 1
            for cmdr_id, name in added:
                existing = self.seen_data.get(cmdr_id)
                if isinstance(existing, dict):
                    if is_unknown_name(existing.get("name")):
                        existing["name"] = name
                        counts["filled"] += 1
                    else:
                        counts["kept"] += 1
                    continue
                self.seen_data[cmdr_id] = {
                    "commander_id": cmdr_id,
                    "name": name,
                    "sound": "neutral",
                    "last_seen": _import_last_seen(None),
                    "seen_count": 0,
                }
                counts["added"] += 1
            if counts["added"] or counts["filled"]:
                self._normalize_seen_entries()
                self._seen_dirty = True
                self.flush_seen_commanders(force=True)
        log.info(
            "Imported seen commander names: %s",
            {key: value for key, value in counts.items() if key != "backup"},
        )
        return counts

    def import_commander_names(self, path: str, plan: dict | None = None) -> dict:
        """Add names from another player's list.

        Only commander ids and names are taken. Timestamps, sounds, kills and
        seen counts in the file are ignored. Existing ids keep their names
        unless that name is unknown. Pass a plan from plan_commander_import
        to write exactly what was confirmed. Calling this writes a backup
        and the list.
        """
        if plan is None:
            plan = self.plan_commander_import(path)
        return self.apply_commander_import(plan)

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
            path = os.path.join(self.backup_dir, name)
            try:
                data = _read_seen_json(path)
                cleaned, _hardened = self._validated_seen_data(data)
            except (OSError, ValueError, RecursionError):
                continue
            log.error("Restored seen commanders from backup %s", name)
            return cleaned
        return None

    def save_seen_commanders(self):
        """Mark the data as changed. The worker writes it out at most every few seconds."""
        self._seen_dirty = True

    def flush_seen_commanders(self, force: bool = False) -> None:
        if not self._seen_dirty or self.json_file_path is None:
            return
        if not force and time.monotonic() - self._seen_saved_at < _SAVE_INTERVAL_SECONDS:
            return
        with self._data_lock:
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
            self._record_history_snapshot(data)
            entries = data.get("Interactions", [])
            frontier_epoch = datetime.datetime(1601, 1, 1)
        
            for entry in entries:
                interactions = entry.get("Interactions", [])
                if "Met" not in interactions:
                    continue
        
                cmdr_id = str(entry["CommanderID"])
                
                
                ts = frontier_epoch + datetime.timedelta(seconds=entry["Epoch"])
    
                existing, skip = self._row_for_sighting(cmdr_id, ts)
                if skip:
                    continue

                info: CommanderEntry = self._sighting_info(cmdr_id, existing, ts)
        
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
             
            self.save_seen_commanders()
    


    
    
    def aggregated_commanders(self):
        with self._agg_lock:
            self._aggregated_commanders()

    def _aggregated_commanders(self):
        if location.system is None:
            # Wait for the game's StartUp. Arrivals read before it are wiped by the reset.
            return
        data = self.aggregate_most_recent_commanders(False)
    
        if not data:
            return
        self._record_history_snapshot(data)
    
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
    
            existing, skip = self._row_for_sighting(cmdr_id, ts)
            if skip:
                continue

            self._touch(cmdr_id)
            current_flags = set(interactions)
            previous_flags = self.last_interactions.get(cmdr_id, set())
            
            killed_now = "Killed" in current_flags and "Killed" not in previous_flags

    
            is_wing = "WingMember" in interactions
    
            info: CommanderEntry = self._sighting_info(cmdr_id, existing, ts)
            self.machine.index_name(cmdr_id, info.get("name") if isinstance(info.get("name"), str) else "")
            if isinstance(existing, dict):
                self.machine.index_name(cmdr_id, existing.get("name") if isinstance(existing.get("name"), str) else "")

            if self.machine.pending and not killed_now:
                self.machine.stage(cmdr_id)
                self.machine.index_name(cmdr_id, info.get("name") if isinstance(info.get("name"), str) else "")
                self._beep_decision(cmdr_id, False, "transition_staged")
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                self.last_interactions[cmdr_id] = current_flags
                changed_entries.append(info)
                continue

            if self._promote_known_name(cmdr_id, info.get("name")):
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                self.last_interactions[cmdr_id] = current_flags
                changed_entries.append(info)
                continue

            if killed_now and is_unknown_name(info.get("name")):
                killed_unknown_ids.append(cmdr_id)
            if is_wing and "WingMember" not in previous_flags and is_unknown_name(info.get("name")):
                new_wing_unknown_ids.append(cmdr_id)
 
            if self._is_relist_silenced(cmdr_id, now_ts):
                # Supercruise re-lists the commanders you just left. They are not new arrivals.
                if self.machine.suppresses(cmdr_id):
                    location.forget(cmdr_id)
                self._beep_decision(cmdr_id, False, "relist_silence")
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                self.last_interactions[cmdr_id] = current_flags
                changed_entries.append(info)
                continue
            inst = location.get_instance().get(cmdr_id)
    
            beep_this_commander = True            

            suppress_until = self._gone.get(cmdr_id, {}).get("suppress_until")
            if suppress_until is not None:
                if now_ts < suppress_until:
                    # Frontier updates the killed commander's Epoch again
                    # after the kill. Keep them out of the instance until the
                    # update settles, so the kill itself is not announced.
                    self._beep_decision(cmdr_id, False, "kill_suppress")
                    location.forget(cmdr_id)
                    self.last_interactions[cmdr_id] = current_flags
                    self._remember_sighting(info, existing)
                    self.seen_data[cmdr_id] = info
                    changed_entries.append(info)
                    continue
                self._gone[cmdr_id].pop("suppress_until", None)

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
                rec = self._gone.setdefault(cmdr_id, {})
                rec["kill_removed"] = True
                location.pvp_kill_ts = None
                location.pvp_kill_victim = None
                rec["departed"] = True
                beep_this_commander = self._beep_decision(cmdr_id, False, "kill_reset")
                rec["suppress_until"] = now_ts + _KILL_RESET_SUPPRESS_SECONDS
                self.last_interactions[cmdr_id] = current_flags
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                changed_entries.append(info)
                log.info(
                    "Kill reset for %s; next sighting will be announced",
                    info.get("name") or cmdr_id,
                )
                continue

            if killed_now:
                # A kill row is not an arrival, even when they were already off the list.
                self.machine.note_killed(cmdr_id)
                self._beep_decision(cmdr_id, False, "kill")
                location.forget(cmdr_id)
                rec = self._gone.setdefault(cmdr_id, {})
                rec["departed"] = True
                rec["suppress_until"] = now_ts + _KILL_RESET_SUPPRESS_SECONDS
                self.last_interactions[cmdr_id] = current_flags
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                changed_entries.append(info)
                continue

            if jump_recent and cmdr_id in location.jump_backup:
                prev = location.jump_backup[cmdr_id]
                if prev.get("here", False):
                    # CommanderHistory bumps Epoch for people still listed when
                    # you jump. They did not jump in. Keep the new Epoch so the
                    # same refresh is not announced after the jump window.
                    if existing is not None:
                        info["seen_count"] = _safe_entry_int(existing, "seen_count", 1)
                    self._remember_sighting(info, existing)
                    self.seen_data[cmdr_id] = info
                    self.last_interactions[cmdr_id] = current_flags
                    changed_entries.append(info)
                    log.info(
                        "Not announcing %s; they were already here when you jumped",
                        info.get("name") or cmdr_id,
                    )
                    self._beep_decision(cmdr_id, False, "jump_backup")
                    continue
    
            if (is_wing and wing_recent) or self._wing_join_name_silent(info.get("name")):
                # WingMember + recent wing change, or a named WingJoin/WingAdd/WingInvite mate.
                # Name-based silence covers mates listed without WingMember (EddiesMinion class).
                reason = "wing_join" if self._wing_join_name_silent(info.get("name")) else "wing_join_batch"
                self._beep_decision(cmdr_id, False, reason)
                beep_this_commander = False
                # Still record presence so they show as here; only the beep is suppressed.
                already_here = bool(isinstance(inst, dict) and inst.get("here"))
                if not inst:
                    location.add_instance(cmdr_id, state=location.state, system=location.system)
                    inst = location.get_instance()[cmdr_id]
                inst["here"] = True
                inst["shown_now"] = True
                self._note_supercruise_join(inst, already_here)
                inst["state"] = location.state
                inst["system"] = location.system
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
                self.last_interactions[cmdr_id] = current_flags
                changed_entries.append(info)
                continue

            if inst:
                if pvp_kill_recent:
                    inst["here"] = True
                    beep_this_commander = self._beep_decision(cmdr_id, False, "pvp_kill_recent")
                       
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
                    beep_this_commander = self._beep_decision(cmdr_id, False, "interdiction_carry")
                    if existing is not None:
                        info["seen_count"] = _safe_entry_int(existing, "seen_count", 1)
                elif was_here:
                    # The game refreshes the record of everyone still in the instance.
                    # A refresh is not a departure: they stay here, silently, until a
                    # jump, supercruise leave, death/load clear, kill or escape removes them.
                    # Quiet timeout only fades Now display; it does not clear "here".
                    beep_this_commander = self._beep_decision(cmdr_id, False, "refresh")
                    if existing is not None:
                        info["seen_count"] = _safe_entry_int(existing, "seen_count", 1)
                else:
                    inst["here"] = True
                    inst["shown_now"] = True
                    self._note_supercruise_join(inst, False)
                    # Right after a game load, the game rewrites everyone already here.
                    beep_this_commander = self._decide_new_arrival(cmdr_id, in_batch, info.get("name"))

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
                inst["shown_now"] = True
                self._note_supercruise_join(inst, False)
                beep_this_commander = self._decide_new_arrival(cmdr_id, in_batch, info.get("name"))
    
    
            if not location.wing:
                allow_beep = True
            else:
                if is_wing:
                    allow_beep = self.wing_notify
                else:
                    allow_beep = True

            if beep_this_commander and not allow_beep:
                self._beep_decision(cmdr_id, False, "wing_muted")
    
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

        for rec in self._gone.values():
            if "suppress_until" in rec and rec["suppress_until"] <= now_ts:
                del rec["suppress_until"]

        if not changed_entries:
            return

    
        if beeps_to_play or connected_entries:
            log.info("TIMING emit t=%.3f file_mtime=%s names=%s", __import__("time").time(), self._history_mtime_for_log(), [str(e.get("name")) + "#" + str(e.get("commander_id")) for e in (beeps_to_play or connected_entries)])
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

    def _history_mtime_for_log(self):
        """Unix time of the newest CommanderHistory write seen by the worker, for timing logs."""
        try:
            return "%.3f" % self.last_modified_timestamp.timestamp()
        except (OverflowError, OSError, ValueError, AttributeError):
            return "none"

    def _remember_sighting(self, info: dict, existing) -> None:
        seen_at = info.get("last_seen")
        previous_last = existing.get("last_seen") if isinstance(existing, dict) else None
        note_seen_time(
            info,
            seen_at,
            existing if isinstance(existing, dict) else None,
            visit_is_new(previous_last, seen_at),
        )

    def _note_supercruise_join(self, inst, already_here: bool) -> None:
        """Mark a commander who first showed up while you were in supercruise.

        StartJump sets the location state before SupercruiseEntry. A refresh of
        someone already here copies that state onto the row, so the state field
        cannot tell a new supercruise contact from someone who stayed behind.
        """
        if location.state == 1 and not already_here and isinstance(inst, dict):
            inst["arrived_in_supercruise"] = True

    def _establish_presence(self, cmdr_id) -> None:
        """Record that this commander is in the current instance, without a beep.

        Used when the journal has already shown they are here (a ship you have
        targeted in normal space, or the commander who just pulled you out).
        A later CommanderHistory epoch is then a refresh.
        """
        cid = str(cmdr_id)
        inst = location.instance.get(cid)
        already_here = isinstance(inst, dict) and bool(inst.get("here"))
        if not isinstance(inst, dict):
            location.add_instance(cid, state=location.state, system=location.system)
            inst = location.instance[cid]
        inst["here"] = True
        inst["shown_now"] = True
        inst["state"] = location.state
        inst["system"] = location.system
        self._note_supercruise_join(inst, already_here)
        self._touch(cid)

    def _establish_presence_by_name(self, name) -> None:
        if not isinstance(name, str) or is_unknown_name(name):
            return
        key = name_key(name)
        for cid, info in self.seen_data.items():
            if isinstance(info, dict) and row_has_name(info, key):
                self._establish_presence(cid)

    def note_ship_present(self, entry: dict) -> None:
        """A targeted player ship in normal space is already in this instance.

        Targeting in supercruise does not put them in the instance you later
        drop into. Unlocking a target does not remove anyone.
        """
        if location.state != 0 or not isinstance(entry, dict):
            return
        if entry.get("event") != "ShipTargeted" or not entry.get("TargetLocked"):
            return
        for kind, name in journal_player_names(entry):
            if kind == "scan":
                self._establish_presence_by_name(name)

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

    def _announced_unknowns(self) -> list[str]:
        """Announced contacts in this instance whose display name is still unknown."""
        ids = []
        for cid in self.machine.announced:
            if not self.is_present(cid):
                continue
            info = self.seen_data.get(str(cid))
            name = info.get("name") if isinstance(info, dict) else None
            if not isinstance(info, dict) or is_unknown_name(name):
                ids.append(str(cid))
        return ids

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

    def _row_for_sighting(self, cmdr_id: str, ts: datetime.datetime):
        """Return (stored row, skip).

        skip is True when the stored last_seen is already this new or newer.
        A missing or unreadable time does not skip and does not raise, so one
        bad row cannot stop the rest of the update.
        """
        existing = self.seen_data.get(cmdr_id)
        if not isinstance(existing, dict):
            return None, False
        last_seen_existing = _comparable_last_seen(existing)
        if last_seen_existing is not None and ts <= last_seen_existing:
            return existing, True
        return existing, False

    def _sighting_info(self, cmdr_id: str, existing, ts: datetime.datetime) -> CommanderEntry:
        return {
            "commander_id": cmdr_id,
            "name": _safe_entry_text(existing, "name", "unknown"),
            "sound": _safe_entry_text(existing, "sound", "neutral"),
            "last_seen": ts.isoformat(),
            "seen_count": _safe_entry_int(existing, "seen_count", 0) + 1,
            "kills": _safe_entry_int(existing, "kills", 0),
        }

    def _known_name_keys(self) -> set[str]:
        keys = set()
        for info in self.seen_data.values():
            if not isinstance(info, dict):
                continue
            name = info.get("name")
            # Hidden characters are not a real saved name. They must not block
            # Update Spelling or a journal name for someone else.
            if not isinstance(name, str) or is_unknown_name(name) or _has_hidden_chars(name):
                continue
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
            if confusable_key(new_name) != confusable_key(old_name):
                continue
            add_alias(info, old_name, new_name)
            info["name"] = new_name
            self.note_manual_name(cmdr_id)
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

    def note_player_targets(self, entry) -> None:
        """Announce each targeted player commander on this journal line."""
        try:
            from commander_names import journal_player_names
            names = [name for kind, name in journal_player_names(entry) if kind == "scan" and name]
        except Exception:
            return
        for name in names:
            self.announce_evidence("target", name)

    def note_kill(self, victim) -> None:
        """A PvP kill by you removes the victim from the list at once.

        The game sets the Killed flag only once, so a later kill of the same commander
        is not seen in their record. The kill event itself is the reliable signal.
        The victim is not announced again until the respawn window has passed."""
        if not isinstance(victim, str) or not victim.strip():
            return
        key = name_key(victim)
        resolved = self.resolve_commander(victim)
        now_ts = time.time()
        for cid, info in list(self.seen_data.items()):
            if not isinstance(info, dict) or not isinstance(info.get("name"), str):
                continue
            if not row_has_name(info, key) and str(cid) != resolved:
                continue
            rec = self._gone.setdefault(cid, {})
            rec["departed"] = True
            rec["removed_at"] = now_ts
            rec["suppress_until"] = now_ts + _KILL_RESET_SUPPRESS_SECONDS
            location.leave_snapshot.discard(cid)
            if self.is_present(cid):
                location.kill_departed.add(cid)
                location.forget(cid)
                rec["kill_removed"] = True
        if resolved:
            self.machine.index_name(resolved, victim)
            self.machine.note_killed(resolved)
        log.info("Kill recorded for %s", victim)

    def note_escape(self, name) -> None:
        """A failed interdiction: the target is no longer with you, so drop them from the list.

        Like a kill, they are announced again only when they come back after the respawn window."""
        if not isinstance(name, str) or not name.strip():
            return
        key = name_key(name)
        now_ts = time.time()
        for cid, info in list(self.seen_data.items()):
            if not isinstance(info, dict) or not isinstance(info.get("name"), str):
                continue
            if not row_has_name(info, key):
                continue
            rec = self._gone.setdefault(cid, {})
            rec["departed"] = True
            rec["removed_at"] = now_ts
            rec["suppress_until"] = now_ts + _KILL_RESET_SUPPRESS_SECONDS
            location.leave_snapshot.discard(cid)
            if self.is_present(cid):
                location.kill_departed.add(cid)
                location.forget(cid)
                rec["kill_removed"] = True
        log.info("Interdiction target %s got away", name)

    def remember_leave_snapshot(self) -> None:
        """Entering supercruise: recently-heard people count as already here.

        The game re-lists commanders who were with you; those must stay silent.
        Only people heard within the last 10 minutes are kept — not every key in
        the visit map. That way a sticky here-after-quiet entry cannot silence a
        real supercruise arrival (Mossyone / ColonelPanic class of misses).
        """
        now = time.monotonic()
        # Keep leave_instance members unless their last signal is known-stale
        # (sticky here after quiet). Also include other recently-heard runaways.
        kept = set()
        for cid in location.leave_snapshot:
            last = self._visit.get(cid)
            if last is None or (isinstance(last, (int, float)) and now - last < _LEAVE_MEMORY_SECONDS):
                kept.add(cid)
        recent = {
            cid
            for cid, last in self._visit.items()
            if isinstance(last, (int, float)) and now - last < _LEAVE_MEMORY_SECONDS
        }
        location.leave_snapshot = (
            kept | recent
        ) - set(location.supercruise_arrivals) - location.kill_departed

    def forget_instance(self) -> None:
        """Death: the instance you were in is gone. Its people stay in the snapshot, so a
        re-list of them is not announced as new. A jump or load clears the snapshot."""
        location.death_snapshot = True
        self._visit.clear()
        self._forget_quiet_left()
        self._instance_dirty = True
        self.flush_instance_seen(force=True)

    def flush_instance_seen(self, force: bool = False) -> None:
        """Save who has been seen in this instance, so a restart in the same session keeps them."""
        if not self._instance_dirty or self._session_start is None:
            return
        if location.system is None:
            # Not known yet after a restart. Writing now would save "no system" over the good file.
            return
        if not force and time.monotonic() - self._instance_saved_at < _SAVE_INTERVAL_SECONDS:
            return
        now_mono = time.monotonic(); now_wall = time.time()
        recent = {cid: now_wall - (now_mono - t) for cid, t in self._visit.items() if t is not None and now_mono - t < _RESTORE_WINDOW_SECONDS}
        data = {"session_start": self._session_start.isoformat(), "system": location.system, "members": sorted(self._visit), "recent": recent}
        try:
            _atomic_write_json(self.instance_seen_path, data)
        except (OSError, TypeError):
            log.exception("Failed to save the instance list")
            return
        self._instance_dirty = False
        self._instance_saved_at = time.monotonic()

    def restore_instance_seen(self, current_system: str | None = None, in_supercruise: bool = False) -> None:
        """After a restart in the same game session, pick up who was already in the instance.

        In supercruise you are outside the instance, so the list is only the silence list
        for the re-list, and nobody is marked present until you drop back in."""
        if self._session_start is None:
            return
        try:
            with open(self.instance_seen_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as err:
            log.info("Not restoring the instance list: could not read it (%s)", err)
            return
        if not isinstance(data, dict) or data.get("session_start") != self._session_start.isoformat():
            log.info("Not restoring the instance list: saved in session %s, now %s", data.get("session_start") if isinstance(data, dict) else None, self._session_start.isoformat())
            return
        if current_system is None or data.get("system") != current_system:
            log.info("Not restoring the instance list: saved in %s, now in %s", data.get("system"), current_system)
            return
        members = data.get("members")
        if isinstance(members, list):
            self._visit = {str(m): None for m in members}
            if in_supercruise:
                location.system = current_system
                location.state = 1
                location.leave_snapshot = set(self._visit)
                location.instance_reset_pending = True
                self._resume_supercruise = True
                log.info("Restored %d commanders from the instance, in supercruise", len(self._visit))
            else:
                now_wall = time.time()
                for cid, when in (data.get("recent") or {}).items():
                    if now_wall - float(when) < _RESTORE_WINDOW_SECONDS:
                        location.instance[str(cid)] = {"state": None, "system": None, "here": True}
                        self._visit[str(cid)] = time.monotonic()
                log.info("Restored %d commanders seen in this instance", len(self._visit))
            self._skip_start_event = True

    def take_resume_supercruise(self) -> bool:
        """True once, for the startup event, when EDMC restarted while you were in supercruise."""
        flag = getattr(self, "_resume_supercruise", False)
        self._resume_supercruise = False
        return flag

    def record_presence_now(self) -> None:
        """Record presence changes at once, before a state change, so supercruise arrivals are not missed."""
        self._log_presence_changes()

    def drop_supercruise_arrivals(self) -> None:
        """Dropping back out of supercruise: anyone first heard only there is not in this instance.

        A successful interdiction is the exception: its target drops into normal
        space with you and stays in the instance."""
        dropped = set(location.supercruise_arrivals)
        target = location.interdiction_drop_target
        if target:
            target_key = name_key(target)
            matched = False
            for cid in list(dropped):
                info = self.seen_data.get(cid)
                if isinstance(info, dict) and row_has_name(info, target_key):
                    dropped.discard(cid)
                    matched = True
            if not matched:
                # The history row can still be unnamed when the drop arrives.
                # Keep unknown rows for the worker to resolve instead of
                # dropping the commander you just interdicted.
                for cid in list(dropped):
                    info = self.seen_data.get(cid)
                    name = info.get("name") if isinstance(info, dict) else None
                    if not isinstance(info, dict) or is_unknown_name(name):
                        dropped.discard(cid)
        if dropped or target:
            kept = set(location.supercruise_arrivals) - dropped
            log.info(
                "Supercruise drop: dropping %s; keeping %s",
                sorted(self._name_for(cid) for cid in dropped),
                sorted(self._name_for(cid) for cid in kept),
            )
        for cid in dropped:
            location.forget(cid)
        now = time.time()
        location.last_drop_ts = now
        # People only heard in supercruise get a short grace so the drop-out
        # re-list does not bring them straight back. People you left behind
        # stay on the leave snapshot, which keeps that re-list quiet.
        location.supercruise_dropped.update({cid: now for cid in dropped if cid not in location.leave_snapshot})
        location.supercruise_arrivals = set()
        # The commander who pulled you out, or who you pulled out, is in this
        # instance. Record them now so a later Met epoch is not a new arrival.
        if target and location.state != 1:
            self._establish_presence_by_name(target)
        sc_ids = set(self.machine.announced) | set(dropped) | set(location.leave_snapshot)
        self._emit_fresh(self.machine.commit(sc_ids))
        if target and location.state != 1:
            seconds = getattr(location, "interdiction_cooldown", None) or INTERDICTION_SUCCESS_COOLDOWN
            self.announce_evidence("interdiction", target, cooldown=seconds)
        # The drop-out has been handled. The leave snapshot still silences
        # people you left behind; everyone else is an ordinary update.
        location.instance_reset_pending = False

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
            # The instance is not cleared here. Commanders stay until the quiet timeout in
            # _expire_quiet_presence, because the game does not always show a leave.
            # Jumps, supercruise, load and death clear the instance on their own events.
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
        self.flush_instance_seen(force=True)
        try:
            self.recorder.close()
        except Exception:
            pass

    def worker_loop(self):
        while not self.worker_stop_event.is_set():
            try:
                if self.worker_stop_event.wait(HISTORY_POLL_SECONDS):
                    break
                self._process_departures()
                self.aggregated_commanders()
                self._resolve_unknown_names()
                self._tick += 1
                if self._tick % 7 == 0:
                    self._apply_kill_tally()
                self._expire_quiet_presence()
                self.flush_instance_seen()
                self.flush_seen_commanders()
                self._log_presence_changes()
            except Exception:
                log.exception("Exception in CommanderHistoryManager worker loop, continuing")

history_inst = CommanderHistoryManager()

