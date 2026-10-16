"""Play full and slider-scaled tones through Linux players and compare loudness.

Runs on a GitHub Ubuntu runner. The plugin must not import wave; this test may.
"""
import math
import os
import signal
import struct
import subprocess
import sys
import time
import types
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

fake_config = types.ModuleType("config")
fake_config.appname = "EDMarketConnector"
sys.modules["config"] = fake_config

import beep_beep  # noqa: E402

LOUD = "/tmp/beep-loud.wav"
QUIET = "/tmp/beep-quiet.wav"
RECORD = "/tmp/beep-rec.wav"


def write_sine(path: str) -> None:
    rate = 44100
    count = rate
    frames = bytearray()
    for index in range(count):
        value = int(0.5 * 32767 * math.sin(2 * math.pi * 440 * index / rate))
        frames += struct.pack("<hh", value, value)
    with wave.open(path, "w") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(frames)


def rms(path: str) -> float:
    with wave.open(path, "r") as handle:
        raw = handle.readframes(handle.getnframes())
        width = handle.getsampwidth()
    if width != 2 or len(raw) < 4:
        raise RuntimeError(f"bad recording {path} bytes={os.path.getsize(path) if os.path.exists(path) else 0}")
    count = len(raw) // 2
    samples = struct.unpack("<" + "h" * count, raw[:count * 2])
    audible = [sample for sample in samples if abs(sample) > 30]
    if len(audible) < 1000:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in audible) / len(audible))


def server_name(info: str) -> str:
    for line in info.splitlines():
        if line.lower().startswith("server name:"):
            return line.split(":", 1)[1].strip()
    return ""


