"""Scenario tests for the notification rules.

Run from the repository root:  python -m unittest discover -s tests
The EDMC-only modules are replaced with small stand-ins, so no game or EDMC is needed.
"""
import datetime
import json
import logging
import os
import sys
import tempfile
import time
import types
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


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


_stub("beep_beep_config", config=_Config())
_stub("logutil", log=_Log())
logging.disable(logging.CRITICAL)

import commander_history as ch  # noqa: E402
from commander_history import location  # noqa: E402


def epoch(when: datetime.datetime) -> int:
    return int((when - datetime.datetime(1601, 1, 1)).total_seconds())


def fresh_history(session=datetime.datetime(2026, 11, 3, 0, 0, 0)):
    location.__init__()
    h = ch.CommanderHistoryManager()
    h.plugin_dir = tempfile.mkdtemp()
    h.instance_seen_path = os.path.join(h.plugin_dir, "instance_seen.json")
    h.json_file_path = os.path.join(h.plugin_dir, "seen.json")
    h.seen_data = {}
    h.set_session_start(session)
    h._batch_until = 0
    sent = []
    h._sound_listener = lambda infos: sent.append([i["commander_id"] for i in infos])
    h.sent = sent
    return h


def add_row(h, cid, name, **kw):
    row = {"commander_id": cid, "name": name, "sound": "neutral",
           "last_seen": "2026-11-03T00:00:00", "seen_count": 1, "kills": 0}
    row.update(kw)
    h.seen_data[cid] = row


def left_at(h, cid):
    return h._gone.get(cid, {}).get("left_at")


def suppressed_until(h, cid):
    return h._gone.get(cid, {}).get("suppress_until")


def present(cid):
    return bool(location.instance.get(cid, {}).get("here"))


def feed(h, cmdr_ids, when):
    """One CommanderHistory update listing these commanders as met at `when`."""
    entries = [{"CommanderID": int(c), "Interactions": ["Met"], "Epoch": epoch(when) + i}
               for i, c in enumerate(cmdr_ids)]
    h.aggregate_most_recent_commanders = lambda first_run=False: {"Interactions": entries}
    h.aggregated_commanders()


class Arrivals(unittest.TestCase):
    def test_new_commander_in_normal_space_beeps(self):
        h = fresh_history()
        add_row(h, "11", "NewPerson")
        location.system, location.state = "Sol", 0
        feed(h, ["11"], datetime.datetime(2026, 11, 3, 1, 5))
        self.assertEqual(h.sent, [["11"]])

    def test_refresh_of_present_commander_is_silent_and_stays(self):
        h = fresh_history()
        add_row(h, "10", "Stayer")
        location.system, location.state = "Sol", 0
        location.add_instance("10", state=0, system="Sol")
        location.instance["10"]["here"] = True
        feed(h, ["10"], datetime.datetime(2026, 11, 3, 1, 0))
        self.assertTrue(present("10"))
        self.assertEqual(h.sent, [])
        self.assertIsNone(left_at(h, "10"))


