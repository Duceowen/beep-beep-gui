<img width="774" height="883" alt="Screenshot 2026-10-19 220132" src="https://github.com/user-attachments/assets/1bc5a035-abfc-4027-9945-03c47ee6473a" />
<img width="978" height="709" alt="Screenshot 2026-10-19 220143" src="https://github.com/user-attachments/assets/e8ad9d21-1aed-4909-b4e7-039c1cce4cc4" />
# BeepBeep — EDMarketConnector Plugin

## Download

**[Download BeepBeep 1.1.1](https://github.com/Duceowen/beep-beep-gui/releases/download/v1.1.1/beep-beep-gui-1.1.1.zip)** (zip) - [all releases](https://github.com/Duceowen/beep-beep-gui/releases)

### Install
1. Download the zip and unzip it
2. Copy the `beep-beep-gui` folder into your **EDMarketConnector `plugins` directory**
3. Launch EDMarketConnector

Upgrading: close EDMC and unzip the new version over the old `beep-beep-gui` folder. Keep the folder, since it holds your seen commanders and kill counts.

## Features
- Plays a configurable sound for each commander detected in your instance
- Option to disable wingman sounds
- Sounds are only played when a commander arrives in your instance (some edge cases may not be covered)
- Optional setting to play sounds when commanders leave
- Seen Commanders window opacity, from the Options menu
- Hide from mouse (Windows): click-through Seen Commanders window while on foot, with a Detect/Hidden/Off button in the EDMC main window
- Configurable alert settings

## Notes
- Only tested with the latest version of EDMarketConnector
- Includes default sounds; you can add your own `.wav` or `.mp3` files in the `sounds` folder
- Windows volume control uses the included DLL. Linux volume is applied to the sound before playback. macOS uses the system player
- Does not require python to be installed.

<details>
<summary><b>Changelog</b> (click to expand)</summary>

## 1.1.2
- Importing commander names checks the file before anything is written. A file over 5 MB or 20,000 entries is refused. A file nested too deeply shows an error instead of freezing
- Import shows a preview of new names, changed names, and skipped or invalid rows. The list is written only after you confirm. Cancel writes nothing, and no backup is made
- A shared file can add names only. Times in the file are ignored, so a future last-seen time cannot hide a commander. Sounds, kills and seen counts are not copied
- Commander ids must be numbers. Names must be 1 to 64 characters, with no hidden control or bidirectional characters. Bad rows are counted and skipped. Good rows are kept
- The backup made just before an import is labeled "Before import" in the restore list
- Restoring a backup, or loading seen_commanders.json at startup, checks each row before replacing the list in memory. A corrupt file leaves the current list in place and is recorded in the log

## 1.1.1
- Right-click a row in Seen Commanders to mark a missed beep, an extra beep, or a related issue (shown as Now but not here, beeped early or late, interdiction, killed, or wrong name). The mark is appended to beep_marks.jsonl in the plugin folder and written to the debug log. Each announce or skip is also logged as a decision line
- Supercruise missed beeps caused by a stale leave snapshot are fixed. Commanders not heard in the last 10 minutes are dropped from that silence list, so a real supercruise arrival beeps. Commanders heard in the last 10 minutes that you left behind stay silent when the game re-lists them
- The Now display logs the fade once, when Now clears, so the debug log is no longer flooded on every presence check
- The quiet timeout only fades the Now display. Beep presence stays until a real leave, so a commander still with you does not beep again because Now faded, and a refresh of someone still here stays silent
- Joining a wing no longer beeps for the wingmates the game lists, including a wing add or an accepted wing invite. They still show as here. An unnamed contact is not silenced by that short window, and a real arrival after it still beeps
- Death, respawn, a Location event that changes system, and the Operations-screen ShutDown reset presence. Respawn is the journal Resurrect event. A system change clears the old instance and keeps the return re-list quiet, including detention or a teleport where you stay in normal space. The Operations screen arrives as ShutDown and clears presence the same way the main menu does
- Update spelling treats o and 0 as look-alikes. When a ship scan, wing name, PvP kill, or interdiction shows a saved name with o and 0 swapped, and never shows the saved letters exactly, the row takes the game's spelling and keeps the old spelling as an alias. Chat alone does not swap them. Two saved commanders who differ only by o and 0 are left unchanged, and so are two different look-alike spellings from the game

## 1.1.0
- Seen Commanders backups: "Back up now" saves a copy on demand, and "Open backups folder" opens the backups folder
- "Restore backup..." lists your backups and restores the one you pick. Your current list is backed up first, so a restore can be undone
- "Import names..." adds commander names from another player's seen_commanders.json. Only commanders with a real name are added, sounds are set to neutral, and kills and seen counts are not copied. Names you already have are kept, and unknown names are filled in. Your list is backed up before the import
- Options window reorganised into Sound, Alerts, Window and Seen commanders sections, with the long explanations moved into a collapsible Help section
- The Options checkboxes, scrollbar and window colours now follow the EDMarketConnector theme instead of plugin colours
- Seen commanders saves are now serialised, so a backup, restore or import cannot be overwritten by the background save
- A seen count of 0 is no longer changed to 1 when the list loads, so imported names show 0 seen

## 1.0.46
- A commander you killed in your instance who respawns and is re-listed while you are in supercruise is announced as a new arrival, instead of being silenced as someone left behind

## 1.0.44
- A commander you killed or who escaped while they were in your instance is no longer announced as a new arrival when the game re-lists them after you die. They count as left behind, like everyone else in the instance at death

## 1.0.43
- The post-load quiet window is 12 seconds from the game's load timestamp, down from 15, so a real arrival about 14 seconds after a load is announced. Arrivals inside the first 12 seconds after a load stay quiet

## 1.0.42
- The post-drop window for a killed or escaped commander's re-list is now 5 seconds, down from 30, so a respawn a few seconds after you drop out is less likely to be silenced

## 1.0.41
- A commander left behind by entering supercruise is no longer announced when the game re-lists them later in the same supercruise. The silence now lasts until you drop out, not just until the next update

## 1.0.40
- A commander heard just before the SupercruiseEntry event is no longer announced again when you drop out of supercruise. The late row is now treated as a supercruise arrival instead of being lost when the instance resets
- When a successful interdiction target's history row has not been named yet, the target is kept in the instance instead of being dropped and replaced by another recent contact

## 1.0.39
- Windows alerts no longer go missing when BeepBeepPlay.dll fails to start the sound. The DLL now reports setup errors, and Beep Beep retries once and then plays the WAV through winsound
- A commander you killed or who escaped is not announced again just after you drop out of supercruise, when the game re-lists them from recent contacts. A genuine return later in the visit still announces
- The target of a successful interdiction stays in the instance when you drop out of supercruise with them
- Linux playback retries the first player once before trying the next player, matching the Windows DLL retry

## 1.0.38
- Upgrading: close EDMC and unzip this version over the old beep-beep-gui folder. Keep the folder: it holds your seen commanders and kill counts. The old location.py and sound_loader.py are left behind but nothing uses them, so they can stay
- Arrivals and leaves follow your instance: entering supercruise clears the visit, and dropping back in announces everyone listed in the new instance
- Restarting EDMC keeps the people already with you (same system and game session), without sounds for them
- Killed or escaped commanders leave the Now list at once and announce again only after the respawn window
- A commander who drops out of supercruise is not re-added by the next update for a short grace period
- A commander removed by the quiet timeout does not sound on return until you jump, load, or die
- Dying keeps the old instance's people off the Now list
- Faster detection: the commander history is checked every 150 ms, so arrivals show sooner
- Commander IDs are in the log lines for presence and sounds, and held-back arrivals say why
- Names are taken only from local chat; renamed commanders still match kills by their earlier names
## 1.0.37
- Arrivals are announced when you drop back into normal space from supercruise in the same system. Entering supercruise clears the instance, so the return is not treated as a stale re-add
- The first arrivals after Beep Beep loads, and the first after a game load, are announced. The post-load silence is now 5 seconds from the game's own timestamp, so EDMC's delivery delay no longer swallows them
- Several arrivals at once are no longer ignored after a supercruise drop-in or the first update after loading
- Commander names come only from local chat. Direct messages no longer name a commander who may not be in your instance
- Seen Commanders checks who is present every 250 ms, so the Now group updates much sooner. The full row refresh stays on its 5 second timer
- Dying clears the instance right away, so the Now tag drops when you are killed. Your own death does not play departure sounds

## 1.0.36
- A commander who drops out of the instance and returns within 60 seconds is not announced again, so stale re-adds after an interdiction, high wake or state change no longer beep
- Several arrivals at once with no jump or interdiction behind them are ignored, so a stale batch no longer plays several sounds
- Now only shows commanders heard in the last 10 minutes, so people who have left no longer stay marked as here
- The edit popup stays above the bottom of the Seen Commanders window and inside the monitor's work area, so it no longer drops below the window or under the taskbar
- Fixed garbled text on the Play button and the sound-change arrow

## 1.0.35
- The saved commander list is written in a smaller format, at most every 5 seconds, and through a safe write so a crash cannot leave it half-written
- The saved list is backed up at each EDMC start (the newest 10 copies are kept in the plugin's backups folder). If the main file cannot be read, the newest backup is restored instead of starting empty
- Kill counts are saved the same safe way

## 1.0.34
- Overlay connection announcements follow the wing setting. Wing members hidden by "Beep for wing members" are no longer shown on the overlay either
- The overlay clears on jumps, carrier jumps, shutdown, and plugin stop, so an old "has jumped in" line no longer hangs around
- Departure sounds respect the wing setting. Wing state is read from the newest journal at startup, so starting EDMC while already in a wing no longer treats wing members as normal
- Now in Last Encounter shows for commanders in your current instance. Presence clears on jumps, main-menu return, game load, and shutdown. A commander not seen for 10 minutes goes back to their last encounter time. Restarting EDMC starts with nobody marked Now until the game or the journal shows them
- A commander who was just announced is not announced again within 60 seconds unless they have left or been killed since
- Commanders who arrive after your interdiction are announced. Commanders already in your instance are not
- Kills column counts PvP kills per commander. Kills are matched by name, so a kill still counts when the name is filled in later. A one-time journal scan fills in kills from earlier sessions
- The sorted column header shows an up or down arrow
- The edit popup stays on screen near the bottom of the window
- Column widths are cached, so the list re-layouts faster
- Jump reset now runs after each FSD jump (the event name was misspelled before)
- Removed unused code

## 1.0.33
- Alerts play on Windows when BeepBeepPlay.dll cannot be loaded. The 64-bit DLL fails with WinError 193 on a 32-bit EDMC install, or when a 32-bit Visual C++ runtime is found first on PATH. Beep Beep now logs the failure and plays the WAV through winsound with the alert volume baked into a temporary copy, instead of stopping every alert
- The included BeepBeepPlay.dll is built with the static Visual C++ runtime, so it no longer needs MSVCP140.dll or VCRUNTIME140.dll

## 1.0.32
- Hide from mouse Detect no longer hides Seen Commanders when Elite Dangerous is closed. The mode checks that the game is running and clears on Shutdown or MainMenu, so leaving the game on foot and reopening EDMC does not leave the window click-through

## 1.0.31
- A killed commander is not announced again a few seconds after the kill. Frontier refreshes the killed entry after combat, and Beep Beep now waits 60 seconds before treating that commander as a new arrival
- A PvP kill no longer resets commanders who already had a Killed flag on record from an earlier encounter

## 1.0.30
- A commander you kill is reset, so their next arrival announces again. The kill marker is consumed by the kill instead of leaking into the next sighting
- Stay on top is reapplied on Linux, so focusing another window does not drop the Seen Commanders window below normal windows. A focused fullscreen or borderless-fullscreen game still owns the fullscreen layer. On KDE Plasma 6, add a window rule for Seen Commanders and set Layer to Overlay

## 1.0.29
- Jump-in overlay announcements stack vertically. Alerts that arrive together are shown as one multi-line message in Modern Overlay instead of overlapping on the same line. Newest announcement is at the bottom; each line still disappears after 3 seconds
- Hide from mouse (Windows): the Seen Commanders window can pass clicks through to the game. The Hide from mouse button in the EDMC main window cycles Detect, Hidden, and Off. Detect mode makes the window click-through whenever your commander is on foot. Hidden always passes clicks; Off always catches them. The title bar reads "Seen Commanders (hidden from mouse)" while clicks pass through

## 1.0.28
- The jumped-in overlay is not shown again for a commander who was already in the instance when you jump away

## 1.0.27
- Options is a shorter list. Sound is the only section heading
- Spelling windows open in front of Options. Trust journal spelling is on the Update name spelling warning, after the warning. Open Seen Commanders Folder stays on Options
- While Options is open, Seen Commanders says Beep Beep Options are Open. Close Options window to edit list, and the list cannot be edited
- The last Confirm writes the checked names, closes the spelling windows, and shows how many names were saved. Go Back and Close write nothing
- The experimental spelling note and the empty-instance note are two separate tips

## 1.0.26
- Seen Commanders records the first time a commander was seen, the last time, and up to eight recent visits. A gap of more than 15 minutes starts a new visit
- An unknown row takes a journal name when that visit has one matching event and one unknown commander. A ship scan, wing add, direct message, or player interdiction can supply the name. A PvP kill can supply it while Autofill on Kill is on. Two unknown commanders in the same visit stay unknown. Learning a name keeps the alert that already played
- Autofill on Kill is in Options and starts on. It controls only the PvP kill path
- Update spelling from journals is experimental. Copy seen_commanders.json somewhere safe first. The list shows each capitalization from a ship scan, wing name, kill, or interdiction. Check the names you want, or turn on Trust journal spelling to select every suggestion. The last Confirm writes the checked names. Go Back and Close leave every saved name as it is. Typed chat stays unused, and a row saved as unknown stays unknown

## 1.0.25
- Alerts play again on Linux Mint. 1.0.24 gave the quieter sound to the first player and stopped if that player exited. Beep Beep now tries the next player, and plays the original sound if the quieter copy cannot be played
- The Alert Volume slider still makes that copy quieter. The player is asked to play it at its normal volume, so a volume flag cannot mute the copy as well

## 1.0.24
- Alert Volume works on Linux even when the player ignores its volume flag. The sound file itself is made quieter, so paplay, pw-play, and aplay all follow the slider
- Thanks to CMDR Kiranos for Linux testing and bug reports

## 1.0.23
- Beep Beep loads again under EDMC. The volume fix no longer imports a module EDMC does not ship

## 1.0.22
- Alert Volume changes the loudness of the alert. The slider no longer jumps from full volume to silence, and Linux uses a player that honors the setting

## 1.0.21
- Mute stops a sound that is already playing. A long file started by paplay, pw-play, or another Linux player no longer keeps going after the mute button is pressed

## 1.0.20
- Seen Commanders name editing works on Linux Mint. The edit dialog no longer calls grab before the window is visible

## 1.0.19
- Linux Mint finds CommanderHistory in the Steam Proton prefix, including ~/.local/share/Steam and ~/.steam/debian-installation

## 1.0.18
- Linux looks for CommanderHistory in the Proton prefix, using the same EDMC journal folder, instead of ~/.local/share

## 1.0.17
- Options is on the main EDMC window, aligned to the right

## 1.0.16
- The column checkbox menu opens next to the right-click, including when the window is on another monitor

## 1.0.15
- Seen Commanders measures its columns once, then resizes from those widths, so the window and EDMC stay responsive

## 1.0.14
- Name stays on the left and takes whatever width is left in the header
- The line on the left of Sound resizes Sound, the line on the left of Seen resizes Seen, and the line on the left of Last Encounter resizes Last Encounter
- There is no resize line on Name and no resize line on the right of Last Encounter
- An older saved Sound width stays fixed, so making the window wider only widens Name

## 1.0.13
- Last Encounter stays wide enough for the full date and time, and the line on its right can resize it
- The line on the right of Seen resizes Seen. Seen still starts wide enough for the count
- Header dividers are a single line
- Right-click column check marks use the EDMC theme

## 1.0.12
- Seen Commanders header dividers are visible. Drag the bar on Name to resize Name, and the bar on Sound to resize Sound
- Seen stays wide enough for the count, and Last Encounter stays wide enough for the full date and time
- Right-click the header to show or hide Name, Sound, Seen, and Last Encounter

## 1.0.11
- Seen Commanders column headers stay inside the window when it is resized, and grow back when the window is expanded

## 1.0.10
- Opacity support for the Seen Commanders window
- Quality of life enhancements to the Options menu

</details>

## Stay on top on Linux

The stay-on-top option uses the window manager's above flag. Beep Beep keeps
reapplying it while the window is open, but the window manager gives a focused
fullscreen or borderless-fullscreen game its own layer. On KDE Plasma 6, add a
window rule for the **Seen Commanders** window and set **Layer** to **Overlay**;
that places it above a fullscreen game. On other desktops, use windowed mode or
Modern Overlay for alerts. Exclusive fullscreen always covers normal windows.

## How it works
By reading a file inside the Frontier Developments folder, we can detect events when a Commander appears in or leaves our instance.
The main issue is that there is no reliable way to distinguish these events using the CommanderHistory file alone.

To work around this, an instance tracker is created which keeps track of who has joined and who has left using edge detection.
Additionally, by using EDMC journal events, we can hook into other events such as wing activity, giving us the ability to mute wing-related notifications.

By saving a separate copy of every Commander you have encountered, we can assign a custom CMDR name and even a separate sound for each one, using the GUI. A row saved as unknown keeps its commander ID. The row records the first time that commander was seen, the last time, and up to eight recent visits, so a journal line can be matched to that visit without the file growing without limit. When a journal event names a player and only one unknown row matches that visit, the journal spelling is saved on the row. A ship scan, wing add, direct message, or player interdiction can supply the name. A PvP kill can supply it too, unless Autofill on Kill is turned off in Options. The name is saved after a short wait, so two unknown commanders in the same visit keep both rows unknown. Options can also update a saved name from a ship scan or similar journal event when that event shows the capitalization the commander used. The list lets you check each change, or Trust journal spelling can select every suggestion without checking them one by one. Nothing is saved until the final confirmation. A successful save closes the spelling windows and shows how many names were written. Go Back and Close leave every saved name as it is. That tool is experimental, so copy seen_commanders.json somewhere safe before using it.

On Windows, sound playback uses the included DLL so each alert can have its own volume. If the DLL cannot load, Beep Beep makes a quieter copy of a WAV and plays it with winsound instead of dropping the alert. Linux makes a quieter copy of the sound and plays that, so the slider works even when the system player ignores volume flags. The first Linux player is retried once, then the next player is tried if it still cannot play the copy. macOS uses the system sound player.

## License
Distributed under the **GNU General Public License v3** (or later).  
No warranty is provided. For full license details, see [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)


Credit goes to the other BeepBeep implementations, which provided the initial inspiration and general concept.

CMDR Kiranos tested Beep Beep on Linux and reported the bugs found there.

If you enjoy this plugin and want to support my work, you can buy me a coffee here: [Ko-fi](https://ko-fi.com/rhaged)
