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
from unittest import mock

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
_stub("beep_beep", beep_inst=types.SimpleNamespace(
    handle_event=lambda *a, **k: None, stop_playback=lambda: None
))
_stub("overlay_announcer", announce_connected=lambda *a: None, clear_connected=lambda: None)
_stub("gui", gui_inst=types.SimpleNamespace(
    add_or_update_commander=lambda *a, **k: None,
    build_plugin_button=lambda p: None,
    options_menu=lambda f: None,
    set_on_foot=lambda x: None,
))
_stub("myNotebook")
_stub("version", __version__="1.0.51")
logging.disable(logging.CRITICAL)

import commander_history as ch  # noqa: E402
from commander_history import location  # noqa: E402
import load as load_mod  # noqa: E402


def epoch(when: datetime.datetime) -> int:
    return int((when - datetime.datetime(1601, 1, 1)).total_seconds())


def fresh_history(session=datetime.datetime(2026, 11, 3, 0, 0, 0)):
    location.__init__()
    h = ch.CommanderHistoryManager()
    h.plugin_dir = tempfile.mkdtemp()
    h.instance_seen_path = os.path.join(h.plugin_dir, "instance_seen.json")
    h.json_file_path = os.path.join(h.plugin_dir, "seen.json")
    h.kill_tally_path = os.path.join(h.plugin_dir, "kill_tally.json")
    h._kill_tally = {}
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
            self.h._visit[cid] = None
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


