# BeepBeep — EDMarketConnector Plugin

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

## Features
- Plays a configurable sound for each commander detected in your instance
- Option to disable wingman sounds
- Sounds are only played when a commander arrives in your instance (some edge cases may not be covered)
- Optional setting to play sounds when commanders leave
- Seen Commanders window opacity, from the Options menu
- Configurable alert settings

## How it works
By reading a file inside the Frontier Developments folder, we can detect events when a Commander appears in or leaves our instance.
The main issue is that there is no reliable way to distinguish these events using the CommanderHistory file alone.

To work around this, an instance tracker is created which keeps track of who has joined and who has left using edge detection.
Additionally, by using EDMC journal events, we can hook into other events such as wing activity, giving us the ability to mute wing-related notifications.

By saving a separate copy of every Commander you have encountered, we can assign a custom CMDR name and even a separate sound for each one, using the GUI. A row saved as unknown keeps its commander ID. The row records the first time that commander was seen, the last time, and up to eight recent visits, so a journal line can be matched to that visit without the file growing without limit. When a journal event names a player and only one unknown row matches that visit, the journal spelling is saved on the row. A ship scan, wing add, direct message, or player interdiction can supply the name. A PvP kill can supply it too, unless Autofill on Kill is turned off in Options. The name is saved after a short wait, so two unknown commanders in the same visit keep both rows unknown. Options can also update a saved name from a ship scan or similar journal event when that event shows the capitalization the commander used. The list lets you check each change, or Trust journal spelling can select every suggestion without checking them one by one. Nothing is saved until the final confirmation. That tool is experimental, so copy seen_commanders.json somewhere safe before using it.

On Windows, sound playback uses the included DLL so each alert can have its own volume. Linux makes a quieter copy of the sound and plays that, so the slider works even when the system player ignores volume flags. If one Linux player cannot play the copy, the next player is tried. macOS uses the system sound player.

## Installation
1. Copy the `beep-beep-gui` folder into your **EDMarketConnector `plugins` directory**
2. Launch EDMarketConnector

## Notes
- Only tested with the latest version of EDMarketConnector
- Includes default sounds; you can add your own `.wav` or `.mp3` files in the `sounds` folder
- Windows volume control uses the included DLL. Linux volume is applied to the sound before playback. macOS uses the system player
- Does not require python to be installed.

## License
Distributed under the **GNU General Public License v3** (or later).  
No warranty is provided. For full license details, see [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)


Credit goes to the other BeepBeep implementations, which provided the initial inspiration and general concept.

CMDR Kiranos tested Beep Beep on Linux and reported the bugs found there.

If you enjoy this plugin and want to support my work, you can buy me a coffee here: [Ko-fi](https://ko-fi.com/rhaged)
