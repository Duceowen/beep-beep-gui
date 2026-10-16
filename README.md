# BeepBeep — EDMarketConnector Plugin

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

By saving a separate copy of every Commander you have encountered, we can assign a custom CMDR name and even a separate sound for each one, using the GUI.

On Windows, sound playback uses the included DLL so each alert can have its own volume. Linux and macOS use the system sound player.

## Installation
1. Copy the `beep-beep-gui` folder into your **EDMarketConnector `plugins` directory**
2. Launch EDMarketConnector

## Notes
- Only tested with the latest version of EDMarketConnector
- Includes default sounds; you can add your own `.wav` or `.mp3` files in the `sounds` folder
- Windows volume control uses the included DLL. Linux and macOS play sounds with the system player
- Does not require python to be installed.

## License
Distributed under the **GNU General Public License v3** (or later).  
No warranty is provided. For full license details, see [GNU GPL v3](https://www.gnu.org/licenses/gpl-3.0.html)


Credit goes to the other BeepBeep implementations, which provided the initial inspiration and general concept.

If you enjoy this plugin and want to support my work, you can buy me a coffee here: [Ko-fi](https://ko-fi.com/rhaged)
