import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from commander_names import plan_spelling_updates, confusable_key


def rec(**names):
    return {i: {"name": n} for i, n in names.items()}


class ConfusableSpelling(unittest.TestCase):
    def test_key(self):
        self.assertEqual(confusable_key("B0B"), confusable_key("bob"))

    def test_o_to_zero_from_scan(self):
        self.assertEqual(plan_spelling_updates(rec(a="Bob"), [("scan", "B0b")]), [("a", "Bob", "B0b")])

    def test_zero_to_o_from_wing(self):
        self.assertEqual(plan_spelling_updates(rec(a="R0ck"), [("wing", "Rock")]), [("a", "R0ck", "Rock")])

    def test_chat_alone_does_not_swap(self):
        self.assertEqual(plan_spelling_updates(rec(a="Bob"), [("chat", "B0b")]), [])

    def test_exact_game_spelling_blocks_swap(self):
        self.assertEqual(plan_spelling_updates(rec(a="Bob"), [("scan", "Bob"), ("scan", "B0b")]), [])

    def test_two_saved_lookalikes_left_alone(self):
        self.assertEqual(plan_spelling_updates(rec(a="Bob", b="B0b"), [("scan", "BOB")]), [])

    def test_two_lookalike_candidates_ambiguous(self):
        self.assertEqual(plan_spelling_updates(rec(a="Boob"), [("scan", "B0ob"), ("scan", "Bo0b")]), [])

    def test_case_update_still_works(self):
        self.assertEqual(plan_spelling_updates(rec(a="bob smith"), [("scan", "Bob Smith")]), [("a", "bob smith", "Bob Smith")])


class ApplyConfusable(unittest.TestCase):
    def test_apply_keeps_alias(self):
        import types
        for name, attrs in (("config", {"appname": "EDMarketConnector"}),
                            ("logutil", {"log": types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None, warning=lambda *a, **k: None, exception=lambda *a, **k: None, error=lambda *a, **k: None)}),
                            ("beep_beep_config", {"config": types.SimpleNamespace(get_config=lambda *a, **k: None, set_config=lambda *a, **k: None, save_config=lambda: None)})):
            if name not in sys.modules:
                mod = types.ModuleType(name); mod.__dict__.update(attrs); sys.modules[name] = mod
        try:
            import commander_history as ch
        except Exception as exc:
            self.skipTest(f"harness: {exc}")
        mgr = ch.CommanderHistoryManager.__new__(ch.CommanderHistoryManager)
        mgr.seen_data = {"a": {"name": "Bob"}}
        mgr.save_seen_commanders = lambda: None
        mgr._gui_listener = None
        self.assertEqual(mgr.apply_spelling_updates([("a", "Bob", "B0b")]), [("a", "Bob", "B0b")])
        self.assertEqual(mgr.seen_data["a"]["name"], "B0b")
        self.assertIn("Bob", mgr.seen_data["a"]["aliases"])
        self.assertEqual(mgr.apply_spelling_updates([("a", "B0b", "Bill")]), [])


if __name__ == "__main__":
    unittest.main()
