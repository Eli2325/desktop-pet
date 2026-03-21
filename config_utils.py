"""Shared configuration utilities.

Centralised here to break the circular-import chain
main -> pet_core -> chat_console -> main.
"""

import json
import os
import sys


def get_resource_path(relative_path: str) -> str:
    """Get resource path (dev environment + packaged exe).

    Priority:
    1. External file next to exe (user-editable)
    2. Bundled resource inside exe (fallback)
    3. Script directory (dev)
    """
    try:
        if getattr(sys, "frozen", False):
            exe_dir = os.path.dirname(sys.executable)
            external = os.path.join(exe_dir, relative_path)
            if os.path.exists(external):
                return external
            base_path = sys._MEIPASS
        else:
            base_path = os.path.dirname(os.path.abspath(__file__))
    except AttributeError:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def get_config_dir() -> str:
    """User-writable config directory (~/.desktop_pet)."""
    config_dir = os.path.join(os.path.expanduser("~"), ".desktop_pet")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def load_json(path: str, default=None):
    """Load a JSON file, returning *default* on any error."""
    if default is None:
        default = {}
    if not os.path.isfile(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, data) -> None:
    """Save *data* as JSON, creating parent directories if needed."""
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
