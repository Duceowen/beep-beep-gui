"""Playback fallback tests. No EDMC, game, or sound device is needed.

Run from the repository root:  python -m unittest discover -s tests
"""
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


class _Config:
    def get_config(self, key, default=None):
        return default

    def set_config(self, key, value):
        pass

    def save_config(self):
        pass


class _Log:
    def __getattr__(self, name):
        return lambda *a, **k: None


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


if "beep_beep_config" not in sys.modules:
    _stub("beep_beep_config", config=_Config())
if "logutil" not in sys.modules:
    _stub("logutil", log=_Log())

import beep_beep  # noqa: E402


def opened(chunks=None):
    proc = mock.Mock()
    drain = mock.Mock()
    drain.join = mock.Mock()
    return proc, chunks or [b""], drain


class LinuxPlayerRetry(unittest.TestCase):
    def setUp(self):
        self.beep = beep_beep.BeepBeep()

    def _play(self, retry_first=False):
        with mock.patch.object(
            beep_beep.shutil, "which", side_effect=lambda p: p in ("paplay", "pw-play")
        ), mock.patch.object(beep_beep.time, "sleep"):
            return self.beep._try_linux_players(
                "alert.wav", 1.0, False, None, retry_first=retry_first
            )

    def test_first_player_is_retried_once(self):
        self.beep._popen_player = mock.Mock(side_effect=[opened(), opened()])
        self.beep._player_kept_running = mock.Mock(side_effect=[False, True])
        self.assertTrue(self._play(retry_first=True))
        self.assertEqual(self.beep._popen_player.call_count, 2)
        for call in self.beep._popen_player.call_args_list:
            self.assertEqual(call.args[0][0], "paplay")

    def test_first_player_failure_falls_to_the_next_player(self):
        self.beep._popen_player = mock.Mock(side_effect=[opened(), opened()])
        self.beep._player_kept_running = mock.Mock(side_effect=[False, True])
        self.assertTrue(self._play())
        self.assertEqual(self.beep._popen_player.call_count, 2)
        self.assertEqual(self.beep._popen_player.call_args_list[1].args[0][0], "pw-play")

    def test_first_player_fails_twice_before_the_next_player(self):
        self.beep._popen_player = mock.Mock(side_effect=[opened(), opened(), opened()])
        self.beep._player_kept_running = mock.Mock(side_effect=[False, False, True])
        self.assertTrue(self._play(retry_first=True))
        self.assertEqual(self.beep._popen_player.call_count, 3)
        players = [call.args[0][0] for call in self.beep._popen_player.call_args_list]
        self.assertEqual(players, ["paplay", "paplay", "pw-play"])

    def test_start_error_skips_to_the_next_player(self):
        self.beep._popen_player = mock.Mock(side_effect=[OSError("no"), opened()])
        self.beep._player_kept_running = mock.Mock(return_value=True)
        self.assertTrue(self._play(retry_first=True))
        self.assertEqual(self.beep._popen_player.call_count, 2)
        self.assertEqual(self.beep._popen_player.call_args_list[1].args[0][0], "pw-play")

    def test_no_player_returns_false(self):
        with mock.patch.object(beep_beep.shutil, "which", return_value=False):
            self.assertFalse(
                self.beep._try_linux_players(
                    "alert.wav", 1.0, False, None, retry_first=True
                )
            )


if __name__ == "__main__":
    unittest.main()
