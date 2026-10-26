"""llamamon: a terminal monitor for a local llama.cpp inference server.

Listens to a llama.cpp server (default http://127.0.0.1:9931) and shows
live model state, request activity, and host hardware telemetry (GPU, CPU,
RAM, disk, PCIe) in the terminal. All settings live in
``~/.config/llamamon/config.toml``.
"""

from pathlib import Path

SCHEMA_VERSION = 1

#: The version people are told: llamamon's own release number.
PRODUCT_VERSION = "0.1.0"


def version() -> str:
    """The build version: the VERSION file next to the package, else PRODUCT_VERSION."""
    root = Path(__file__).resolve().parents[1]
    try:
        text = (root / "VERSION").read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    return PRODUCT_VERSION
