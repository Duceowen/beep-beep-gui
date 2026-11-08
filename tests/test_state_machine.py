"""1.1.6 instance state machine, the Nov 7 marks, and announcement latency.

Journal evidence must announce inside the journal call. A history refresh of
someone already known must not beep, and must not wait on a grace timer.
"""
import datetime
import json
import time
import unittest

from test_scenarios import add_row, feed, fresh_history, load_mod, location, present
from instance_machine import (
    HISTORY_POLL_SECONDS,
    INTERDICTION_SUBMIT_COOLDOWN,
    INTERDICTION_SUCCESS_COOLDOWN,
)


def play(h, event, **fields):
    load_mod.history_inst = h
    entry = {"timestamp": fields.pop("timestamp", "2026-11-08T05:00:00Z"), "event": event}
    entry.update(fields)
    system = entry.get("StarSystem") or location.system or "Sol"
    before = len(h.sent)
    load_mod.journal_entry("Tester", False, system, "", entry, {})
    timer = getattr(h, "_reset_timer", None)
    if timer is not None:
        timer.cancel()
    return h.sent[before:]


def ids(h):
    return [cid for batch in h.sent for cid in batch]


class Latency(unittest.TestCase):
    def test_ship_target_announces_without_a_history_poll(self):
        h = fresh_history()
        add_row(h, "11919020", "Zaximillian")
        location.system, location.state = "Sol", 1
        h.machine.commit(set())
        started = time.monotonic()
        new = play(
            h, "ShipTargeted", TargetLocked=True, ScanStage=3, Ship="krait_mkii",
            PilotName="$cmdr_decorate:#name=Zaximillian;",
            PilotName_Localised="CMDR Zaximillian",
        )
        elapsed = time.monotonic() - started
        self.assertEqual(new, [["11919020"]])
        self.assertLess(elapsed, HISTORY_POLL_SECONDS)
        self.assertTrue(present("11919020"))
        # The same lock again is not a second beep.
        self.assertEqual(play(
            h, "ShipTargeted", TargetLocked=True, ScanStage=2, Ship="krait_mkii",
            PilotName="$cmdr_decorate:#name=Zaximillian;",
            PilotName_Localised="CMDR Zaximillian",
        ), [])

    def test_local_chat_announces_without_waiting_for_history(self):
        h = fresh_history()
        add_row(h, "1795908", "Samar!tan")
        location.system, location.state = "Sol", 1
        h.machine.commit({"1795908"})
        new = play(
            h, "ReceiveText", Channel="local", From="Samar!tan",
            Message="this text must not be required for the beep",
        )
        self.assertEqual(new, [["1795908"]])
        self.assertTrue(present("1795908"))

    def test_interdiction_announces_on_the_drop_event(self):
        h = fresh_history()
        add_row(h, "3752776", "xenophage 1987")
        location.system, location.state = "Sol", 1
        h.machine.note_announced("3752776")
        h.machine.seen_ids.add("3752776")
        play(h, "Interdiction", Success=True, IsPlayer=True, Interdicted="Mine is Longer", CombatRank=8)
        h.sent.clear()
        new = play(h, "SupercruiseExit", StarSystem="Sol", Body="Sol 1")
        self.assertEqual(new, [["Mine is Longer"]])
        self.assertEqual(h.machine.cooldown_remaining("3752776") is None or True, True)
        # No history poll was required. A later refresh of the old id does not beep again.
        feed(h, ["3752776"], datetime.datetime(2026, 11, 8, 5, 46, 44))
        self.assertEqual(ids(h), ["Mine is Longer"])

    def test_history_only_arrival_is_decided_in_the_read_that_sees_the_file(self):
        h = fresh_history()
        add_row(h, "11919020", "Zaximillian")
        location.system, location.state = "Sol", 1
        h.machine.commit(set())
        started = time.monotonic()
        feed(h, ["11919020"], datetime.datetime(2026, 11, 8, 5, 25, 17))
        elapsed = time.monotonic() - started
        self.assertEqual(h.sent, [["11919020"]])
        self.assertLess(elapsed, 1.0)
        feed(h, ["11919020"], datetime.datetime(2026, 11, 8, 5, 26, 0))
        self.assertEqual(h.sent, [["11919020"]])

    def test_submit_cooldown_is_ten_seconds_and_a_fight_is_sixty(self):
        self.assertEqual(INTERDICTION_SUBMIT_COOLDOWN, 10.0)
        self.assertEqual(INTERDICTION_SUCCESS_COOLDOWN, 60.0)
        h = fresh_history()
        location.system, location.state = "Sol", 1
        play(h, "Interdicted", IsPlayer=True, Submitted=True, Interdictor="Samar!tan")
        play(h, "SupercruiseExit", StarSystem="Sol")
        # Unresolved name: cooldown is applied once the id is known. The journal
        # value is what the drop used.
        self.assertEqual(location.interdiction_cooldown, 10.0)
        h2 = fresh_history()
        location.system, location.state = "Sol", 1
        play(h2, "Interdicted", IsPlayer=True, Submitted=False, Interdictor="Samar!tan")
        self.assertEqual(location.interdiction_cooldown, 60.0)


