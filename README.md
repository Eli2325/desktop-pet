# Desktop Pet (PyQt6)

A customizable desktop pet for Windows, built with PyQt6.

## Download

- Latest release (Windows package): [v1.0.0](https://github.com/Eli2325/desktop-pet/releases/tag/v1.0.0)
- Baidu Netdisk (CN mirror): https://pan.baidu.com/s/1PAdM-lBGmQIQhdQZXBcEHA (Code: `xkaq`)

## Features

- Animated desktop pet with drag, poke, headpat, wall/ceiling actions
- Configurable behavior, sleep, reminders, and app-triggered bubble messages
- AI console with prompt presets, memory blackbox, and optional screenshot mode
- External `assets` folder support for easy artwork replacement
- Onedir packaging (`exe + folder`) so users can customize materials safely

## Quick Start (Dev)

### 1) Requirements

- Python 3.10+ (Windows recommended)
- PyQt6 and dependencies

### 2) Install

```bash
pip install pyqt6 requests pyinstaller
```

### 3) Run

```bash
python main.py
```

## Configuration Location

Runtime user config files are stored in:

```text
C:\Users\<YourUserName>\.desktop_pet\
```

Files include:

- `bubbles.json`
- `pet_settings.json`
- `app_map.json`
- `filters.json`

## Packaging (Folder + EXE, Recommended)

This project is packaged in **onedir** mode (not single-file), so end users can replace artwork in `assets`.

Manual packaging command:

```bash
pyinstaller build_pet.spec --clean --distpath dist_pack --workpath build_pack
```

Output folder:

```text
dist_pack\DesktopPet\
├── DesktopPet.exe
├── assets\        # optional external override folder (copy from project assets)
└── _internal\assets\  # bundled fallback assets
```

## Customize Artwork

Replace files in `assets` with your own GIF files using the same names, then restart the app.

Common files:

- `idle.GIF`, `walk.GIF`, `drag.GIF`, `fall.GIF`
- `wall_slide.GIF`, `ceiling_hang.GIF`
- `sleep_day.GIF`, `sleep_night.GIF`
- `poke.GIF`, `headpat.GIF` (optional interactions)

## Open Source Notes

- Do **not** commit personal API keys or local config secrets.
- Build artifacts (`build*`, `dist*`, caches, logs) are ignored by `.gitignore`.

## License

MIT License. See `LICENSE`.

