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


config = _Config()
_stub("logutil", log=_Log())
_stub("beep_beep_config", config=config)

import cooldown_timer as cd  # noqa: E402
import overlay_announcer as oa  # noqa: E402


class OverlayCountdown(unittest.TestCase):
    def setUp(self):
        cd.stop()
        self.sent = []
        oa._send = self.sent.append
        oa._initialised = True
        oa._define_group = mock.Mock()
        oa._cooldown_group_ready = False
        config.values = {
            "overlay_announce": True,
            "cooldown_timer": True,
            "cooldown_overlay": True,
        }
        self.timer_patcher = mock.patch.object(oa.threading, "Timer")
        self.timer_cls = self.timer_patcher.start()
        self.timer_cls.return_value = mock.Mock()
        self.active_patcher = mock.patch.object(cd, "is_active", return_value=True)
        self.active_patcher.start()

    def tearDown(self):
        self.active_patcher.stop()
        self.timer_patcher.stop()
        oa.stop_cooldown()
        cd.stop()

    def test_uses_dedicated_id_and_static_text(self):
        oa.start_cooldown()
        self.assertEqual(len(self.sent), 1)
        payload = self.sent[0]
        self.assertEqual(payload["id"], oa.COOLDOWN_ID)
        self.assertEqual(payload["text"], "FSD cooldown")
        self.assertEqual(payload["ttl"], oa.COOLDOWN_TTL)

    def test_registers_a_separate_group_once(self):
        oa.start_cooldown()
        oa.start_cooldown()
        self.assertEqual(oa._define_group.call_count, 1)
        kwargs = oa._define_group.call_args.kwargs
        self.assertEqual(kwargs["plugin_group_prefixes"], [oa.COOLDOWN_ID_PREFIX])
        self.assertEqual(kwargs["plugin_name"], oa.PLUGIN_NAME)

    def test_stop_clears_only_the_cooldown_id(self):
        oa.start_cooldown()
        oa.stop_cooldown()
        last = self.sent[-1]
        self.assertEqual(last["id"], oa.COOLDOWN_ID)
        self.assertEqual(last["ttl"], 0)

    def test_overlay_announce_off_sends_nothing(self):
        config.values["overlay_announce"] = False
        oa.start_cooldown()
        self.assertEqual(self.sent, [])

    def test_master_toggle_off_sends_nothing(self):
        config.values["cooldown_timer"] = False
        oa.start_cooldown()
        self.assertEqual(self.sent, [])

    def test_overlay_toggle_off_sends_nothing(self):
        config.values["cooldown_overlay"] = False
        oa.start_cooldown()
        self.assertEqual(self.sent, [])

    def test_inactive_state_clears_without_showing_text(self):
        with mock.patch.object(cd, "is_active", return_value=False):
            oa.start_cooldown()
        self.assertEqual(self.sent[-1]["ttl"], 0)


if __name__ == "__main__":
    unittest.main()