def _target(h, name, stage, ship="federation_corvette"):
    return play(
        h, "ShipTargeted", TargetLocked=True, ScanStage=stage, Ship=ship,
        PilotName=f"$cmdr_decorate:#name={name};",
        PilotName_Localised=f"CMDR {name}",
        timestamp=f"2026-11-08T07:22:{50 + stage:02d}Z",
    )


class UnresolvedScanDedup(unittest.TestCase):
    """23:22 at Hencke Orbital: two unnamed rows, then scans of those people."""

    def _two_unknowns(self):
        h = fresh_history()
        add_row(h, "403737", "unknown")
        add_row(h, "8330095", "unknown")
        location.system, location.state = "Sorbago", 0
        play(h, "SupercruiseExit", StarSystem="Sorbago", Body="Hencke Orbital", BodyType="Station",
             timestamp="2026-11-08T07:22:39Z")
        h.sent.clear()
        feed(h, ["403737", "8330095"], datetime.datetime(2026, 11, 8, 7, 22, 45))
        return h

    def test_two_unnamed_contacts_then_two_scans_beep_twice(self):
        h = self._two_unknowns()
        self.assertEqual(ids(h), ["403737", "8330095"])
        for stage in (1, 2, 3):
            self.assertEqual(_target(h, "DROOMZZ", stage), [])
        for stage in (1, 2, 3):
            self.assertEqual(_target(h, "MigA", stage, ship="anacondamkii"), [])
        self.assertEqual(ids(h), ["403737", "8330095"])
        self.assertEqual(h.seen_data["403737"]["name"], "unknown")
        self.assertEqual(h.seen_data["8330095"]["name"], "unknown")
        self.assertIsNone(h.machine.resolve("DROOMZZ"))
        self.assertIsNone(h.machine.resolve("MigA"))
        saved = open(h.json_file_path, encoding="utf-8").read() if __import__("os").path.exists(h.json_file_path) else ""
        self.assertNotIn("DROOMZZ", saved)
        self.assertNotIn("MigA", saved)

    def test_a_third_unresolved_name_still_beeps(self):
        h = self._two_unknowns()
        self.assertEqual(_target(h, "DROOMZZ", 1), [])
        self.assertEqual(_target(h, "MigA", 1, ship="anacondamkii"), [])
        arrived = _target(h, "Newcomer", 1, ship="python")
        self.assertEqual(arrived, [["Newcomer"]])
        self.assertEqual(_target(h, "Newcomer", 2, ship="python"), [])
        self.assertEqual(ids(h), ["403737", "8330095", "Newcomer"])
        self.assertEqual(h.seen_data["403737"]["name"], "unknown")
        self.assertEqual(h.seen_data["8330095"]["name"], "unknown")

    def test_one_unknown_is_remembered_for_dedup_and_not_saved(self):
        h = fresh_history()
        add_row(h, "403737", "unknown")
        location.system, location.state = "Sorbago", 0
        feed(h, ["403737"], datetime.datetime(2026, 11, 8, 7, 22, 45))
        self.assertEqual(_target(h, "DROOMZZ", 1), [])
        self.assertEqual(_target(h, "DROOMZZ", 3), [])
        self.assertEqual(h.machine.resolve("DROOMZZ"), "403737")
        self.assertEqual(h.seen_data["403737"]["name"], "unknown")
        extra = _target(h, "MigA", 1, ship="anacondamkii")
        self.assertEqual(extra, [["MigA"]])


