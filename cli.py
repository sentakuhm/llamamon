"""llamamon's command line: `monitor` (the terminal app), `status` (one snapshot), `config` (the file).

```
llamamon                        watch the server from config.toml in a full-screen monitor
llamamon status                 one snapshot, printed and done
llamamon config show            the settings as they take effect, and where they live
llamamon config set url http://127.0.0.1:9931
llamamon config path
```
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time

from llamamon import config, version
from llamamon import hardware
from llamamon.client import ClientError, Endpoint
from llamamon.render import (VIEWS, _head, _state_line, _request_row, _slot_lines, _hardware_lines, safe)


def _settings_from(argv_extra: dict) -> dict:
    settings = config.load()
    for key in ("url", "interval", "timeout", "requests", "theme", "color", "glyphs"):
        if argv_extra.get(key) is not None:
            settings[key] = argv_extra[key]
    return settings


def _endpoint(settings: dict) -> tuple[Endpoint, hardware.Sampler]:
    endpoint = Endpoint(settings["url"], timeout=float(settings["timeout"]))
    sampler = hardware.Sampler(refresh_s=max(1.0, float(settings["interval"])))
    return endpoint, sampler


def _print_status(settings: dict) -> int:
    """One round of collection, printed as the plain views: 0 on, 3 when the server cannot be reached."""
    endpoint, sampler = _endpoint(settings)
    try:
        snap = endpoint.snapshot(sampler)
    except ClientError as e:
        print(f"llamamon status: {e}  [CONNECTING/DISCONNECTED]")
        return 3
    engine = snap.get("engine") or {}
    label = f"{engine.get('model') or 'model'} " \
            f"{(engine.get('max_context') or 0) / 1024:.0f}K" if engine.get("max_context") else "model"
    print(_head("Status", settings["url"], label, "connected", False,
                snap.get("time"), time.time(), 100).rstrip())
    print(_state_line(snap.get("live") or {}))
    slots = _slot_lines(snap.get("slots") or [])
    print("Slots:  SLOT  STATE    PROMPT     READ   CACHED   OUTPUT  REMAIN   TOK/S")
    for line in slots[:16]:
        print(line)
    print("Engine:")
    print(f"  model {engine.get('model') or '--'}   type {engine.get('ftype') or '--'}   ctx {engine.get('max_context')} tokens"
          f"   slots {int(engine['slots']) if engine.get('slots') else '--'}")
    print(f"  file: {engine.get('path') or '--'}")
    print("Hardware (sampled from this machine):")
    for line in _hardware_lines(snap.get("hardware") or {}, snap.get("hardware_static") or {}):
        print("  " + line)
    recent = (snap.get("requests") or [])[:int(settings["requests"])]
    if recent:
        print(f"Recent requests ({len(recent)}):")
        for r in recent:
            print("  " + _request_row(r))
    else:
        print("Recent requests: none reached this monitor yet")
    return 0


def _run_monitor(settings: dict) -> int:
    """The interactive monitor (imports Textual and Rich only here, so status stays light)."""
    missing = [m for m in ("rich", "textual") if importlib.util.find_spec(m) is None]
    if missing:
        sys.stderr.write(
            "llamamon: this interpreter is missing the packages the monitor needs: %s.\n"
            "        Starting and watching never install anything. Install them yourself:\n"
            "        python -m pip install -r llamamon/requirements.txt\n"
            "        (status, config and help work without them)\n"
            % ", ".join(missing)
        )
        return 4
    from llamamon.monitor import MonitorApp
    endpoint, sampler = _endpoint(settings)
    first: dict | None
    try:
        first = endpoint.snapshot(sampler)
    except ClientError:
        first = None
    app = MonitorApp(endpoint, sampler, first,
                     interval=float(settings["interval"]), requests=int(settings["requests"]),
                     theme=settings["theme"], color=settings["color"], glyphs=settings["glyphs"])
    app.run()
    return 0


def main() -> int:
    """The entry point: `python -m llamamon` / `llamamon.py`."""
    parser = argparse.ArgumentParser(prog="llamamon", description=__doc__.splitlines()[0],
                                     epilog="Settings: ~/.config/llamamon/config.toml")
    parser.add_argument("command", nargs="?", default="monitor",
                       choices=("monitor", "status", "config"),
                       help="what to do (default: monitor)")
    parser.add_argument("--url", help="the llama.cpp server root (default from config.toml)")
    parser.add_argument("--port", type=int, help="shorthand: watch http://127.0.0.1:<port> instead")
    parser.add_argument("--interval", type=float, help="seconds between polls")
    parser.add_argument("--timeout", type=float, help="seconds one HTTP request may take")
    parser.add_argument("--requests", type=int, help="recent requests the views keep")
    parser.add_argument("--theme", choices=config.WORDS["theme"], help="dark, light, terminal or tokyonight")
    parser.add_argument("--color", choices=config.WORDS["color"], help="colour the views (never: NO_COLOR honoured)")
    parser.add_argument("--glyphs", choices=config.WORDS["glyphs"], help="unicode or ascii drawing")
    parser.add_argument("--version", action="version", version=f"llamamon {version()}")
    parser.add_argument("rest", nargs="*", help=argparse.SUPPRESS)
    argv = sys.argv[1:]
    args = parser.parse_args(argv)
    if args.port is not None:
        args.url = f"http://127.0.0.1:{args.port}"
    settings = _settings_from({key: getattr(args, key, None) for key in
                               ("url", "interval", "timeout", "requests", "theme", "color", "glyphs")})
    if args.command == "config":
        rest = args.rest
        if rest and rest[0] == "path":
            print(config.CONFIG_FILE)
            return 0
        if rest and rest[0] == "show":
            loaded = config.load()
            print(f"# {config.CONFIG_FILE} as it takes effect")
            for key in config.ORDER:
                print(f"{key} = {config._toml_value(loaded[key])}")
            return 0
        if rest and rest[0] == "help":
            print(config.describe())
            return 0
        if rest and rest[0] == "init":
            print(config.save(dict(config.DEFAULTS)))
            return 0
        if len(rest) >= 3 and rest[0] == "set":
            try:
                settings, shown = config.set_value(config.load(), rest[1], " ".join(rest[2:]))
            except ValueError as e:
                print(f"llamamon config: {e}")
                return 2
            path = config.save(settings)
            print(f"saved: {shown}  -> {path}")
            return 0
        print("llamamon config: try  show | set <key> <value> | path | init | help")
        return 2
    if args.command == "status":
        return _print_status(settings)
    return _run_monitor(settings)
