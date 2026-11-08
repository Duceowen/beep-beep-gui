"""The Seen Commanders window must refill after an import is confirmed.

Sorting by Name used to raise KeyError while the new tree was empty, so the
window kept only its column headers until EDMC was restarted. The saved file
was already correct.

Run from the repository root:  python -m unittest discover -s tests
"""
import os
import shutil
import sys
import tempfile
import threading
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


if "config" not in sys.modules:
    _stub("config", appname="EDMarketConnector")
if "logutil" not in sys.modules:
    _stub("logutil", log=_Log())
if "beep_beep_config" not in sys.modules:
    _stub("beep_beep_config", config=_Config())

try:
    import tkinter as tk
except ImportError:
    tk = None

if tk is not None:
    import beep_beep_config
    import commander_history as ch
    import gui as gui_mod
    from gui import SeenCommandersGUI
    from commander_history import history_inst


class _Defaults:
    """Return the caller's default. Some test modules stub get_config to None."""

    def get_config(self, key, default=None):
        return default

    def set_config(self, key, value):
        pass

    def save_config(self):
        pass


def _row(cid, name, **extra):
    row = {
        "commander_id": cid,
        "name": name,
        "sound": "neutral",
        "last_seen": "2026-11-01T00:00:00",
        "seen_count": 1,
        "kills": 0,
    }
    row.update(extra)
    return row


def _find_button(widget, text):
    try:
        children = widget.winfo_children()
    except tk.TclError:
        return None
    for child in children:
        try:
            if child.winfo_class() == "Button" and child.cget("text") == text:
                return child
        except tk.TclError:
            pass
        found = _find_button(child, text)
        if found is not None:
            return found
    return None


@unittest.skipUnless(tk is not None, "tkinter is not installed")
class ImportListRefresh(unittest.TestCase):
    def setUp(self):
        defaults = _Defaults()
        gui_mod.config = defaults
        ch.config = defaults
        beep_beep_config.config = defaults
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.mkdtemp()
        self._plugin = history_inst.plugin_dir
        self._json = history_inst.json_file_path
        self._seen = dict(history_inst.seen_data)
        history_inst.plugin_dir = self.tmp
        history_inst.json_file_path = os.path.join(self.tmp, "seen_commanders.json")
        history_inst.seen_data.clear()
        history_inst.seen_data["1"] = _row("1", "Alpha")
        history_inst.seen_data["2"] = _row("2", "Bravo")
        self.gui = SeenCommandersGUI()
        self.gui.open(self.root)
        self.gui.sort_field = "name"
        self.gui.sort_asc = True

    def tearDown(self):
        job = getattr(self.gui, "_header_layout_job", None)
        if job:
            try:
                self.gui.window.after_cancel(job)
            except tk.TclError:
                pass
        try:
            self.gui.window.destroy()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        history_inst.plugin_dir = self._plugin
        history_inst.json_file_path = self._json
        history_inst.seen_data.clear()
        history_inst.seen_data.update(self._seen)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def names(self):
        return [self.gui.tree.set(item, "name") for item in self.gui.tree.get_children("")]

    def test_confirm_refills_when_sorted_by_name_or_sound(self):
        for extra in range(3, 40):
            history_inst.seen_data[str(extra)] = _row(str(extra), "New %d" % extra, seen_count=0)
        history_inst.seen_data["40"] = _row("40", "OddSound", sound={"not": "a string"}, seen_count=0)
        self.gui.reload_seen_list()
        shown = self.names()
        self.assertIn("Alpha", shown)
        self.assertIn("Bravo", shown)
        self.assertIn("New 10", shown)
        self.assertIn("OddSound", shown)
        self.assertEqual(len(shown), len(history_inst.seen_data))

        self.gui.sort_field = "sound"
        self.gui.reload_seen_list()
        self.assertEqual(len(self.names()), len(history_inst.seen_data))
        self.assertIn("Alpha", self.names())

    def test_large_import_refills_every_row(self):
        for extra in range(3, 803):
            history_inst.seen_data[str(extra)] = _row(str(extra), "Cmdr %d" % extra, seen_count=0)
        self.gui.reload_seen_list()
        shown = self.names()
        self.assertEqual(len(shown), 802)
        self.assertIn("Alpha", shown)
        self.assertIn("Cmdr 800", shown)

    def test_cancel_leaves_the_original_list(self):
        before = self.names()
        plan = {
            "added": [{"id": "9", "name": "Imported"}],
            "filled": [],
            "kept": 0,
            "skipped": 0,
            "invalid": 0,
            "total": 1,
        }
        self.gui._confirm_import_plan(self.gui.window, plan)
        cancel = _find_button(self.gui.window, "Cancel")
        self.assertIsNotNone(cancel)
        cancel.invoke()
        self.assertEqual(self.names(), before)
        self.assertNotIn("9", history_inst.seen_data)
        self.assertFalse(os.path.isdir(history_inst.backup_dir))

    def test_import_button_shows_merged_names(self):
        plan = {
            "added": [
                {"id": "9", "name": "Imported"},
                {"id": "10", "name": "Second"},
            ],
            "filled": [],
            "kept": 0,
            "skipped": 0,
            "invalid": 0,
            "total": 2,
        }
        self.gui._confirm_import_plan(self.gui.window, plan)
        button = _find_button(self.gui.window, "Import")
        self.assertIsNotNone(button)
        button.invoke()
        shown = self.names()
        self.assertIn("Alpha", shown)
        self.assertIn("Bravo", shown)
        self.assertIn("Imported", shown)
        self.assertIn("Second", shown)
        self.assertEqual(history_inst.seen_data["9"]["name"], "Imported")
        self.assertEqual(len(shown), 4)

    def test_worker_update_during_reload_does_not_clear_the_list(self):
        for extra in range(3, 30):
            history_inst.seen_data[str(extra)] = _row(str(extra), "New %d" % extra)
        errors = []

        def once():
            try:
                self.gui.add_or_update_commander([{
                    "commander_id": "1",
                    "name": "Alpha",
                    "sound": "neutral",
                    "last_seen": "2026-11-01T00:00:00",
                    "seen_count": 2,
                }])
            except Exception as exc:
                errors.append(exc)

        thread = threading.Thread(target=once)
        thread.start()
        thread.join(timeout=2)
        self.gui.reload_seen_list()
        once_during = threading.Thread(target=once)
        once_during.start()
        self.gui.reload_seen_list()
        once_during.join(timeout=2)
        self.gui._flush_gui_pending()
        self.assertEqual(errors, [])
        shown = self.names()
        self.assertEqual(len(shown), len(history_inst.seen_data))
        self.assertIn("Alpha", shown)
        self.assertIn("New 10", shown)
        self.assertEqual(threading.current_thread(), threading.main_thread())