class InterdictionDrop(unittest.TestCase):
    def test_indicted_target_stays_in_the_instance(self):
        h = fresh_history()
        add_row(h, "501", "Target")
        add_row(h, "502", "Bystander")
        location.system, location.state = "Shinrarta Dezhra", 1
        feed(h, ["501", "502"], datetime.datetime(2026, 11, 3, 1, 28))
        h._log_presence_changes()
        self.assertTrue(present("501"))
        self.assertTrue(present("502"))
        location.interdiction("Target")
        location.set(0, "Shinrarta Dezhra", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        self.assertTrue(present("501"))
        self.assertFalse(present("502"))


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


class DropOutOrder(unittest.TestCase):
    def test_arrival_just_before_drop_out_is_still_dropped(self):
        h = fresh_history()
        location.system, location.state = "Sol", 0
        location.set(1, "Sol", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        add_row(h, "3", "LateArrival")
        location.state = 1
        feed(h, ["3"], datetime.datetime(2026, 11, 3, 1, 28))
        # no worker tick here: the drop-out is handled first, as in load.py
        h.record_presence_now()
        location.set(0, "Sol", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        self.assertFalse(present("3"))

    def test_arrival_between_start_jump_and_entry_is_still_dropped(self):
        h = fresh_history()
        location.system, location.state = "Sol", 0
        add_row(h, "3", "Roookie Mistake")
        location.set(1, "Sol", "StartJump")
        feed(h, ["3"], datetime.datetime(2026, 11, 3, 1, 28))
        self.assertTrue(present("3"))
        location.set(1, "Sol", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        location.set(0, "Sol", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        self.assertFalse(present("3"))
        h.sent.clear()
        feed(h, ["3"], datetime.datetime(2026, 11, 3, 1, 29))
        self.assertEqual(h.sent, [])


class LongSupercruiseRelist(unittest.TestCase):
    def test_left_behind_commander_relisted_later_in_supercruise_is_silent(self):
        h = fresh_history()
        location.system, location.state = "Sol", 0
        add_row(h, "7", "Stayer")
        location.add_instance("7", state=0, system="Sol")
        location.instance["7"]["here"] = True
        h._visit["7"] = None
        location.set(1, "Sol", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        # the first worker tick after entry, then a re-list about a minute later
        feed(h, ["7"], datetime.datetime(2026, 11, 3, 1, 28))
        h.sent.clear()
        feed(h, ["7"], datetime.datetime(2026, 11, 3, 1, 29))
        self.assertEqual(h.sent, [])


class KilledCommanderRespawnsInSupercruise(unittest.TestCase):
    """Repro: you killed Victim in your instance, entered supercruise, and Victim respawned
    elsewhere and was re-listed. The re-list is a new arrival and must beep, not be silenced
    as someone left behind."""

    def test_respawn_relisted_in_supercruise_beeps(self):
        h = fresh_history()
        location.system, location.state = "Sol", 0
        add_row(h, "9", "Victim")
        location.add_instance("9", state=0, system="Sol")
        location.instance["9"]["here"] = True
        h._visit["9"] = None
        h.note_kill("Victim")
        self.assertFalse(present("9"))
        h._gone["9"].pop("suppress_until", None)
        h._gone["9"].pop("removed_at", None)
        location.set(1, "Sol", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        h.sent.clear()
        feed(h, ["9"], datetime.datetime(2026, 11, 3, 1, 40))
        self.assertEqual(h.sent, [["9"]])


class InterdictionUnknownTarget(unittest.TestCase):
    def test_unresolved_target_is_kept_when_rows_are_unknown(self):
        h = fresh_history()
        add_row(h, "501", "unknown")
        add_row(h, "502", "unknown")
        add_row(h, "503", "Bystander")
        location.system, location.state = "Shinrarta Dezhra", 1
        feed(h, ["501", "502", "503"], datetime.datetime(2026, 11, 3, 1, 28))
        h._log_presence_changes()
        location.interdiction("Korvin Amberskiy")
        location.set(0, "Shinrarta Dezhra", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        self.assertTrue(present("501"))
        self.assertTrue(present("502"))
        self.assertFalse(present("503"))


class RestartInSupercruise(unittest.TestCase):
    def saved_visit(self, members):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "instance_seen.json")
        now = time.time()
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"session_start": "2026-11-03T00:00:00", "system": "Sol",
                       "members": members, "recent": {m: now for m in members}}, handle)
        return path

    def restore(self, path, in_supercruise):
        location.__init__()
        h = ch.CommanderHistoryManager()
        h.plugin_dir = os.path.dirname(path)
        h.instance_seen_path = path
        h.set_session_start(datetime.datetime(2026, 11, 3, 0, 0, 0))
        h.restore_instance_seen("Sol", in_supercruise=in_supercruise)
        return h

    def test_restart_in_supercruise_marks_nobody_present(self):
        self.restore(self.saved_visit(["101", "102"]), in_supercruise=True)
        self.assertFalse(present("101"))
        self.assertFalse(present("102"))
        self.assertEqual(location.state, 1)
        self.assertEqual(set(location.leave_snapshot), {"101", "102"})

    def test_restart_in_supercruise_relist_is_silent_and_drop_in_announces(self):
        h = self.restore(self.saved_visit(["101", "102"]), in_supercruise=True)
        h.sent = []
        h._sound_listener = lambda infos: h.sent.append([i["commander_id"] for i in infos])
        h._batch_until = 0
        for cid in ("101", "102"):
            h.seen_data[cid] = {"commander_id": cid, "name": cid, "sound": "neutral",
                                "last_seen": "2026-11-03T00:00:00", "seen_count": 1, "kills": 0}
        feed(h, ["101", "102"], datetime.datetime(2026, 11, 3, 1, 0))
        self.assertEqual(h.sent, [])
        self.assertTrue(h.take_resume_supercruise())
        location.set(0, "Sol", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        feed(h, ["101", "102"], datetime.datetime(2026, 11, 3, 1, 2))
        self.assertEqual(sorted(sum(h.sent, [])), ["101", "102"])

    def test_restart_in_normal_space_marks_recent_present(self):
        self.restore(self.saved_visit(["101"]), in_supercruise=False)
        self.assertTrue(present("101"))
        self.assertNotEqual(location.state, 1)

    def test_journal_says_supercruise_entry(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "Journal.2026-11-03T000000.01.log")
        lines = [
            {"event": "LoadGame", "StarSystem": "Sol", "timestamp": "2026-11-03T00:00:00Z"},
            {"event": "SupercruiseEntry", "StarSystem": "Sol", "timestamp": "2026-11-03T00:10:00Z"},
        ]
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(x) for x in lines) + "\n")
        self.assertTrue(ch.read_in_supercruise(d))
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"event": "SupercruiseExit", "StarSystem": "Sol",
                                     "timestamp": "2026-11-03T00:11:00Z"}) + "\n")
        self.assertFalse(ch.read_in_supercruise(d))

class StartJumpAtRestart(unittest.TestCase):
    def journal(self, lines):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "Journal.2026-11-03T000000.01.log")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(json.dumps(x) for x in lines) + "\n")
        return d

    def test_supercruise_jump_started_counts_as_supercruise(self):
        d = self.journal([
            {"event": "LoadGame", "StarSystem": "Sol", "timestamp": "2026-11-03T00:00:00Z"},
            {"event": "SupercruiseExit", "StarSystem": "Sol", "timestamp": "2026-11-03T00:05:00Z"},
            {"event": "StartJump", "JumpType": "Supercruise", "timestamp": "2026-11-03T00:06:00Z"},
        ])
        self.assertTrue(ch.read_in_supercruise(d))

    def test_exit_after_the_jump_counts_as_normal_space(self):
        d = self.journal([
            {"event": "StartJump", "JumpType": "Supercruise", "timestamp": "2026-11-03T00:06:00Z"},
            {"event": "SupercruiseExit", "StarSystem": "Sol", "timestamp": "2026-11-03T00:07:00Z"},
        ])
        self.assertFalse(ch.read_in_supercruise(d))

    def test_hyperspace_jump_is_not_supercruise(self):
        d = self.journal([
            {"event": "StartJump", "JumpType": "Hyperspace", "StarSystem": "Kivah", "timestamp": "2026-11-03T00:06:00Z"},
        ])
        self.assertFalse(ch.read_in_supercruise(d))

