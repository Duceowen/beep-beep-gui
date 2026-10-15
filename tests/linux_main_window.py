#!/usr/bin/env python3
"""Place the Beep Beep row the way EDMC does, on Linux Tk.

EDMC builds a plugin frame, calls plugin_app, and destroys that frame when
plugin_app raises or returns nothing. This script checks both the old
Windows-only button color and the current button bar.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

config_mod = types.ModuleType("config")
config_mod.appname = "EDMarketConnector"
sys.modules["config"] = config_mod

notebook = types.ModuleType("myNotebook")
notebook.Frame = object
sys.modules["myNotebook"] = notebook

import tkinter as tk


def place_like_edmc(plugin_app):
    """Mirror EDMarketConnector._config_plugins and plug.Plugin.get_app."""
    root = tk.Tk()
    root.withdraw()
    body = tk.Frame(root)
    body.grid()
    ui_row = 4
    plugin_frame = tk.Frame(body, name="plugin_1")
    failure = None
    try:
        appitem = plugin_app(plugin_frame)
    except Exception as exc:
        failure = exc
        appitem = None
    if appitem is None or not isinstance(appitem, tk.Widget):
        plugin_frame.destroy()
        root.update_idletasks()
        return root, None, [], failure

    tk.Frame(body, highlightthickness=1).grid(row=ui_row, columnspan=2, sticky=tk.EW)
    ui_row += 1
    plugin_frame.grid(row=ui_row, columnspan=2, sticky=tk.NSEW)
    plugin_frame.columnconfigure(1, weight=1)
    ui_row += 1
    appitem.grid(row=ui_row - 1, column=0, columnspan=2, sticky=tk.EW)
    root.deiconify()
    root.update_idletasks()
    root.update()
    buttons = [w for w in appitem.winfo_children() if isinstance(w, tk.Button)]
    return root, appitem, buttons, failure


def old_plugin_app(parent):
    """The Windows path that aborts plugin_app on Linux Tk."""
    container = tk.Frame(parent)
    container.pack(padx=5, pady=5)
    tk.Button(container, text="Beep Beep", command=lambda: None).pack(side="left")
    mute = tk.Button(container, width=2)
    mute.pack(side="left", padx=(4, 0))
    mute.config(
        text="🔊",
        bg="SystemButtonFace",
        fg="black",
        relief="raised",
    )
    return parent


def main() -> int:
    probe_root = tk.Tk()
    probe_root.withdraw()
    probe = tk.Button(probe_root)
    try:
        probe.config(bg="SystemButtonFace")
    except tk.TclError as exc:
        print(f"X11_REJECT SystemButtonFace: {exc}")
    else:
        print("FAILED: this Tk accepted SystemButtonFace, so it is not Linux Tk")
        probe_root.destroy()
        return 2
    probe_root.destroy()

    root, appitem, buttons, failure = place_like_edmc(old_plugin_app)
    print(f"OLD_ROW exists={appitem is not None} buttons={len(buttons)} error={failure}")
    root.destroy()
    if failure is None or "SystemButtonFace" not in str(failure):
        print("FAILED: old button color did not raise on this Tk")
        return 3
    if appitem is not None:
        print("FAILED: EDMC would still have shown the old row")
        return 3

    from beep_beep_config import config
    from load import plugin_app

    config.save_config = lambda: None
    root, appitem, buttons, failure = place_like_edmc(plugin_app)
    if failure is not None or appitem is None:
        print(f"FAILED: current plugin_app produced no row: {failure}")
        root.destroy()
        return 4

    labels = [button.cget("text") for button in buttons]
    sizes = [
        (button.cget("text"), button.winfo_width(), button.winfo_height(), int(button.winfo_ismapped()))
        for button in buttons
    ]
    print("NEW_ROW", labels, sizes)
    if labels != ["Beep Beep", "🔊"] and not (len(labels) == 2 and labels[0] == "Beep Beep" and labels[1] in ("🔊", "🔇")):
        print("FAILED: expected Beep Beep and the mute button")
        root.destroy()
        return 5
    for button in buttons:
        if not button.winfo_ismapped() or button.winfo_width() <= 1 or button.winfo_height() <= 1:
            print(f"FAILED: button not visible: {button.cget('text')}")
            root.destroy()
            return 5

    mute = buttons[1]
    before = mute.cget("text")
    mute.invoke()
    root.update_idletasks()
    after = mute.cget("text")
    print(f"MUTE_TOGGLE {before} -> {after} relief={mute.cget('relief')} bg={mute.cget('bg')}")
    if after == before or after not in ("🔊", "🔇"):
        print("FAILED: mute button did not toggle")
        root.destroy()
        return 6
    mute.invoke()
    root.update_idletasks()
    if mute.cget("text") != before:
        print("FAILED: mute button did not toggle back")
        root.destroy()
        return 6

    root.destroy()
    print("LINUX_MAIN_WINDOW_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