@unittest.skipUnless(tk is not None, "tkinter is not installed")
class SelectionHighlight(unittest.TestCase):
    """A click's green bar is the tree selection. It should not sit there."""

    def setUp(self):
        defaults = _Defaults()
        gui_mod.config = defaults
        ch.config = defaults
        beep_beep_config.config = defaults
        self.root = tk.Tk()
        self.root.withdraw()
        self.tmp = tempfile.mkdtemp()
        self._plugin = history_inst.plugin_dir
        self._json = history_inst.json_file_path
        self._seen = dict(history_inst.seen_data)
        history_inst.plugin_dir = self.tmp
        history_inst.json_file_path = os.path.join(self.tmp, "seen_commanders.json")
        history_inst.seen_data.clear()
        history_inst.seen_data["1"] = _row("1", "Klemyr")
        history_inst.seen_data["2"] = _row("2", "Droomzz")
        self.gui = SeenCommandersGUI()
        self.gui.open(self.root)
        self.root.update_idletasks()

    def tearDown(self):
        self.gui._cancel_selection_clear()
        try:
            self.gui.window.destroy()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass
        history_inst.plugin_dir = self._plugin
        history_inst.json_file_path = self._json
        history_inst.seen_data.clear()
        history_inst.seen_data.update(self._seen)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_selection_bar_is_not_the_list_accent(self):
        bar, text = gui_mod.selection_colors("#101010", "#13ec39")
        self.assertEqual(text, "#13ec39")
        self.assertNotEqual(bar.lower(), "#13ec39")
        self.assertNotEqual(bar.lower(), "#101010")
        self.assertEqual(gui_mod.SELECTION_CLEAR_MS, 5000)

    def test_click_selection_clears_and_resets_and_holds_for_the_editor(self):
        item = self.gui.tree_items["1"]
        self.gui.tree.selection_set(item)
        self.root.update()
        self.assertTrue(self.gui.tree.selection())
        first = self.gui._selection_clear_job
        self.assertIsNotNone(first)
        self.gui.tree.selection_set(self.gui.tree_items["2"])
        self.root.update()
        second = self.gui._selection_clear_job
        self.assertIsNotNone(second)
        self.assertNotEqual(first, second)
        self.gui._clear_tree_selection()
        self.assertEqual(self.gui.tree.selection(), ())

        self.gui.tree.selection_set(item)
        self.root.update_idletasks()
        editor = tk.Toplevel(self.gui.window)
        self.gui._edit_popup = editor
        self.gui._clear_tree_selection()
        self.assertEqual(self.gui.tree.selection(), (item,))
        editor.destroy()
        self.gui._edit_popup = None
        self.gui._clear_tree_selection()
        self.assertEqual(self.gui.tree.selection(), ())

        bindings = self.gui.tree.bind()
        self.assertIn("<Double-Button-1>", bindings)
        self.assertIn("<Button-3>", bindings)

    def test_shift_held_does_not_clear(self):
        item = self.gui.tree_items["1"]
        self.gui.tree.selection_set(item)
        self.root.update()
        self.gui._selection_keys = {"Shift_L"}
        self.gui._clear_tree_selection()
        self.assertEqual(self.gui.tree.selection(), (item,))
        self.gui._selection_keys.clear()
        self.gui._clear_tree_selection()
        self.assertEqual(self.gui.tree.selection(), ())


if __name__ == "__main__":
    unittest.main()
