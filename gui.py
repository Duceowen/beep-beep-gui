from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkFont
import os
import sys
from beep_beep_config import config
from commander_history import history_inst
from beep_beep import beep_inst
from sound_loader import sound_inst

try:
    import theme as edmc_theme
except Exception:
    edmc_theme = None


_WIN_DATETIME_FMT = None

MIN_COL_WIDTH = 50
SEEN_COLUMNS = ("name", "sound", "seen_count", "last_seen")
COLUMN_LABELS = (
    ("name", "Name"),
    ("sound", "Sound"),
    ("seen_count", "Seen"),
    ("last_seen", "Last Encounter"),
)
COLUMN_BUFFER = 24
DIVIDER_HIT = 8
DIVIDER_LINE = 1


def layout_seen_columns(available, visible, fixed, mins):
    """Width for each Seen Commanders column.

    Name stays on the left and takes whatever space is left. Sound, Seen,
    and Last Encounter keep the widths in ``fixed``. A very narrow window
    shrinks Name first, then those columns, so the row still fits.
    """
    order = [col for col in SEEN_COLUMNS if col in visible]
    if not order:
        return [0, 0, 0, 0]
    available = max(len(order), int(available))
    held = [col for col in ("sound", "seen_count", "last_seen") if col in order]
    slack = "name" if "name" in order else order[0]
    if slack in held:
        held = [col for col in held if col != slack]
    widths = {col: 0 for col in SEEN_COLUMNS}
    for col in held:
        widths[col] = max(0, int(fixed.get(col, 0)))
    held_sum = sum(widths[col] for col in held)
    slack_min = max(1, int(mins.get(slack, 1))) if slack else 0
    if not slack:
        scaled = _scale_parts([widths[col] for col in held], available)
        for col, width in zip(held, scaled):
            widths[col] = width
    elif available >= held_sum + slack_min:
        widths[slack] = available - held_sum
    elif available > held_sum:
        widths[slack] = available - held_sum
    else:
        slack_w = slack_min if available > slack_min + len(held) else 1
        scaled = _scale_parts([widths[col] for col in held], available - slack_w)
        for col, width in zip(held, scaled):
            widths[col] = width
        widths[slack] = max(0, available - sum(widths[col] for col in held))
    total = sum(widths[col] for col in order)
    if total != available:
        widths[slack] = max(0, widths.get(slack, 0) + available - total)
    return [widths[col] for col in SEEN_COLUMNS]


def _scale_parts(parts, budget):
    if not parts:
        return []
    budget = max(len(parts), int(budget))
    cleaned = [max(0, int(part)) for part in parts]
    total = sum(cleaned) or len(cleaned)
    scaled = [
        max(1, int(round((part or (total / len(cleaned))) * budget / total)))
        for part in cleaned
    ]
    scaled[-1] += budget - sum(scaled)
    if scaled[-1] < 1:
        scaled[-1] = 1
        overflow = sum(scaled) - budget
        for index in range(len(scaled) - 1, -1, -1):
            if overflow <= 0:
                break
            give = min(overflow, max(0, scaled[index] - 1))
            scaled[index] -= give
            overflow -= give
    return scaled


def set_always_on_top(window, enabled):
    """Keep a window above others so a fullscreen game cannot cover it."""
    try:
        window.wm_attributes("-topmost", bool(enabled))
    except Exception:
        pass


def _winpattern_to_strftime(pattern):
    _y = lambda n: "%y"
    _M = lambda n: {1: "%#m", 2: "%m", 3: "%b", 4: "%B"}.get(n, "%m")
    _d = lambda n: {1: "%#d", 2: "%d", 3: "%a", 4: "%A"}.get(n, "%d")
    _H = lambda n: {1: "%#H", 2: "%H"}.get(n, "%H")
    _h = lambda n: {1: "%#I", 2: "%I"}.get(n, "%I")
    _m = lambda n: {1: "%#M", 2: "%M"}.get(n, "%M")
    _s = lambda n: {1: "%#S", 2: "%S"}.get(n, "%S")
    tokens = {"y": _y, "M": _M, "d": _d, "H": _H, "h": _h, "m": _m, "s": _s, "t": lambda n: "%p"}
    out = []
    i, n = 0, len(pattern)
    while i < n:
        ch = pattern[i]
        j = i
        while j < n and pattern[j] == ch:
            j += 1
        run = j - i
        if ch in tokens:
            out.append(tokens[ch](run))
        elif not ch.isalpha():
            out.append(pattern[i:j].replace("%", "%%"))
        i = j
    return "".join(out)


def _win_datetime_format():
    global _WIN_DATETIME_FMT
    if _WIN_DATETIME_FMT is not None:
        return _WIN_DATETIME_FMT
    pattern = None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\International") as key:
                def q(name, default=""):
                    try:
                        return winreg.QueryValueEx(key, name)[0]
                    except OSError:
                        return default
                date_fmt = q("sShortDate", "M/d/yyyy")
                time_fmt = q("sShortTime") or q("sTimeFormat", "h:mm tt")
            pattern = _winpattern_to_strftime(date_fmt) + " " + _winpattern_to_strftime(time_fmt)
        except Exception:
            pattern = None
    _WIN_DATETIME_FMT = pattern or "%x %X"
    return _WIN_DATETIME_FMT


