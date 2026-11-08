"""False new-arrival beeps from 7 Nov 2026 (times in the journals are UTC).

Samar!tan pulled the commander out of supercruise and was already targeted.
Sabbath91 was targeted in supercruise, then listed again with wingmates after
each drop, and was not in the new instance. SIXART. was targeted in the
instance right after a short supercruise hop, then beeped when history caught up.

A commander who was not already with you must still beep after the drop.
"""
import datetime
import time
import unittest

from test_scenarios import add_row, epoch, feed, fresh_history, load_mod, location, present


def play(h, event, **fields):
    load_mod.history_inst = h
    entry = {"timestamp": fields.pop("timestamp", "2026-11-07T12:00:00Z"), "event": event}
    entry.update(fields)
    system = entry.get("StarSystem") or location.system or "Sol"
    load_mod.journal_entry("Tester", False, system, "", entry, {})
    timer = getattr(h, "_reset_timer", None)
    if timer is not None:
        timer.cancel()


def beeped(h):
    return [cid for batch in h.sent for cid in batch]


def feed_rows(h, rows, when):
    """One CommanderHistory update. rows are (commander id, interaction flags)."""
    base = epoch(when)
    entries = [
        {"CommanderID": int(cid), "Interactions": flags, "Epoch": base + i}
        for i, (cid, flags) in enumerate(rows)
    ]
    h.aggregate_most_recent_commanders = lambda first_run=False: {"Interactions": entries}
    h.aggregated_commanders()


def here(cid, system):
    location.add_instance(cid, state=0, system=system)
    location.instance[cid]["here"] = True


