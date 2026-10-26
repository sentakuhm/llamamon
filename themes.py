"""Colour themes: the terminal's palettes.

`dark`, `light` and `terminal` are the monitor's original looks; `tokyonight` is the editor palette of
the Tokyo Night theme (background `#1a1b26`, Storm panel `#24283b`, foreground `#c0caf5`, muted `#565f89`,
and the palette's red `#f7768e`, green `#9ece6a`, yellow `#e0af68`, blue `#7aa2f7`).

Both drawing layers read this table: Textual's `MonitorApp` takes the (screen, panel, foreground, muted)
tuple for its CSS, and the Overview takes the accent/blue/amber/red/grey colours for its tiles.
"""

from __future__ import annotations

#: (screen background, panel background, foreground, muted) for the Textual shell.
SHELL = {
    "dark": ("#0e1113", "#161a1d", "#eef1f3", "#8e979f"),
    "light": ("#f9fafb", "#ffffff", "#221e1f", "#6f6f6f"),
    "terminal": ("transparent", "transparent", "ansi_default", "ansi_default"),
    "tokyonight": ("#16161e", "#1a1b26", "#c0caf5", "#565f89"),
}

#: (blue, amber, red, accent, grey) for the Overview's tiles, bars and state colours.
PALETTE = {
    "dark": ("#5aa9ff", "#f5c56b", "#f28b82", "#34d399", "#59636b"),
    "light": ("#5aa9ff", "#f5c56b", "#f28b82", "#0c8a60", "#59636b"),
    "terminal": ("#5aa9ff", "#f5c56b", "#f28b82", "#34d399", "#59636b"),
    "tokyonight": ("#7aa2f7", "#e0af68", "#f7768e", "#9ece6a", "#565f89"),
}

#: The accent used by the plain-text views' heading highlight.
ACCENT = {"light": "#0c8a60", "tokyonight": "#9ece6a"}
#: The muted colour used by the plain-text views' hints.
MUTED = {"light": "#6f6f6f", "tokyonight": "#565f89"}

CHOICES = ("dark", "light", "terminal", "tokyonight")
