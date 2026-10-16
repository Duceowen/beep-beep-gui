"""Play full and slider-scaled tones through Linux players and compare loudness.

Runs on a GitHub Ubuntu runner. The plugin must not import wave; this test may.
"""
import math
import os
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


def start_pipewire() -> bool:
    runtime = "/tmp/beep-runtime"
    os.makedirs(runtime, mode=0o700, exist_ok=True)
    os.environ["XDG_RUNTIME_DIR"] = runtime
    subprocess.run(["pulseaudio", "-k"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for cmd in (["pipewire"], ["wireplumber"], ["pipewire-pulse"]):
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
    info = subprocess.run(["pactl", "info"], capture_output=True, text=True)
    print(info.stdout)
    print(info.stderr)
    return info.returncode == 0 and "PipeWire" in info.stdout


def start_pulse() -> bool:
    subprocess.run(["pulseaudio", "-k"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    started = subprocess.run(
        ["pulseaudio", "-D", "--exit-idle-time=-1"],
        capture_output=True,
        text=True,
    )
    time.sleep(0.5)
    info = subprocess.run(["pactl", "info"], capture_output=True, text=True)
    print(info.stdout)
    print(started.stderr)
    print(info.stderr)
    return info.returncode == 0


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
        ["pactl", "load-module", "module-null-sink", "sink_name=beepvol", "channels=2", "rate=48000"],
        capture_output=True,
        text=True,
    )
    print("load-module", loaded.returncode, loaded.stdout.strip(), loaded.stderr.strip())
    time.sleep(0.5)
    names = sink_names()
    if "beepvol" not in names:
        created = subprocess.run(
            [
                "pw-cli", "create-node", "adapter",
                "{ factory.name=support.null-audio-sink node.name=beepvol media.class=Audio/Sink object.linger=1 audio.channels=2 audio.position=[FL,FR] }",
            ],
            capture_output=True,
            text=True,
        )
        print("pw-cli", created.returncode, created.stdout.strip(), created.stderr.strip())
        time.sleep(0.5)
        names = sink_names()
    if "beepvol" in names:
        sink = "beepvol"
    elif names:
        sink = names[-1]
    else:
        raise RuntimeError("no playback sink")
    # PipeWire 1.0 answers set-default-sink with "Failure: Not supported".
    # Players still accept the sink by name and through PULSE_SINK.
    chosen = subprocess.run(["pactl", "set-default-sink", sink], capture_output=True, text=True)
    print("default", sink, chosen.returncode, chosen.stdout.strip(), chosen.stderr.strip())
    os.environ["PULSE_SINK"] = sink
    return sink


def capture(play_cmd: list[str], sink: str) -> float:
    if os.path.exists(RECORD):
        os.remove(RECORD)
    recorder = subprocess.Popen(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "pulse", "-i", f"{sink}.monitor",
            "-t", "1.8", RECORD,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    played = subprocess.run(play_cmd, capture_output=True, text=True)
    recorder.wait(timeout=8)
    error = recorder.stderr.read().decode("utf-8", "replace")
    if played.returncode != 0 or not os.path.isfile(RECORD):
        raise RuntimeError(
            f"play failed {play_cmd}\n{played.stderr}\nrecorder:\n{error}"
        )
    level = rms(RECORD)
    print(f"{play_cmd} rms={level:.1f}")
    if error.strip():
        print(error.strip())
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

    backend = "pipewire" if start_pipewire() else ""
    if not backend:
        print("PipeWire did not start, using PulseAudio")
        if not start_pulse():
            raise SystemExit("no audio server")
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