def start_pulse() -> bool:
    """Start a private PulseAudio daemon.

    pactl prints ``Server Name: pulseaudio`` for that daemon. A PipeWire
    socket prints ``PulseAudio (on PipeWire ...)``, and playback to its null
    sink times out. The old check looked for the capitalised word PulseAudio,
    so a running daemon was reported as not started.
    """
    runtime = "/tmp/beep-runtime"
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    os.environ["XDG_RUNTIME_DIR"] = runtime
    os.environ.pop("PULSE_SERVER", None)
    socket = os.path.join(runtime, "pulse", "native")
    subprocess.run(["pulseaudio", "-k"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = subprocess.run(
        ["pulseaudio", "-D", "--exit-idle-time=-1"],
        capture_output=True,
        text=True,
    )
    print(started.stderr, flush=True)
    info = None
    deadline = time.time() + 8
    while time.time() < deadline:
        info = subprocess.run(["pactl", "info"], capture_output=True, text=True)
        if info.returncode == 0 and server_name(info.stdout):
            break
        time.sleep(0.4)
    print(info.stdout if info else "", flush=True)
    print(info.stderr if info else "", flush=True)
    if info is None or info.returncode != 0:
        return False
    name = server_name(info.stdout)
    print("server", name, flush=True)
    if name.lower() != "pulseaudio":
        return False
    os.environ["PULSE_SERVER"] = "unix:" + socket
    return True


def sink_names() -> list[str]:
    listed = subprocess.run(["pactl", "list", "short", "sinks"], capture_output=True, text=True)
    print(listed.stdout)
    print(listed.stderr)
    names = []
    for line in listed.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.append(parts[1])
    return names


def prepare_sink() -> str:
    loaded = subprocess.run(
        ["pactl", "load-module", "module-null-sink", "sink_name=beepvol", "channels=2", "rate=44100"],
        capture_output=True,
        text=True,
    )
    print("load-module", loaded.returncode, loaded.stdout.strip(), loaded.stderr.strip(), flush=True)
    time.sleep(0.5)
    names = sink_names()
    if "beepvol" not in names:
        raise RuntimeError(f"null sink missing: {names}")
    chosen = subprocess.run(["pactl", "set-default-sink", "beepvol"], capture_output=True, text=True)
    print("default", chosen.returncode, chosen.stdout.strip(), chosen.stderr.strip(), flush=True)
    if chosen.returncode != 0:
        raise RuntimeError(chosen.stderr.strip() or "could not select beepvol")
    os.environ["PULSE_SINK"] = "beepvol"
    return "beepvol"


def capture(play_cmd: list[str], sink: str) -> float:
    if os.path.exists(RECORD):
        os.remove(RECORD)
    recorder = subprocess.Popen(
        [
            "parecord",
            f"--device={sink}.monitor",
            "--file-format=wav",
            "--channels=2",
            "--rate=44100",
            "--format=s16le",
            RECORD,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.4)
    if recorder.poll() is not None:
        error = recorder.stderr.read().decode("utf-8", "replace")
        raise RuntimeError(f"parecord exited early\n{error}")
    try:
        played = subprocess.run(play_cmd, capture_output=True, text=True, timeout=8)
    except subprocess.TimeoutExpired as exc:
        recorder.kill()
        raise RuntimeError(f"play timed out {play_cmd}\n{exc}") from exc
    time.sleep(0.3)
    recorder.send_signal(signal.SIGINT)
    try:
        recorder.wait(timeout=5)
    except subprocess.TimeoutExpired:
        recorder.kill()
        recorder.wait(timeout=3)
    error = recorder.stderr.read().decode("utf-8", "replace")
    size = os.path.getsize(RECORD) if os.path.isfile(RECORD) else 0
    if played.returncode != 0 or size < 100:
        raise RuntimeError(
            f"play failed {play_cmd}\n{played.stderr}\nrecorder:\n{error}\nsize={size}"
        )
    level = rms(RECORD)
    print(f"{play_cmd} rms={level:.1f} bytes={size}", flush=True)
    if error.strip():
        print(error.strip(), flush=True)
    return level


def ratio(name: str, loud: float, quiet: float) -> None:
    print(f"{name}: loud={loud:.1f} quiet={quiet:.1f} ratio={quiet / loud if loud else 0:.3f}")
    if loud < 500:
        raise SystemExit(f"{name} recording is silent")
    if not 0.07 <= (quiet / loud) <= 0.14:
        raise SystemExit(f"{name} did not follow the 0.1 amplitude")


def main() -> None:
    source = open(os.path.join(ROOT, "beep_beep.py"), encoding="utf-8").read()
    if "import wave" in source:
        raise SystemExit("plugin imports wave")
    write_sine(LOUD)
    beep = beep_beep.beep_inst
    quiet_path, amplitude, cleanup = beep._prepare_playback(LOUD, 0.1)
    if amplitude != 1.0 or not cleanup:
        raise SystemExit(f"expected a scaled file at unity, got {quiet_path} {amplitude}")
    os.replace(cleanup, QUIET)

    if not start_pulse():
        raise SystemExit("PulseAudio did not start")
    backend = "pulse"
    print("backend", backend)
    sink = prepare_sink()

    paplay_loud = capture(["paplay", "--device", sink, "--volume=65536", LOUD], sink)
    paplay_quiet = capture(["paplay", "--device", sink, "--volume=65536", QUIET], sink)
    ratio("paplay unity flag", paplay_loud, paplay_quiet)

    aplay_loud = capture(["aplay", "-D", "pulse", LOUD], sink)
    aplay_quiet = capture(["aplay", "-D", "pulse", QUIET], sink)
    ratio("aplay", aplay_loud, aplay_quiet)

    plugin_cmd = beep._external_player_command(QUIET, 1.0)
    print("plugin command", plugin_cmd)
    if not plugin_cmd:
        raise SystemExit("no linux player command")
    player = os.path.basename(plugin_cmd[0])
    if player == "paplay" and "--volume=65536" not in plugin_cmd:
        raise SystemExit(f"paplay would scale twice: {plugin_cmd}")
    if player == "aplay" and any(arg.startswith("--volume") for arg in plugin_cmd):
        raise SystemExit(f"aplay cannot honor a volume flag: {plugin_cmd}")
    if player not in {"paplay", "aplay", "pw-play", "ffplay", "play"}:
        raise SystemExit(f"unexpected player {plugin_cmd}")
    print("ALL PASSED", backend)


if __name__ == "__main__":
    main()