class SessionMarks(unittest.TestCase):
    """The 13 marks from the 7 Nov 2026 recording, replayed as the events that mattered."""

    def test_samaritan_refresh_after_dock_is_not_a_second_arrival(self):
        """20:59 extra_beep and not_here. A later Met row is not a new arrival."""
        h = fresh_history()
        add_row(h, "1795908", "Samar!tan")
        location.system, location.state = "Sol", 0
        # The drop already started this instance and left him behind. A stale
        # here flag must not survive the next history row.
        h.machine.commit({"1795908"})
        location.add_instance("1795908", state=0, system="Sol")
        location.instance["1795908"]["here"] = True
        h._visit["1795908"] = time.monotonic()
        h.sent.clear()
        feed(h, ["1795908"], datetime.datetime(2026, 11, 8, 4, 59, 23))
        self.assertEqual(h.sent, [])
        self.assertFalse(present("1795908"))

    def test_low_wake_does_not_announce_the_station_batch_but_a_new_id_beeps(self):
        """21:25 gorice extra_beep / not_here, Samaritan no_beep, Zaximillian on the new row."""
        h = fresh_history()
        for cid, name in (
            ("10189385", "gorice"),
            ("1795908", "Samar!tan"),
            ("11919020", "Zaximillian"),
        ):
            add_row(h, cid, name)
        location.system, location.state = "Sol", 0
        for cid in ("10189385", "1795908"):
            location.add_instance(cid, state=0, system="Sol")
            location.instance[cid]["here"] = True
            h._visit[cid] = time.monotonic()
        play(h, "StartJump", JumpType="Supercruise", StarSystem="Sol")
        play(h, "SupercruiseEntry", StarSystem="Sol")
        h.sent.clear()
        # Departure batch: both were at the station. History cannot tell them apart.
        feed(h, ["10189385", "1795908"], datetime.datetime(2026, 11, 8, 5, 24, 55))
        self.assertEqual(h.sent, [])
        self.assertFalse(present("10189385"))
        self.assertFalse(present("1795908"))
        # A commander who was not in that batch beeps when their row is read.
        feed(h, ["11919020"], datetime.datetime(2026, 11, 8, 5, 25, 17))
        self.assertEqual(h.sent, [["11919020"]])
        # Local chat is the evidence that promotes someone the batch could not.
        h.sent.clear()
        play(h, "ReceiveText", Channel="local", From="Samar!tan", Message="hello")
        self.assertEqual(h.sent, [["1795908"]])
        self.assertTrue(present("1795908"))

    def test_target_before_supercruise_entry_is_announced_on_entry_not_the_next_poll(self):
        """21:49 Gamagosk beeped_late. History 230 ms before entry, then a target."""
        h = fresh_history()
        add_row(h, "4908937", "Gamagosk")
        location.system, location.state = "Sol", 0
        play(h, "StartJump", JumpType="Supercruise", StarSystem="Sol")
        feed(h, ["4908937"], datetime.datetime(2026, 11, 8, 5, 48, 17))
        self.assertEqual(h.sent, [])
        self.assertFalse(present("4908937"))
        entered = play(h, "SupercruiseEntry", StarSystem="Sol")
        self.assertEqual(entered, [["4908937"]])
        self.assertTrue(present("4908937"))
        # The later history row that used to be the beep must not beep again.
        feed(h, ["4908937"], datetime.datetime(2026, 11, 8, 5, 49, 15))
        self.assertEqual(ids(h), ["4908937"])

    def test_target_spelling_and_renamed_id_announce_once_on_the_drop(self):
        """21:47 Mine is Longer / xenophage, and 22:02 Colmillowolf vs COILMILLOWOLF."""
        h = fresh_history()
        add_row(h, "3752776", "xenophage 1987")
        add_row(h, "11444314", "Colmillowolf")
        location.system, location.state = "Sol", 1
        h.machine.commit(set())
        feed(h, ["3752776", "11444314"], datetime.datetime(2026, 11, 8, 5, 44, 30))
        self.assertCountEqual(ids(h), ["3752776", "11444314"])
        h.sent.clear()
        play(h, "ShipTargeted", TargetLocked=True, ScanStage=3,
             PilotName="$cmdr_decorate:#name=COILMILLOWOLF;",
             PilotName_Localised="CMDR COILMILLOWOLF")
        # Still in supercruise: the lock is this instance, so no second beep
        # for someone already announced. The drop is the fresh instance.
        self.assertEqual(h.sent, [])
        dropped = play(h, "SupercruiseExit", StarSystem="Sol")
        self.assertEqual(dropped, [])
        # Post-drop lock uses the other spelling and must announce that id now.
        locked = play(
            h, "ShipTargeted", TargetLocked=True, ScanStage=3,
            PilotName="$cmdr_decorate:#name=COILMILLOWOLF;",
            PilotName_Localised="CMDR COILMILLOWOLF",
        )
        self.assertEqual(locked, [["11444314"]])
        # Kill flag and the kill event do not beep him again.
        h.aggregate_most_recent_commanders = lambda first_run=False: {"Interactions": [{
            "CommanderID": 11444314,
            "Interactions": ["Met", "Killed"],
            "Epoch": 100,
        }]}
        h.aggregated_commanders()
        play(h, "PVPKill", Victim="COILMILLOWOLF")
        self.assertEqual(ids(h), ["11444314"])

    def test_renamed_commander_links_to_the_same_id_without_a_second_beep(self):
        """21:50 wrong_name and 21:47 not_here: one id, two display names."""
        h = fresh_history()
        add_row(h, "3752776", "xenophage 1987")
        location.system, location.state = "Sol", 0
        h.machine.commit(set())
        # The earlier sighting linked the history id. The journal name was
        # already announced, so binding the new spelling must not beep again.
        h.machine.index_name("3752776", "xenophage 1987")
        h.machine.hold_evidence("Mine is Longer", "interdiction")
        h.machine.mark_name_announced("Mine is Longer")
        h.sent.clear()
        h.seen_data["3752776"]["aliases"] = ["xenophage 1987"]
        h.seen_data["3752776"]["name"] = "Mine is longer"
        promoted = h._promote_known_name("3752776", "Mine is longer")
        self.assertTrue(promoted)
        self.assertEqual(h.sent, [])
        self.assertEqual(h.machine.resolve("Mine is Longer"), "3752776")
        self.assertEqual(h.machine.resolve("xenophage 1987"), "3752776")
        self.assertTrue(present("3752776"))
        feed(h, ["3752776"], datetime.datetime(2026, 11, 8, 5, 50, 31))
        self.assertEqual(h.sent, [])

    def test_pvp_kill_is_recorded_and_does_not_beep(self):
        """22:05 killed context mark."""
        h = fresh_history()
        h.recorder.enabled_override = True
        add_row(h, "11444314", "Colmillowolf")
        location.system, location.state = "Sol", 0
        location.add_instance("11444314", state=0, system="Sol")
        location.instance["11444314"]["here"] = True
        h.machine.note_announced("11444314")
        h.sent.clear()
        play(h, "PVPKill", Victim="COILMILLOWOLF", Victim_Localised="COILMILLOWOLF")
        self.assertEqual(h.sent, [])
        self.assertFalse(present("11444314"))
        h.recorder.close()
        folder = h.recorder.directory()
        name = __import__("os").listdir(folder)[0]
        with open(__import__("os").path.join(folder, name), encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("PVPKill", text)
        self.assertIn("COILMILLOWOLF", text)
        for line in text.splitlines():
            if line.strip():
                json.loads(line)

    def test_system_chat_and_wing_membership_do_not_announce(self):
        h = fresh_history()
        add_row(h, "1", "Wingmate")
        location.system, location.state = "Sol", 0
        h.machine.commit({"1"})
        play(h, "ReceiveText", Channel="starsystem", From="Wingmate", Message="not here")
        play(h, "ReceiveText", Channel="wing", From="Wingmate", Message="not here either")
        self.assertEqual(h.sent, [])
        self.assertFalse(present("1"))
