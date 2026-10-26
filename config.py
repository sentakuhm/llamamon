"""llamamon's settings: one small TOML file at ``~/.config/llamamon/config.toml``.

```toml
url = "http://127.0.0.1:9931"
interval = 1.0
timeout = 4.0
requests = 12
theme = "dark"
color = "auto"
glyphs = "auto"
```

`load()` reads it and merges the file over the defaults (unknown keys are kept so a hand-edited file
round-trips); `save()` writes it back out. Reading uses stdlib `tomllib`; writing is the few lines
of plain `key = value` TOML this file holds — no dependency outside the standard library.
"""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path

from llamamon.client import server_url

CONFIG_DIR = Path.home() / ".config" / "llamamon"
CONFIG_FILE = CONFIG_DIR / "config.toml"

DEFAULTS: dict = {
    "url": "http://127.0.0.1:9931",          # the llama.cpp server to watch
    "interval": 1.0,                          # seconds between polls
    "timeout": 4.0,                           # seconds one HTTP request may take
    "requests": 12,                           # recent requests the views keep
    "theme": "dark",                          # dark | light | terminal | tokyonight (auto: terminal)
    "color": "auto",                          # auto | always | never (NO_COLOR wins)
    "glyphs": "auto",                         # auto | unicode | ascii
}

NUMBERS = ("interval", "timeout", "requests")
BOUNDED = {                       # key: (low, high, converts to int)
    "interval": (0.2, 60.0, False),
    "timeout": (0.5, 30.0, False),
    "requests": (1, 50, True),
}
WORDS = {                          # key: allowed values
    "theme": ("auto", "dark", "light", "terminal", "tokyonight"),
    "color": ("auto", "always", "never"),
    "glyphs": ("auto", "unicode", "ascii"),
}

ORDER = ("url", "interval", "timeout", "requests", "theme", "color", "glyphs")
COMMENTS = {
    "url": "# the llama.cpp server to watch",
    "interval": "# seconds between polls",
    "timeout": "# seconds one HTTP request may take",
    "requests": "# recent requests the views keep",
    "theme": "# dark | light | terminal | tokyonight",
    "color": "# auto | always | never (NO_COLOR wins)",
    "glyphs": "# auto | unicode | ascii",
}


def _number(key: str, value):
    low, high, as_int = BOUNDED[key]
    try:
        n = int(value) if isinstance(value, str) else value
        n = float(n)
    except (TypeError, ValueError):
        return None
    if not low <= n <= high or (as_int and float(n) != int(n)):
        return None
    return int(n) if as_int else n


def load(path: Path | None = None) -> dict:
    """The settings as they take effect: the file over the defaults, unknown keys kept."""
    settings = dict(DEFAULTS)
    file_path = path or CONFIG_FILE
    try:
        with open(file_path, "rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError:
        print(f"llamamon: {file_path} does not parse as TOML; using the defaults", file=sys.stderr)
        return settings
    except OSError:
        return settings
    if not isinstance(data, dict):
        return settings
    for key, value in data.items():
        if key in NUMBERS:
            fixed = _number(key, value)
            if fixed is not None:
                settings[key] = fixed
        elif key == "url":
            if isinstance(value, str) and value.strip():
                settings["url"] = value.strip()
        elif key in WORDS:
            if isinstance(value, str) and value.strip().lower() in WORDS[key]:
                settings[key] = value.strip().lower()
        elif key in DEFAULTS:
            pass
        else:
            settings[key] = value          # keep a key this tool does not use, so the file round-trips
    if settings["color"] == "auto" and os.environ.get("NO_COLOR") is not None:
        settings["color"] = "never"
    return settings


def save(settings: dict, path: Path | None = None) -> Path:
    """Write the settings back out with the comments, and return the file's path."""
    path = path or CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# llamamon settings\n"]
    for key in ORDER:
        value = settings.get(key, DEFAULTS[key])
        lines.append(f'{COMMENTS[key]}\n{key} = {_toml_value(value)}\n')
    for key in sorted(k for k in settings if k not in ORDER):
        lines.append(f"{key} = {_toml_value(settings[key])}\n")
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_value(settings: dict, key: str, raw: str) -> tuple[dict, str]:
    """Change one setting from the command line; return the new settings and how to say it back."""
    if key == "url":
        value = raw.strip()
        if not value:
            raise ValueError("the url cannot be empty")
        value = server_url(value)
    elif key in NUMBERS:
        value = _number(key, raw)
        if value is None:
            raise ValueError(f"{key} must be a number between {BOUNDED[key][0]} and {BOUNDED[key][1]}"
                             + (" (a whole number)" if BOUNDED[key][2] else ""))
    elif key in WORDS:
        value = raw.strip().lower()
        if value not in WORDS[key]:
            raise ValueError(f"{key} must be one of: {', '.join(WORDS[key])}")
    else:
        raise ValueError(f"unknown setting: {key}")
    new = dict(settings)
    new[key] = value
    return new, f'{key} = {_toml_value(value)}'


def describe() -> str:
    """Where the file is and how it is read, for `llamamon config help`."""
    return (f"Settings live in {CONFIG_FILE} and are read by every command.\n"
            "Edit that file by hand, or change one setting:  llamamon config set url http://127.0.0.1:9931\n"
            "Show the settings as they take effect:  llamamon config show")
