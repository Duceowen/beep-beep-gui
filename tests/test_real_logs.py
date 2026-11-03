"""Scenarios built from real log data (EDMarketConnector debug log and the journal, 2 Nov 2026).

Times are the journal and plugin log times, UTC. Run from the repository root:
    python -m unittest discover -s tests
"""
import datetime
import time
import unittest

from test_scenarios import (
    add_row, feed, fresh_history, left_at, location, ch, present, suppressed_until,
)


class KillTimeline(unittest.TestCase):
    """Real: Plague lll was killed at 23:51:03 (journal PVPKill) and was re-added at 23:51:45
    (plugin: 'Presence added: plague lll' with overlay), 42 s later."""

    def setUp(self):
        self.h = fresh_history()
        self.sent = self.h.sent
        add_row(self.h, "501", "plague lll")
        add_row(self.h, "502", "Friend")
        location.system, location.state = "Shinrarta Dezhra", 0
        for cid in ("501", "502"):
            location.add_instance(cid, state=0, system="Shinrarta Dezhra")
            location.instance[cid]["here"] = True
        self.h._presence_saved = {"501", "502"}

    def test_test8_kill_leaves_the_list_at_once_with_no_leave_sound(self):
        # journal PVPKill for plague lll
        self.h.note_kill("plague lll")
        self.h._log_presence_changes()
        self.assertFalse(present("501"))
        self.assertTrue(present("502"))
        # a kill is not a departure: no departure record, so no leave sound
        self.assertNotIn("501", location.departed)
        self.h._process_departures()
        self.assertEqual(self.sent, [])

    def test_test9_respawn_inside_window_is_silent_then_announced_once(self):
        self.h.note_kill("plague lll")
        self.h._log_presence_changes()
        # 5 s after the kill: the victim's record is refreshed, but they are still dead
        now = time.time()
        suppressed = suppressed_until(self.h, "501")
        self.assertGreater(suppressed, now)
        feed(self.h, ["501"], datetime.datetime(2026, 11, 3, 23, 51, 8))
        self.assertEqual(self.sent, [])
        self.assertFalse(present("501"))
        # 42 s after the kill (real respawn gap): the window is over, so they announce once
        self.h._gone["501"]["suppress_until"] = time.time() - 1
        feed(self.h, ["501"], datetime.datetime(2026, 11, 3, 23, 51, 45))
        self.assertEqual(self.sent, [["501"]])
        # a later refresh while they are present is not announced again
        feed(self.h, ["501"], datetime.datetime(2026, 11, 3, 23, 52, 10))
        self.assertEqual(self.sent, [["501"]])


class EscapeTimeline(unittest.TestCase):
    """Real: Interdiction Success:false for Nova-Prime-598 at 23:54:41 (journal).
    The target was still on the list afterwards in the old code."""

    def test_test10_escaped_target_leaves_the_list_at_once(self):
        h = fresh_history()
        add_row(h, "601", "Nova-Prime-598")
        location.system, location.state = "Shinrarta Dezhra", 1
        location.add_instance("601", state=1, system="Shinrarta Dezhra")
        location.instance["601"]["here"] = True
        h._presence_saved = {"601"}
        h.note_escape("Nova-Prime-598")
        h._log_presence_changes()
        self.assertFalse(present("601"))
        self.assertIsNone(left_at(h, "601"))
        self.assertNotIn("601", location.departed)


class RenameTimeline(unittest.TestCase):
    """Real: Shadow Amoras (id 619221) was renamed to fadedpanties on 2 Nov at 19:07.
    The journal then shows PVPKill Victim 'fadedpanties' at 19:07:13. The kill must still count."""

    def test_test11_kill_after_rename_counts_on_the_row(self):
        h = fresh_history()
        add_row(h, "619221", "Shadow Amoras")
        info = h.seen_data["619221"]
        ch.add_alias(info, "Shadow Amoras", "fadedpanties")
        info["name"] = "fadedpanties"
        self.assertEqual(info.get("aliases"), ["Shadow Amoras"])
        # the kill names the new spelling, and the old one still works
        h.record_kill("fadedpanties")
        h.record_kill("fadedpanties")
        h._apply_kill_tally()
        self.assertEqual(h.seen_data["619221"].get("kills"), 2)

    def test_test11_kill_by_old_name_still_finds_the_renamed_commander(self):
        h = fresh_history()
        add_row(h, "619221", "fadedpanties", aliases=["Shadow Amoras"])
        location.add_instance("619221", state=0, system="Shinrarta Dezhra")
        location.instance["619221"]["here"] = True
        h.note_kill("shadow amoras")
        self.assertFalse(present("619221"))


if __name__ == "__main__":
    unittest.main()
