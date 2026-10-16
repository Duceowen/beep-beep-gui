import datetime
import math
import os
import signal
import struct
import subprocess
import platform
import shutil
import threading
import time
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

    def _forget_temp(self, path: str | None) -> None:
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _relax_temp(self, path: str) -> None:
        """Let the desktop player read a file mkstemp created as owner-only."""
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass

    def _scale_s16(self, raw: bytes, amplitude: float) -> bytes:
        usable = len(raw) - (len(raw) % 2)
        chunk = raw[:usable]
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                import audioop
            return audioop.mul(chunk, 2, amplitude)
        except ImportError:
            pass
        import array
        import sys
        samples = array.array("h")
        samples.frombytes(chunk)
        if sys.byteorder != "little":
            samples.byteswap()
        for index, sample in enumerate(samples):
            value = int(sample * amplitude)
            if value > 32767:
                value = 32767
            elif value < -32768:
                value = -32768
            samples[index] = value
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    def _scale_u8(self, raw: bytes, amplitude: float) -> bytes:
        out = bytearray(len(raw))
        for index, sample in enumerate(raw):
            value = int((sample - 128) * amplitude)
            if value > 127:
                value = 127
            elif value < -128:
                value = -128
            out[index] = value + 128
        return bytes(out)

    def _scale_s24(self, raw: bytes, amplitude: float) -> bytes:
        usable = len(raw) - (len(raw) % 3)
        out = bytearray(usable)
        for index in range(0, usable, 3):
            value = raw[index] | (raw[index + 1] << 8) | (raw[index + 2] << 16)
            if value & 0x800000:
                value -= 0x1000000
            value = int(value * amplitude)
            if value > 8388607:
                value = 8388607
            elif value < -8388608:
                value = -8388608
            if value < 0:
                value += 0x1000000
            out[index] = value & 0xFF
            out[index + 1] = (value >> 8) & 0xFF
            out[index + 2] = (value >> 16) & 0xFF
        return bytes(out)

    def _scale_s32(self, raw: bytes, amplitude: float) -> bytes:
        import array
        import sys
        usable = len(raw) - (len(raw) % 4)
        samples = array.array("i")
        samples.frombytes(raw[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
        for index, sample in enumerate(samples):
            value = int(sample * amplitude)
            if value > 2147483647:
                value = 2147483647
            elif value < -2147483648:
                value = -2147483648
            samples[index] = value
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    def _scale_f32(self, raw: bytes, amplitude: float) -> bytes:
        import array
        import sys
        usable = len(raw) - (len(raw) % 4)
        samples = array.array("f")
        samples.frombytes(raw[:usable])
        if sys.byteorder != "little":
            samples.byteswap()
        for index, sample in enumerate(samples):
            samples[index] = sample * amplitude
        if sys.byteorder != "little":
            samples.byteswap()
        return samples.tobytes()

    def _scale_pcm_payload(self, raw: bytes, bits: int, amplitude: float, floating: bool) -> bytes | None:
        if not raw:
            return None
        if floating:
            if bits != 32:
                return None
            return self._scale_f32(raw, amplitude)
        if bits == 16:
            return self._scale_s16(raw, amplitude)
        if bits == 8:
            return self._scale_u8(raw, amplitude)
        if bits == 24:
            return self._scale_s24(raw, amplitude)
        if bits == 32:
            return self._scale_s32(raw, amplitude)
        return None

    def _quiet_wav_bytes(self, src: bytes, amplitude: float) -> bytes | None:
        """Return a copy of a PCM wav with samples multiplied by amplitude."""
        if len(src) < 44 or src[0:4] != b"RIFF" or src[8:12] != b"WAVE":
            return None
        pos = 12
        audio_format = None
        bits = None
        block = None
        channels = None
        data_off = None
        data_size = None
        while pos + 8 <= len(src):
            chunk_id = src[pos:pos + 4]
            size = struct.unpack_from("<I", src, pos + 4)[0]
            start = pos + 8
            if start + size > len(src):
                return None
            if chunk_id == b"fmt " and size >= 16 and audio_format is None:
                audio_format, channels, _rate, _byte_rate, block, bits = struct.unpack_from("<HHIIHH", src, start)
                if audio_format == 0xFFFE and size >= 40:
                    audio_format = struct.unpack_from("<H", src, start + 24)[0]
            elif chunk_id == b"data" and data_off is None:
                data_off = start
                data_size = size
            pos = start + size + (size & 1)
        if audio_format is None or data_off is None or not channels or not block or not bits:
            return None
        floating = audio_format == 3
        if audio_format not in (1, 3):
            return None
        width = bits // 8
        if width <= 0 or block != channels * width:
            return None
        scaled = self._scale_pcm_payload(src[data_off:data_off + data_size], bits, amplitude, floating)
        if not scaled or len(scaled) > data_size:
            return None
        out = bytearray(src)
        out[data_off:data_off + len(scaled)] = scaled
        return bytes(out)

    def _write_temp_wav(self, data: bytes) -> str | None:
        import tempfile
        try:
            fd, path = tempfile.mkstemp(
                prefix="beep-beep-", suffix=".wav", dir=self.plugin_dir
            )
        except OSError:
            return None
        try:
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
        except OSError:
            os.close(fd)
            self._forget_temp(path)
            return None
        os.close(fd)
        self._relax_temp(path)
        return path

    def _render_quiet_wav(self, full_path: str, amplitude: float) -> str | None:
        """Decode mp3 or an odd wav through ffmpeg or sox when PCM scaling cannot."""
        import tempfile
        try:
            fd, path = tempfile.mkstemp(
                prefix="beep-beep-", suffix=".wav", dir=self.plugin_dir
            )
        except OSError:
            return None
        os.close(fd)
        level = f"{max(0.0, amplitude):.6f}"
        commands = []
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            commands.append([
                ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-i", full_path, "-filter:a", f"volume={level}", path,
            ])
        sox = shutil.which("sox")
        if sox:
            commands.append([sox, "-v", level, full_path, path])
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
            except OSError:
                continue
            if result.returncode == 0 and os.path.isfile(path) and os.path.getsize(path) > 44:
                self._relax_temp(path)
                return path
        self._forget_temp(path)
        return None

    def _write_quiet_wav(self, full_path: str, amplitude: float) -> str | None:
        ext = os.path.splitext(full_path)[1].lower()
        if ext == ".wav":
            try:
                with open(full_path, "rb") as handle:
                    quiet = self._quiet_wav_bytes(handle.read(), amplitude)
            except OSError:
                quiet = None
            if quiet:
                return self._write_temp_wav(quiet)
        return self._render_quiet_wav(full_path, amplitude)

    def _prepare_playback(self, full_path: str, amplitude: float):
        """Return the file to play, the player amplitude, and a temp path to delete.

        Linux players often ignore their volume flag. The samples are scaled
        first, then the player is asked for full scale so the flag cannot
        make the sound quiet a second time.
        """
        if amplitude >= 1.0:
            return full_path, 1.0, None
        quiet = self._write_quiet_wav(full_path, amplitude)
        if quiet:
            return quiet, 1.0, quiet
        log.info("Could not scale %s; asking the player to set the volume", full_path)
        return full_path, amplitude, None

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

    def _popen_player(self, cmd: list[str]):
        """Start a player and drain its stderr so a full pipe cannot stall it."""
        with self._play_lock:
            if self.mute:
                return None
            kwargs = {
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.PIPE,
                "stdin": subprocess.DEVNULL,
            }
            # Own session so stopping the player does not signal EDMC.
            if os.name == "posix":
                kwargs["start_new_session"] = True
            proc = subprocess.Popen(cmd, **kwargs)
            self._players = [item for item in self._players if item.poll() is None]
            self._players.append(proc)
        chunks: list[bytes] = []

        def drain() -> None:
            try:
                data = proc.stderr.read() if proc.stderr else b""
            except Exception:
                data = b""
            chunks.append(data or b"")

        drain_thread = threading.Thread(target=drain, daemon=True)
        drain_thread.start()
        return proc, chunks, drain_thread

    def _delete_after(self, proc: subprocess.Popen, path: str) -> None:
        started = time.monotonic()
        try:
            proc.wait()
        except Exception:
            pass
        # A wrapper can exit before the player it started has opened the file.
        if time.monotonic() - started < 0.5:
            time.sleep(15.0)
        self._forget_temp(path)

    def _command_for(
        self,
        player: str,
        full_path: str,
        amplitude: float,
        with_volume: bool = True,
    ) -> list[str]:
        level = f"{amplitude:.4f}"
        if player == "ffplay":
            cmd = [player, "-nodisp", "-autoexit"]
            if with_volume:
                cmd.extend(["-af", f"volume={level}"])
            cmd.append(full_path)
            return cmd
        if player == "play":
            cmd = [player, "-q"]
            if with_volume:
                cmd.extend(["-v", level])
            cmd.append(full_path)
            return cmd
        if player == "pw-play":
            cmd = [player]
            if with_volume:
                cmd.append(f"--volume={level}")
            cmd.append(full_path)
            return cmd
        if player == "paplay":
            cmd = [player]
            if with_volume:
                pulse = max(0, min(65536, int(round(amplitude * 65536))))
                cmd.append(f"--volume={pulse}")
            cmd.append(full_path)
            return cmd
        if player == "mpg123":
            cmd = [player]
            if with_volume:
                factor = max(0, min(32768, int(round(amplitude * 32768))))
                cmd.extend(["-f", str(factor)])
            cmd.extend(["-q", full_path])
            return cmd
        if player == "cvlc":
            cmd = [player, "--intf", "dummy", "--play-and-exit"]
            if with_volume:
                cmd.extend(["--gain", level])
            cmd.append(full_path)
            return cmd
        if player == "afplay":
            if with_volume:
                return [player, "-v", level, full_path]
            return [player, full_path]
        return [player, full_path]

    def _linux_players(self, full_path: str) -> tuple[str, ...]:
        ext = os.path.splitext(full_path)[1].lower()
        # paplay reaches the Mint desktop session. pw-play is the PipeWire
        # tool. aplay is last because it often targets a device the desktop
        # is not using.
        if ext == ".wav":
            return ("paplay", "pw-play", "ffplay", "play", "aplay")
        if ext == ".mp3":
            return ("mpg123", "ffplay", "cvlc")
        return ()

    def _external_player_command(
        self,
        full_path: str,
        amplitude: float,
        with_volume: bool = True,
    ) -> list[str] | None:
        system = platform.system()
        if system == "Darwin":
            return self._command_for("afplay", full_path, amplitude, with_volume)
        if system == "Windows":
            return None
        for player in self._linux_players(full_path):
            if shutil.which(player):
                return self._command_for(player, full_path, amplitude, with_volume)
        return None

    def _player_kept_running(self, proc: subprocess.Popen, started: float) -> bool:
        """True when the player is still playing, or finished a real playback."""
        try:
            code = proc.wait(timeout=0.35)
        except subprocess.TimeoutExpired:
            return True
        if self.mute:
            return True
        return code == 0 and (time.monotonic() - started) >= 0.2

    def _try_linux_players(
        self,
        full_path: str,
        amplitude: float,
        with_volume: bool,
        cleanup: str | None,
    ) -> bool:
        """Try each installed player. A player that exits immediately is skipped."""
        tried = False
        for player in self._linux_players(full_path):
            if not shutil.which(player):
                continue
            if self.mute:
                self._forget_temp(cleanup)
                return True
            cmd = self._command_for(player, full_path, amplitude, with_volume)
            tried = True
            started = time.monotonic()
            try:
                opened = self._popen_player(cmd)
            except OSError as exc:
                log.error("Could not start %s: %s", player, exc)
                continue
            if opened is None:
                self._forget_temp(cleanup)
                return True
            proc, chunks, drain = opened
            if self._player_kept_running(proc, started):
                if cleanup:
                    threading.Thread(
                        target=self._delete_after,
                        args=(proc, cleanup),
                        daemon=True,
                    ).start()
                log.info("Playing %s with %s", os.path.basename(full_path), player)
                return True
            drain.join(timeout=0.2)
            err = b"".join(chunks).decode("utf-8", "replace").strip()
            log.error("Player failed (%s): %s", " ".join(cmd), err or "exited before playback")
        if not tried:
            log.error("No suitable sound player found for %s", full_path)
        return False

    def _play_linux(self, full_path: str, amplitude: float) -> None:
        """Play on Linux. The slider is baked into a copy of the file.

        The copy is played at the player's normal volume. A unity volume flag
        on that copy makes some Mint players exit or mute, and 1.0.24 stopped
        after the first player. Later players still get a turn. If the copy
        cannot be played, the original file is played.
        """
        cleanup = None
        play_path = full_path
        try:
            try:
                play_path, _ignored, cleanup = self._prepare_playback(full_path, amplitude)
            except Exception:
                log.exception("Could not scale %s", full_path)
                self._forget_temp(cleanup)
                cleanup = None
                play_path = full_path

            scaled = cleanup is not None
            # Volume flags are only for a file that was not already scaled.
            if self._try_linux_players(
                play_path, amplitude, not scaled and amplitude < 1.0, cleanup
            ):
                cleanup = None
                return
            if not scaled:
                log.error("No sound player could play %s", full_path)
                return
            log.error("Quieter copy of %s could not be played; playing the original", full_path)
            self._forget_temp(cleanup)
            cleanup = None
            if self._try_linux_players(full_path, amplitude, True, None):
                return
            self._try_linux_players(full_path, amplitude, False, None)
        finally:
            self._forget_temp(cleanup)

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

            if system == "Linux":
                self._play_linux(full_path, amplitude)
                return

            cmd = self._external_player_command(full_path, amplitude)
            if cmd is None:
                log.error("No suitable sound player found for %s", full_path)
                return
            opened = self._popen_player(cmd)
            if opened is None:
                return
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

