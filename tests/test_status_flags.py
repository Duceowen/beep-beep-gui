import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from status_flags import on_foot_from_status  # noqa: E402


class StatusOnFoot(unittest.TestCase):
    def test_in_taxi_clears_stale_on_foot(self):
        self.assertFalse(on_foot_from_status(0, (1 << 1) | (1 << 0)))

    def test_multicrew_clears_on_foot(self):
        self.assertFalse(on_foot_from_status(0, 1 << 2))

    def test_on_foot_is_true(self):
        self.assertTrue(on_foot_from_status(0, 1 << 0))

    def test_main_ship_is_not_on_foot(self):
        self.assertFalse(on_foot_from_status(1 << 24, 0))

    def test_inconclusive_keeps_journal_value(self):
        self.assertIsNone(on_foot_from_status(0, 0))
        self.assertIsNone(on_foot_from_status(None, None))
        self.assertIsNone(on_foot_from_status("bad", 0))


if __name__ == "__main__":
    unittest.main()
