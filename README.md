# AudioMixer

A per-app volume mixer for Windows 10/11, in the style of the OBS audio mixer.
Every app that plays sound gets its own channel with a fader, a level meter, mute and solo.

## Features

- Per-app volume, mute, solo and output device
- Channels for input and output devices, system sounds and the master volume
- Global hotkeys to mute apps and to switch presets
- Presets: save and restore volumes, mutes, outputs and visible sources
- Automations: switch preset when an app opens or closes, when the output device changes, or at a set time
- Ducking: lower some apps while others play, with several independent groups
- Volume limits per app
- Quick switch of the default output device, remembering each device's volume
- Fade in/out on mute, solo and preset changes
- Themes, accent color, UI scale
- Languages: Italian, English, German, Spanish, Russian

## Install

Run `release\AudioMixer_Setup_<version>.exe`. Settings are stored in `%APPDATA%\AudioMixer`.

## Folders

| Folder | Contents |
|--------|----------|
| `release\` | The installer to give to users (not in git: publish it as a release download) |
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
2. `scripts\Crea Installer.bat` builds `release\AudioMixer_Setup_<version>.exe` with [NSIS](https://nsis.sourceforge.io/).
   The version is read from `APP_VERSION` in `mixer.pyw`.

## Tests

```
python test_fader.py
```

## Translations

The UI text is written in Italian inside `mixer.pyw`. Each file in `lingue/` maps it to another
language. To add a language, copy `lingue/en.json`, translate the values and set `"language"`.

## License

Copyright (c) 2026. All rights reserved. No license is granted yet.
