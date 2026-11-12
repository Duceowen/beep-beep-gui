from commander_history import history_inst, read_session_state, read_current_system, read_in_supercruise, location
from beep_beep import beep_inst
from overlay_announcer import announce_connected, clear_connected
from gui import gui_inst
from logutil import log
from version import __version__  # noqa: F401
from i18n import tl
import myNotebook as nb  # noqa

def plugin_start3(plugin_dir):
    log.info("beep_beep plugin starting (%s)", plugin_dir)
    # EDMC does not replay old journal lines, so take the wing state from the
    # journal. Otherwise a plugin started mid-wing treats wing members as normal.
    wing, session_start = read_session_state()
    location.set_wing(bool(wing))
    history_inst.set_session_start(session_start)
    history_inst.restore_instance_seen(read_current_system(), read_in_supercruise())
    log.info("Startup from journal: wing=%s session_start=%s", wing, session_start)
    history_inst.backup_seen_commanders()
    history_inst.load_seen_commanders()
    history_inst.subscribe_sound(beep_inst.handle_event)       
    history_inst.subscribe_gui(gui_inst.add_or_update_commander)
    history_inst.subscribe_overlay(announce_connected)
    history_inst.aggregated_commanders_load()
    history_inst.start_kill_backfill()
    history_inst.start_worker()
    return tl("Beep Beep")


def plugin_app(parent):
    # EDMC grids this widget into the main window. The parent frame is
    # EDMC's own row; return the button bar that belongs inside it.
    return gui_inst.build_plugin_button(parent)


