"""Keep plugin windows from holding EDMC open after it is asked to exit.

EDMC only starts its shutdown when its own window receives the close.
On Windows an extra unowned top-level window is a separate process window:
a session script or taskkill can deliver the close there, and a modal grab
makes Tk ignore the close on EDMC's window until the dialog is dismissed.
A logoff, restart, or shutdown is delivered as WM_SAVE_YOURSELF, which EDMC
does not handle, so the process is left for Windows to kill.
"""

import sys

from logutil import log


class Lifetime:
    def __init__(self):
        self._windows = []
        self._afters = []
        self._shutting_down = False
        self._exit_requested = False
        self._host = None

    @property
    def shutting_down(self):
        return self._shutting_down

    def set_host(self, widget):
        """Remember EDMC's window so a session end can ask it to exit."""
        if widget is None:
            return
        try:
            host = widget.winfo_toplevel()
        except Exception:
            host = widget
        if any(entry["window"] is host for entry in self._windows):
            return
        self._host = host

    def track(self, window, on_close=None, *, own=False):
        """Remember a plugin Toplevel and watch it for session end."""
        if window is None:
            return
        for entry in self._windows:
            if entry["window"] is window:
                if on_close is not None:
                    entry["on_close"] = on_close
                if own:
                    entry["own"] = True
                    self._own(window)
                return
        self._windows.append({"window": window, "on_close": on_close, "own": own})
        try:
            window.protocol("WM_SAVE_YOURSELF", self.session_end)
        except Exception:
            log.exception("Could not watch a plugin window for session end")
        try:
            window.bind("<Map>", lambda _event, win=window: self._on_map(win), add="+")
        except Exception:
            pass
        try:
            window.bind(
                "<Destroy>",
                lambda event, win=window: self._on_destroy(win, event),
                add="+",
            )
        except Exception:
            pass
        if own:
            self._own(window)

    def reown(self, window):
        """Put the Windows owner back after Tk rebuilds the window style."""
        if self._shutting_down:
            return
        self._own(window)

    def note_after(self, widget, job):
        """Remember a callback so shutdown can cancel it."""
        if job is not None:
            self._afters.append((widget, job))
        return job

    def session_end(self):
        """Windows logoff, restart, or shutdown (Tk's WM_SAVE_YOURSELF)."""
        if self._exit_requested:
            return
        self._exit_requested = True
        self._shutting_down = True
        log.info("Windows session is ending; asking EDMC to exit")
        self._release_grabs()
        host = self._host_window()
        if host is None:
            self._close_windows()
            return

        def run():
            if not self._invoke_host_close(host):
                self._close_windows()

        try:
            host.after(0, run)
        except Exception:
            log.exception("Could not schedule EDMC's close")
            run()

    def shutdown(self):
        """Destroy plugin windows and cancel their scheduled callbacks.

        Safe to call more than once. Does not write the commander list;
        the caller flushes that through the existing save.
        """
        self._shutting_down = True
        self._release_grabs()
        self._cancel_afters()
        self._close_windows()

    def _host_window(self):
        host = self._host
        if host is None:
            return None
        if any(entry["window"] is host for entry in self._windows):
            return None
        try:
            if not host.winfo_exists():
                return None
        except Exception:
            return None
        return host

    def _invoke_host_close(self, host):
        try:
            command = host.tk.call("wm", "protocol", host._w, "WM_DELETE_WINDOW")
        except Exception:
            log.exception("Could not read EDMC's close handler")
            return False
        if not command:
            return False
        try:
            host.tk.call(command)
        except Exception:
            log.exception("EDMC close handler failed")
            return False
        return True

    def _on_map(self, window):
        if self._shutting_down:
            return
        self._own(window)

    def _own(self, window):
        entry = next((item for item in self._windows if item["window"] is window), None)
        if entry is None or not entry.get("own"):
            return
        host = self._host_window()
        if host is None:
            return
        own_to_host(window, host)

    def _on_destroy(self, window, event):
        widget = getattr(event, "widget", None)
        if widget is not None and widget is not window:
            return
        self._windows = [entry for entry in self._windows if entry["window"] is not window]

    def _release_grabs(self):
        for entry in list(self._windows):
            window = entry["window"]
            try:
                window.grab_release()
            except Exception:
                pass

    def _cancel_afters(self):
        jobs = self._afters
        self._afters = []
        for widget, job in jobs:
            try:
                widget.after_cancel(job)
            except Exception:
                pass

    def _close_windows(self):
        # Dialogs are opened after the pop-out. Close them first.
        entries = list(reversed(self._windows))
        self._windows = []
        for entry in entries:
            window = entry["window"]
            try:
                alive = bool(window.winfo_exists())
            except Exception:
                alive = False
            if not alive:
                continue
            closer = entry.get("on_close")
            if closer is not None:
                try:
                    closer()
                except Exception:
                    log.exception("Plugin window close failed")
            try:
                if window.winfo_exists():
                    window.destroy()
            except Exception:
                pass


def own_to_host(window, host) -> bool:
    """Own a plugin window by EDMC so a process close reaches EDMC.

    On Windows an unowned top-level window can be the process main window.
    The close is then delivered to the plugin, whose handler only closes that
    window, and EDMC keeps running. The owner is applied with the Win32 call
    rather than Tk's transient(), which would turn the pop-out into a dialog
    frame. Other platforms leave the pop-out independent.
    """
    if sys.platform != "win32":
        return False
    if window is None or host is None or window is host:
        return False
    try:
        if not window.winfo_exists() or not host.winfo_exists():
            return False
        child = int(window.winfo_id())
        owner = int(host.winfo_id())
    except Exception:
        return False
    try:
        return assign_owner(child, owner)
    except Exception:
        log.exception("Could not attach the plugin window to EDMC")
        return False


def assign_owner(child: int, owner: int) -> bool:
    """Set GWLP_HWNDPARENT so ``child`` is not the process main window."""
    if not child or not owner or int(child) == int(owner):
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    get_ancestor = user32.GetAncestor
    get_ancestor.argtypes = [wintypes.HWND, wintypes.UINT]
    get_ancestor.restype = wintypes.HWND
    # GA_ROOT. winfo_id() is often the inner hwnd; the close goes to the root.
    child_root = int(get_ancestor(int(child), 2) or child)
    owner_root = int(get_ancestor(int(owner), 2) or owner)
    if not child_root or not owner_root or child_root == owner_root:
        return False
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        set_long = user32.SetWindowLongPtrW
    else:
        set_long = user32.SetWindowLongW
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    set_long.restype = ctypes.c_void_p
    # GWLP_HWNDPARENT. An owned window is skipped by CloseMainWindow and by
    # taskkill's search for the unowned top-level window.
    set_long(child_root, -8, owner_root)
    return True


lifetime = Lifetime()