class Supercruise(unittest.TestCase):
    def setUp(self):
        self.h = fresh_history()
        for cid, name in [("1", "Friend"), ("2", "Other")]:
            add_row(self.h, cid, name)
            location.add_instance(cid, state=0, system="Sol")
            location.instance[cid]["here"] = True
            self.h._instance_seen.add(cid)
        location.system, location.state = "Sol", 0
        location.set(1, "Sol", "SupercruiseEntry")
        location.leave_instance()
        self.h.remember_leave_snapshot()
        self.h.note_instance_reset()

    def test_supercruise_relist_of_people_you_left_is_silent(self):
        location.state = 1
        feed(self.h, ["1", "2"], datetime.datetime(2026, 11, 3, 1, 28))
        self.assertEqual(self.h.sent, [])

    def test_new_commander_in_supercruise_beeps(self):
        add_row(self.h, "3", "Arrives")
        location.state = 1
        feed(self.h, ["3"], datetime.datetime(2026, 11, 3, 1, 28))
        self.assertEqual(self.h.sent, [["3"]])

    def test_commander_first_heard_in_supercruise_is_dropped_on_drop_out(self):
        add_row(self.h, "3", "Arrives")
        location.state = 1
        feed(self.h, ["3"], datetime.datetime(2026, 11, 3, 1, 28))
        self.h._log_presence_changes()  # the worker tick that records supercruise arrivals
        self.assertTrue(present("3"))
        location.set(0, "Sol", "SupercruiseExit")
        self.h.drop_supercruise_arrivals()
        self.assertFalse(present("3"))

    def test_drop_in_announces_people_listed_in_the_new_instance(self):
        location.state = 1
        feed(self.h, ["1", "2"], datetime.datetime(2026, 11, 3, 1, 28))
        self.assertEqual(self.h.sent, [])
        location.set(0, "Sol", "SupercruiseExit")
        self.h.drop_supercruise_arrivals()
        feed(self.h, ["1", "2"], datetime.datetime(2026, 11, 3, 1, 29))
        self.assertEqual(sorted(sum(self.h.sent, [])), ["1", "2"])

    def test_removal_at_supercruise_entry_is_not_a_departure(self):
        self.assertIsNone(left_at(self.h, "1"))
        self.assertIsNone(left_at(self.h, "2"))


class KillsAndEscapes(unittest.TestCase):
    def test_kill_removes_victim_and_leaves_others(self):
        h = fresh_history()
        add_row(h, "1", "Victim")
        add_row(h, "2", "Friend")
        for cid in ("1", "2"):
            location.add_instance(cid, state=0, system="Sol")
            location.instance[cid]["here"] = True
        h.note_kill("victim")
        self.assertFalse(present("1"))
        self.assertTrue(present("2"))
        self.assertIsNotNone(suppressed_until(h, "1"))

    def test_kill_matches_an_earlier_name(self):
        h = fresh_history()
        add_row(h, "9", "NewName")
        h.seen_data["9"]["aliases"] = ["OldName"]
        location.add_instance("9", state=0, system="Sol")
        location.instance["9"]["here"] = True
        h.note_kill("oldname")
        self.assertFalse(present("9"))

    def test_escape_removes_target(self):
        h = fresh_history()
        add_row(h, "5", "Runner")
        location.add_instance("5", state=0, system="Sol")
        location.instance["5"]["here"] = True
        h.note_escape("runner")
        self.assertFalse(present("5"))

    def test_respawn_inside_window_is_suppressed_then_announced(self):
        h = fresh_history()
        add_row(h, "1", "Victim")
        location.system, location.state = "Sol", 0
        h.note_kill("victim")
        # inside the 30 s window: the update is ignored
        h._gone["1"]["suppress_until"] = time.time() + 30
        feed(h, ["1"], datetime.datetime(2026, 11, 3, 1, 5))
        self.assertEqual(h.sent, [])
        # after the window: a new sighting is announced
        h._gone["1"].pop("suppress_until", None)
        feed(h, ["1"], datetime.datetime(2026, 11, 3, 1, 6))
        self.assertEqual(h.sent, [["1"]])


class MergedRecord(unittest.TestCase):
    def test_kill_removal_is_not_a_leave(self):
        h = fresh_history()
        add_row(h, "1", "Victim")
        location.add_instance("1", state=0, system="Sol"); location.instance["1"]["here"] = True
        h.note_kill("victim")
        h._log_presence_changes()
        self.assertIsNone(left_at(h, "1"))

    def test_escape_removal_is_not_a_leave(self):
        h = fresh_history()
        add_row(h, "5", "Runner")
        location.add_instance("5", state=0, system="Sol"); location.instance["5"]["here"] = True
        h.note_escape("runner")
        h._log_presence_changes()
        self.assertIsNone(left_at(h, "5"))

    def test_quiet_timeout_marks_departed_so_return_announces(self):
        h = fresh_history()
        add_row(h, "7", "Quiet")
        location.system, location.state = "Sol", 0
        location.add_instance("7", state=0, system="Sol"); location.instance["7"]["here"] = True
        h._announced_at["7"] = time.monotonic() - 10
        h._gone.setdefault("7", {})["departed"] = True
        self.assertTrue(h._should_announce("7", time.monotonic()))
        self.assertFalse(h._gone["7"].get("departed"))

    def test_load_keeps_active_kill_window(self):
        h = fresh_history()
        add_row(h, "3", "Dead")
        h._gone["3"] = {"departed": True, "suppress_until": time.time() + 30}
        h._clear_departures()
        self.assertIsNotNone(suppressed_until(h, "3"))
        self.assertNotIn("departed", h._gone["3"])

