"""Shutdown leaves EDMC able to exit and does not rewrite a clean commander list.

Run from the repository root:  python -m unittest discover -s tests
"""
import json
import os
import sys
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


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


def _stub(name, **attrs):
    mod = types.ModuleType(name)
    mod.__dict__.update(attrs)
    sys.modules[name] = mod
    return mod


if "beep_beep_config" not in sys.modules:
    _stub("beep_beep_config", config=_Config())
if "logutil" not in sys.modules:
    _stub("logutil", log=_Log())

import lifetime as lifetime_mod  # noqa: E402
from lifetime import Lifetime  # noqa: E402
from test_scenarios import add_row, fresh_history  # noqa: E402


_ORDER = {"n": 0}


def _stamp():
    _ORDER["n"] += 1
    return _ORDER["n"]


class FakeWindow:
    def __init__(self, name):
        self._w = name
        self.protocols = {}
        self.binds = []
        self.destroyed = False
        self.destroyed_at = None
        self.exists = True
        self.grabs = 0
        self.cancelled = []
        self.master = None
        self._hwnd = 1

    def winfo_id(self):
        return self._hwnd

    def protocol(self, name, func=None):
        if func is None:
            return self.protocols.get(name)
        self.protocols[name] = func

    def bind(self, sequence, func, add=None):
        self.binds.append(sequence)

    def winfo_exists(self):
        return self.exists

    def winfo_toplevel(self):
        return self

    def grab_release(self):
        if self.grabs < 0:
            raise RuntimeError("no grab")
        self.grabs = 0

    def destroy(self):
        if self.destroyed:
            return
        self.destroyed = True
        self.destroyed_at = _stamp()
        self.exists = False

    def after_cancel(self, job):
        self.cancelled.append(job)

    def transient(self, host):
        raise AssertionError("transient() would restyle the pop-out as a dialog")


class FakeHost(FakeWindow):
    def __init__(self, life):
        super().__init__(".")
        self.life = life
        self.afters = []
        self.closed = 0
        self.tk = self
        self.protocols["WM_DELETE_WINDOW"] = self._close

    def after(self, delay, func):
        self.afters.append(func)
        return func

    def call(self, *args):
        if len(args) == 4 and args[0] == "wm" and args[1] == "protocol":
            return "host-close" if self.protocols.get(args[3]) else ""
        if args == ("host-close",):
            self.protocols["WM_DELETE_WINDOW"]()
            return ""
        raise AssertionError(args)

    def _close(self):
        self.closed += 1
        self.life.shutdown()


