"""Troubleshooting marks: debug_snapshot is read-only; mark lines stay greppable."""
import json
import os
import sys
import tempfile
import time
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


class _Log:
    def __getattr__(self, name):
        return lambda *a, **k: None


_stub("beep_beep_config", config=types.SimpleNamespace(
    get_config=lambda *a, **k: None,
    set_config=lambda *a, **k: None,
    save_config=lambda: None,
))
_stub("logutil", log=_Log())
_stub("config", appname="EDMarketConnector")

import commander_history as ch  # noqa: E402
from commander_history import CommanderHistoryManager, location  # noqa: E402


class DebugSnapshot(unittest.TestCase):
    def setUp(self):
        location.__init__()
        self.h = CommanderHistoryManager()
        self.h.plugin_dir = tempfile.mkdtemp()
        self.h._marks_path = os.path.join(self.h.plugin_dir, "beep_marks.jsonl")
        self.h.seen_data = {
            "42": {
                "commander_id": "42",
                "name": 'Test "Cmdr"',
                "sound": "neutral",
                "last_seen": "2026-11-06T00:00:00",
                "seen_count": 1,
                "kills": 0,
            }
        }
        location.system = "Sol"
        location.state = 0
        location.add_instance("42", state=0, system="Sol")
        location.instance["42"]["here"] = True
        location.instance["42"]["shown_now"] = True
        self.h._visit["42"] = time.monotonic() - 12.5
        self.h._announced_at["42"] = time.monotonic() - 90.0
        self.h._gone["42"] = {"left_at": time.monotonic() - 5.0, "departed": False}
        self.h._last_journal_event = "SupercruiseExit"
        self.h._last_journal_ts = "2026-11-06T10:00:00Z"

    def test_debug_snapshot_does_not_mutate(self):
        before_gone = json.loads(json.dumps(self.h._gone, default=str))
        before_visit = dict(self.h._visit)
        before_ann = dict(self.h._announced_at)
        before_inst = json.loads(json.dumps(location.instance))
        before_journal = (self.h._last_journal_event, self.h._last_journal_ts)
        snap = self.h.debug_snapshot("42")
        self.assertEqual(snap["id"], "42")
        self.assertTrue(snap["here"])
        self.assertEqual(snap["last_journal_event"], "SupercruiseExit")
        self.assertEqual(self.h._gone, before_gone)
        self.assertEqual(self.h._visit, before_visit)
        self.assertEqual(self.h._announced_at, before_ann)
        self.assertEqual(location.instance, before_inst)
        self.assertEqual((self.h._last_journal_event, self.h._last_journal_ts), before_journal)

    def test_format_beep_mark_line(self):
        snap = {
            "cmdr": 'Test "Cmdr"',
            "id": "42",
            "utc": "2026-11-06T18:00:00.000Z",
            "here": True,
            "shown_now": False,
            "last_signal_age": 12.5,
            "last_announced_age": 90.0,
            "left_at_age": 5.0,
            "visit": "heard",
            "system": "Sol",
            "state": 0,
            "last_journal_event": "SupercruiseExit",
        }
        line = CommanderHistoryManager.format_beep_mark_line("no_beep", snap)
        self.assertIn("BEEPMARK kind=no_beep", line)
        self.assertIn('cmdr="Test \\"Cmdr\\""', line)
        self.assertIn("id=42", line)
        self.assertIn("here=True", line)
        self.assertIn("shown_now=False", line)
        self.assertIn("last_signal_age=12.5s", line)
        self.assertIn("last_journal_event=SupercruiseExit", line)

    def test_record_beep_mark_appends_jsonl(self):
        with mock.patch.object(ch, "log"):
            self.h.record_beep_mark("no_beep", "42")
        with open(self.h._marks_path, encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "no_beep")
        self.assertEqual(rows[0]["id"], "42")
        self.assertEqual(rows[0]["cmdr"], 'Test "Cmdr"')


if __name__ == "__main__":
    unittest.main()