class UnknownSystemDoesNotOverwrite(unittest.TestCase):
    def test_no_flush_while_system_is_unknown(self):
        h = fresh_history()
        path = h.instance_seen_path
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"session_start": "2026-11-03T00:00:00", "system": "Sol",
                       "members": ["7"], "recent": {"7": time.time()}}, handle)
        location.system = None
        h._visit = {"9": None}
        h._instance_dirty = True
        h._instance_saved_at = 0
        h.flush_instance_seen(force=True)
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle).get("system"), "Sol")

    def test_flush_writes_once_system_is_known(self):
        h = fresh_history()
        location.system = None
        h._visit = {"9": None}
        h._instance_dirty = True
        h._instance_saved_at = 0
        h.flush_instance_seen(force=True)
        self.assertFalse(os.path.exists(h.instance_seen_path))
        location.system = "Sol"
        h.flush_instance_seen(force=True)
        with open(h.instance_seen_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle).get("system"), "Sol")

class DeathKeepsOldPeopleSilent(unittest.TestCase):
    def test_relist_after_death_is_silent_and_new_people_announce(self):
        h = fresh_history()
        add_row(h, "1", "JoshLike")
        add_row(h, "2", "Newcomer")
        location.system, location.state = "Sol", 0
        location.add_instance("1", state=0, system="Sol")
        location.instance["1"]["here"] = True
        h._visit["1"] = None
        location.leave_instance(record_departures=False)
        h.forget_instance()
        self.assertIn("1", location.leave_snapshot)
        location.state = 0
        feed(h, ["1"], datetime.datetime(2026, 11, 3, 4, 17, 38))
        self.assertEqual(h.sent, [])
        self.assertFalse(present("1"))
        feed(h, ["1", "2"], datetime.datetime(2026, 11, 3, 4, 18, 0))
        self.assertEqual(h.sent, [["2"]])

    def test_jump_after_death_clears_the_snapshot(self):
        h = fresh_history()
        location.leave_snapshot = {"1"}
        location.death_snapshot = True
        location.system = "Sol"
        location.set(1, "Vega")
        location.jump()
        self.assertEqual(location.leave_snapshot, set())
        self.assertFalse(location.death_snapshot)

class ClearSnapshotTogether(unittest.TestCase):
    def test_load_clears_supercruise_arrivals_too(self):
        h = fresh_history()
        location.system, location.state = "Sol", 1
        location.supercruise_arrivals.add("77")
        location.leave_snapshot = {"77"}
        h.clear_presence("2026-11-03T05:00:00Z")
        self.assertEqual(location.supercruise_arrivals, set())
        self.assertEqual(location.leave_snapshot, set())

    def test_jump_clears_snapshot_and_arrivals_together(self):
        h = fresh_history()
        location.supercruise_arrivals.add("77")
        location.leave_snapshot = {"77"}
        location.system = "Sol"
        location.set(1, "Vega")
        location.jump()
        self.assertEqual(location.supercruise_arrivals, set())
        self.assertEqual(location.leave_snapshot, set())