class ShutdownWindows(unittest.TestCase):
    def test_shutdown_closes_dialogs_first_and_is_idempotent(self):
        life = Lifetime()
        pop = FakeWindow("pop")
        dialog = FakeWindow("dialog")
        pop.grabs = 1
        dialog.grabs = -1  # release fails; shutdown still continues
        life.track(pop, own=False)
        life.track(dialog, own=False)
        life.note_after(pop, "job-1")
        life.shutdown()
        life.shutdown()
        self.assertTrue(pop.destroyed)
        self.assertTrue(dialog.destroyed)
        self.assertLess(dialog.destroyed_at, pop.destroyed_at)
        self.assertEqual(pop.grabs, 0)
        self.assertEqual(pop.cancelled, ["job-1"])
        self.assertEqual(life._afters, [])
        self.assertEqual(life._windows, [])

    def test_closing_the_popout_does_not_exit_edmc(self):
        life = Lifetime()
        host = FakeHost(life)
        pop = FakeWindow("pop")
        life.set_host(host)
        life.track(pop, on_close=pop.destroy, own=False)
        life._windows[0]["on_close"]()
        self.assertTrue(pop.destroyed)
        self.assertEqual(host.closed, 0)
        self.assertEqual(host.afters, [])
        self.assertFalse(life._exit_requested)

    def test_session_end_asks_edmc_to_exit_once(self):
        life = Lifetime()
        host = FakeHost(life)
        pop = FakeWindow("pop")
        other = FakeWindow("other")
        life.set_host(host)
        life.track(pop, on_close=pop.destroy, own=False)
        life.track(other, on_close=other.destroy, own=False)
        pop.protocols["WM_SAVE_YOURSELF"]()
        other.protocols["WM_SAVE_YOURSELF"]()
        self.assertEqual(len(host.afters), 1)
        self.assertEqual(host.closed, 0)
        host.afters[0]()
        self.assertEqual(host.closed, 1)
        self.assertTrue(pop.destroyed)
        self.assertTrue(other.destroyed)

    def test_session_end_without_a_host_still_closes_plugin_windows(self):
        life = Lifetime()
        pop = FakeWindow("pop")
        life.track(pop, own=False)
        pop.protocols["WM_SAVE_YOURSELF"]()
        self.assertTrue(pop.destroyed)
        self.assertTrue(life.shutting_down)

    def test_windows_popout_is_owned_by_edmc(self):
        window = FakeWindow("pop")
        host = FakeWindow(".")
        window._hwnd = 10
        host._hwnd = 20
        seen = []

        def assign(child, owner):
            seen.append((child, owner))
            return True

        with mock.patch.object(lifetime_mod.sys, "platform", "win32"), mock.patch.object(
            lifetime_mod, "assign_owner", assign
        ):
            self.assertTrue(lifetime_mod.own_to_host(window, host))
        self.assertEqual(seen, [(10, 20)])

    def test_non_windows_popout_stays_independent(self):
        if sys.platform == "win32":
            self.skipTest("ownership is the Windows behavior")
        window = FakeWindow("pop")
        host = FakeWindow(".")
        called = []
        with mock.patch.object(lifetime_mod, "assign_owner", lambda *args: called.append(args)):
            self.assertFalse(lifetime_mod.own_to_host(window, host))
        self.assertEqual(called, [])

    def test_a_window_is_not_owned_by_itself(self):
        window = FakeWindow("pop")
        with mock.patch.object(lifetime_mod.sys, "platform", "win32"):
            self.assertFalse(lifetime_mod.own_to_host(window, window))

    def test_assign_owner_rejects_an_empty_handle(self):
        self.assertFalse(lifetime_mod.assign_owner(0, 20))
        self.assertFalse(lifetime_mod.assign_owner(10, 0))
        self.assertFalse(lifetime_mod.assign_owner(10, 10))


class ShutdownSaves(unittest.TestCase):
    def test_stop_leaves_a_clean_seen_file_byte_for_byte(self):
        history = fresh_history()
        original = b'{"9":{"name":"Kept","sound":"neutral"}}\n'
        with open(history.json_file_path, "wb") as handle:
            handle.write(original)
        history.seen_data = {"9": {"name": "Kept", "sound": "neutral"}}
        history._seen_dirty = False
        history.stop_worker()
        with open(history.json_file_path, "rb") as handle:
            self.assertEqual(handle.read(), original)

    def test_stop_writes_pending_commanders_without_dropping_them(self):
        history = fresh_history()
        add_row(history, "1", "Alpha")
        add_row(history, "2", "Beta")
        history._seen_dirty = True
        history.stop_worker()
        with open(history.json_file_path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertEqual(set(saved), {"1", "2"})
        self.assertEqual(saved["1"]["name"], "Alpha")
        self.assertEqual(saved["2"]["name"], "Beta")
        self.assertFalse(history._seen_dirty)

    def test_reset_timer_is_a_daemon(self):
        history = fresh_history()
        history.trigger()
        timer = history._reset_timer
        self.assertIsNotNone(timer)
        self.assertTrue(timer.daemon)
        history.stop_worker()
        timer.join(timeout=1)
        self.assertFalse(timer.is_alive())


class ShutdownSource(unittest.TestCase):
    def test_dialogs_do_not_grab_or_steal_focus(self):
        with open(os.path.join(ROOT, "gui.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertNotIn("grab_set(", source)
        self.assertNotIn("focus_force(", source)

    def test_plugin_stop_closes_windows_before_the_save(self):
        with open(os.path.join(ROOT, "load.py"), encoding="utf-8") as handle:
            source = handle.read()
        stop = source.split("def plugin_stop", 1)[1]
        self.assertLess(stop.index("gui_inst.shutdown"), stop.index("history_inst.stop_worker"))
        self.assertLess(stop.index("beep_inst.stop_playback"), stop.index("history_inst.stop_worker"))
        self.assertIn("beep_beep plugin stopped!", stop)
