import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cooldown_timer as cd  # noqa: E402


class CooldownTimer(unittest.TestCase):
    def setUp(self):
        cd.stop()

    def tearDown(self):
        cd.stop()

    def test_active_window_has_a_safety_cap(self):
        cd.start("Target", "501", seconds=60.0, now=100.0)
        self.assertTrue(cd.is_active(100.0))
        self.assertTrue(cd.is_active(160.0))
        self.assertFalse(cd.is_active(200.0))

    def test_kill_matching_the_target_stops_the_timer(self):
        cd.start("Target", "501", seconds=60.0, now=100.0)
        self.assertFalse(cd.stop_if_target("Bystander", "999"))
        self.assertTrue(cd.is_active(100.0))
        self.assertTrue(cd.stop_if_target("TARGET", None))
        self.assertFalse(cd.is_active(100.0))

    def test_kill_matching_the_resolved_id_stops_the_timer(self):
        cd.start("Target", "501", seconds=60.0, now=100.0)
        self.assertTrue(cd.stop_if_target(None, "501"))
        self.assertFalse(cd.is_active(100.0))

    def test_stop_clears_everything(self):
        cd.start("Target", "501", seconds=60.0, now=100.0)
        cd.stop()
        self.assertFalse(cd.is_active(100.0))
        self.assertFalse(cd.is_full_window())

    def test_status_edge_requires_a_fresh_event(self):
        self.assertFalse(cd.fresh_context(0.0, False, now=100.0))
        self.assertFalse(cd.fresh_context(95.0, True, now=100.0))
        self.assertFalse(cd.fresh_context(80.0, False, now=100.0))
        self.assertTrue(cd.fresh_context(99.0, False, now=100.0))

    def test_interdiction_event_seconds(self):
        self.assertEqual(cd.seconds_for_event("Interdiction", {"Success": True}), 60.0)
        self.assertEqual(cd.seconds_for_event("Interdiction", {"Success": False}), 60.0)
        self.assertEqual(cd.seconds_for_event("Interdicted", {"Submitted": True}), 10.0)
        self.assertEqual(cd.seconds_for_event("Interdicted", {"Submitted": False}), 60.0)

    def test_status_flag_edges(self):
        cd._status_active = False
        self.assertIsNone(cd.note_status_flags(0))
        self.assertEqual(cd.note_status_flags(cd.FSD_COOLDOWN_FLAG), "start")
        self.assertIsNone(cd.note_status_flags(cd.FSD_COOLDOWN_FLAG))
        self.assertEqual(cd.note_status_flags(1 << 16), "end")
        self.assertIsNone(cd.note_status_flags(0))
        cd._status_active = False

    def test_full_window_only_for_the_60_second_timer(self):
        cd.start("Target", "501", seconds=60.0, now=100.0)
        self.assertTrue(cd.is_full_window())
        cd.start("Target", "501", seconds=10.0, now=100.0)
        self.assertFalse(cd.is_full_window())
        self.assertTrue(cd.stop_if_target(None, "501"))
        self.assertFalse(cd.is_full_window())


if __name__ == "__main__":
    unittest.main()
