"""Windows click-through (mouse pass-through) helper for plugin windows.

On win32 click-through needs WS_EX_TRANSPARENT plus a valid layered
setup on the root window: WS_EX_LAYERED with initialized
SetLayeredWindowAttributes (alpha 255) when the window is not already
layered by Tk's -alpha, and never a bare WS_EX_LAYERED, which would
render an empty white box. Windows that already carry Tk-managed
layering (GetLayeredWindowAttributes reports a non-zero alpha) keep it
untouched so configured opacity is preserved; stray un-owned layering
without attributes is stripped again when click-through is disabled.

State is keyed by the Tk window handle (winfo_id) because GetAncestor can
resolve to a different root before and after the window is mapped; every
write resolves the root fresh and applies to both windows, so a <Map> event
always lands on the correct root.

On other platforms this is a graceful no-op.
"""
import sys
import threading

from logutil import log

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
GA_ROOT = 2
LWA_ALPHA = 1

_lock = threading.Lock()
_state = {}  # Tk winfo_id -> bool, remembered so <Map> can re-apply it


def supported() -> bool:
    return sys.platform == "win32"


def _root_hwnd(hwnd: int) -> int:
    import ctypes
    user32 = ctypes.windll.user32
    root = user32.GetAncestor(int(hwnd), GA_ROOT)
    return int(root or 0)


def _attrs_alpha(user32, hwnd: int):
    """Alpha reported by GetLayeredWindowAttributes, or None when absent."""
    import ctypes
    from ctypes import wintypes
    col = wintypes.COLORREF(0)
    pct = wintypes.BYTE(0)
    flags = wintypes.DWORD(0)
    try:
        user32.GetLayeredWindowAttributes.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.COLORREF),
            ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD)]
        user32.GetLayeredWindowAttributes.restype = wintypes.BOOL
        ok = user32.GetLayeredWindowAttributes(
            int(hwnd), ctypes.byref(col), ctypes.byref(pct), ctypes.byref(flags))
        return int(pct.value) if ok else None
    except Exception:
        return None


def _write(hwnd: int, enabled: bool) -> bool:
    """Apply click-through to the input window and its root (they can differ)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.SetWindowLongW.restype = ctypes.c_long
    user32.SetLayeredWindowAttributes.argtypes = [
        wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD]
    user32.SetLayeredWindowAttributes.restype = wintypes.BOOL
    try:
        root = _root_hwnd(hwnd)
    except Exception:
        root = 0
    targets = [(root or int(hwnd), True)]
    if int(hwnd) not in [t for t, _ in targets]:
        targets.append((int(hwnd), False))
    ok = False
    for target, is_root in targets:
        try:
            style = user32.GetWindowLongW(target, GWL_EXSTYLE) & 0xFFFFFFFF
            alpha = _attrs_alpha(user32, target)
            if enabled:
                style |= WS_EX_TRANSPARENT
                if is_root:
                    # Click-through needs a layered root; initialize attrs so
                    # the window renders instead of showing a white box.
                    style |= WS_EX_LAYERED
                elif not alpha:
                    style &= ~WS_EX_LAYERED
                user32.SetWindowLongW(target, GWL_EXSTYLE, style)
                if is_root and not alpha:
                    user32.SetLayeredWindowAttributes(target, 0, 255, LWA_ALPHA)
            else:
                style &= ~WS_EX_TRANSPARENT
                if not alpha:
                    style &= ~WS_EX_LAYERED
                user32.SetWindowLongW(target, GWL_EXSTYLE, style)
            ok = True
        except Exception:
            log.exception("Failed to set click-through=%s for hwnd %s", enabled, target)
    return ok


def apply(hwnd: int, enabled: bool) -> bool:
    """Enable or disable click-through for a Tk window."""
    if not supported() or not hwnd:
        return False
    with _lock:
        _state[int(hwnd)] = bool(enabled)
    ok = _write(hwnd, enabled)
    log.info(
        "Click-through %s for hwnd %s (root resolve %s)",
        "enabled" if enabled else "disabled",
        hwnd,
        "ok" if ok else "deferred",
    )
    return ok


def reapply(hwnd: int) -> None:
    """Re-apply the remembered click-through state for ``hwnd``.

    Windows can reset the extended style after redraws or reparents, so
    this is meant to be bound to the window's <Map> event.
    """
    if not supported() or not hwnd:
        return
    with _lock:
        enabled = _state.get(int(hwnd))
    if enabled is None:
        return
    _write(hwnd, enabled)


def forget(hwnd: int) -> None:
    """Drop remembered state (call when the window is destroyed)."""
    if not supported() or not hwnd:
        return
    with _lock:
        _state.pop(int(hwnd), None)
