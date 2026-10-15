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
        self.scrollbar = None
        self.options_button = None
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
        # Leave this frame unmanaged. EDMC grids the widget plugin_app returns.
        # Packing it here, or raising while it is built, makes EDMC drop the row.
        container = tk.Frame(parent)

        btn_show = tk.Button(
            container,
            text="Beep Beep",
            command=lambda: self.open(parent)
        )
        btn_show.pack(side="left", padx=(5, 0), pady=5)

        mute_btn = tk.Button(container, width=3)
        mute_btn.pack(side="left", padx=(4, 5), pady=5)

        def update_mute_button(muted):
            mute_btn.config(
                text="🔇" if muted else "🔊",
                relief="sunken" if muted else "raised"
            )

        def toggle_mute():
            new_value = not config.get_config("mute", False)
            config.set_config("mute", new_value)
            config.save_config()
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

        if self.window and self.window.winfo_exists():
            self.window.lift()
            self.build_ui(self.window)
            return
    
        self.window = tk.Toplevel(parent)
        self.window.title("Seen Commanders")
    
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(100, 100)  
    
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

    def _enforce_column_limits(self, _event=None):
        self._fit_columns()

    def _apply_initial_col_widths(self, parent):
        try:
            width = parent.winfo_width()
        except Exception:
            width = 1
        if width <= 1:
            width = getattr(self, "_pending_width", None)
        if not width or width <= 1:
            try:
                width = int(config.get_config("seen_window_width", 800))
            except (TypeError, ValueError):
                width = 800
        if width <= 1:
            width = 800
        saved = config.get_config("seen_col_widths", None)
        if not isinstance(saved, dict):
            saved = {}
        try:
            name_w = int(saved.get("name"))
        except (TypeError, ValueError):
            name_w = int(0.40 * width)
        try:
            sound_w = int(saved.get("sound"))
        except (TypeError, ValueError):
            sound_w = int(0.25 * width)
        try:
            count_w = int(saved.get("seen_count"))
        except (TypeError, ValueError):
            count_w = int(0.15 * width)
        name_w = max(MIN_COL_WIDTH, name_w)
        sound_w = max(MIN_COL_WIDTH, sound_w)
        count_w = max(MIN_COL_WIDTH, count_w)
        last_w = max(MIN_COL_WIDTH, width - name_w - sound_w - count_w)
        for col, value in (("name", name_w), ("sound", sound_w), ("seen_count", count_w), ("last_seen", last_w)):
            try:
                self.tree.column(col, width=value)
            except Exception:
                pass
        self._layout_tree_header()

    def _fit_columns(self, persist=False):
        if not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        total_width = self.tree.winfo_width()
        if total_width <= 1:
            return
        cols = ("name", "sound", "seen_count", "last_seen")
        widths = []
        for col in cols:
            try:
                widths.append(max(MIN_COL_WIDTH, int(self.tree.column(col, "width"))))
            except Exception:
                widths.append(MIN_COL_WIDTH)
        total = sum(widths)
        if total > total_width:
            overflow = total - total_width
            for i in range(len(cols) - 1, -1, -1):
                if overflow <= 0:
                    break
                take = min(widths[i] - MIN_COL_WIDTH, overflow)
                widths[i] -= take
                overflow -= take
        elif total < total_width:
            widths[-1] += total_width - total
        for col, value in zip(cols, widths):
            try:
                self.tree.column(col, width=value)
            except Exception:
                pass
        if getattr(self, "header_frame", None) and self.header_frame.winfo_exists():
            self.header_frame.after_idle(self._layout_tree_header)
        if persist:
            self._save_col_widths()

    def _save_col_widths(self):
        if not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        try:
            name_w = int(self.tree.column("name", "width"))
            sound_w = int(self.tree.column("sound", "width"))
            count_w = int(self.tree.column("seen_count", "width"))
        except Exception:
            return
        config.set_config("seen_col_widths", {"name": name_w, "sound": sound_w, "seen_count": count_w})
        config.save_config()

    def _on_tree_configure(self, _event=None):
        if not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        after_id = getattr(self, "_fit_after_id", None)
        if after_id:
            try:
                self.tree.after_cancel(after_id)
            except Exception:
                pass
        self._fit_after_id = self.tree.after(120, self._fit_columns)
        
        
    def attach_resize_listener(self):
        if not self.window or not self.window.winfo_exists():
            return
    
        self.resize_after_id = None 
    
        def on_resize(_event):    
            if not self.window or not self.window.winfo_exists():
                return
        
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
        popup.geometry(f"+{event.x_root}+{event.y_root}")
        popup.grab_set()

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

        popup.transient(self.window)
        self.apply_edmc_theme(popup)

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

            self.header_frame = tk.Frame(parent, height=row_height)
            self.header_frame.grid(row=0, column=0, columnspan=3, sticky="ew")
            self.header_frame.grid_propagate(False)
            self.header_labels = {}
            self.header_handles = {}
            _cols = (("name", "Name"), ("sound", "Sound"), ("seen_count", "Seen"), ("last_seen", "Last Encounter"))
            for _i, (_col, _label) in enumerate(_cols):
                _lbl = tk.Label(self.header_frame, text=_label, anchor="w", cursor="hand2")
                _lbl.bind("<Button-1>", lambda _e, c=_col: self.on_header_click(c))
                self.header_labels[_col] = _lbl
                if _i < len(_cols) - 1:
                    _handle = tk.Frame(self.header_frame, width=6, cursor="sb_h_double_arrow")
                    _handle.bind("<ButtonPress-1>", lambda _e, c=_col: self._start_col_resize(_e, c))
                    _handle.bind("<B1-Motion>", self._do_col_resize)
                    _handle.bind("<ButtonRelease-1>", self._end_col_resize)
                    self.header_handles[_col] = _handle

            self.tree.column("name", anchor="w")
            self.tree.column("sound", anchor="w")
            self.tree.column("seen_count", anchor="w")
            self.tree.column("last_seen", anchor="w")

            for _col in ("name", "sound", "seen_count", "last_seen"):
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
        
        self.options_button = tk.Button(
            parent,
            text="Options",
            command=self.open_options_popup
        )
        self.options_button.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(5, 5))
        
        parent.grid_rowconfigure(2, weight=0)

        self.apply_edmc_theme_to_window(parent)

    def _layout_tree_header(self, _event=None):
        tree = getattr(self, "tree", None)
        header = getattr(self, "header_frame", None)
        if not tree or not tree.winfo_exists():
            return
        if not header or not header.winfo_exists():
            return
        height = header.winfo_height()
        if height <= 1:
            return
        x = 0
        for col in ("name", "sound", "seen_count", "last_seen"):
            try:
                width = tree.column(col, "width")
            except Exception:
                width = 100
            label = self.header_labels.get(col)
            if label and label.winfo_exists():
                label.place(x=x, y=0, width=width, height=height)
            handle = self.header_handles.get(col)
            if handle and handle.winfo_exists():
                handle.place(x=x + width - 3, y=0, width=6, height=height)
            x += width

    def _start_col_resize(self, event, col):
        if not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        cols = ("name", "sound", "seen_count", "last_seen")
        if col not in cols or cols.index(col) >= len(cols) - 1:
            return
        self._resize_col = col
        self._resize_next_col = cols[cols.index(col) + 1]
        self._resize_x0 = event.x_root
        try:
            self._resize_w0 = self.tree.column(col, "width")
            self._resize_next_w0 = self.tree.column(self._resize_next_col, "width")
        except Exception:
            self._resize_w0 = 100
            self._resize_next_w0 = 100

    def _do_col_resize(self, event):
        col = getattr(self, "_resize_col", None)
        next_col = getattr(self, "_resize_next_col", None)
        if not col or not next_col or not getattr(self, "tree", None) or not self.tree.winfo_exists():
            return
        combined = self._resize_w0 + self._resize_next_w0
        new_width = self._resize_w0 + (event.x_root - self._resize_x0)
        new_width = max(MIN_COL_WIDTH, min(new_width, combined - MIN_COL_WIDTH))
        try:
            self.tree.column(col, width=int(new_width))
            self.tree.column(next_col, width=int(combined - new_width))
        except Exception:
            return
        self._layout_tree_header()

    def _end_col_resize(self, _event=None):
        self._resize_col = None
        self._resize_next_col = None
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

   
    def open_options_popup(self):
        popup = tk.Toplevel(self.window)
        popup.title("Options")
        popup.transient(self.window)
        popup.grab_set()
    
    
        self.restore_options_geometry(popup)

    
        self.options_menu(popup)
        self.apply_edmc_theme(popup)
    
        popup.grid_rowconfigure(0, weight=1)
        popup.grid_columnconfigure(0, weight=1)
        
        self.attach_options_resize_listener(popup)

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
    
            item = self.tree.insert("", index, values=(
                self.name_vars[cmdr_id].get(),
                self.sound_vars[cmdr_id].get().capitalize(),
                self.seen_count_vars[cmdr_id].get(),
                self.last_seen_vars[cmdr_id].get()
            ))
            self.tree_items[cmdr_id] = item
            self.widgets[cmdr_id] = {}

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
                    self.tree.set(item_id, "last_seen", self.format_last_seen(info["last_seen"]))
                    self.tree.set(item_id, "name", info.get("name", "unknown"))
                    self.tree.set(item_id, "sound", info.get("sound", sound_inst.neutral).capitalize())
                    self.tree.set(item_id, "seen_count", str(info.get("seen_count", 0)))
                    
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
    
        for index, cmdr_id in enumerate(sorted_ids):
            item_id = self.tree_items.get(cmdr_id)
            if not item_id:
                continue
            current_index = self.tree.index(item_id)
            if current_index != index:
                self.tree.move(item_id, "", index)
    
    def refresh_gui(self):
        if not self.tree or not self.tree.winfo_exists():
            return
    
        for cmdr_id, data in history_inst.seen_data.items():
            item_id = self.tree_items.get(cmdr_id)
    
            if not item_id:
                continue
    
            try:
                new_time = self.format_last_seen(data["last_seen"])
                self.tree.set(item_id, "last_seen", new_time)
                self.tree.set(item_id, "seen_count", str(data.get("seen_count", 0)))
            except Exception:
                pass


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
    

    def add_checkbox(self, frame, row, text, var, *, target=None, attr=None):
        def on_toggle():
            v = bool(var.get())
            if target and attr:
                setattr(target, attr, v)
            if attr:
                config.set_config(attr, v)
                config.save_config()

        holder = tk.Frame(frame)
        holder.grid(row=row, column=0, columnspan=2, padx=5, pady=6, sticky="w")

        size = 22
        canvas = tk.Canvas(holder, width=size, height=size,
                           highlightthickness=0, bd=0, takefocus=0)
        canvas.pack(side="left")
        label = tk.Label(holder, text=text)
        label.pack(side="left", padx=(10, 0))

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
    

    def add_slider(self, frame, row, label, var, *, attr=None, from_=0, to=100):
        tk.Label(frame, text=label).grid(row=row, column=0, padx=5, pady=5, sticky="w")
        slider = tk.Scale(frame, from_=from_, to=to, orient=tk.HORIZONTAL, variable=var)
        slider.grid(row=row, column=1, padx=5, pady=5, sticky="we")

        def on_change(*_):
            if attr:
                config.set_config(attr, var.get())

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
        frame.columnconfigure(0, weight=1)
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
                
        tk.Button(
            frame,
            text="Open Sounds Folder",
            command=self.open_sounds_folder
        ).grid(row=row, column=0, padx=5, sticky="w")

        tk.Button(
            frame,
            text="▶ Play",
            command=lambda: beep_inst.play_sound(sound_inst.neutral)
        ).grid(row=row, column=1, padx=5, sticky="e")
        
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
                "Use the 'Reload Sounds' button to update the list immediately."
            ),
            title="Sound Files"
        )

        tk.Button(
            frame,
            text="Reload sounds",
            command=sound_inst.reload
        ).grid(row=row, column=1, padx=5, sticky="e")
        
        row += 1
    
        row = self.add_info_box(
            frame,
            row,
            (
                "Click the 'Reload Sounds' button to re-scan the 'sounds' folder "
                "and update available sound files. This allows you to add new sounds "
                "or replace existing ones without restarting EDMC."
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
        
        row += 1
        
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
                "waiting ~5 seconds, or simply , will reset this."
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
