from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk
import tkinter.font as tkFont
import os
import myNotebook as nb # noqa
from beep_beep_config import config
from commander_history import history_inst
from beep_beep import beep_inst
from sound_loader import sound_inst

class SeenCommandersGUI:
    def __init__(self):
        self.plugin_dir = os.path.dirname(__file__)
        self.tree_items = {} 
        self.tree = None 
        self.name_vars = {}
        self.last_seen_vars = {}
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


    def format_time_ago(self, ts_iso: str) -> str:
        try:
            ts = datetime.fromisoformat(ts_iso)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = now - ts

            seconds = int(delta.total_seconds())
            minutes, sec = divmod(seconds, 60)
            hours, minutes = divmod(minutes, 60)
            days, hours = divmod(hours, 24)

            if days > 0:
                return f"{days} d {hours} h ago"
            if hours > 0:
                return f"{hours} h {minutes} min ago"
            if minutes > 0:
                return f"{minutes} min {sec} s ago"
            return f"{seconds} s ago"
        
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
        # Capture the theme color. The Windows system button color is not valid
        # on Linux Tk, and that error aborts plugin_app before the row is shown.
        idle_bg = mute_btn.cget("bg")
        idle_fg = mute_btn.cget("fg")

        def update_mute_button(muted):
            mute_btn.config(
                text="🔇" if muted else "🔊",
                bg="lightgrey" if muted else idle_bg,
                fg="black" if muted else idle_fg,
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
        
        
        def enforce_column_limits(_event):
            total_width = self.tree.winfo_width()

            limits = {
                "name": (int(0.2 * total_width), int(0.5 * total_width)),
                "sound": (int(0.15 * total_width), int(0.4 * total_width)),
                "last_seen": (int(0.1 * total_width), int(0.3 * total_width))
            }
            
            for col, (min_w, max_w) in limits.items():
                width = self.tree.column(col, "width")
                if width < min_w:
                    self.tree.column(col, width=min_w)
                elif width > max_w:
                    self.tree.column(col, width=max_w)
        
        self.tree.bind("<ButtonRelease-1>", enforce_column_limits)         
        enforce_column_limits(None)
        
        
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
        
        combo = ttk.Combobox(
            popup,
            textvariable=sound_var,
            values=[s.capitalize() for s in sound_inst.sound_files],
            state="readonly"
        )
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
    
        def on_name_enter(_event):
            combo.focus_set()
            combo.event_generate('<Down>')
            return "break"
    
        name_entry.bind("<Return>", on_name_enter)
    
        def on_combo_enter(_event):
            save_popup_changes()
            return "break"
    
        combo.bind("<Return>", on_combo_enter)

    def build_ui(self, parent):
        sort_field = config.get_config("sort_field", "last_seen")
        sort_asc = config.get_config("sort_asc", False)
    
        if not hasattr(self, "tree") or not self.tree or not self.tree.winfo_exists() or self.tree.master != parent:
            for attr in ["scrollbar", "tree"]:
                if hasattr(self, attr) and getattr(self, attr) and getattr(self, attr).winfo_exists():
                    getattr(self, attr).destroy()
                    
            style = ttk.Style(parent)
            default_font = tkFont.nametofont("TkDefaultFont")
            row_height = default_font.metrics("linespace") + 4
            style.configure("Treeview", rowheight=row_height)
    
            self.tree = ttk.Treeview(parent, columns=("name", "sound", "last_seen"), show="headings")
            self.tree.grid(row=1, column=0, columnspan=3, sticky="nsew")
            
            self.tree.heading("name", text="Name", anchor="w", command=lambda: self.on_header_click("name"))
            self.tree.heading("sound", text="Sound", anchor="w", command=lambda: self.on_header_click("sound"))
            self.tree.heading("last_seen", text="Last Seen", anchor="w", command=lambda: self.on_header_click("last_seen"))
            
            self.tree.column("name", anchor="w")
            self.tree.column("sound", anchor="w")
            self.tree.column("last_seen", anchor="w")
            
            self.scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self.tree.yview)
            self.tree.configure(yscroll=self.scrollbar.set)
            self.scrollbar.grid(row=1, column=3, sticky="ns")
    
            parent.grid_rowconfigure(1, weight=1)
            for col in range(3):
                parent.grid_columnconfigure(col, weight=1)
    
            self.widgets.clear()
            self.sound_vars.clear()
            self.name_vars.clear()
            self.last_seen_vars.clear()
            self.tree_items = {}

        if history_inst.seen_data:
            new_ids = [cmdr_id for cmdr_id in history_inst.seen_data if cmdr_id not in self.tree_items]
            if new_ids:
                self.add_rows(new_ids)
    
        self.sort_rows(sort_field, sort_asc)
        
        self.options_button = ttk.Button(
            parent,
            text="Options",
            command=self.open_options_popup
        )
        self.options_button.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(5, 5))
        
        parent.grid_rowconfigure(2, weight=0)        
       
    def open_options_popup(self):
        popup = tk.Toplevel(self.window)
        popup.title("Options")
        popup.transient(self.window)
        popup.grab_set()
    
    
        self.restore_options_geometry(popup)

    
        options_frame = self.options_menu(popup)
    
        options_frame.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
    
        popup.grid_rowconfigure(0, weight=1)
        popup.grid_columnconfigure(0, weight=1)
        
        self.attach_options_resize_listener(popup)

    
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


            self.last_seen_vars.setdefault(cmdr_id, tk.StringVar(value=self.format_time_ago(info.get("last_seen"))))
    
            index = 0
            for sid in sorted_ids:
                if sid == cmdr_id:
                    break
                if sid in self.tree_items:
                    index += 1
    
            item = self.tree.insert("", index, values=(
                self.name_vars[cmdr_id].get(),
                self.sound_vars[cmdr_id].get().capitalize(),
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
            self.last_seen_vars.setdefault(cmdr_id_str, tk.StringVar()).set(self.format_time_ago(info["last_seen"]))
    
            item_id = self.tree_items.get(cmdr_id_str)
            if self.window and self.window.winfo_exists() and hasattr(self, 'tree') and self.tree.winfo_exists():
                if item_id:
                    self.tree.set(item_id, "last_seen", self.format_time_ago(info["last_seen"]))
                    self.tree.set(item_id, "name", info.get("name", "unknown"))
                    self.tree.set(item_id, "sound", info.get("sound", sound_inst.neutral).capitalize())
                    
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
                new_time = self.format_time_ago(data["last_seen"])
                self.tree.set(item_id, "last_seen", new_time)
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
            width = screen_width * 75 // 100
            height = screen_height * 75 // 100
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
            v = var.get()
            if target and attr:
                setattr(target, attr, v)
            if attr:
                config.set_config(attr, v)
                config.save_config()
    
        chk = tk.Checkbutton(frame, text=text, variable=var, command=on_toggle)
        chk.grid(row=row, column=0, padx=5, pady=5, sticky="w")
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
        frame_container = parent  # this is the nb.Frame passed from plugin_prefs
        frame_container.grid_rowconfigure(0, weight=1)
        frame_container.grid_columnconfigure(0, weight=1)
    
        canvas = tk.Canvas(frame_container)
        scrollbar = tk.Scrollbar(frame_container, orient="vertical", command=canvas.yview)
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

        return frame_container

    
    
gui_inst = SeenCommandersGUI()
