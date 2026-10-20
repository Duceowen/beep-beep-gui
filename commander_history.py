import datetime
import json
import os
import platform
import re
import threading
import time
from typing import Callable, Dict, NotRequired, TypedDict
from logutil import log
from beep_beep_config import config
from location import location
from commander_names import (
    UnknownNameResolver,
    backfill_seen_record,
    candidate_unknown_ids,
    close_seen_time,
    is_unknown_name,
    name_key,
    note_seen_time,
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
        self.listeners: list[Callable[[list[CommanderEntry]], None]] = []
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

    @staticmethod
    def check_if_file_is_newer_than_timestamp(filepath: str, timestamp: datetime.datetime) -> bool:
        file_mtime = datetime.datetime.fromtimestamp(os.path.getmtime(filepath))
        return file_mtime > timestamp

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
            log.exception("Failed to load seen_commanders.json, starting empty")
            self.seen_data = {}
                
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

    def save_seen_commanders(self):
        if self.json_file_path is None:
            return
    
        try:
            with open(self.json_file_path, "w", encoding="utf-8") as f:
                json.dump(self.seen_data, f, indent=2)
        except (OSError, TypeError):
            log.exception("Failed to save seen_commanders.json")
            
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
                }
        
                self._remember_sighting(info, existing)
                self.seen_data[cmdr_id] = info
             
            self.save_seen_commanders()
    


    
    
    def aggregated_commanders(self):
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
            }

            if killed_now and is_unknown_name(info.get("name")):
                killed_unknown_ids.append(cmdr_id)
            if is_wing and "WingMember" not in previous_flags and is_unknown_name(info.get("name")):
                new_wing_unknown_ids.append(cmdr_id)
 
            inst = location.get_instance().get(cmdr_id)
    
            beep_this_commander = True            

            kill_reset = "Killed" in current_flags and (
                pvp_kill_recent
                or (
                    killed_now
                    and (inst is not None or cmdr_id in location.jump_backup)
                )
            )
            if kill_reset:
                # A kill ends the visit. Clear every presence record so the
                # next sighting is a fresh arrival, and consume the kill marker
                # so the return itself is not mistaken for another kill. This
                # runs before jump suppression: a victim who followed you
                # through a jump is still dead and can come back.
                location.forget(cmdr_id)
                location.pvp_kill_ts = None
                location.pvp_kill_victim = None
                beep_this_commander = False
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
                if interdiction_recent:
                    # An interdiction carries a commander with you from the
                    # old instance into the new one; they are still present,
                    # so don't mark a departure or re-announce them.
                    inst["here"] = True
                    beep_this_commander = False
                
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
            
                if was_here:
                    # The game refreshes the Epoch for commanders that are still
                    # present in the instance. Treat this as a refresh, not a
                    # departure, so we don't re-announce the arrival on the next
                    # edge while they remain in the same instance. A refresh is
                    # not a new sighting, so the seen count is left unchanged.
                    beep_this_commander = False
                    if existing is not None:
                        info["seen_count"] = existing.get("seen_count", 1)
                else:
                    inst["here"] = True
                    beep_this_commander = True

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
                beep_this_commander = True
    
    
            if not location.wing:
                allow_beep = True
            else:
                if is_wing:
                    allow_beep = self.wing_notify
                else:
                    allow_beep = True
                  
    
            if beep_this_commander and not interdiction_recent:
                connected_entries.append(info)

            if allow_beep and beep_this_commander and interdiction_recent == False:
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

        if not changed_entries:
            return
    
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
        if not isinstance(entry, dict):
            return
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

        infos = [self.seen_data[cmdr_id] for cmdr_id in departed if cmdr_id in self.seen_data]
        if infos and self._sound_listener:
            self._sound_listener(infos)
            log.info(
                "BeepBeep: departure sound for %s",
                ", ".join(info.get("name", "unknown") for info in infos),
            )

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

    def worker_loop(self):
        while not self.worker_stop_event.is_set():
            try:
                if self.worker_stop_event.wait(1):
                    break
                self._process_departures()
                self.aggregated_commanders()
                self._resolve_unknown_names()
            except Exception:
                log.exception("Exception in CommanderHistoryManager worker loop, continuing")

history_inst = CommanderHistoryManager()

