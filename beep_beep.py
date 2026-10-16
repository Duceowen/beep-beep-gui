import datetime
import os
import subprocess
import platform
import shutil
import threading
from logutil import log
from beep_beep_config import config
from commander_history import history_inst
from sound_loader import sound_inst

class BeepBeep:
    def __init__(self):
        self.last_beep = datetime.datetime.min
        self.plugin_dir = os.path.dirname(__file__)

    @property
    def volume(self) -> float:
        return config.get_config("volume", 100)

    @property
    def mute(self) -> bool:
        return config.get_config("mute", False)
    
    @property
    def sounds(self) -> int:
        return config.get_config("sounds", 1)

    def _play_sound_cross_platform(self, full_path: str, vol: float):
        system = platform.system()
        try:
            if system == "Windows":
                dll_path = os.path.join(self.plugin_dir, "BeepBeepPlay.dll")
                if os.path.isfile(dll_path):
                    import ctypes
                    dll = ctypes.CDLL(dll_path)
                    dll.BeepBeepPlay.argtypes = [ctypes.c_char_p, ctypes.c_float]
                    dll.BeepBeepPlay.restype = ctypes.c_int
                    dll.BeepBeepPlay(full_path.encode("utf-8"), vol)
                    return
                else:
                    log.warning("BeepBeepPlay.dll not found, using fallback")
                    import winsound
                    winsound.PlaySound(full_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    return
            elif system == "Darwin":
                subprocess.Popen(["afplay", "-v", str(vol), full_path],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
            else:
                ext = os.path.splitext(full_path)[1].lower()
                if ext == ".wav":
                    for player in ["paplay", "pw-play", "aplay", "ffplay", "play"]:
                        if shutil.which(player):
                            if player == "ffplay":
                                cmd = [player, "-nodisp", "-autoexit", "-af", f"volume={vol}", full_path]
                            elif player == "play":
                                cmd = [player, "-q", full_path]
                            else:
                                cmd = [player, full_path]
                            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            return
                elif ext == ".mp3":
                    for player in ["mpg123", "ffplay", "cvlc"]:
                        if shutil.which(player):
                            if player == "ffplay":
                                cmd = [player, "-nodisp", "-autoexit", "-af", f"volume={vol}", full_path]
                            else:
                                cmd = [player, full_path]
                            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    return
                log.error("No suitable sound player found for %s", full_path)
        except Exception as e:
            log.error("Failed to play sound %s: %s", full_path, e)

    def play_sound(self, base_name: str):
        if self.mute or base_name.lower() == "none":
            return
        
        file_to_play = sound_inst.sound_map.get(base_name)
    
        if not file_to_play or file_to_play == "none":
            file_to_play = sound_inst.neutral
            if not file_to_play:
                log.info("No neutral sound loaded, cannot play '%s'", base_name)
                return
    
        full_path = os.path.join(self.plugin_dir, "sounds", file_to_play)
    
        if not os.path.isfile(full_path):
            log.info("Cannot find sound %s, trying neutral instead", full_path)
            full_path = os.path.join(self.plugin_dir, "sounds", sound_inst.neutral)
            if not os.path.isfile(full_path):
                log.info("No neutral sound found, cannot play '%s'", base_name)
                return
    
        vol = self.volume / 100.0
    
        self._play_sound_cross_platform(full_path, vol)

        
    def handle_event(self, info: dict | list[dict]):
        if not isinstance(info, list):
            info = [info]
    
        if not self.sounds:
            return
    
        now = datetime.datetime.utcnow()
        possible_sounds: list[str] = []
    
        for entry in info:
            cmdr_id = entry["commander_id"]
            existing = history_inst.seen_data.get(cmdr_id, {})
            selected = existing.get("sound", sound_inst.neutral)
            if selected in ("none.wav", "none"):
                continue
    
            possible_sounds.append(selected)
            history_inst.seen_data[cmdr_id] = existing
    
        if possible_sounds:
            self.last_beep = now
            history_inst.save_seen_commanders()
            self._schedule_sounds(possible_sounds)
        
    def _schedule_sounds(self, sounds: list[str]):
        max_sounds = self.sounds
        for i, sound_file in enumerate(sounds[:max_sounds]):
            threading.Timer(
                i * 0.2,
                lambda s=sound_file: self.play_sound(s)
            ).start()
           
beep_inst = BeepBeep()