def journal_entry(cmdrname: str, is_beta: bool, system: str, station: str, entry: dict, state: dict) -> None:
    try:
        history_inst.recorder.record_journal(entry)
    except Exception:
        pass
    event = entry.get("event")
    if isinstance(event, str) and event:
        history_inst.note_last_journal(event, entry.get("timestamp"))
    if event in ("SupercruiseEntry", "SupercruiseExit", "FSDJump", "Location", "PVPKill"):
        log.info("TIMING journal %s ts=%s recv=%.3f", event, entry.get("timestamp"), __import__("time").time())
    on_foot = bool(state.get("OnFoot"))
    if event in ("Shutdown", "ShutDown"):
        on_foot = False
    elif event == "Music" and entry.get("MusicTrack") == "MainMenu":
        on_foot = False
    gui_inst.set_on_foot(on_foot)
    if event in ("FSDJump", "CarrierJump"):
        history_inst.note_jump(entry.get("timestamp"))
    if event in ("StartUp", "LoadGame", "Shutdown", "ShutDown") or (
        event == "Music" and entry.get("MusicTrack") == "MainMenu"
    ):
        # Main menu, load, quit, or Operations lobby (EDMC often sees that as ShutDown):
        # the next history list is a new instance. A same-session EDMC restart stays quiet.
        history_inst.clear_presence(entry.get("timestamp"))
    if event in ("StartJump", "CarrierJump", "Shutdown", "ShutDown"):
        # Connection announcements belong to the system you are leaving.
        clear_connected()
    history_inst.note_journal_name(entry)
    system = entry.get("StarSystem", system)
    
    if event in ("StartUp", "LoadGame", "Resurrect", "Resurrected", "Died"):
        # Journal writes "Resurrect"; older code looked for "Resurrected".
        resume = event in ("StartUp", "LoadGame") and history_inst.take_resume_supercruise()
        location.set(1 if resume else 0, system)
        if event == "Died":
            # Being killed takes you out of the instance. Clear it now so the Now tag drops
            # at once instead of waiting for the quiet timeout.
            location.leave_instance(record_departures=False, after_death=True)
            history_inst.forget_instance()
            history_inst.note_instance_reset()

    elif event == "SupercruiseEntry":        
        location.set(1, system)
        location.leave_instance()
        history_inst.remember_leave_snapshot()
        history_inst.note_instance_reset()
        history_inst.trigger()

    elif event in ("SupercruiseExit", "Location", "CarrierJump"):
        if event == "SupercruiseExit":
            # Record anyone who arrived since the last worker tick while still in supercruise.
            history_inst.record_presence_now()
        if event == "Location":
            new_system = entry.get("StarSystem", system) or system
            # Respawn / detention / teleport: state often stays normal-space, so Location.set
            # would not clear the instance. Drop stale people and open load_batch.
            if location.system and new_system and new_system != location.system:
                history_inst.clear_presence(entry.get("timestamp"))
        location.set(0, system, event)
        if event == "SupercruiseExit":
            history_inst.drop_supercruise_arrivals()

        history_inst.trigger()

    elif event == "StartJump":
        location.set(1, entry.get("StarSystem", system))
        location.jump()
        history_inst.note_transition_pending(entry.get("JumpType"))
        
    elif event == "FSDJump":
        # Hyperspace arrives in supercruise. There is no SupercruiseEntry.
        location.set(1, system)
        history_inst.note_hyperspace_arrival()
        history_inst.trigger()
        
    elif event in ("WingJoin", "WingAdd"):
        location.set_wing(True)
        location.wing_changed()
        names = []
        if event == "WingJoin":
            others = entry.get("Others")
            if isinstance(others, list):
                names.extend(n for n in others if isinstance(n, str))
        name = entry.get("Name")
        if isinstance(name, str) and name.strip():
            names.append(name)
        if names:
            location.add_wing_roster(names)
            history_inst.note_wing_mates(names)

    elif event == "WingInvite":
        # Invite accepted shortly after lists the invitee; silence their CH listing.
        name = entry.get("Name")
        if isinstance(name, str) and name.strip():
            history_inst.note_wing_mates([name])

    elif event == "WingLeave":
        location.set_wing(False)
        location.wing_changed()
        location.clear_wing_join_names()
    
    elif event == "Interdiction":
        # Only a successful interdiction keeps its target through the drop-in.
        if entry.get("Success") is False and entry.get("IsPlayer") is True:
            history_inst.note_escape(entry.get("Interdicted"))
        elif entry.get("IsPlayer") is True:
            location.interdiction(entry.get("Interdicted"))
            # Outgoing Success does not say whether they submitted.
            location.interdiction_cooldown = 60.0
        else:
            location.interdiction(entry.get("Interdicted") if entry.get("Success") is not False else None)

    elif event == "Interdicted":
        # The other commander pulled you out of supercruise. They drop in with you.
        # Escaping is EscapeInterdiction, which does not land you in their instance.
        if entry.get("IsPlayer") is True:
            location.interdiction(entry.get("Interdictor"))
            location.interdiction_cooldown = 10.0 if entry.get("Submitted") is True else 60.0

    elif event == "ShipTargeted":
        # A locked player ship is in this instance, in normal space or supercruise.
        # Announce on this line. Do not wait for CommanderHistory.
        history_inst.note_ship_present(entry)
        history_inst.note_player_targets(entry)

    elif event == "ReceiveText":
        channel = str(entry.get("Channel") or "").strip().lower()
        if channel == "local":
            who = entry.get("From_Localised") or entry.get("From")
            # Chat is presence and name evidence only. It never beeps.
            history_inst.note_target_silent(who)
            
    elif event == "PVPKill":
        victim = entry.get("Victim")        
        location.pvp_kill(victim)
        history_inst.record_kill(victim)
        history_inst.note_kill(victim)

    # A history file that changed before this line is applied now, not on the next poll.
    history_inst.poll_history_now()

def plugin_prefs(parent, cmdr, is_beta):
    frame_container = nb.Frame(parent)
    gui_inst.options_menu(frame_container)
    return frame_container

def prefs_changed(cmdr, is_beta):
    # EDMC calls this after the commander applies Settings, including language.
    gui_inst.refresh_language()


def plugin_stop():
    clear_connected()
    beep_inst.stop_playback()
    history_inst.stop_worker()
    log.info("beep_beep plugin stopped!")
