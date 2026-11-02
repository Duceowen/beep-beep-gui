from commander_history import history_inst, read_session_state
from beep_beep import beep_inst
from overlay_announcer import announce_connected, clear_connected
from location import location
from gui import gui_inst
from logutil import log
from version import __version__  # noqa: F401
import myNotebook as nb  # noqa

def plugin_start3(plugin_dir):
    log.info("beep_beep plugin starting (%s)", plugin_dir)
    # EDMC does not replay old journal lines, so take the wing state from the
    # journal. Otherwise a plugin started mid-wing treats wing members as normal.
    wing, session_start = read_session_state()
    location.set_wing(bool(wing))
    history_inst.set_session_start(session_start)
    log.info("Startup from journal: wing=%s session_start=%s", wing, session_start)
    history_inst.load_seen_commanders()
    history_inst.subscribe_sound(beep_inst.handle_event)       
    history_inst.subscribe_gui(gui_inst.add_or_update_commander)
    history_inst.subscribe_overlay(announce_connected)
    history_inst.aggregated_commanders_load()
    history_inst.start_kill_backfill()
    history_inst.start_worker()
    return "Beep Beep"


def plugin_app(parent):
    # EDMC grids this widget into the main window. The parent frame is
    # EDMC's own row; return the button bar that belongs inside it.
    return gui_inst.build_plugin_button(parent)


def journal_entry(cmdrname: str, is_beta: bool, system: str, station: str, entry: dict, state: dict) -> None:
    event = entry.get("event")
    on_foot = bool(state.get("OnFoot"))
    if event in ("Shutdown", "ShutDown"):
        on_foot = False
    elif event == "Music" and entry.get("MusicTrack") == "MainMenu":
        on_foot = False
    gui_inst.set_on_foot(on_foot)
    if event in ("FSDJump", "CarrierJump"):
        history_inst.note_jump(entry.get("timestamp"))
    if event in ("StartUp", "LoadGame") or (event == "Music" and entry.get("MusicTrack") == "MainMenu"):
        # Leaving to the main menu or loading in again ends the instance.
        history_inst.clear_presence()
    if event in ("StartJump", "CarrierJump", "Shutdown", "ShutDown"):
        # Connection announcements belong to the system you are leaving.
        clear_connected()
    history_inst.note_journal_name(entry)
    system = entry.get("StarSystem", system)
    
    if event in ("StartUp", "LoadGame", "Resurrected", "Died"):
        location.set(0, system)

    elif event == "SupercruiseEntry":        
        location.set(1, system)
        history_inst.trigger()

    elif event in ("SupercruiseExit", "Location", "CarrierJump"): 
        location.set(0, system, event)

        history_inst.trigger()

    elif event == "StartJump":
        location.set(1, entry.get("StarSystem", system))
        location.jump()
        
    elif event == "FSDJump":
        history_inst.trigger()
        
    elif event in ("WingJoin", "WingAdd"):
        location.set_wing(True)
        location.wing_changed()
    
    elif event == "WingLeave":
        location.set_wing(False)
        location.wing_changed()
    
    elif event == "Interdiction":
        location.interdiction()
            
    elif event == "PVPKill":
        victim = entry.get("Victim")        
        location.pvp_kill(victim)
        history_inst.record_kill(victim)
        

def plugin_prefs(parent, cmdr, is_beta):
    frame_container = nb.Frame(parent)
    gui_inst.options_menu(frame_container)
    return frame_container

def plugin_stop():
    clear_connected()
    beep_inst.stop_playback()
    history_inst.stop_worker()
    log.info("beep_beep plugin stopped!")
