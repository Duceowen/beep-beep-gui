import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


class _Config:
    def __init__(self):
        self.values = {}

    def get_config(self, key, default=None):
        return self.values.get(key, default)


class _Log:
    def __getattr__(self, name):
        return lambda *a, **k: None


if "beep_beep_config" in sys.modules:
    config = sys.modules["beep_beep_config"].config
else:
    config = _Config()
    _stub("beep_beep_config", config=config)
if "logutil" not in sys.modules:
    _stub("logutil", log=_Log())

import overlay_announcer as oa  # noqa: E402


class PvpKillOverlay(unittest.TestCase):
    def setUp(self):
        self.sent = []
        oa._send = self.sent.append
        oa._initialised = True
        oa._define_group = mock.Mock()
        oa._pvp_group_ready = False
        config.values = {"pvp_overlay": True}
        self.timer_patcher = mock.patch.object(oa.threading, "Timer")
        self.timer_cls = self.timer_patcher.start()
        self.timer_cls.return_value = mock.Mock()
        oa.clear_pvp_kills()

    def tearDown(self):
        oa.clear_pvp_kills()
        self.timer_patcher.stop()

    def test_kill_banner(self):
        oa.announce_pvp_kill("kill", "Target", 5, None, "Deciat")
        payload = self.sent[-1]
        self.assertTrue(payload["id"].startswith(oa.PVP_ID_PREFIX))
        self.assertIn("KILL: CMDR Target", payload["text"])
        self.assertIn("Deciat", payload["text"])
        self.assertEqual(payload["color"], oa.PVP_KILL_COLOR)
        self.assertEqual(payload["ttl"], oa.PVP_TTL_SECONDS)

    def test_death_banner_is_red(self):
        oa.announce_pvp_kill("death", "Killer", 8, "Anaconda", "Sorbago")
        payload = self.sent[-1]
        self.assertIn("DEATH: CMDR Killer", payload["text"])
        self.assertEqual(payload["color"], oa.PVP_DEATH_COLOR)

    def test_own_group_registered_once(self):
        oa.announce_pvp_kill("kill", "One")
        oa.announce_pvp_kill("kill", "Two")
        self.assertEqual(oa._define_group.call_count, 1)
        kwargs = oa._define_group.call_args.kwargs
        self.assertEqual(kwargs["plugin_group_prefixes"], [oa.PVP_ID_PREFIX])
        self.assertEqual(kwargs["plugin_name"], oa.PLUGIN_NAME)

    def test_setting_off_sends_nothing(self):
        config.values["pvp_overlay"] = False
        oa.announce_pvp_kill("kill", "Target")
        self.assertEqual(self.sent, [])

    def test_clear_sends_ttl_zero_for_active_ids(self):
        oa.announce_pvp_kill("kill", "Target")
        active_ids = {entry["id"] for entry in oa._pvp_entries}
        before = len(self.sent)
        oa.clear_pvp_kills()
        clears = self.sent[before:]
        self.assertEqual({p["id"] for p in clears}, active_ids)
        self.assertTrue(all(p["ttl"] == 0 for p in clears))


if __name__ == "__main__":
    unittest.main()