class DroppedArrivalsStayOut(unittest.TestCase):
    """Real: Valanga and BrokenWeltall were heard in supercruise, dropped at the drop-out,
    then re-added by the next normal-space update and kept showing Now."""

    def test_dropped_supercruise_arrival_is_not_re_added_by_the_next_update(self):
        h = fresh_history()
        add_row(h, "701", "Valanga")
        location.system, location.state = "Shinrarta Dezhra", 1
        location.set(1, "Shinrarta Dezhra", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        location.state = 1
        location.add_instance("701", state=1, system="Shinrarta Dezhra")
        location.instance["701"]["here"] = True
        location.supercruise_arrivals.add("701")
        h._presence_saved = {"701"}
        location.set(0, "Shinrarta Dezhra", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        self.assertFalse(present("701"))
        feed(h, ["701"], datetime.datetime(2026, 11, 3, 4, 28, 6))
        self.assertFalse(present("701"))
        self.assertEqual(h.sent, [])

    def test_update_after_grace_period_shows_and_announces(self):
        h = fresh_history()
        add_row(h, "703", "Returns")
        location.system, location.state = "Shinrarta Dezhra", 0
        location.supercruise_dropped = {"703": time.time() - 60}
        feed(h, ["703"], datetime.datetime(2026, 11, 3, 4, 29, 0))
        self.assertTrue(present("703"))
        self.assertEqual(h.sent, [["703"]])

    def test_jump_lets_a_commander_be_announced_again(self):
        h = fresh_history()
        add_row(h, "702", "Back")
        location.supercruise_dropped = {"702": time.time()}
        location.system = "Shinrarta Dezhra"
        location.set(1, "Vega")
        location.jump()
        self.assertEqual(location.supercruise_dropped, {})

class ArrivalDecision(unittest.TestCase):
    def test_new_commander_outside_the_batch_beeps(self):
        h = fresh_history()
        self.assertTrue(h._decide_new_arrival("1", in_batch=False))

    def test_load_batch_is_quiet(self):
        h = fresh_history()
        self.assertFalse(h._decide_new_arrival("1", in_batch=True))
        self.assertNotIn("1", h._announced_at)

    def test_recent_announce_is_quiet_until_departed(self):
        h = fresh_history()
        self.assertTrue(h._decide_new_arrival("1", in_batch=False))
        self.assertFalse(h._decide_new_arrival("1", in_batch=False))
        h._gone.setdefault("1", {})["departed"] = True
        self.assertTrue(h._decide_new_arrival("1", in_batch=False))

class QuietTimeoutReturn(unittest.TestCase):
    """Quiet timeout fades Now only. Beep presence (`here`) stays until a real leave.

    A CommanderHistory refresh while still here must not beep (was_here path).
    """

    def setUp(self):
        self.h = fresh_history()
        add_row(self.h, "801", "Valanga")
        location.system, location.state = "Shinrarta Dezhra", 0
        location.add_instance("801", state=0, system="Shinrarta Dezhra")
        location.instance["801"]["here"] = True
        location.instance["801"]["shown_now"] = True
        self.h._visit["801"] = time.monotonic() - 10_000
        self.h._presence_saved = {"801"}
        self.h._announced_at["801"] = time.monotonic() - 120

    def test_quiet_timeout_clears_now_but_keeps_here(self):
        self.assertFalse(self.h.is_shown_present("801"))  # visit already stale
        # Force shown_now on then expire
        location.instance["801"]["shown_now"] = True
        # visit still stale → expire fades Now
        self.h._expire_quiet_presence()
        self.assertTrue(present("801"))
        self.assertFalse(location.instance["801"].get("shown_now"))
        self.assertFalse(self.h.is_shown_present("801"))
        self.assertNotIn("quiet_left", self.h._gone.get("801", {}))

    def test_refresh_after_quiet_timeout_does_not_beep(self):
        self.h._expire_quiet_presence()
        self.assertTrue(present("801"))
        feed(self.h, ["801"], datetime.datetime(2026, 11, 3, 5, 10, 23))
        self.assertEqual(self.h.sent, [])
        self.assertTrue(present("801"))
        # Now comes back when they refresh
        self.assertTrue(self.h.is_shown_present("801"))
        self.assertTrue(location.instance["801"].get("shown_now"))

    def test_jump_then_return_does_beep(self):
        self.h._expire_quiet_presence()
        self.assertTrue(present("801"))
        # Real leave: jump clears visit/announce; instance is emptied by Location on jump path
        location.instance.clear()
        self.h.note_jump("2026-11-03T05:20:00Z")
        feed(self.h, ["801"], datetime.datetime(2026, 11, 3, 5, 21, 0))
        self.assertEqual(self.h.sent, [["801"]])

    def test_death_scrubs_stale_quiet_left(self):
        self.h._gone.setdefault("801", {})["quiet_left"] = True
        self.h.forget_instance()
        self.assertNotIn("quiet_left", self.h._gone.get("801", {}))

class RelistRule(unittest.TestCase):
    def setUp(self):
        self.h = fresh_history()
        location.leave_snapshot = {"11"}
        self.now = time.time()

    def test_supercruise_relist_of_someone_left_is_silenced(self):
        location.state = 1
        location.instance_reset_pending = True
        self.assertTrue(self.h._is_relist_silenced("11", self.now))

    def test_death_relist_of_someone_left_is_silenced(self):
        location.state = 0
        location.death_snapshot = True
        self.assertTrue(self.h._is_relist_silenced("11", self.now))

    def test_someone_not_in_the_snapshot_is_never_silenced(self):
        location.state = 1
        location.instance_reset_pending = True
        self.assertFalse(self.h._is_relist_silenced("99", self.now))

    def test_drop_grace_silences_then_releases(self):
        location.state = 0
        location.supercruise_dropped = {"11": self.now}
        self.assertTrue(self.h._is_relist_silenced("11", self.now + 5))
        self.assertFalse(self.h._is_relist_silenced("11", self.now + 60))

class SameSessionStart(unittest.TestCase):
    """Real: EDMC reloaded during a session (synthesized StartUp). The game re-listed everyone
    already here and they were announced, because the post-load window was never opened."""

    def test_same_session_start_opens_a_quiet_window_from_now(self):
        h = fresh_history(session=datetime.datetime(2026, 11, 3, 5, 39, 38))
        location.system, location.state = "Shinrarta Dezhra", 0
        h.clear_presence("2026-11-03T05:39:38Z")
        self.assertGreater(h._batch_until, time.monotonic() + 30)
        add_row(h, "901", "AlreadyHere")
        feed(h, ["901"], datetime.datetime(2026, 11, 3, 5, 42, 34))
        self.assertEqual(h.sent, [])
        self.assertTrue(present("901"))

class QuietReturnAfterSupercruise(unittest.TestCase):
    """Real: Defiant act and roshinator were removed by the quiet timeout at 05:50. You entered
    supercruise at 05:58 and dropped back in at 05:59, and they stayed silent."""

    def test_drop_in_after_supercruise_announces_a_quiet_removed_commander(self):
        h = fresh_history()
        add_row(h, "1001", "Defiant act")
        location.system, location.state = "Shinrarta Dezhra", 0
        location.add_instance("1001", state=0, system="Shinrarta Dezhra")
        location.instance["1001"]["here"] = True
        h._visit["1001"] = time.monotonic() - 10_000
        h._presence_saved = {"1001"}
        h._expire_quiet_presence()
        self.assertTrue(present("1001"))  # here stays; Now faded
        self.assertFalse(h.is_shown_present("1001"))
        location.set(1, "Shinrarta Dezhra", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        location.set(0, "Shinrarta Dezhra", "SupercruiseExit")
        h.drop_supercruise_arrivals()
        feed(h, ["1001"], datetime.datetime(2026, 11, 3, 5, 59, 14))
        self.assertEqual(h.sent, [["1001"]])

class QuietReturnAfterHyperspace(unittest.TestCase):
    """A hyperspace jump (FSDJump) starts a new instance, so a quiet-timeout removal must not
    silence the return in the system you jumped to."""

    def test_fsd_jump_lets_a_quiet_removed_commander_announce(self):
        h = fresh_history()
        add_row(h, "1101", "Quiet")
        location.system, location.state = "Shinrarta Dezhra", 0
        location.add_instance("1101", state=0, system="Shinrarta Dezhra")
        location.instance["1101"]["here"] = True
        h._visit["1101"] = time.monotonic() - 10_000
        h._presence_saved = {"1101"}
        h._expire_quiet_presence()
        self.assertTrue(present("1101"))
        self.assertFalse(h.is_shown_present("1101"))
        location.instance.clear()  # jump leaves the instance
        h.note_jump("2026-11-03T06:10:00Z")
        location.system = "Kivah"
        feed(h, ["1101"], datetime.datetime(2026, 11, 3, 6, 10, 30))
        self.assertEqual(h.sent, [["1101"]])

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
        h._visit = {m: None for m in members}
        for cid in recent_ids:
            h._visit[cid] = time.monotonic()
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
        self.assertIn("b", h2._visit)

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
        h._visit = {"x": None}
        location.system = "Sol"
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

class LeaveSnapshotRecentOnly(unittest.TestCase):
    """Sticky here-after-quiet must not put stale commanders into leave_snapshot.

    Repro shape (Mossyone / ColonelPanic marks): heard earlier, quiet for a while with
    here still True, enter supercruise, then they appear again — must beep as a SC arrival,
    not be relist-silenced.
    """

    def test_stale_sticky_here_not_in_leave_snapshot_beeps_in_supercruise(self):
        h = fresh_history()
        location.system, location.state = "Sorbago", 0
        add_row(h, "5645652", "Mossyone")
        location.add_instance("5645652", state=0, system="Sorbago")
        location.instance["5645652"]["here"] = True
        location.instance["5645652"]["shown_now"] = False
        # Heard long ago (beyond leave-memory), but sticky here from the Now/beep split.
        h._visit["5645652"] = time.monotonic() - 900
        location.set(1, "Sorbago", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        self.assertNotIn("5645652", location.leave_snapshot)
        feed(h, ["5645652"], datetime.datetime(2026, 11, 6, 19, 27, 25))
        self.assertEqual(h.sent, [["5645652"]])
        self.assertTrue(present("5645652"))

    def test_recently_heard_left_behind_still_silent_in_supercruise(self):
        h = fresh_history()
        location.system, location.state = "Sorbago", 0
        add_row(h, "2627947", "ColonelPanic")
        location.add_instance("2627947", state=0, system="Sorbago")
        location.instance["2627947"]["here"] = True
        h._visit["2627947"] = time.monotonic() - 30
        location.set(1, "Sorbago", "SupercruiseEntry")
        location.leave_instance()
        h.remember_leave_snapshot()
        h.note_instance_reset()
        self.assertIn("2627947", location.leave_snapshot)
        feed(h, ["2627947"], datetime.datetime(2026, 11, 6, 19, 27, 25))
        self.assertEqual(h.sent, [])


class QuietExpireLogsOnce(unittest.TestCase):
    def test_expire_logs_once_when_now_fades(self):
        h = fresh_history()
        add_row(h, "1", "QuietOne")
        location.system, location.state = "Sol", 0
        location.add_instance("1", state=0, system="Sol")
        location.instance["1"]["here"] = True
        location.instance["1"]["shown_now"] = True
        h._visit["1"] = time.monotonic() - 10_000
        with mock.patch("commander_history.log") as log:
            h._expire_quiet_presence()
            h._expire_quiet_presence()
            h._expire_quiet_presence()
        info_calls = [c for c in log.info.call_args_list if c.args and "cleared Now display only" in str(c.args[0])]
        self.assertEqual(len(info_calls), 1)
        self.assertFalse(location.instance["1"].get("shown_now"))
        self.assertTrue(present("1"))



class WingJoinSilence(unittest.TestCase):
    """WingJoin/WingAdd/WingInvite names silence CH listings briefly (EddiesMinion class)."""

    def test_named_wingmate_listing_is_silent_without_wingmember(self):
        h = fresh_history()
        add_row(h, "970782", "EddiesMinion")
        location.system, location.state = "Col 285 Sector YU-F c11-1", 0
        location.set_wing(True)
        h.note_wing_mates(["EddiesMinion", "Castorhill"])
        feed(h, ["970782"], datetime.datetime(2026, 11, 6, 21, 22, 34))
        self.assertEqual(h.sent, [])
        self.assertTrue(present("970782"))

    def test_later_real_wingmate_arrival_still_beeps(self):
        h = fresh_history()
        add_row(h, "970782", "EddiesMinion")
        location.system, location.state = "Col 285 Sector YU-F c11-1", 0
        location.set_wing(True)
        h.note_wing_mates(["EddiesMinion"])
        # Expire the silence window.
        location.wing_join_names["eddiesminion"] = time.time() - 30.0
        feed(h, ["970782"], datetime.datetime(2026, 11, 6, 21, 23, 0))
        self.assertEqual(h.sent, [["970782"]])

    def test_unnamed_row_is_not_silenced_by_wing_window(self):
        h = fresh_history()
        add_row(h, "99", "unknown")
        location.system, location.state = "Sol", 0
        h.note_wing_mates(["EddiesMinion"])
        feed(h, ["99"], datetime.datetime(2026, 11, 6, 21, 22, 34))
        self.assertEqual(h.sent, [["99"]])

    def test_wing_invite_name_silences_listing(self):
        h = fresh_history()
        add_row(h, "1", "Castorhill")
        location.system, location.state = "Sol", 0
        h.note_wing_mates(["Castorhill"])  # WingInvite accepted path
        feed(h, ["1"], datetime.datetime(2026, 11, 6, 21, 22, 30))
        self.assertEqual(h.sent, [])
        self.assertTrue(present("1"))


class OperationsLobbyReturn(unittest.TestCase):
    """Operations lobby looks like ShutDown; return re-lists must stay quiet via load_batch."""

    def test_shutdown_opens_load_batch_like_menu(self):
        h = fresh_history()
        add_row(h, "1", "EddiesMinion")
        location.system, location.state = "Col 285 Sector YU-F c11-1", 0
        location.add_instance("1", state=0, system=location.system)
        location.instance["1"]["here"] = True
        # None timestamp: full load_batch window (journal lag would eat a stale ts).
        h.clear_presence(None)
        self.assertFalse(present("1"))
        self.assertGreater(h._batch_until, time.monotonic())
        feed(h, ["1"], datetime.datetime(2026, 11, 6, 21, 22, 40))
        self.assertEqual(h.sent, [])
        self.assertTrue(present("1"))

    def test_location_system_change_after_death_clears_stale_and_batches(self):
        """Died -> MainMenu re-lists old system -> Location to detention must not keep them here with beeps."""
        h = fresh_history()
        load_mod.history_inst = h
        add_row(h, "244965", "Sajtkremporkolt")
        location.system, location.state = "Sorbago", 0
        location.add_instance("244965", state=0, system="Sorbago")
        location.instance["244965"]["here"] = True
        h._batch_until = 0
        # Location teleport to detention (fresh ts so load_batch is not eaten by lag).
        now_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {
            "timestamp": now_ts,
            "event": "Location",
            "StarSystem": "Col 285 Sector YU-F c11-1",
        }
        load_mod.journal_entry(
            "Space Pope Duce", False, "Sorbago", "", entry, {}
        )
        self.assertEqual(location.system, "Col 285 Sector YU-F c11-1")
        self.assertFalse(present("244965"))
        self.assertGreater(h._batch_until, time.monotonic())
        feed(h, ["244965"], datetime.datetime(2026, 11, 6, 21, 22, 5))
        self.assertEqual(h.sent, [])


class ResurrectEventName(unittest.TestCase):
    def test_journal_resurrect_updates_location(self):
        h = fresh_history()
        load_mod.history_inst = h
        location.system, location.state = "Sorbago", 0
        entry = {
            "timestamp": "2026-11-06T21:21:57Z",
            "event": "Resurrect",
            "Option": "rebuy",
            "Cost": 1,
            "Bankrupt": False,
        }
        load_mod.journal_entry(
            "Space Pope Duce", False, "Sorbago", "", entry, {}
        )
        self.assertEqual(location.state, 0)
        self.assertEqual(location.system, "Sorbago")
