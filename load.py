from commander_history import (
    history_inst,
    read_session_state,
    read_current_system,
    read_in_supercruise,
    location,
)
from beep_beep import beep_inst
from overlay_announcer import (
    announce_connected,
    clear_connected,
    start_cooldown as start_overlay_cooldown,
    stop_cooldown as stop_overlay_cooldown,
    announce_pvp_kill,
    clear_pvp_kills,
    ensure_pvp_group,
    announce_pvp_whois,
    clear_pvp_whois,
    ensure_whois_group,
)
import cooldown_timer
import pvp_whois
from gui import gui_inst
from logutil import log
import time
from version import __version__  # noqa: F401
from i18n import tl
from beep_beep_config import config
import myNotebook as nb  # noqa


_cooldown_context = {
    "name": None, "id": None, "seconds": cooldown_timer.DEFAULT_SECONDS,
    "at": 0.0, "used": True,
}


def set_cooldown_context(target, seconds, cmdr_id=None):
    _cooldown_context["name"] = target
    _cooldown_context["id"] = cmdr_id
    _cooldown_context["seconds"] = seconds
    _cooldown_context["at"] = time.monotonic()
    _cooldown_context["used"] = False


def on_cooldown_start(target, cmdr_id, seconds):
    set_cooldown_context(target, seconds, cmdr_id)
    _cooldown_context["used"] = True
    if not config.get_config("cooldown_timer", True):
        return
    cooldown_timer.start(target_name=target, target_id=cmdr_id, seconds=seconds)
    start_overlay_cooldown()


def dashboard_entry(cmdr, is_beta, entry):
    """EDMC calls this when Status.json changes, about once a second."""
    gui_inst.set_status_on_foot(entry)
    flags = entry.get("Flags", 0) if isinstance(entry, dict) else 0
    edge = cooldown_timer.note_status_flags(flags)
    if edge == "start":
        if not config.get_config("cooldown_timer", True) or cooldown_timer.is_active():
            return
        if not cooldown_timer.fresh_context(_cooldown_context["at"], _cooldown_context["used"]):
            # Ordinary FSD cooldown (supercruise exit), not an interdiction.
            return
        _cooldown_context["used"] = True
        name = _cooldown_context["name"]
        cmdr_id = _cooldown_context["id"]
        if cmdr_id is None and isinstance(name, str):
            cmdr_id = history_inst.resolve_commander(name)
        seconds = _cooldown_context["seconds"]
        cooldown_timer.start(target_name=name, target_id=cmdr_id, seconds=seconds)
        start_overlay_cooldown()
        log.info("FSD cooldown flag set")
    elif edge == "end" and cooldown_timer.is_active():
        cooldown_timer.stop()
        stop_overlay_cooldown()
        log.info("FSD cooldown flag cleared")


def on_cooldown_left(cmdr_id):
    # A late submit: the target jumps away before the assumed 60 s is over.
    if not cooldown_timer.is_full_window():
        return
    if cooldown_timer.stop_if_target(None, cmdr_id):
        stop_overlay_cooldown()
        log.info("Cooldown cleared: target %s jumped away", cmdr_id)


def on_whois_result(kind, data):
    if not isinstance(data, dict):
        return
    announce_pvp_whois(
        kind,
        data.get("name"),
        data.get("kills", 0),
        data.get("deaths", 0),
        data.get("combat_logger", False),
    )


def on_targeted_for_whois(entry, own_cmdr):
    if not config.get_config("pvp_overlay", False):
        return
    try:
        stage = int(entry.get("ScanStage") or 0)
    except (TypeError, ValueError):
        return
    raw = entry.get("PilotName")
    prefix = "$cmdr_decorate:#name="
    if stage < 1 or not isinstance(raw, str) or not raw.startswith(prefix):
        return
    name = raw[len(prefix):-1] if raw.endswith(";") else raw[len(prefix):]
    if not name or name.casefold() == str(own_cmdr or "").casefold():
        return
    pvp_whois.request(name, on_whois_result)


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
    history_inst.subscribe_cooldown(on_cooldown_start)
    history_inst.subscribe_cooldown_left(on_cooldown_left)
    history_inst.aggregated_commanders_load()
    history_inst.start_kill_backfill()
    history_inst.start_worker()
    ensure_pvp_group()
    ensure_whois_group()
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
        log.info("TIMING journal %s ts=%s recv=%.3f", event, entry.get("timestamp"), time.time())
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
            killer = entry.get("KillerName") or entry.get("Killer")
            if isinstance(killer, str) and killer.strip() and not killer.startswith("$"):
                announce_pvp_kill(
                    "death",
                    killer,
                    entry.get("KillerRank"),
                    entry.get("KillerShip"),
                    system,
                )

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
        location.interdiction_win = False
        location.interdiction_self = False
        # Win or lose, an outgoing interdiction uses the 60 s cooldown. Always
        # refresh the context so a status edge cannot reuse an earlier 10 s one.
        set_cooldown_context(
            entry.get("Interdicted"),
            cooldown_timer.seconds_for_event("Interdiction", entry),
        )
        if entry.get("Success") is False and entry.get("IsPlayer") is True:
            history_inst.note_escape(entry.get("Interdicted"))
        elif entry.get("IsPlayer") is True:
            location.interdiction(entry.get("Interdicted"))
            # Outgoing Success does not say whether they submitted.
            location.interdiction_cooldown = 60.0
            location.interdiction_win = True
        else:
            location.interdiction(
                entry.get("Interdicted") if entry.get("Success") is not False else None
            )

    elif event == "Interdicted":
        # The other commander pulled you out of supercruise. They drop in with you.
        # Escaping is EscapeInterdiction, which does not land you in their instance.
        location.interdiction_win = False
        location.interdiction_self = False
        if entry.get("IsPlayer") is True:
            location.interdiction(entry.get("Interdictor"))
            seconds = cooldown_timer.seconds_for_event("Interdicted", entry)
            location.interdiction_cooldown = seconds
            set_cooldown_context(entry.get("Interdictor"), seconds)
            location.interdiction_self = True

    elif event == "ShipTargeted":
        # A locked player ship is in this instance, in normal space or supercruise.
        # Announce on this line. Do not wait for CommanderHistory.
        history_inst.note_ship_present(entry)
        history_inst.note_player_targets(entry)
        on_targeted_for_whois(entry, cmdrname)

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
        if cooldown_timer.stop_if_target(victim):
            stop_overlay_cooldown()
        announce_pvp_kill(
            "kill",
            entry.get("Victim_Localised") or victim,
            entry.get("CombatRank"),
            entry.get("VictimShip"),
            system,
        )

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
    # Close plugin windows before anything else. An open pop-out or a modal
    # dialog is what keeps EDMC's process alive after a close, a taskkill,
    # or a Windows logoff. The commander list is written afterwards by the
    # same flush the worker uses, and only when it has unsaved changes.
    log.info("beep_beep plugin stopping")
    try:
        gui_inst.shutdown()
    except Exception:
        log.exception("beep_beep window shutdown failed")
    try:
        clear_connected()
        stop_overlay_cooldown()
        clear_pvp_kills()
        clear_pvp_whois()
        cooldown_timer.stop()
    except Exception:
        log.exception("beep_beep overlay clear failed")
    try:
        beep_inst.stop_playback()
    except Exception:
        log.exception("beep_beep playback stop failed")
    try:
        history_inst.stop_worker()
    except Exception:
        log.exception("beep_beep worker stop failed")
    log.info("beep_beep plugin stopped!")