class ThemedScrollbar(tk.Canvas):
    def __init__(self, master, command=None, background="grey4", foreground="#13ec39",
                 activebackground="#13ec39", width=14, **kw):
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        super().__init__(master, width=width, background=background, **kw)
        self.command = command
        self._trough = background
        self._thumb = foreground
        self._thumb_active = activebackground
        self._first = 0.0
        self._last = 1.0
        self._drag = False
        self._offset = 0
        self._top = 0
        self._bottom = 0
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_move)
        self.bind("<ButtonRelease-1>", self._on_release)

    def apply_theme(self, palette):
        bg = palette.get("background")
        fg = palette.get("foreground")
        abg = palette.get("activebackground") or fg
        if bg:
            self._trough = bg
            try:
                self.configure(background=bg)
            except Exception:
                pass
        if fg:
            self._thumb = fg
        if abg:
            self._thumb_active = abg
        self._redraw()

    def set(self, first, last):
        try:
            self._first = float(first)
            self._last = float(last)
        except Exception:
            return
        self._redraw()

    def _redraw(self):
        self.delete("thumb")
        w = self.winfo_width()
        h = self.winfo_height()
        if h <= 1 or w <= 1:
            return
        if self._last - self._first >= 0.999:
            self._top = 0
            self._bottom = h
            return
        top = int(self._first * h)
        bottom = int(self._last * h)
        if bottom - top < 8:
            bottom = min(h, top + 8)
        self._top = top
        self._bottom = bottom
        color = self._thumb_active if self._drag else self._thumb
        self.create_rectangle(2, top + 1, w - 2, bottom - 1, fill=color, outline="", tags="thumb")

    def _on_press(self, event):
        h = self.winfo_height() or 1
        if self._top <= event.y <= self._bottom:
            self._drag = True
            self._offset = event.y - self._top
        else:
            self._drag = True
            self._offset = max(0, (self._bottom - self._top) // 2)
            self._move_to(event.y - self._offset, h)
        self._redraw()

    def _on_move(self, event):
        if not self._drag:
            return
        self._move_to(event.y - self._offset, self.winfo_height() or 1)

    def _on_release(self, _event):
        self._drag = False
        self._redraw()

    def _move_to(self, top, height):
        if not self.command:
            return
        frac = max(0.0, min(1.0, top / height))
        self.command("moveto", frac)


class HeaderDivider(tk.Canvas):
    """Grab strip in the header color with a single theme-colored line."""

    def __init__(self, master):
        super().__init__(
            master,
            width=DIVIDER_HIT,
            height=8,
            cursor="sb_h_double_arrow",
            background="#2a2a2a",
            highlightthickness=0,
            bd=0,
        )
        self.resizable = True
        self.grip_width = DIVIDER_HIT
        self.line = self.create_line(
            DIVIDER_HIT // 2, 0, DIVIDER_HIT // 2, 8,
            width=DIVIDER_LINE, fill="#f2f2f2",
        )
        self.bind("<Configure>", self._place_line, add="+")

    def _place_line(self, _event=None):
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        x = width // 2
        try:
            self.coords(self.line, x, 0, x, height)
            self.itemconfigure(self.line, width=DIVIDER_LINE)
        except Exception:
            pass

    def apply_theme(self, palette):
        foreground = palette.get("foreground") or "#f2f2f2"
        background = palette.get("background") or "#2a2a2a"
        try:
            self.configure(background=background)
            self.itemconfigure(self.line, fill=foreground, width=DIVIDER_LINE)
        except Exception:
            pass


class SeenCommandersGUI:
    def __init__(self):
        self.plugin_dir = os.path.dirname(__file__)
        self.tree_items = {} 
        self.tree = None 
        self.name_vars = {}
        self.last_seen_vars = {}
        self.seen_count_vars = {}
        self.widgets = {}
        self.sound_vars = {}
        self.window = None
        self.parent = None
        self.plugin_parent = None
        self.scrollbar = None
        self.resize_after_id = None
        self.row_offset = 0
        self.sort_field = "last_seen"
        self.sort_asc = False       
        self.refresh_interval = 5000        
        self.beep_inst = None
        self._refresh_id = None
        self.ui_cache = {
            "name": {},
            "sound": {},
            "seen_count": {},
            "last_seen": {},
        }        

        
    def on_history_event(self, data):
        if not self.window or not self.window.winfo_exists():
            return
    
        self.window.after(
            0,
            lambda: self._handle_history_event(data)
        )
    
    
    def _handle_history_event(self, data):
        self.add_or_update_commander(data)
        self.refresh_gui()


    def format_last_seen(self, ts_iso: str) -> str:
        try:
            ts = datetime.fromisoformat(ts_iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            local = ts.astimezone()
            return local.strftime(_win_datetime_format())
        
        except (ValueError, TypeError):
            return ts_iso            

    

    def build_plugin_button(self, parent):
        # Leave this frame unmanaged. EDMC grids the widget plugin_app returns
        # across the whole main window. Packing it here, or raising while it
        # is built, makes EDMC drop the row. Children packed left and right
        # land on the edges of that row.
        self.plugin_parent = parent
        container = tk.Frame(parent)

        btn_show = tk.Button(
            container,
            text="Beep Beep",
            command=lambda: self.open(parent)
        )
        btn_show.pack(side="left", padx=(5, 0), pady=5)

        mute_btn = tk.Button(container, width=3)
        mute_btn.pack(side="left", padx=(4, 5), pady=5)

        options_btn = tk.Button(
            container,
            text="Options",
            command=self.open_options_popup,
        )
        options_btn.pack(side="right", padx=(5, 5), pady=5)

        def update_mute_button(muted):
            mute_btn.config(
                text="🔇" if muted else "🔊",
                relief="sunken" if muted else "raised"
            )

        def toggle_mute():
            new_value = not config.get_config("mute", False)
            beep_inst.set_muted(new_value)
            update_mute_button(new_value)

        mute_btn.config(command=toggle_mute)
        update_mute_button(config.get_config("mute", False))

        return container


    def open(self, parent):
        self.parent = parent
    
        x = config.get_config("seen_window_x", 50)
        y = config.get_config("seen_window_y", 50)
        width = config.get_config("seen_window_width", 800)
        height = config.get_config("seen_window_height", 400)
    
        width = max(100, width)
        height = max(100, height)

        self._pending_width = width

        self.topmost_var = tk.BooleanVar(value=bool(config.get_config("stay_on_top", False)))

        if self.window and self.window.winfo_exists():
            self.window.lift()
            self.build_ui(self.window)
            self._apply_topmost()
            self.apply_seen_opacity()
            return
    
        self.window = tk.Toplevel(parent)
        self.window.title("Seen Commanders")
    
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(100, 100)
        self.apply_seen_opacity()  
    
        self.window.grid_rowconfigure(1, weight=1)
        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_columnconfigure(1, weight=1)
        self.window.grid_columnconfigure(2, weight=1)
        
        
        self.build_ui(self.window)
        self.make_tree_editable()
        self.start_auto_refresh()
        self.attach_resize_listener()
        
        
        self.tree.bind("<ButtonRelease-1>", self._enforce_column_limits)
        self.window.after(50, self._enforce_column_limits)

        self._apply_topmost()
        self.apply_seen_opacity()
        self.window.after(150, self._apply_topmost)
        self.window.after(150, self.apply_seen_opacity)

    def _apply_topmost(self, window=None):
        """Apply the saved "stay on top" preference to a window."""
        win = window if window is not None else self.window
        if not win:
            return
        try:
            if not win.winfo_exists():
                return
        except Exception:
            return
        set_always_on_top(win, bool(config.get_config("stay_on_top", False)))

    def _grab_when_viewable(self, popup):
        """Grab a dialog after the window manager has mapped it.

        Linux Mint raises TclError from grab_set() while the window is not
        viewable. That error was aborting the Seen Commanders editor before
        its fields were created.
        """
        def attempt(tries_left):
            try:
                if not popup.winfo_exists():
                    return
                popup.update_idletasks()
                if popup.winfo_viewable():
                    popup.grab_set()
                    popup.lift()
                    return
            except tk.TclError:
                return
            if tries_left > 0:
                popup.after(20, lambda: attempt(tries_left - 1))

        popup.after(0, lambda: attempt(15))

    def _on_topmost_toggle(self, value):
        self._apply_topmost()
        self._apply_topmost(getattr(self, "options_popup", None))

    def _enforce_column_limits(self, _event=None):
        self._fit_columns()

    def _widget_width(self, widget):
        if not widget:
            return 0
        try:
            if not widget.winfo_exists():
                return 0
            return widget.winfo_width()
        except Exception:
            return 0

    def _seen_row_width(self):
        widths = []
        for widget in (getattr(self, "header_frame", None), getattr(self, "tree", None)):
            width = self._widget_width(widget)
            if width > 1:
                widths.append(width)
        if not widths:
            return 0
        return min(widths)

    def _visible_columns(self):
        enabled = self._column_enabled()
        visible = [col for col in SEEN_COLUMNS if enabled.get(col, True)]
        return visible or ["name"]

    def _column_enabled(self):
        saved = config.get_config("seen_columns_enabled", None)
        enabled = {col: True for col in SEEN_COLUMNS}
        if isinstance(saved, dict):
            for col in SEEN_COLUMNS:
                if col in saved:
                    enabled[col] = bool(saved[col])
        if not any(enabled.values()):
            enabled["name"] = True
        return enabled

    def _saved_col_widths(self):
        saved = config.get_config("seen_col_widths", None)
        return saved if isinstance(saved, dict) else {}

    def _legacy_share_save(self, saved):
        """Older builds stored Name and Sound as parts of 1000, not pixels."""
        try:
            return int(saved["name"]) + int(saved["sound"]) == 1000
        except (KeyError, TypeError, ValueError):
            return False

    def _user_fixed_widths(self):
        cached = getattr(self, "_user_fixed", None)
        if isinstance(cached, dict):
            return cached
        saved = self._saved_col_widths()
        keys = ("seen_count", "last_seen")
        if not self._legacy_share_save(saved):
            keys = ("sound",) + keys
        fixed = {}
        for col in keys:
            if col not in saved:
                continue
            try:
                fixed[col] = max(1, int(saved[col]))
            except (TypeError, ValueError):
                continue
        self._user_fixed = fixed
        return fixed

    def _set_user_fixed(self, col, width):
        fixed = dict(self._user_fixed_widths())
        try:
            fixed[col] = max(1, int(width))
        except (TypeError, ValueError):
            return
        self._user_fixed = fixed

    def _migrate_legacy_sound(self, available):
        """Turn the old Name/Sound ratio into one Sound width.

        Name then keeps every new pixel when the window changes size.
        """
        if getattr(self, "_sound_migrated", False):
            return
        user = self._user_fixed_widths()
        if "sound" in user:
            self._sound_migrated = True
            return
        saved = self._saved_col_widths()
        if not self._legacy_share_save(saved):
            return
        try:
            saved_window = int(config.get_config("seen_window_width", 0) or 0)
        except (TypeError, ValueError):
            saved_window = 0
        # The first layout pass can run before the window has its real width.
        if saved_window > int(available) + 80:
            return
        try:
            share = int(saved["sound"]) / 1000.0
        except (TypeError, ValueError):
            return
        content = self._locked_widths()
        seen = int(user.get("seen_count", content.get("seen_count", 0)))
        last_floor = int(content.get("last_seen", 0))
        last_user = user.get("last_seen")
        last = last_floor if last_user is None else max(last_floor, int(last_user))
        flex = int(available) - seen - last
        sound = int(round(share * flex))
        name_min = self._min_label_width("name")
        sound_min = self._min_label_width("sound")
        if sound < sound_min or flex - sound < name_min:
            return
        self._set_user_fixed("sound", sound)
        self._sound_migrated = True
        try:
            self._save_col_widths()
        except Exception:
            self._sound_migrated = False

    def _preferred_fixed(self):
        """Widths Sound, Seen, and Last Encounter keep until the window is too narrow.

        Name is not included. It stays on the left and fills the remaining space.
        Last Encounter never starts narrower than the full date and time.
        """
        content = self._locked_widths()
        user = self._user_fixed_widths()
        sound = user.get("sound", content.get("sound", 0))
        seen = user.get("seen_count", content.get("seen_count", 0))
        last_floor = int(content.get("last_seen", 0))
        last_user = user.get("last_seen")
        last = last_floor if last_user is None else max(last_floor, int(last_user))
        return {"sound": int(sound), "seen_count": int(seen), "last_seen": int(last)}

    def _fixed_floor(self, col):
        if col == "last_seen":
            return max(1, int(self._locked_widths().get("last_seen", self._min_label_width(col))))
        return max(1, self._min_label_width(col))

    def _text_px(self, text):
        try:
            font = tkFont.nametofont("TkDefaultFont")
            return font.measure("" if text is None else str(text))
        except Exception:
            return MIN_COL_WIDTH

    def _wide_timestamp_sample(self):
        fmt = _win_datetime_format()
        samples = []
        for stamp in (
            datetime(2026, 9, 30, 22, 59),
            datetime(2026, 12, 31, 12, 59),
            datetime(2026, 5, 28, 8, 8),
        ):
            try:
                samples.append(stamp.strftime(fmt))
            except Exception:
                continue
        if not samples:
            return ""
        return max(samples, key=self._text_px)

    def _locked_widths(self):
        if not getattr(self, "_content_dirty", True):
            cached = getattr(self, "_locked_cache", None)
            if isinstance(cached, dict):
                return cached
        locked = {}
        tree = getattr(self, "tree", None)
        tree_ok = tree is not None and tree.winfo_exists()
        for col, label in COLUMN_LABELS:
            if col == "name":
                continue
            widest = self._text_px(label)
            if col == "last_seen":
                widest = max(widest, self._text_px(self._wide_timestamp_sample()))
            if tree_ok:
                for item in tree.get_children(""):
                    try:
                        value = tree.set(item, col)
                    except Exception:
                        continue
                    widest = max(widest, self._text_px(value))
            locked[col] = widest + COLUMN_BUFFER
        self._locked_cache = locked
        self._content_dirty = False
        return locked

    def _min_label_width(self, col):
        label = dict(COLUMN_LABELS).get(col, col)
        return max(36, self._text_px(label) + 12)

    def _layout_signature(self, available):
        preferred = self._preferred_fixed()
        return (
            available,
            tuple(self._visible_columns()),
            preferred.get("sound"),
            preferred.get("seen_count"),
            preferred.get("last_seen"),
        )

    def _column_floor(self, col):
        if col == "name":
            return max(1, self._min_label_width(col))
        return max(1, self._fixed_floor(col))

    def _apply_column_widths(self, widths):
        if not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        visible = set(self._visible_columns())
        applied = {}
        for col, width in zip(SEEN_COLUMNS, widths):
            width = 0 if col not in visible else max(1, int(width))
            applied[col] = width
            floor = 0 if width == 0 else max(1, min(width, self._column_floor(col)))
            try:
                current = int(self.tree.column(col, "width"))
                current_min = int(self.tree.column(col, "minwidth"))
            except Exception:
                current = -1
                current_min = -1
            if current == width and current_min == floor:
                continue
            try:
                # Tk will not shrink a column until its minwidth is lowered.
                self.tree.column(col, minwidth=0)
                self.tree.column(col, width=width, stretch=False)
                self.tree.column(col, minwidth=floor)
            except Exception:
                pass
        shown = tuple(self._visible_columns())
        if getattr(self, "_display_columns", None) != shown:
            try:
                self.tree.configure(displaycolumns=shown)
            except Exception:
                pass
            self._display_columns = shown
        self._applied_widths = applied

    def _apply_initial_col_widths(self, parent):
        width = self._widget_width(parent)
        if width <= 1:
            width = getattr(self, "_pending_width", None) or 0
        if not width or width <= 1:
            try:
                width = int(config.get_config("seen_window_width", 800))
            except (TypeError, ValueError):
                width = 800
        widths = layout_seen_columns(
            width,
            self._visible_columns(),
            self._preferred_fixed(),
            {col: self._min_label_width(col) for col in SEEN_COLUMNS},
        )
        self._apply_column_widths(widths)
        self._layout_tree_header(widths)

    def _fit_columns(self, persist=False):
        if getattr(self, "_resize_col", None):
            self._layout_tree_header()
            return
        if not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        available = self._seen_row_width()
        if available <= 1:
            return
        dirty = bool(getattr(self, "_content_dirty", True))
        self._migrate_legacy_sound(available)
        user = self._user_fixed_widths()
        header = getattr(self, "header_frame", None)
        try:
            header_h = header.winfo_height() if header and header.winfo_exists() else 0
        except Exception:
            header_h = 0
        cheap = (
            available,
            tuple(self._visible_columns()),
            user.get("sound"),
            user.get("seen_count"),
            user.get("last_seen"),
            header_h,
        )
        # Same window size and same saved widths: do not touch the widgets.
        # Touching them schedules another resize and keeps EDMC busy.
        if not persist and not dirty and header_h > 1 and cheap == getattr(self, "_fitted_cheap", None):
            return
        widths = layout_seen_columns(
            available,
            self._visible_columns(),
            self._preferred_fixed(),
            {col: self._min_label_width(col) for col in SEEN_COLUMNS},
        )
        self._fitting = True
        try:
            self._apply_column_widths(widths)
            self._layout_tree_header(widths)
        finally:
            self._fitting = False
        if header_h > 1:
            self._fitted_cheap = cheap
            self._fitted_sig = cheap
        if persist:
            self._save_col_widths()

    def _save_col_widths(self):
        payload = {}
        for col, width in self._user_fixed_widths().items():
            payload[col] = max(1, int(width))
        config.set_config("seen_col_widths", payload)
        config.save_config()

    def _sync_column_vars(self):
        enabled = self._column_enabled()
        if not hasattr(self, "_column_vars"):
            self._column_vars = {}
        master = self.window if getattr(self, "window", None) else None
        for col, _label in COLUMN_LABELS:
            var = self._column_vars.get(col)
            if var is None:
                self._column_vars[col] = tk.BooleanVar(master=master, value=enabled[col])
            else:
                var.set(enabled[col])

    def _on_column_toggle(self, col):
        var = self._column_vars.get(col)
        enabled = self._column_enabled()
        enabled[col] = bool(var.get()) if var is not None else True
        if not any(enabled.values()):
            enabled[col] = True
            if var is not None:
                var.set(True)
        config.set_config("seen_columns_enabled", enabled)
        config.save_config()
        self._fitted_sig = None
        self._fitted_cheap = None
        self._fit_columns()

    def _menu_palette(self):
        palette = dict(self._edmc_palette())
        background = palette.get("background")
        foreground = palette.get("foreground")
        if not background or not foreground:
            header = getattr(self, "header_frame", None)
            label = None
            labels = getattr(self, "header_labels", None) or {}
            if labels:
                label = next(iter(labels.values()))
            try:
                background = background or header.cget("background")
            except Exception:
                background = background or "#1c1c1c"
            try:
                foreground = foreground or (label.cget("foreground") if label is not None else None)
            except Exception:
                foreground = foreground or "#f2f2f2"
        palette["background"] = background or "#1c1c1c"
        palette["foreground"] = foreground or "#f2f2f2"
        palette["activebackground"] = palette.get("activebackground") or palette["foreground"]
        palette["activeforeground"] = palette.get("activeforeground") or palette["background"]
        return palette

    def _close_column_menu(self):
        popup = getattr(self, "_column_menu", None)
        self._column_menu = None
        self._column_menu_redraws = []
        if popup is None:
            return
        try:
            if popup.winfo_exists():
                popup.destroy()
        except Exception:
            pass

    def _dismiss_column_menu(self, event):
        popup = getattr(self, "_column_menu", None)
        if popup is None:
            return
        try:
            if not popup.winfo_exists():
                self._column_menu = None
                return
            widget = getattr(event, "widget", None)
            if widget is not None and widget.winfo_toplevel() is popup:
                return
        except Exception:
            return
        self._close_column_menu()

    def _redraw_column_menu(self):
        for redraw in list(getattr(self, "_column_menu_redraws", []) or []):
            try:
                redraw()
            except Exception:
                pass

    def _add_column_menu_row(self, parent, col, label, palette):
        background = palette["background"]
        foreground = palette["foreground"]
        active = palette["activebackground"]
        active_foreground = palette["activeforeground"]
        row = tk.Frame(parent, background=background, highlightthickness=0, bd=0)
        row.pack(fill="x")
        size = 16
        canvas = tk.Canvas(
            row,
            width=size,
            height=size,
            background=background,
            highlightthickness=0,
            bd=0,
        )
        canvas.pack(side="left", padx=(8, 4), pady=3)
        box = canvas.create_rectangle(
            2, 2, size - 2, size - 2, width=1, outline=foreground, fill=background,
        )
        tick = canvas.create_line(
            size * 0.28, size * 0.52,
            size * 0.44, size * 0.70,
            size * 0.74, size * 0.32,
            width=2, capstyle="round", joinstyle="round", fill=foreground,
        )
        canvas._beep_check_items = (box, tick)
        text = tk.Label(
            row,
            text=label,
            anchor="w",
            background=background,
            foreground=foreground,
            highlightthickness=0,
            bd=0,
        )
        text.pack(side="left", fill="x", expand=True, padx=(0, 14), pady=2)

        def redraw():
            checked = bool(self._column_vars[col].get())
            canvas.itemconfigure(tick, state=("normal" if checked else "hidden"))

        def toggle(_event=None):
            var = self._column_vars[col]
            var.set(not bool(var.get()))
            self._on_column_toggle(col)
            self._redraw_column_menu()
            return "break"

        def enter(_event=None):
            row.configure(background=active)
            canvas.configure(background=active)
            text.configure(background=active, foreground=active_foreground)
            canvas.itemconfigure(box, fill=active)

        def leave(_event=None):
            row.configure(background=background)
            canvas.configure(background=background)
            text.configure(background=background, foreground=foreground)
            canvas.itemconfigure(box, fill=background)

        for widget in (row, canvas, text):
            widget.bind("<Button-1>", toggle)
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)
        redraw()
        self._column_menu_redraws.append(redraw)

    def _pointer_root(self, event):
        try:
            return int(event.x_root), int(event.y_root)
        except Exception:
            pass
        widget = getattr(event, "widget", None)
        if widget is not None:
            try:
                return (
                    int(widget.winfo_rootx()) + int(getattr(event, "x", 0)),
                    int(widget.winfo_rooty()) + int(getattr(event, "y", 0)),
                )
            except Exception:
                pass
        return 0, 0

    def _screen_bounds(self, widget):
        """Left, top, right, bottom of the whole desktop, including other monitors."""
        if sys.platform == "win32":
            try:
                import ctypes
                user32 = ctypes.windll.user32
                left = int(user32.GetSystemMetrics(76))
                top = int(user32.GetSystemMetrics(77))
                span_w = int(user32.GetSystemMetrics(78))
                span_h = int(user32.GetSystemMetrics(79))
                if span_w > 0 and span_h > 0:
                    return left, top, left + span_w, top + span_h
            except Exception:
                pass
        try:
            left = int(widget.winfo_vrootx())
            top = int(widget.winfo_vrooty())
            return left, top, left + int(widget.winfo_vrootwidth()), top + int(widget.winfo_vrootheight())
        except Exception:
            return 0, 0, int(widget.winfo_screenwidth()), int(widget.winfo_screenheight())

    def _move_popup(self, popup, x, y):
        popup.update_idletasks()
        width = max(1, popup.winfo_reqwidth())
        height = max(1, popup.winfo_reqheight())
        left, top, right, bottom = self._screen_bounds(popup)
        x = min(max(int(x), left), max(left, right - width))
        y = min(max(int(y), top), max(top, bottom - height))
        popup.geometry(f"{width}x{height}+{x}+{y}")
        return x, y

    def _popup_column_menu(self, event):
        if not getattr(self, "window", None) or not self.window.winfo_exists():
            return "break"
        self._close_column_menu()
        self._sync_column_vars()
        palette = self._menu_palette()
        popup = tk.Toplevel(self.window)
        # Keep it hidden until it has a position. A borderless window on
        # Windows otherwise opens at the top-right of the screen.
        popup.withdraw()
        popup.overrideredirect(True)
        popup.configure(background=palette["foreground"], highlightthickness=0, bd=0)
        try:
            popup.attributes("-topmost", bool(self.window.attributes("-topmost")))
        except Exception:
            pass
        inner = tk.Frame(popup, background=palette["background"], highlightthickness=0, bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        self._column_menu = popup
        self._column_menu_redraws = []
        for col, label in COLUMN_LABELS:
            self._add_column_menu_row(inner, col, label, palette)
        x, y = self._pointer_root(event)
        x, y = self._move_popup(popup, x, y)
        popup.deiconify()
        popup.lift()
        if not getattr(self, "_column_menu_bound", False):
            self.window.bind_all("<Button-1>", self._dismiss_column_menu, add="+")
            self._column_menu_bound = True
        popup.bind("<Escape>", lambda _e: self._close_column_menu())
        try:
            popup.focus_set()
        except Exception:
            pass
        # Windows drops the first position when the borderless window is shown.
        self._move_popup(popup, x, y)
        return "break"

    def _on_tree_configure(self, event=None):
        self._fit_columns_for_resize(event)

    def _on_header_configure(self, event=None):
        self._fit_columns_for_resize(event)

    def _fit_columns_for_resize(self, event=None):
        if getattr(self, "_fitting", False) or getattr(self, "_resize_col", None):
            return
        width = getattr(event, "width", 0) if event is not None else 0
        if width is not None and width <= 1:
            return
        if getattr(self, "_fit_job", None):
            return
        window = getattr(self, "window", None)
        if not window or not window.winfo_exists():
            self._fit_columns()
            return

        def run():
            self._fit_job = None
            if getattr(self, "_resize_col", None):
                return
            self._fit_columns()

        try:
            self._fit_job = window.after_idle(run)
        except Exception:
            self._fit_columns()
        
        
    def attach_resize_listener(self):
        if not self.window or not self.window.winfo_exists():
            return
    
        self.resize_after_id = None 
    
        def on_resize(event):
            if not self.window or not self.window.winfo_exists():
                return
            if event.widget is not self.window:
                return

            self._fit_columns_for_resize(event)

            if self.resize_after_id:
                try:
                    self.window.after_cancel(self.resize_after_id)
                except Exception:
                    pass

            self.resize_after_id = self.window.after(
                300,
                self.save_window_geometry
            )


    
        self.window.bind("<Configure>", on_resize)
        
        def on_window_close():
            self.save_window_geometry()
            self.window.destroy()
    
        self.window.protocol("WM_DELETE_WINDOW", on_window_close)        


    def save_window_geometry(self):
        width = max(100, self.window.winfo_width())
        height = max(100, self.window.winfo_height())
        x = self.window.winfo_x()
        y = self.window.winfo_y()

        config.set_config("seen_window_x", x)
        config.set_config("seen_window_y", y)
        config.set_config("seen_window_width", width)
        config.set_config("seen_window_height", height)
        config.save_config()

        self.resize_after_id = None


    def apply_seen_opacity(self, value=None):
        if value is None:
            value = config.get_config("seen_window_opacity", 100)
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 100
        value = max(10, min(100, value))
        window = self.window
        if not window or not window.winfo_exists():
            return
        try:
            window.attributes("-alpha", value / 100.0)
        except tk.TclError:
            pass


    def make_tree_editable(self):
        self.tree.bind("<Double-1>", self.on_tree_double_click_popup)
        self.tree.bind("<Return>", self.on_tree_enter_pressed)

    def on_tree_enter_pressed(self, _event):
        selection = self.tree.selection()
        if not selection:
            return
    
        row_id = selection[0]
        cmdr_id = next((k for k, v in self.tree_items.items() if v == row_id), None)
        if not cmdr_id:
            return
    
        bbox = self.tree.bbox(row_id)
        if bbox:
            x, y, width, height = bbox
            x_root = self.tree.winfo_rootx() + x + width // 2
            y_root = self.tree.winfo_rooty() + y + height // 2
        else:
            x_root = self.tree.winfo_rootx() + 50
            y_root = self.tree.winfo_rooty() + 50
    
        class FakeEvent:
            def __init__(self, y, x_root, y_root):
                self.y = y
                self.x_root = x_root
                self.y_root = y_root            
            
    
        e = FakeEvent(y + height // 2, x_root, y_root)
    
        self.on_tree_double_click_popup(e)        
    
    def on_tree_double_click_popup(self, event):
        row_id = self.tree.identify_row(event.y)
        if not row_id:
            return
    
        cmdr_id = next((k for k, v in self.tree_items.items() if v == row_id), None)
        if not cmdr_id:
            return
    
        info = history_inst.seen_data[cmdr_id]
    
        popup = tk.Toplevel(self.window)
        popup.title(f"Edit Commander {info.get('name', 'unknown')}")
        popup.transient(self.window)
        popup.bind("<Escape>", lambda e: popup.destroy())
    
    
        tk.Label(popup, text="Name:").grid(row=0, column=0, padx=5, pady=5, sticky="w")
        name_var = tk.StringVar(value=info.get("name", "unknown"))
        name_entry = tk.Entry(popup, textvariable=name_var)
        name_entry.grid(row=0, column=1, padx=5, pady=5)
        name_entry.focus_set()
        name_entry.selection_range(0, tk.END)
        name_entry.icursor(0)

        tk.Label(popup, text="Sound:").grid(row=1, column=0, padx=5, pady=5, sticky="w")
        
        sound_key = info.get("sound", "neutral").lower()
        sound_var = tk.StringVar(value=sound_key.capitalize())
        
        combo = tk.OptionMenu(
            popup,
            sound_var,
            *[s.capitalize() for s in sound_inst.sound_files]
        )
        combo.configure(width=14, anchor="w")
        combo.grid(row=1, column=1, padx=5, pady=5, sticky="w")
        
        tk.Button(
            popup,
            text="▶ Play",
            command=lambda: beep_inst.play_sound(sound_var.get().lower())
        ).grid(row=1, column=2, padx=5, pady=5, sticky="w")


        
        def save_popup_changes():
            new_name = name_var.get()
            new_sound = sound_var.get().lower() 
        
            self.name_vars[cmdr_id].set(new_name)
            self.sound_vars[cmdr_id].set(new_sound)

            history_inst.seen_data[cmdr_id]["name"] = new_name
            history_inst.seen_data[cmdr_id]["sound"] = new_sound
            
            self.tree.set(row_id, "name", new_name)
            self.tree.set(row_id, "sound", new_sound.capitalize())
        
            history_inst.save_seen_commanders()
        
            sort_field = config.get_config("sort_field", "last_seen")
            sort_asc = config.get_config("sort_asc", False)
            self.sort_rows(sort_field, sort_asc)
        
            popup.destroy()
  
        tk.Button(popup, text="Save", command=save_popup_changes).grid(row=2, column=0, columnspan=2, pady=10)
    
        def open_sound_menu(_event=None):
            try:
                menu = combo["menu"]
                menu.post(combo.winfo_rootx(), combo.winfo_rooty() + combo.winfo_height())
            except Exception:
                pass

        def on_name_enter(_event):
            combo.focus_set()
            open_sound_menu()
            return "break"
    
        name_entry.bind("<Return>", on_name_enter)
    
        def on_combo_enter(_event):
            save_popup_changes()
            return "break"
    
        combo.bind("<Return>", on_combo_enter)
        combo.bind("<KP_Enter>", on_combo_enter)

        self._move_popup(popup, event.x_root, event.y_root)
        self._apply_topmost(popup)
        self.apply_edmc_theme(popup)
        name_entry.focus_set()
        self._grab_when_viewable(popup)

    def build_ui(self, parent):
        sort_field = config.get_config("sort_field", "last_seen")
        sort_asc = config.get_config("sort_asc", False)
    
        if not hasattr(self, "tree") or not self.tree or not self.tree.winfo_exists() or self.tree.master != parent:
            for attr in ["scrollbar", "tree", "header_frame"]:
                if hasattr(self, attr) and getattr(self, attr) and getattr(self, attr).winfo_exists():
                    getattr(self, attr).destroy()
                    
            style = ttk.Style(parent)
            default_font = tkFont.nametofont("TkDefaultFont")
            row_height = default_font.metrics("linespace") + 4
            style.configure("Treeview", rowheight=row_height)
    
            self.tree = ttk.Treeview(parent, columns=("name", "sound", "seen_count", "last_seen"), show="", style="Beep.Treeview")
            self.tree.grid(row=1, column=0, columnspan=3, sticky="nsew")

            self.header_frame = tk.Frame(parent, height=row_height + 6)
            self.header_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
            self.header_frame.grid_propagate(False)
            self.header_frame.bind("<Button-3>", self._popup_column_menu)
            self.header_frame.bind("<Control-Button-1>", self._popup_column_menu)
            self.header_labels = {}
            self.header_handles = {}
            for _col, _label in COLUMN_LABELS:
                _lbl = tk.Label(
                    self.header_frame,
                    text=_label,
                    anchor="w",
                    cursor="hand2",
                    bd=0,
                    highlightthickness=0,
                    padx=0,
                )
                _lbl.bind("<Button-1>", lambda _e, c=_col: self.on_header_click(c))
                _lbl.bind("<Button-3>", self._popup_column_menu)
                _lbl.bind("<Control-Button-1>", self._popup_column_menu)
                self.header_labels[_col] = _lbl
            for _col in ("sound", "seen_count", "last_seen"):
                _handle = HeaderDivider(self.header_frame)
                self._bind_divider(_handle, _col)
                self.header_handles[_col] = _handle

            self.tree.column("name", anchor="w")
            self.tree.column("sound", anchor="w")
            self.tree.column("seen_count", anchor="w")
            self.tree.column("last_seen", anchor="w")

            for _col in SEEN_COLUMNS:
                try:
                    self.tree.column(_col, stretch=False, minwidth=MIN_COL_WIDTH)
                except Exception:
                    pass
            self._apply_initial_col_widths(parent)

            self.scrollbar = ThemedScrollbar(parent, command=self.tree.yview)
            self.tree.configure(yscroll=self.scrollbar.set)
            self.scrollbar.grid(row=1, column=3, sticky="ns")

            parent.grid_rowconfigure(1, weight=1)
            for col in range(3):
                parent.grid_columnconfigure(col, weight=1)

            self.tree.bind("<Configure>", self._on_tree_configure, add="+")
            self.header_frame.bind("<Configure>", self._on_header_configure, add="+")
            self.header_frame.after_idle(self._layout_tree_header)
    
            self.widgets.clear()
            self.sound_vars.clear()
            self.name_vars.clear()
            self.last_seen_vars.clear()
            self.seen_count_vars.clear()
            self.tree_items = {}

        if history_inst.seen_data:
            new_ids = [cmdr_id for cmdr_id in history_inst.seen_data if cmdr_id not in self.tree_items]
            if new_ids:
                self.add_rows(new_ids)
    
        self.sort_rows(sort_field, sort_asc)

        self.apply_edmc_theme_to_window(parent)

    def _bind_divider(self, divider, col):
        if divider.resizable:
            divider.bind("<ButtonPress-1>", lambda _e, c=col: self._start_col_resize(_e, c))
            divider.bind("<B1-Motion>", self._do_col_resize)
            divider.bind("<ButtonRelease-1>", self._end_col_resize)
            try:
                divider.configure(cursor="sb_h_double_arrow")
            except Exception:
                pass
        divider.bind("<Button-3>", self._popup_column_menu)
        divider.bind("<Control-Button-1>", self._popup_column_menu)

    def _divider_can_resize(self, col, shown):
        if col not in shown or col == "name":
            return False
        return shown.index(col) > 0

    def _layout_tree_header(self, widths=None, _event=None):
        tree = getattr(self, "tree", None)
        header = getattr(self, "header_frame", None)
        if not tree or not tree.winfo_exists():
            return
        if not header or not header.winfo_exists():
            return
        if widths is None:
            stored = getattr(self, "_applied_widths", None) or {}
            widths = []
            for col in SEEN_COLUMNS:
                if col in stored:
                    widths.append(stored[col])
                    continue
                try:
                    widths.append(int(tree.column(col, "width")))
                except Exception:
                    widths.append(0)
        height = header.winfo_height()
        if height <= 1:
            height = header.winfo_reqheight()
        if height <= 1:
            return
        limit = header.winfo_width()
        visible = self._visible_columns()
        width_map = dict(zip(SEEN_COLUMNS, widths))
        for handle in getattr(self, "header_handles", {}).values():
            if handle and handle.winfo_exists():
                handle.place_forget()
        x = 0
        shown = [col for col in SEEN_COLUMNS if col in visible and width_map.get(col, 0) > 0]
        for index, col in enumerate(shown):
            width = max(0, int(width_map.get(col, 0)))
            if limit > 1 and x >= limit:
                width = 0
            elif limit > 1:
                width = min(width, limit - x)
            label = self.header_labels.get(col)
            if label and label.winfo_exists():
                if width <= 0:
                    label.place_forget()
                else:
                    inset = min(6, max(0, width // 6))
                    label.place(x=x + inset, y=0, width=max(1, width - inset), height=height)
            handle = self.header_handles.get(col)
            if handle and handle.winfo_exists() and index > 0 and width > 0:
                grip = getattr(handle, "grip_width", DIVIDER_HIT)
                can_resize = self._divider_can_resize(col, shown)
                handle.configure(cursor="sb_h_double_arrow" if can_resize else "")
                hx = x - grip // 2
                if limit > 1:
                    hx = min(max(0, hx), max(0, limit - grip))
                handle.place(x=hx, y=0, width=grip, height=height)
                tk.Misc.lift(handle)
            x += width
        for col, label in self.header_labels.items():
            if col not in shown and label and label.winfo_exists():
                label.place_forget()

    def _start_col_resize(self, event, col):
        shown = [c for c in self._visible_columns() if int((getattr(self, "_applied_widths", None) or {}).get(c, 0)) > 0]
        if not self._divider_can_resize(col, shown):
            return
        applied = getattr(self, "_applied_widths", None) or {}
        visible = set(self._visible_columns())
        self._resize_col = col
        self._resize_x0 = event.x_root
        self._resize_start = {
            c: int(applied.get(c, 0)) if c in visible else 0
            for c in SEEN_COLUMNS
        }

    def _resize_widths(self, start, col, delta):
        """Drag the line on the left of ``col``.

        Moving right shrinks that column. Name stays on the left and takes the
        space. The other columns keep their widths, so a line further right
        stays on that column's edge.
        """
        widths = {c: int(start.get(c, 0)) for c in SEEN_COLUMNS}
        total = sum(widths.values())
        order = [c for c in SEEN_COLUMNS if widths[c] > 0]
        if total <= 0 or col not in order or order.index(col) == 0:
            return widths
        absorber = "name" if widths.get("name", 0) > 0 else order[order.index(col) - 1]
        if absorber == col:
            return widths
        requested = int(start[col]) - int(delta)
        floor = self._fixed_floor(col)
        absorb_floor = self._min_label_width(absorber) if absorber == "name" else self._fixed_floor(absorber)
        others = total - int(start[col]) - int(start[absorber])
        cap = total - others - max(1, absorb_floor)
        if cap < 1:
            cap = 1
        low = floor if floor <= cap else 1
        chosen = int(min(max(requested, low), cap))
        widths[col] = chosen
        widths[absorber] = total - others - chosen
        return widths

    def _do_col_resize(self, event):
        col = getattr(self, "_resize_col", None)
        start = getattr(self, "_resize_start", None)
        if col not in SEEN_COLUMNS or not start:
            return
        delta = int(event.x_root - self._resize_x0)
        widths = self._resize_widths(start, col, delta)
        ordered = [widths[c] for c in SEEN_COLUMNS]
        self._apply_column_widths(ordered)
        self._layout_tree_header(ordered)

    def _end_col_resize(self, _event=None):
        col = getattr(self, "_resize_col", None)
        if not col:
            return
        applied = getattr(self, "_applied_widths", None) or {}
        start = getattr(self, "_resize_start", None) or {}
        if col in ("sound", "seen_count", "last_seen"):
            new_w = int(applied.get(col, 0))
            if abs(new_w - int(start.get(col, new_w))) >= 2:
                self._set_user_fixed(col, new_w)
        self._resize_col = None
        self._resize_start = None
        self._fitted_sig = None
        self._fitted_cheap = None
        self._fit_columns(persist=True)

    def apply_edmc_theme_to_window(self, parent):
        palette = self._edmc_palette()
        if not palette.get("background") and not palette.get("foreground"):
            return
        background = palette.get("background")
        foreground = palette.get("foreground")
        activebackground = palette.get("activebackground") or background
        activeforeground = palette.get("activeforeground") or foreground
        try:
            parent.configure(background=background)
        except Exception:
            pass
        style = ttk.Style(parent)
        style.configure("Beep.Treeview", background=background, fieldbackground=background, foreground=foreground)
        style.map(
            "Beep.Treeview",
            background=[("selected", activebackground)],
            foreground=[("selected", activeforeground)],
        )
        style.configure("Beep.Treeview.Heading", background=background, foreground=foreground)
        if getattr(self, "tree", None) and self.tree.winfo_exists():
            try:
                self.tree.configure(style="Beep.Treeview")
            except Exception:
                pass
        self.apply_edmc_theme(parent)

   
    def _options_owner(self):
        """Window that should own the Options dialog.

        The button lives on the main EDMC row, and Seen Commanders may be
        closed, so the dialog cannot depend on that window.
        """
        for candidate in (getattr(self, "plugin_parent", None), self.window):
            if not candidate:
                continue
            try:
                if not candidate.winfo_exists():
                    continue
                return candidate.winfo_toplevel()
            except Exception:
                continue
        return None

    def open_options_popup(self):
        owner = self._options_owner()
        if owner is None:
            return
        existing = getattr(self, "options_popup", None)
        try:
            if existing is not None and existing.winfo_exists():
                existing.lift()
                existing.focus_set()
                return
        except Exception:
            pass
        popup = tk.Toplevel(owner)
        popup.title("Options")
        popup.transient(owner)

        self.options_popup = popup
        self._apply_topmost(popup)

        self.restore_options_geometry(popup)

    
        self.options_menu(popup)
        self.apply_edmc_theme(popup)
    
        popup.grid_rowconfigure(0, weight=1)
        popup.grid_columnconfigure(0, weight=1)
        
        self.attach_options_resize_listener(popup)
        self._grab_when_viewable(popup)

    def _edmc_palette(self):
        palette = {
            "background": None,
            "foreground": None,
            "activebackground": None,
            "activeforeground": None,
            "disabledforeground": None,
        }
        theme_obj = getattr(edmc_theme, "theme", None) if edmc_theme is not None else None
        current = getattr(theme_obj, "current", None)
        if isinstance(current, dict):
            for key in palette:
                value = current.get(key)
                if value:
                    palette[key] = value
        return palette

    def _walk_widgets(self, widget):
        yield widget
        try:
            children = widget.winfo_children()
        except Exception:
            children = []
        for child in children:
            yield from self._walk_widgets(child)

    def _theme_widget(self, widget, palette):
        if isinstance(widget, ThemedScrollbar):
            widget.apply_theme(palette)
            return
        if isinstance(widget, HeaderDivider):
            widget.apply_theme(palette)
            return
        if isinstance(widget, ttk.Combobox):
            self._theme_combobox(widget, palette)
            return
        try:
            keys = widget.keys()
        except Exception:
            return
        background = palette.get("background")
        foreground = palette.get("foreground")
        config_dict = {}
        is_container = isinstance(widget, (tk.Frame, tk.LabelFrame, tk.Toplevel, tk.Canvas))
        is_text = isinstance(widget, (tk.Label, tk.Button, tk.Checkbutton, tk.Radiobutton, tk.Menubutton))
        is_scale = isinstance(widget, tk.Scale)
        is_scrollbar = isinstance(widget, tk.Scrollbar)

        if background:
            if (is_container or is_text or is_scale or is_scrollbar) and "background" in keys:
                config_dict["background"] = background
            if (is_container or is_text or is_scale or is_scrollbar) and "highlightbackground" in keys:
                config_dict["highlightbackground"] = background
            if (is_text or is_scale) and palette.get("activebackground") and "activebackground" in keys:
                config_dict["activebackground"] = palette["activebackground"]
            if (is_scale or is_scrollbar) and "troughcolor" in keys:
                config_dict["troughcolor"] = background

        if foreground:
            if (is_text or is_scale) and "foreground" in keys:
                config_dict["foreground"] = foreground
            if (is_text or is_scale) and palette.get("activeforeground") and "activeforeground" in keys:
                config_dict["activeforeground"] = palette["activeforeground"]
            if (is_text or is_scale) and palette.get("disabledforeground") and "disabledforeground" in keys:
                config_dict["disabledforeground"] = palette["disabledforeground"]
            if isinstance(widget, tk.LabelFrame) and "foreground" in keys:
                config_dict["foreground"] = foreground

        if isinstance(widget, (tk.Entry, tk.Spinbox)):
            if background:
                config_dict["background"] = background
                if "highlightbackground" in keys:
                    config_dict["highlightbackground"] = background
                if "highlightcolor" in keys:
                    config_dict["highlightcolor"] = foreground or background
                if "insertbackground" in keys:
                    config_dict["insertbackground"] = foreground or background
            if foreground:
                config_dict["foreground"] = foreground
            if "selectbackground" in keys:
                config_dict["selectbackground"] = palette.get("activebackground") or background
            if "selectforeground" in keys:
                config_dict["selectforeground"] = palette.get("activeforeground") or foreground

        if isinstance(widget, tk.Menubutton):
            try:
                menu = widget["menu"]
                menu_keys = menu.keys()
                if background and "background" in menu_keys:
                    menu.configure(background=background)
                    if "activebackground" in menu_keys and palette.get("activebackground"):
                        menu.configure(activebackground=palette["activebackground"])
                if foreground and "foreground" in menu_keys:
                    menu.configure(foreground=foreground)
                    if "activeforeground" in menu_keys and palette.get("activeforeground"):
                        menu.configure(activeforeground=palette["activeforeground"])
                if "bd" in menu_keys:
                    menu.configure(bd=0)
                if "activeborderwidth" in menu_keys:
                    menu.configure(activeborderwidth=0)
            except Exception:
                pass

        check_items = getattr(widget, "_beep_check_items", None)
        if isinstance(widget, tk.Canvas) and check_items:
            try:
                box_id, tick_id = check_items
                if foreground:
                    widget.itemconfigure(box_id, outline=foreground)
                    widget.itemconfigure(tick_id, fill=foreground)
                if background:
                    widget.itemconfigure(box_id, fill=background)
            except Exception:
                pass

        if config_dict:
            try:
                widget.configure(**config_dict)
            except Exception:
                pass

    def _theme_combobox(self, combo, palette):
        background = palette.get("background")
        foreground = palette.get("foreground")
        activebackground = palette.get("activebackground") or background
        activeforeground = palette.get("activeforeground") or foreground
        try:
            style = ttk.Style(combo)
            style.configure(
                "Beep.TCombobox",
                fieldbackground=background,
                background=background,
                foreground=foreground,
                arrowcolor=foreground,
                bordercolor=background,
                lightcolor=background,
                darkcolor=background,
                selectbackground=activebackground,
                selectforeground=activeforeground,
            )
            style.map(
                "Beep.TCombobox",
                fieldbackground=[("readonly", background), ("disabled", background)],
                background=[("readonly", background), ("disabled", background)],
                foreground=[("readonly", foreground), ("disabled", foreground)],
                arrowcolor=[("readonly", foreground), ("disabled", foreground)],
                selectbackground=[("readonly", activebackground)],
                selectforeground=[("readonly", activeforeground)],
            )
            combo.configure(style="Beep.TCombobox")
        except Exception:
            pass
        try:
            combo.option_add("*TCombobox*Listbox.background", background)
            combo.option_add("*TCombobox*Listbox.foreground", foreground)
            combo.option_add("*TCombobox*Listbox.selectBackground", activebackground)
            combo.option_add("*TCombobox*Listbox.selectForeground", activeforeground)
        except Exception:
            pass

    def apply_edmc_theme(self, popup):
        palette = self._edmc_palette()
        if not palette.get("background") and not palette.get("foreground"):
            return
        for widget in self._walk_widgets(popup):
            self._theme_widget(widget, palette)
        self._theme_titlebar(popup)
        try:
            popup.after(150, lambda: self._theme_titlebar(popup))
        except Exception:
            pass

    def _theme_titlebar(self, window):
        if sys.platform != "win32":
            return
        try:
            import ctypes
            handle = window.winfo_id()
            user32 = ctypes.windll.user32
            hwnd = user32.GetAncestor(handle, 2)
            if not hwnd:
                hwnd = handle
            dark = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))
            palette = self._edmc_palette()
            background = palette.get("background")
            foreground = palette.get("foreground")
            if background:
                value = ctypes.c_uint(self._colorref(window, background))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(value), ctypes.sizeof(value))
            if foreground:
                value = ctypes.c_uint(self._colorref(window, foreground))
                ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _colorref(self, window, color):
        try:
            r, g, b = window.winfo_rgb(color)
        except Exception:
            return 0
        return ((b >> 8) & 0xFF) << 16 | ((g >> 8) & 0xFF) << 8 | ((r >> 8) & 0xFF)

    
    def on_header_click(self, field):
        if getattr(self, "sort_field", None) == field:
            self.sort_asc = not self.sort_asc
        else:
            self.sort_field = field
            self.sort_asc = field != "last_seen"
    
        config.set_config("sort_field", self.sort_field)
        config.set_config("sort_asc", self.sort_asc)
        config.save_config()
    
        self.sort_rows(self.sort_field, self.sort_asc)
    
    def add_rows(self, cmdr_ids: list[str]):
        if not hasattr(self, "tree_items"):
            self.tree_items = {}
        if not hasattr(self, "widgets"):
            self.widgets = {}
    
        sort_field = getattr(self, "sort_field", "last_seen")
        sort_asc = getattr(self, "sort_asc", False)
    
        def sort_key(cmdr_id):
            if sort_field == "name":
                return self.name_vars[cmdr_id].get().lower()
            if sort_field == "sound":
                return self.sound_vars[cmdr_id].get().lower()
            if sort_field == "seen_count":
                try:
                    return int(history_inst.seen_data[cmdr_id].get("seen_count", 0))
                except (TypeError, ValueError):
                    return 0
            if sort_field == "last_seen":
                try:
                    ts = datetime.fromisoformat(history_inst.seen_data[cmdr_id].get("last_seen", ""))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    return ts
                except Exception:
                    return datetime(1, 1, 1, tzinfo=timezone.utc)
            return cmdr_id
    
        all_ids = list(history_inst.seen_data.keys())
        sorted_ids = sorted(all_ids, key=sort_key, reverse=not sort_asc)
    
        for cmdr_id in cmdr_ids:
            info = history_inst.seen_data[cmdr_id]
    
            self.name_vars.setdefault(cmdr_id, tk.StringVar(value=info.get("name", "unknown")))
            if cmdr_id not in self.sound_vars:

                sound_key = info.get("sound", "neutral").lower()
                if sound_key not in sound_inst.sound_files:
                    sound_key = "neutral"
                
                self.sound_vars[cmdr_id] = tk.StringVar(value=sound_key)


            self.last_seen_vars.setdefault(cmdr_id, tk.StringVar(value=self.format_last_seen(info.get("last_seen"))))
            self.seen_count_vars.setdefault(cmdr_id, tk.StringVar(value=str(info.get("seen_count", 0))))
    
            index = 0
            for sid in sorted_ids:
                if sid == cmdr_id:
                    break
                if sid in self.tree_items:
                    index += 1
    
            values = (
                self.name_vars[cmdr_id].get(),
                self.sound_vars[cmdr_id].get().capitalize(),
                self.seen_count_vars[cmdr_id].get(),
                self.last_seen_vars[cmdr_id].get()
            )
            item = self.tree.insert("", index, values=values)
            self.tree_items[cmdr_id] = item
            self.widgets[cmdr_id] = {}
            self._remember_cells(cmdr_id, values)
            self._row_order = None
            self._content_dirty = True

    def add_or_update_commander(self, data):
        if not isinstance(data, list):
            data = [data]
    
        new_cmdr_ids = []
    
        for info in data:
            cmdr_id = info["commander_id"]
            cmdr_id_str = str(cmdr_id)
    
            self.name_vars.setdefault(cmdr_id_str, tk.StringVar()).set(info.get("name", "unknown"))
            self.sound_vars.setdefault(cmdr_id_str, tk.StringVar()).set(info.get("sound", sound_inst.neutral).lower())
            self.last_seen_vars.setdefault(cmdr_id_str, tk.StringVar()).set(self.format_last_seen(info["last_seen"]))
            self.seen_count_vars.setdefault(cmdr_id_str, tk.StringVar()).set(str(info.get("seen_count", 0)))
    
            item_id = self.tree_items.get(cmdr_id_str)
            if self.window and self.window.winfo_exists() and hasattr(self, 'tree') and self.tree.winfo_exists():
                if item_id:
                    values = (
                        info.get("name", "unknown"),
                        info.get("sound", sound_inst.neutral).capitalize(),
                        str(info.get("seen_count", 0)),
                        self.format_last_seen(info["last_seen"]),
                    )
                    prev = (getattr(self, "_cell_snapshot", {}) or {}).get(cmdr_id_str)
                    if prev != values:
                        self.tree.set(item_id, "name", values[0])
                        self.tree.set(item_id, "sound", values[1])
                        self.tree.set(item_id, "seen_count", values[2])
                        self.tree.set(item_id, "last_seen", values[3])
                        self._remember_cells(cmdr_id_str, values)
                        if prev is None or prev[1:] != values[1:]:
                            self._content_dirty = True
                            self._fitted_cheap = None
                else:
                    new_cmdr_ids.append(cmdr_id_str)
    
        if new_cmdr_ids and self.window and self.window.winfo_exists() and hasattr(self, 'tree') and self.tree.winfo_exists():
            self.add_rows(new_cmdr_ids)
    
        if self.window and self.window.winfo_exists() and hasattr(self, 'tree') and self.tree.winfo_exists():
            sort_field = config.get_config("sort_field", "last_seen")
            sort_asc = config.get_config("sort_asc", False)
            self.sort_rows(sort_field, sort_asc)
       
    def sort_rows(self, sort_field, sort_asc):
        if not hasattr(self, "tree") or not self.window or not self.window.winfo_exists():
            return        
        
        def sort_key(cmdr_id):
            if sort_field == "name":
                return self.name_vars[cmdr_id].get().lower()
            if sort_field == "sound":
                return self.sound_vars[cmdr_id].get().lower()
            if sort_field == "seen_count":
                try:
                    return int(history_inst.seen_data[cmdr_id].get("seen_count", 0))
                except (TypeError, ValueError):
                    return 0
            if sort_field == "last_seen":
                try:
                    ts = datetime.fromisoformat(history_inst.seen_data[cmdr_id].get("last_seen", ""))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    return ts
                except Exception:
                    return datetime(1, 1, 1, tzinfo=timezone.utc)
            return cmdr_id
    
        sorted_ids = sorted(history_inst.seen_data.keys(), key=sort_key, reverse=not sort_asc)
        order = [cmdr_id for cmdr_id in sorted_ids if self.tree_items.get(cmdr_id)]
        previous = getattr(self, "_row_order", None)
        if previous == order:
            return
        live = list(previous or [])
        for index, cmdr_id in enumerate(order):
            if index < len(live) and live[index] == cmdr_id:
                continue
            item_id = self.tree_items.get(cmdr_id)
            if not item_id:
                continue
            try:
                current = live.index(cmdr_id)
            except ValueError:
                current = -1
            try:
                self.tree.move(item_id, "", index)
            except Exception:
                continue
            if current >= 0:
                live.pop(current)
                live.insert(index, cmdr_id)
            else:
                live.insert(index, cmdr_id)
        self._row_order = order

    def _remember_cells(self, cmdr_id, values):
        snapshot = getattr(self, "_cell_snapshot", None)
        if not isinstance(snapshot, dict):
            snapshot = {}
            self._cell_snapshot = snapshot
        snapshot[cmdr_id] = tuple(values)

    def refresh_gui(self):
        if not self.tree or not self.tree.winfo_exists():
            return
        snapshot = getattr(self, "_cell_snapshot", None)
        if not isinstance(snapshot, dict):
            snapshot = {}
            self._cell_snapshot = snapshot
        changed = False
        for cmdr_id, data in history_inst.seen_data.items():
            item_id = self.tree_items.get(cmdr_id)
            if not item_id:
                continue
            try:
                new_time = self.format_last_seen(data["last_seen"])
                new_count = str(data.get("seen_count", 0))
            except Exception:
                continue
            prev = snapshot.get(cmdr_id)
            if prev and prev[2] == new_count and prev[3] == new_time:
                continue
            try:
                self.tree.set(item_id, "last_seen", new_time)
                self.tree.set(item_id, "seen_count", new_count)
            except Exception:
                continue
            if prev:
                snapshot[cmdr_id] = (prev[0], prev[1], new_count, new_time)
            else:
                snapshot[cmdr_id] = ("", "", new_count, new_time)
            changed = True
        if not changed:
            return
        self._content_dirty = True
        self._fitted_sig = None
        self._fitted_cheap = None
        self._fit_columns()


    def start_auto_refresh(self):
    
        if self._refresh_id:
            try:
                self.window.after_cancel(self._refresh_id)
            except Exception:
                pass
    
        def refresh():
            if not self.window or not self.window.winfo_exists():
                return
    
            self.refresh_gui()
    
            self._refresh_id = self.window.after(
                self.refresh_interval,
                refresh
            )
    
        refresh()

    
        
    def attach_options_resize_listener(self, popup):
        resize_after_id = None
    
        def on_resize(_event):
            nonlocal resize_after_id
            if resize_after_id:
                try:
                    popup.after_cancel(resize_after_id)
                except Exception:
                    pass
            resize_after_id = popup.after(300, lambda: self.save_options_geometry(popup))
    
        popup.bind("<Configure>", on_resize)
    
        def on_window_close():
            self.save_options_geometry(popup)
            popup.destroy()
    
        popup.protocol("WM_DELETE_WINDOW", on_window_close)
    
    
    
    def save_options_geometry(self, popup):
        if not popup or not popup.winfo_exists():
            return
    
        width = max(100, popup.winfo_width())
        height = max(100, popup.winfo_height())
        x = popup.winfo_x()
        y = popup.winfo_y()
    
        config.set_config("options_window_x", x)
        config.set_config("options_window_y", y)
        config.set_config("options_window_width", width)
        config.set_config("options_window_height", height)
        config.save_config()
    
    
    def restore_options_geometry(self, popup):
        width = config.get_config("options_window_width", None)
        height = config.get_config("options_window_height", None)
        x = config.get_config("options_window_x", None)
        y = config.get_config("options_window_y", None)
    

        if width is None or height is None or x is None or y is None:
            screen_width = popup.winfo_screenwidth()
            screen_height = popup.winfo_screenheight()
            width = min(600, screen_width - 100)
            height = min(800, screen_height - 100)
            x = (screen_width - width) // 2   
            y = (screen_height - height) // 2 


        popup.geometry(f"{width}x{height}+{x}+{y}")
        
    def open_sounds_folder(self):
        import subprocess
        import platform
        folder = os.path.join(self.plugin_dir, "sounds")
        os.makedirs(folder, exist_ok=True)
        system = platform.system()
        if system == "Windows":
            os.startfile(folder)
        elif system == "Darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    

    def add_checkbox(self, frame, row, text, var, *, target=None, attr=None, callback=None):
        def on_toggle():
            v = bool(var.get())
            if target and attr:
                setattr(target, attr, v)
            if attr:
                config.set_config(attr, v)
                config.save_config()
            if callback:
                try:
                    callback(v)
                except Exception:
                    pass

        holder = tk.Frame(frame)
        holder.grid(row=row, column=0, columnspan=2, padx=5, pady=(8, 2), sticky="w")

        size = 22
        canvas = tk.Canvas(holder, width=size, height=size,
                           highlightthickness=0, bd=0, takefocus=0)
        canvas.grid(row=0, column=0, sticky="")
        label = tk.Label(holder, text=text)
        label.grid(row=0, column=1, sticky="w", padx=(8, 0))

        box = canvas.create_rectangle(3, 3, size - 3, size - 3, width=2,
                                      outline="#13ec39")
        tick = canvas.create_line(
            size * 0.26, size * 0.52,
            size * 0.44, size * 0.72,
            size * 0.76, size * 0.28,
            width=3, capstyle="round", joinstyle="round", fill="#13ec39",
        )
        canvas._beep_check_items = (box, tick)

        def redraw():
            canvas.itemconfigure(tick, state=("normal" if bool(var.get()) else "hidden"))

        def toggle(_event=None):
            var.set(not bool(var.get()))
            redraw()
            on_toggle()
            return "break"

        for widget in (canvas, label):
            widget.bind("<Button-1>", toggle)

        redraw()
        return row + 1
    

    def add_slider(self, frame, row, label, var, *, attr=None, from_=0, to=100, command=None):
        tk.Label(frame, text=label).grid(row=row, column=0, padx=5, pady=5, sticky="w")
        slider = tk.Scale(frame, from_=from_, to=to, orient=tk.HORIZONTAL, variable=var)
        slider.grid(row=row, column=1, padx=5, pady=5, sticky="we")

        def on_change(*_):
            value = var.get()
            if attr:
                config.set_config(attr, value)
            if command:
                command(value)

        def on_save(_event):
            config.save_config()

        var.trace_add("write", on_change)
        slider.bind("<ButtonRelease-1>", on_save)
        return row + 1
    



    def add_info_box(self, frame, row, text, *, title="Info", columnspan=2):
        info_frame = tk.LabelFrame(frame, text=title, bd=2, relief="groove")

        info_frame.grid(
            row=row,
            column=0,
            columnspan=columnspan,
            padx=5,
            pady=(2, 8),
            sticky="ew"
        )

        label = tk.Label(
            info_frame,
            text=text,
            justify="left",
            anchor="nw"
        )
        label.pack(padx=10, pady=6, fill="x")
    
        def update_wrap(event):
            width = event.width
            if width > 50:
                label.config(wraplength=width - 20)
    
        info_frame.bind("<Configure>", update_wrap)
    
        return row + 1

    def options_menu(self, parent):
        # Use parent directly
        frame_container = parent  # the frame passed from plugin_prefs
        frame_container.grid_rowconfigure(0, weight=1)
        frame_container.grid_columnconfigure(0, weight=1)

        canvas = tk.Canvas(frame_container, highlightthickness=2)
        scrollbar = ThemedScrollbar(frame_container, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        frame = tk.Frame(canvas)
        frame.columnconfigure(1, weight=1)
        canvas_window = canvas.create_window((0,0), window=frame, anchor="nw")
    
        def on_canvas_configure(event):
            canvas.itemconfigure(canvas_window, width=event.width)
        canvas.bind("<Configure>", on_canvas_configure)
    
        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
        frame.bind("<Configure>", on_frame_configure)



        row = 0
    
        row = self.add_slider(
            frame,
            row,
            "Alert Volume (%)",
            tk.IntVar(value=config.get_config("volume", 100)),
            from_=0,
            to=100,
            attr="volume"
        )

        try:
            seen_opacity = int(config.get_config("seen_window_opacity", 100))
        except (TypeError, ValueError):
            seen_opacity = 100
        seen_opacity = max(10, min(100, seen_opacity))

        row = self.add_slider(
            frame,
            row,
            "Seen Commanders opacity (%)",
            tk.IntVar(value=seen_opacity),
            from_=10,
            to=100,
            attr="seen_window_opacity",
            command=self.apply_seen_opacity
        )
                
        actions = tk.Frame(frame)
        actions.grid(row=row, column=0, columnspan=2, padx=5, pady=(2, 6), sticky="w")
        tk.Button(
            actions,
            text="Open Sounds Folder",
            command=self.open_sounds_folder
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            actions,
            text="Play",
            command=lambda: beep_inst.play_sound(sound_inst.neutral)
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            actions,
            text="Reload Sounds",
            command=sound_inst.reload
        ).pack(side="left")
        row += 1

        row = self.add_info_box(
            frame,
            row,
            (
                "To add sounds, place them in the 'sounds' folder inside the plugin. "
                "Only WAV and MP3 files are supported. "
                "The default files 'foe', 'friend', and 'neutral' in the 'default' folder "
                "should not be modified. To override a default sound, simply add a file "
                "with the same name in the main 'sounds' folder (ext does not matter). "
                "The plugin automatically loads the newest file for each name. "
                "Use Reload Sounds to update the list immediately."
            ),
            title="Sound Files"
        )

        row = self.add_info_box(
            frame,
            row,
            (
                "Reload Sounds re-scans the sounds folder and updates the available "
                "files, so new sounds can be added without restarting EDMC."
            ),
            title="Reload Sounds"
        )

        row = self.add_slider(
            frame,
            row,
            "Multiple sounds",
            tk.IntVar(value=config.get_config("sounds", 1)),
            from_=1,
            to=10,
            attr="sounds"
        )

        row = self.add_info_box(
            frame,
            row,
            (
                "If there are multiple CMDRS, it will play each one with a 200 ms delay."
            ),
            title="Multiple sounds"
        )

        row = self.add_checkbox(
            frame,
            row,
            "Beep on leaving",
            tk.BooleanVar(value=config.get_config("beep_on_leave", False)),
            attr="beep_on_leave"
        )

        row = self.add_info_box(
            frame,
            row,
            (
                "When enabled, if a CMDR or yourself leaves your instance, a sound will play. "
                "For example, if you are in Supercruise and someone leaves, the sound will play."
            ),
            title="Beep on leaving"
        )

        row = self.add_checkbox(
            frame,
            row,
            "Keep Seen Commanders on top of the game",
            tk.BooleanVar(value=bool(config.get_config("stay_on_top", False))),
            attr="stay_on_top",
            callback=self._on_topmost_toggle
        )

        row = self.add_info_box(
            frame,
            row,
            (
                "Keeps the Seen Commanders window above Elite Dangerous and other "
                "windows. Only works when the game runs in windowed or borderless "
                "mode; exclusive fullscreen always paints over everything."
            ),
            title="Stay on top"
        )
    
        row = self.add_checkbox(
            frame,
            row,
            "Notify on Wing Member encounters",
            tk.BooleanVar(value=config.get_config("wing_notify", False)),
            attr="wing_notify"
        )
    
        row = self.add_info_box(
            frame,
            row,
            (
                "When disabled, no sounds will play for wing members. "
                "This setting only affects wing-related alerts."
            ),
            title="Wing Notify"
        )
    
        row = self.add_checkbox(
            frame,
            row,
            "Announce connections in Modern Overlay",
            tk.BooleanVar(value=config.get_config("overlay_announce", False)),
            attr="overlay_announce"
        )

        row = self.add_info_box(
            frame,
            row,
            (
                "When enabled, a \"Commander <name> has jumped in\" message is shown at the center "
                "of the screen (raised slightly above center) through the EDMC Modern "
                "Overlay plugin whenever a "
                "new commander enters your instance. The name is taken from the Seen "
                "Commanders list (rename a commander there to change what is shown). "
                "Requires the EDMC Modern Overlay plugin to be installed and running."
            ),
            title="Modern Overlay announcements"
        )
    
        row = self.add_info_box(
            frame,
            row,
            (
                "If EDMC is started while the game is already running, commanders who are "
                "already in your instance may trigger sounds in reverse — for example, "
                "alerts may play when they leave instead of when they arrive. \n\n"
                "Going to any EMPTY instance (Normal Space, Supercruise, or Hyperspace) and "
                "waiting about 5 seconds will reset this."
            ),
            title="General Notes"
        )

        def _on_mousewheel(event):
            num = getattr(event, "num", None)
            if num == 4:
                canvas.yview_scroll(-1, "units")
            elif num == 5:
                canvas.yview_scroll(1, "units")
            else:
                delta = getattr(event, "delta", 0) or 0
                if delta:
                    canvas.yview_scroll(int(-delta / 120), "units")
            return "break"

        for _w in self._walk_widgets(frame_container):
            _w.bind("<MouseWheel>", _on_mousewheel, add="+")
            _w.bind("<Button-4>", _on_mousewheel, add="+")
            _w.bind("<Button-5>", _on_mousewheel, add="+")

        return frame_container

    
    
gui_inst = SeenCommandersGUI()