class FalseNewArrivals(unittest.TestCase):
    def test_samaritan_incoming_interdiction_is_not_a_later_beep(self):
        """11:57 PM PST: Interdicted, drop, two target locks, beep at 12:00:54 AM."""
        h = fresh_history()
        add_row(h, "1795908", "Samar!tan")
        location.system, location.state = "Sorbago", 0
        here("1795908", "Sorbago")
        h._visit["1795908"] = time.monotonic()

        play(h, "StartJump", JumpType="Supercruise", StarSystem="Sorbago",
             timestamp="2026-11-07T07:52:15Z")
        feed(h, ["1795908"], datetime.datetime(2026, 11, 7, 7, 52, 22))
        self.assertEqual(h.sent, [])
        self.assertFalse(location.instance["1795908"].get("arrived_in_supercruise"))

        play(h, "SupercruiseEntry", StarSystem="Sorbago", timestamp="2026-11-07T07:52:20Z")
        self.assertIn("1795908", location.leave_snapshot)
        self.assertNotIn("1795908", location.supercruise_arrivals)

        play(h, "Interdicted", Submitted=False, IsPlayer=True, Interdictor="Samar!tan",
             CombatRank=8, timestamp="2026-11-07T07:57:04Z")
        play(h, "SupercruiseExit", StarSystem="Sorbago", timestamp="2026-11-07T07:57:05Z")
        self.assertTrue(present("1795908"))

        h.sent.clear()
        play(h, "ShipTargeted", TargetLocked=True, ScanStage=3,
             PilotName="$cmdr_decorate:#name=Samar!tan;",
             PilotName_Localised="CMDR Samar!tan",
             timestamp="2026-11-07T07:57:06Z")
        play(h, "ShipTargeted", TargetLocked=True, ScanStage=3,
             PilotName="$cmdr_decorate:#name=Samar!tan;",
             PilotName_Localised="CMDR Samar!tan",
             timestamp="2026-11-07T07:59:23Z")
        feed(h, ["1795908"], datetime.datetime(2026, 11, 7, 7, 57, 11))
        feed(h, ["1795908"], datetime.datetime(2026, 11, 7, 8, 0, 54))
        self.assertEqual(h.sent, [])
        self.assertTrue(present("1795908"))

    def test_sabbath91_listed_with_wing_after_drop_does_not_beep(self):
        """2:30 PM PST: targeted in supercruise, drop, history lists him and three wingmates."""
        h = fresh_history()
        system = "Shinrarta Dezhra"
        people = {
            "1186115": "Sabbath91",
            "10562473": "Nomis14",
            "11779612": "Chockymoster",
            "9396046": "Chalia Sylthana",
        }
        location.system, location.state = system, 0
        location.set_wing(True)
        for cid, name in people.items():
            add_row(h, cid, name)
            here(cid, system)
            h._visit[cid] = time.monotonic()

        play(h, "SupercruiseEntry", StarSystem=system, timestamp="2026-11-07T22:30:31Z")
        for cid in people:
            self.assertIn(cid, location.leave_snapshot)
        feed(h, list(people), datetime.datetime(2026, 11, 7, 22, 30, 33))
        self.assertEqual(h.sent, [])

        play(h, "ShipTargeted", TargetLocked=True, ScanStage=3,
             PilotName="$cmdr_decorate:#name=Sabbath91;",
             PilotName_Localised="CMDR Sabbath91",
             timestamp="2026-11-07T22:30:54Z")
        self.assertFalse(present("1186115"))

        play(h, "SupercruiseExit", StarSystem=system, timestamp="2026-11-07T22:31:52Z")
        h.sent.clear()
        add_row(h, "282133", "Dangerous.com")
        feed_rows(h, [
            ("11779612", ["Met", "WingMember"]),
            ("1186115", ["Met"]),
            ("9396046", ["Met", "WingMember"]),
            ("10562473", ["Met", "WingMember"]),
            ("282133", ["Met"]),
        ], datetime.datetime(2026, 11, 7, 22, 31, 59))
        self.assertNotIn("1186115", beeped(h))
        for cid in ("10562473", "11779612", "9396046"):
            self.assertNotIn(cid, beeped(h))
        self.assertEqual(beeped(h), ["282133"])
        self.assertFalse(present("1186115"))
        self.assertTrue(present("282133"))

    def test_sabbath91_second_drop_does_not_beep(self):
        """2:32 PM PST: supercruise again, drop six seconds later, another foe beep."""
        h = fresh_history()
        system = "Shinrarta Dezhra"
        location.system, location.state = system, 0
        location.set_wing(True)
        add_row(h, "1186115", "Sabbath91")
        here("1186115", system)
        h._visit["1186115"] = time.monotonic()

        play(h, "SupercruiseEntry", StarSystem=system, timestamp="2026-11-07T22:30:31Z")
        play(h, "ShipTargeted", TargetLocked=True, ScanStage=3,
             PilotName="$cmdr_decorate:#name=Sabbath91;",
             PilotName_Localised="CMDR Sabbath91",
             timestamp="2026-11-07T22:30:54Z")
        play(h, "SupercruiseExit", StarSystem=system, timestamp="2026-11-07T22:31:52Z")
        feed(h, ["1186115"], datetime.datetime(2026, 11, 7, 22, 31, 59))
        self.assertEqual(h.sent, [])
        self.assertIn("1186115", location.leave_snapshot)

        play(h, "SupercruiseEntry", StarSystem=system, timestamp="2026-11-07T22:32:38Z")
        play(h, "SupercruiseExit", StarSystem=system, timestamp="2026-11-07T22:32:44Z")
        h.sent.clear()
        feed(h, ["1186115"], datetime.datetime(2026, 11, 7, 22, 32, 51))
        self.assertEqual(h.sent, [])
        self.assertFalse(present("1186115"))

    def test_sixart_targeted_after_drop_is_not_a_later_beep(self):
        """2:50 PM PST: drop, target locks at :36 and :53, beep at 2:52:06."""
        h = fresh_history()
        system = "Shinrarta Dezhra"
        location.system, location.state = system, 0
        add_row(h, "8541963", "SIXART.")
        here("8541963", system)
        h._visit["8541963"] = time.monotonic()

        play(h, "StartJump", JumpType="Supercruise", StarSystem=system,
             timestamp="2026-11-07T22:50:22Z")
        feed(h, ["8541963"], datetime.datetime(2026, 11, 7, 22, 50, 28))
        self.assertEqual(h.sent, [])
        self.assertFalse(location.instance["8541963"].get("arrived_in_supercruise"))

        play(h, "SupercruiseEntry", StarSystem=system, timestamp="2026-11-07T22:50:27Z")
        self.assertIn("8541963", location.leave_snapshot)
        self.assertNotIn("8541963", location.supercruise_arrivals)
        play(h, "SupercruiseExit", StarSystem=system, timestamp="2026-11-07T22:50:32Z")

        h.sent.clear()
        for stamp in ("2026-11-07T22:50:36Z", "2026-11-07T22:50:53Z", "2026-11-07T22:50:55Z"):
            play(h, "ShipTargeted", TargetLocked=True, ScanStage=3,
                 PilotName="$cmdr_decorate:#name=SIXART.;",
                 PilotName_Localised="CMDR SIXART.",
                 timestamp=stamp)
        self.assertTrue(present("8541963"))
        feed(h, ["8541963"], datetime.datetime(2026, 11, 7, 22, 52, 6))
        self.assertEqual(h.sent, [])
        self.assertTrue(present("8541963"))

    def test_genuine_arrival_after_drop_still_beeps(self):
        h = fresh_history()
        system = "Shinrarta Dezhra"
        location.system, location.state = system, 0
        add_row(h, "1", "LeftBehind")
        add_row(h, "2", "JustArrived")
        here("1", system)
        h._visit["1"] = time.monotonic()

        play(h, "SupercruiseEntry", StarSystem=system, timestamp="2026-11-07T22:32:38Z")
        play(h, "SupercruiseExit", StarSystem=system, timestamp="2026-11-07T22:32:44Z")
        feed(h, ["1"], datetime.datetime(2026, 11, 7, 22, 32, 51))
        self.assertEqual(h.sent, [])
        self.assertFalse(present("1"))

        feed(h, ["2"], datetime.datetime(2026, 11, 7, 22, 45, 37))
        self.assertEqual(h.sent, [["2"]])
        self.assertTrue(present("2"))
