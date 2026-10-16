import datetime
import math
import os
import signal
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
        self._play_lock = threading.Lock()
        self._players: list[subprocess.Popen] = []
        self._timers: list[threading.Timer] = []
        self._winsound_active = False

    @property
    def volume(self) -> float:
        return config.get_config("volume", 100)

    @property
    def mute(self) -> bool:
        return config.get_config("mute", False)
    
    @property
    def sounds(self) -> int:
        return config.get_config("sounds", 1)

    def _amplitude(self) -> float:
        """Turn the 0-100 slider into playback amplitude.

        100% is full level. The steps are even in decibels, about -40 dB at
        1%, and silence at 0.
        """
        try:
            percent = float(self.volume)
        except (TypeError, ValueError):
            percent = 100.0
        percent = max(0.0, min(100.0, percent))
        if percent <= 0.0:
            return 0.0
        if percent >= 100.0:
            return 1.0
        decibels = -40.0 * (1.0 - percent / 100.0)
        return 10 ** (decibels / 20.0)

    def _dll_volume(self, amplitude: float) -> float:
        """Convert linear amplitude to the float BeepBeepPlay.dll expects.

        The DLL maps its argument v with (1-v)^2 * -10000 hundredths of a
        decibel. Passing the slider percent through that curve directly makes
        the middle of the slider nearly silent.
        """
        amplitude = max(0.0, min(1.0, amplitude))
        if amplitude <= 0.0:
            return 0.0
        if amplitude >= 1.0:
            return 1.0
        db100 = max(-10000.0, 2000.0 * math.log10(amplitude))
        return 1.0 - math.sqrt(-db100 / 10000.0)

    def set_muted(self, muted: bool) -> None:
        config.set_config("mute", bool(muted))
        config.save_config()
        if muted:
            self.stop_playback()

    def stop_playback(self) -> None:
        """Stop a sound that is already playing and cancel sounds waiting to start."""
        with self._play_lock:
            timers = self._timers
            players = self._players
            self._timers = []
            self._players = []
            winsound_active = self._winsound_active
            self._winsound_active = False
        for timer in timers:
            timer.cancel()
        for proc in players:
            self._terminate_player(proc)
        if winsound_active:
            self._purge_winsound()

    def _terminate_player(self, proc: subprocess.Popen) -> None:
        if proc.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    proc.kill()
                except OSError:
                    return
        else:
            try:
                proc.kill()
            except OSError:
                return
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass

    def _purge_winsound(self) -> None:
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            log.exception("Failed to stop winsound playback")

    def _spawn_player(self, cmd: list[str]) -> subprocess.Popen | None:
        with self._play_lock:
            if self.mute:
                return None
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
                "stdin": subprocess.DEVNULL,
            }
            # Own session so stopping the player does not signal EDMC.
            if os.name == "posix":
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **kwargs)
            self._players = [item for item in self._players if item.poll() is None]
            self._players.append(proc)
            return proc

    def _command_for(self, player: str, full_path: str, amplitude: float) -> list[str]:
        level = f"{amplitude:.4f}"
        if player == "ffplay":
            return [player, "-nodisp", "-autoexit", "-af", f"volume={level}", full_path]
        if player == "play":
            return [player, "-q", "-v", level, full_path]
        if player == "pw-play":
            return [player, f"--volume={level}", full_path]
        if player == "paplay":
            pulse = max(0, min(65536, int(round(amplitude * 65536))))
            return [player, f"--volume={pulse}", full_path]
        if player == "mpg123":
            factor = max(0, min(32768, int(round(amplitude * 32768))))
            return [player, "-f", str(factor), "-q", full_path]
        if player == "cvlc":
            return [player, "--intf", "dummy", "--play-and-exit", "--gain", level, full_path]
        if player == "afplay":
            return [player, "-v", level, full_path]
        return [player, full_path]

    def _external_player_command(self, full_path: str, amplitude: float) -> list[str] | None:
        system = platform.system()
        if system == "Darwin":
            return self._command_for("afplay", full_path, amplitude)
        if system == "Windows":
            return None
        ext = os.path.splitext(full_path)[1].lower()
        # pw-play and ffplay honor volume. paplay's --volume flag is ignored
        # when PipeWire is providing PulseAudio, which is the usual Mint setup.
        if ext == ".wav":
            players = ("pw-play", "ffplay", "play", "paplay", "aplay")
        elif ext == ".mp3":
            players = ("mpg123", "ffplay", "cvlc")
        else:
            return None
        for player in players:
            if shutil.which(player):
                return self._command_for(player, full_path, amplitude)
        return None

    def _play_sound_cross_platform(self, full_path: str, amplitude: float):
        system = platform.system()
        try:
            if system == "Windows":
                dll_path = os.path.join(self.plugin_dir, "BeepBeepPlay.dll")
                if os.path.isfile(dll_path):
                    if self.mute:
                        return
                    import ctypes
                    dll = ctypes.CDLL(dll_path)
                    dll.BeepBeepPlay.argtypes = [ctypes.c_char_p, ctypes.c_float]
                    dll.BeepBeepPlay.restype = ctypes.c_int
                    dll.BeepBeepPlay(full_path.encode("utf-8"), self._dll_volume(amplitude))
                    return
                log.warning("BeepBeepPlay.dll not found, using fallback")
                import winsound
                with self._play_lock:
                    if self.mute:
                        return
                    winsound.PlaySound(full_path, winsound.SND_FILENAME | winsound.SND_ASYNC)
                    self._winsound_active = True
                return

            cmd = self._external_player_command(full_path, amplitude)
            if cmd is None:
                log.error("No suitable sound player found for %s", full_path)
                return
            self._spawn_player(cmd)
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
    
        amplitude = self._amplitude()
        if amplitude <= 0.0:
            return

        self._play_sound_cross_platform(full_path, amplitude)

        
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
            timer = threading.Timer(
                i * 0.2,
                lambda s=sound_file: self.play_sound(s)
            )
            timer.daemon = True
            with self._play_lock:
                if self.mute:
                    return
                self._timers.append(timer)
            timer.start()
           
beep_inst = BeepBeep()