class Persistence(unittest.TestCase):
    def save(self, h, members, recent_ids, system="Sol", session=datetime.datetime(2026, 11, 3, 0, 0, 0)):
        h._instance_seen = set(members)
        for cid in recent_ids:
            h._last_signal[cid] = time.monotonic()
        location.system = system
        h._instance_dirty = True
        h._instance_saved_at = 0
        h.flush_instance_seen(force=True)

    def load(self, path, session, system):
        location.__init__()
        h = ch.CommanderHistoryManager()
        h.plugin_dir = os.path.dirname(path)
        h.instance_seen_path = path
        h.set_session_start(session)
        h.restore_instance_seen(system)
        return h

    def test_same_system_restores_recent_and_not_old(self):
        h = fresh_history()
        self.save(h, ["a", "b"], ["a"])
        h2 = self.load(h.instance_seen_path, datetime.datetime(2026, 11, 3, 0, 0, 0), "Sol")
        self.assertTrue(present("a"))
        self.assertFalse(present("b"))
        self.assertIn("b", h2._instance_seen)

    def test_different_system_restores_nothing(self):
        h = fresh_history()
        self.save(h, ["a"], ["a"], system="Sol")
        self.load(h.instance_seen_path, datetime.datetime(2026, 11, 3, 0, 0, 0), "Kivah")
        self.assertFalse(present("a"))

    def test_different_session_restores_nothing(self):
        h = fresh_history()
        self.save(h, ["a"], ["a"], system="Sol")
        self.load(h.instance_seen_path, datetime.datetime(2026, 11, 4, 0, 0, 0), "Sol")
        self.assertFalse(present("a"))

    def test_synthesized_startup_after_restore_keeps_presence_once(self):
        h = fresh_history()
        self.save(h, ["a"], ["a"], system="Sol")
        h2 = self.load(h.instance_seen_path, datetime.datetime(2026, 11, 3, 0, 0, 0), "Sol")
        location.set(0, "Sol")
        self.assertTrue(present("a"))
        h2.clear_presence(None)
        self.assertTrue(present("a"))
        h2.clear_presence(None)
        self.assertFalse(present("a"))

    def test_jump_writes_the_list_at_once(self):
        h = fresh_history()
        h._instance_seen = {"x"}
        h.note_jump("2026-11-03T01:00:00Z")
        self.assertTrue(os.path.exists(h.instance_seen_path))

    def test_death_writes_an_empty_list_at_once(self):
        h = fresh_history()
        self.save(h, ["x"], [], system="Sol")
        h.forget_instance()
        with open(h.instance_seen_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle).get("members"), [])


class Journal(unittest.TestCase):
    def test_current_system_is_last_arrival_not_a_jump_target(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "Journal.2026-11-03T000000.01.log")
        lines = [
            {"event": "LoadGame", "StarSystem": "Shinrarta Dezhra", "timestamp": "2026-11-03T00:00:00Z"},
            {"event": "FSDJump", "StarSystem": "Kivah", "timestamp": "2026-11-03T00:30:00Z"},
            {"event": "StartJump", "JumpType": "Hyperspace", "StarSystem": "Timbalderis",
             "timestamp": "2026-11-03T00:31:00Z"},
        ]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(x) for x in lines) + "\n")
        self.assertEqual(ch.read_current_system(d), "Kivah")
        self.assertIsNone(ch.read_current_system(tempfile.mkdtemp()))


if __name__ == "__main__":
    unittest.main()
