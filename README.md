# AudioMixer

A per-app volume mixer for Windows 10/11, in the style of the OBS audio mixer.
Every app that plays sound gets its own channel with a fader, a level meter, mute and solo.

## Features

- Per-app volume, mute, solo and output device
- Channels for input and output devices, system sounds and the master volume
- Duplicate an output on other outputs (right-click a device strip): hear the speakers' audio
  in the headphones too, live, without a virtual cable
- Groups (buses): one fader moves several apps together and keeps their volume ratios
- Global hotkeys to mute apps and to switch presets (a Stream Deck can send these keys)
- MIDI controllers: assign a knob or fader to an app's volume, a button to its mute
- Presets: save and restore volumes, mutes, outputs and visible sources; export and import them,
  with rules and ducking, as a JSON profile
- Automations: switch preset when an app opens or closes, when the output device changes, or at a set time
- Ducking: lower some apps while others play, with several independent groups
- Volume limits per app, and a safety volume for newly connected headphones or speakers
- Quick switch of the default output device, remembering each device's volume
- Optional: remember each app's volume per output device
- Optional: mute ads (Spotify Free) while they play
- Fade in/out on mute, solo and preset changes
- Strips with custom color, icon and width; quick search in the source list
- User plugins (optional, off by default)
- Per-app volume up/down hotkeys, on-screen notice when a hotkey changes something
- Optional: show new apps automatically, hide strips of closed apps
- Tray icon shows when the microphone is muted
- Themes, accent color, UI scale, guided tour on first start
- Languages: Italian, English, German, Spanish, Russian

## Download

Get the latest version from the **[Releases page](https://github.com/DrP1n/AudioMixer/releases/latest)**:

- **Installer:** `AudioMixer_Setup_<version>.exe`. Settings are stored in `%APPDATA%\AudioMixer`.
- **Portable:** `AudioMixer_Portable_<version>.zip`, unzip it anywhere. The `portable.txt` file
  next to the exe makes it keep its settings in a `data\` folder beside it.

The program checks for new releases at start and shows a tray message when one is available.

The exe is not signed: Windows SmartScreen shows a warning (choose *More info* → *Run anyway*),
and Smart App Control blocks it.

## Folders

| Folder | Contents |
|--------|----------|
| `release\` | Installer and portable zip to give to users (not in git: publish them as a release download) |
| `scripts\` | Build scripts: they turn the source into the installer |
| root, `lingue\` | Source code, icon, translations, tests |

## Run from source

Requires Python 3 on Windows.

```
pip install -r requirements.txt
python mixer.pyw
```

## Build

1. `scripts\Crea AudioMixer.bat` builds `dist\AudioMixer.exe` with PyInstaller.
2. `scripts\Crea Installer.bat` builds the installer with [NSIS](https://nsis.sourceforge.io/) and the
   portable zip, both in `release\`. The version is read from `APP_VERSION` in `mixer.pyw`.

**Signing:** set `AUDIOMIXER_PFX` (path to a code-signing `.pfx` certificate) and
`AUDIOMIXER_PFX_PASSWORD`, with `signtool` on the PATH, and step 2 signs the exe and the installer.

**Update check:** set `UPDATE_REPO = "user/repo"` in `mixer.pyw`. The mixer then checks the latest
GitHub release at start and shows a tray message when a newer version exists.

## Streaming: what you hear vs. what the stream hears

Windows cannot give one app two different volumes on two devices. Use the per-app output instead:
send the apps the stream must not hear (for example Discord) to your headphones, and capture only
the device the other apps play on in OBS. Save it as a "Streaming" preset to switch with one key.
For a fully separate stream mix you need a virtual audio cable (such as VB-Cable) as one of the outputs.

## Plugins

Turn on *User plugins* in Settings > General, then put `.py` files in the plugin folder
(`%APPDATA%\AudioMixer\plugins`). `_esempio.py` in that folder shows the API: `setup(api)`,
`on_refresh(api)`, and `api.apps()`, `get_volume`, `set_volume`, `get_mute`, `set_mute`, `presets`,
`apply_preset`, `log`. Plugins run with your permissions: only use files you trust.

## Tests

```
python test_fader.py
python test_features.py
```

## Translations

The UI text is written in Italian inside `mixer.pyw`. Each file in `lingue/` maps it to another
language. To add a language, copy `lingue/en.json`, translate the values and set `"language"`.

## License

Copyright (C) 2026 DrP1n

AudioMixer is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License v3.0](LICENSE) (or any later version). It comes with no warranty.

## Contributing

Issues and pull requests are welcome. Run both test files before sending a change, and add new
UI text in Italian inside `tr(...)` with its translations in `lingue/`.
