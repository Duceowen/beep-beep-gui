import datetime
import json
import os
import platform
import re
import threading
import time
from typing import Callable, Dict, TypedDict
from logutil import log
from beep_beep_config import config
from location import location

class CommanderEntry(TypedDict):
    commander_id: str
    name: str
    sound: str
    last_seen: str
    seen_count: int

CommanderDict = Dict[str, CommanderEntry]  

class CommanderHistoryManager:
    def __init__(self):
        self.plugin_dir = os.path.dirname(__file__)
        self.seen_data: CommanderDict = {}
        if platform.system() == "Windows":
            base_dir = os.getenv("LOCALAPPDATA", "")
        elif platform.system() == "Darwin":
            base_dir = os.path.expanduser("~/Library/Application Support")
        else:
            base_dir = os.getenv("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
        self.commander_history_dir = os.path.join(
            base_dir, "Frontier Developments", "Elite Dangerous", "CommanderHistory"
        )
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


    @property
    def wing_notify(self) -> bool:
        return config.get_config("wing_notify", False)
    
    @property
    def beep_on_leave(self) -> bool:
        return config.get_config("beep_on_leave", False)    

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
                
        for cmdr_id, entry in self.seen_data.items():
            if "sound" in entry:
                entry["sound"] = os.path.splitext(entry["sound"])[0].lower()
            try:
                entry["seen_count"] = int(entry.get("seen_count", 1) or 1)
            except (TypeError, ValueError):
                entry["seen_count"] = 1

    def save_seen_commanders(self):
        if self.json_file_path is None:
            return
    
        try:
            with open(self.json_file_path, "w", encoding="utf-8") as f:
                json.dump(self.seen_data, f, indent=2)
        except (OSError, TypeError):
            log.exception("Failed to save seen_commanders.json")
            
    def aggregate_most_recent_commanders(self, first_run=False):
        self.changed = False
        try:
            abs_paths = [
                e.path
                for e in os.scandir(self.commander_history_dir)
                if e.is_file() and self.is_cmdr_history_file(e.name)
            ]
        except (FileNotFoundError, PermissionError, OSError) as err:
            log.error("Failed to list directory %s", self.commander_history_dir)
            log.exception(err)
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
 
    
            inst = location.get_instance().get(cmdr_id)
    
            beep_this_commander = True            

            if jump_recent and cmdr_id in location.jump_backup:
                prev = location.jump_backup[cmdr_id]
                if prev.get("here", False):
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
                       
                    if killed_now:
                        if info["name"] == "unknown":
                            info["name"] = location.pvp_kill_victim.lower().capitalize()
                            
                        if cmdr_id in self.last_interactions:
                            self.last_interactions[cmdr_id].discard("Killed")                        
                
                    self.last_interactions[cmdr_id] = current_flags
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
   
            self.seen_data[cmdr_id] = info
            changed_entries.append(info)
                            
            self.last_interactions[cmdr_id] = current_flags
    
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
        location.pvp_kill_ts = None
        location.pvp_kill_victim = None
        location.interdiction_ts = None

        


        self.save_seen_commanders()

      
    def _process_departures(self):
        departed = location.departed
        if not departed:
            return

        location.departed = []

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
            except Exception:
                log.exception("Exception in CommanderHistoryManager worker loop, continuing")

history_inst = CommanderHistoryManager()

