"""The Textual shell around the read-only renderers: polling, freezing, themes, the filter.

It only ever reads the llama.cpp server: watching never sends it anything, and `q` quits without
touching the server. Because llama.cpp answers one request at a time, a busy server delays the
monitor's own calls; when /health still answers, that shows as STALE (a request is running), not
as DISCONNECTED.
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import time

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Input, Static

from llamamon import nav
from llamamon.overview import build as overview_build
from llamamon.overview import render as overview_render
from llamamon.render import VIEWS, render_view, safe
from llamamon.themes import SHELL, ACCENT, MUTED


def choose_glyphs(requested: str, encoding: str | None) -> str:
    """Prefer readable Unicode when the output stream can encode its graph cells."""
    if requested != "auto":
        return requested
    if not encoding:
        return "ascii"
    try:
        "▁█░".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "ascii"
    return "unicode"


class MonitorApp(App):
    TITLE = "llamamon"
    _dark = SHELL["dark"]
    CSS = f"""
    Screen {{ background: {_dark[0]}; color: {_dark[2]}; }}
    #shell {{ width: 100%; height: 100%; background: {_dark[1]}; padding: 0 1; }}
    #content {{ width: 100%; height: 1fr; color: {_dark[2]}; }}
    #filter {{ height: 3; display: none; border: solid #34d399; }}
    #message {{ height: 1; color: {_dark[3]}; }}
    """
    BINDINGS = [
        Binding(str(i + 1), f"view_{i}", f"{name} view", show=False)
        for i, name in enumerate(VIEWS)
    ] + [
        Binding("q", "quit", "Quit"), Binding("ctrl+c", "quit", "Quit", show=False),
        Binding("space", "freeze", "Freeze"), Binding("r", "refresh", "Refresh"),
        Binding("t", "theme_cycle", "Theme"), Binding("slash", "filter_open", "Filter"),
        Binding("escape", "filter_clear", "Clear filter", show=False),
        Binding("left", "previous_view", "Previous view", show=False),
        Binding("right", "next_view", "Next view", show=False),
        Binding("b", "browser", "Open in browser", show=False),
        Binding("question_mark", "help", "Help"), Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, endpoint, sampler, snapshot: dict | None = None, *, interval: float = 1.0,
                 requests: int = 12, theme: str = "auto", color: str = "auto", glyphs: str = "auto"):
        super().__init__()
        self.endpoint, self.sampler = endpoint, sampler
        self.latest = snapshot if snapshot is not None else {"source": "pending"}
        self.displayed = self.latest
        self.has_snapshot = snapshot is not None
        self.metrics_model = overview_build(snapshot) if snapshot is not None else None
        self.connection_state = "connected" if self.has_snapshot else "connecting"
        self.interval = interval
        self.requests = requests
        self.view = 0
        self.frozen = False
        self.polling = False
        self.closing = False
        self.error: str | None = None
        self.failures = 0
        self.next_poll_at = 0.0
        self.next_age_repaint = 0.0
        self.filter_text = ""
        self.theme_choice = "terminal" if theme == "auto" else theme
        self.color_choice = "never" if os.environ.get("NO_COLOR") is not None else color
        self.glyph_choice = choose_glyphs(glyphs, sys.stdout.encoding)

    def compose(self) -> ComposeResult:
        with Vertical(id="shell"):
            yield Static("", id="content", markup=False)
            yield Input(placeholder="Filter recent requests by text, then Enter", max_length=80, id="filter")
            yield Static("Read only  |  llamamon never sends the server anything", id="message", markup=False)

    def on_mount(self) -> None:
        self._apply_theme()
        self._paint()
        self.query_one("#filter", Input).disabled = True
        self.set_focus(None)
        self.set_interval(self.interval, self._schedule_poll)
        if not self.has_snapshot:
            self._schedule_poll(force=True)

    def _apply_theme(self) -> None:
        bg, panel, fg, mute = SHELL[self.theme_choice]
        if self.color_choice == "never":
            bg, panel, fg, mute = "transparent", "transparent", "ansi_default", "ansi_default"
        self.screen.styles.background = bg
        self.query_one("#shell").styles.background = panel
        self.query_one("#content").styles.color = fg
        self.query_one("#message").styles.color = mute

    def _view_count(self) -> int:
        return nav.count()

    def _shown(self) -> dict:
        shown = dict(self.displayed)
        shown["requests"] = (self.displayed.get("requests") or [])[:self.requests]
        return shown

    def _paint(self, size=None) -> None:
        size = self.screen.size if size is None else size
        snapshot = self._shown()
        if self.view == 0 and size.width >= 78 and size.height >= 23 and self.metrics_model is not None:
            display = overview_render(
                self.metrics_model, width=max(30, min(size.width - 2, 100)), height=max(8, size.height - 2),
                connection=self.endpoint.base_url, connection_state=self.connection_state, frozen=self.frozen,
                glyphs=self.glyph_choice, color=self.color_choice != "never", theme=self.theme_choice,
                view_label=nav.title(0), bullet="●")
        else:
            content = render_view(snapshot, VIEWS[self.view] if self.view < len(VIEWS) else "Overview",
                                  width=max(30, size.width - 2), height=max(8, size.height - 2),
                                  frozen=self.frozen, filter_text=self.filter_text,
                                  glyphs="ascii" if self.glyph_choice == "solid" else self.glyph_choice,
                                  connection=self.endpoint.base_url, connection_state=self.connection_state)
            display = Text(content, no_wrap=True, overflow="crop")
            if self.color_choice != "never":
                accent = ACCENT.get(self.theme_choice, "#34d399")
                muted = MUTED.get(self.theme_choice, "#8e979f")
                offset = 0
                for line in content.splitlines(keepends=True):
                    stripped = line.strip()
                    if stripped.startswith(("llamamon", "Model state", "Recent requests", "Slots (", "Now running",
                                            "TIME", "Activity", "Graphics card", "GPU ", "PCIe link", "Context", "Model:")):
                        display.stylize(f"bold {accent}", offset, offset + len(line.rstrip("\n")))
                    elif stripped.startswith(("---", "llamamon  ", "Read only")):
                        display.stylize(muted, offset, offset + len(line.rstrip("\n")))
                    offset += len(line)
                display.highlight_regex(r"\[LIVE\]|\[\d [^\]]+\]", f"bold {accent}")
                display.highlight_regex(r"\[(?:STALE|FROZEN|CONNECTING)\]", "bold yellow")
                display.highlight_regex(r"\[(?:DISCONNECTED|AUTH REQUIRED|INCOMPATIBLE)\]", "bold red")
        self.query_one("#content", Static).update(display)
        if self.connection_state == "connecting":
            msg = "Connecting to llama.cpp"
        elif self.connection_state == "telemetry_unavailable":
            msg = "Telemetry unavailable; status only"
        elif self.frozen:
            msg = "Frozen display; collection continues"
        else:
            msg = "Read only  |  q quits without touching the server"
        self.query_one("#message", Static).update(self.error or msg)

    def _schedule_poll(self, force: bool = False) -> None:
        now = time.monotonic()
        if not self.closing and not self.polling and (force or now >= self.next_poll_at):
            self.polling = True  # claim the cycle before create_task can be called again
            asyncio.create_task(self._poll())
        elif not self.closing and self.is_mounted and self.connection_state != "connected" and now >= self.next_age_repaint:
            self.next_age_repaint = now + 1.0
            self._paint()

    async def _poll(self) -> None:
        try:
            fresh = await asyncio.to_thread(self.endpoint.snapshot, self.sampler)
            self.latest = fresh
            self.metrics_model = overview_build(fresh)
            if not self.frozen:
                self._show_snapshot(fresh)
            self.has_snapshot = True
            self.connection_state = "connected"
            self.error = None
            self.failures = 0
            self.next_poll_at = 0.0
        except Exception as e:                               # a ClientError or anything the sampler tripped
            self.endpoint.note_gap()
            self.error = str(e) or "Collection failed"
            self.failures += 1
            alive = await asyncio.to_thread(self.endpoint.probe)
            if alive:                                        # the server is answering between requests: a generation is just running
                self.connection_state = "stale" if self.has_snapshot else "connecting"
                self.next_poll_at = time.monotonic() + self.interval
            else:
                self.connection_state = "stale" if self.has_snapshot and self.failures < 3 else "disconnected"
                self.next_poll_at = time.monotonic() + min(30.0, 2.0 ** min(self.failures, 5) *
                                                           random.uniform(0.8, 1.2))
        finally:
            self.polling = False
            if not self.closing and self.is_mounted:
                self._paint()

    def _show_snapshot(self, fresh: dict) -> None:
        self.displayed = fresh

    def action_browser(self) -> None:
        import webbrowser
        try:
            opened = webbrowser.open(self.endpoint.base_url)
        except Exception:                                    # noqa: BLE001
            opened = False
        self.error = f"Opened {self.endpoint.base_url} in your browser" if opened else \
            f"Open {self.endpoint.base_url} in a browser yourself (none could be started here)"
        self._paint()

    def on_resize(self, event) -> None:
        self._paint(event.size)

    def action_quit(self) -> None:
        self.closing = True
        self.exit()

    def action_freeze(self) -> None:
        self.frozen = not self.frozen
        if not self.frozen:
            self._show_snapshot(self.latest)
        self._paint()

    def action_refresh(self) -> None:
        self._schedule_poll(force=True)

    def action_theme_cycle(self) -> None:
        choices = ("terminal", "dark", "light", "tokyonight")
        self.theme_choice = choices[(choices.index(self.theme_choice) + 1) % len(choices)]
        self._apply_theme()
        self._paint()

    def action_filter_open(self) -> None:
        field = self.query_one("#filter", Input)
        field.disabled = False
        field.styles.display = "block"
        field.value = self.filter_text
        field.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        cleaned = safe(event.value, 80)
        if event.value != cleaned:
            event.input.value = cleaned

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.filter_text = safe(event.value, 80)
        event.input.styles.display = "none"
        event.input.disabled = True
        self.set_focus(None)
        self._paint()

    def action_filter_clear(self) -> None:
        self.filter_text = ""
        field = self.query_one("#filter", Input)
        field.styles.display = "none"
        field.disabled = True
        self.set_focus(None)
        self._paint()

    def action_previous_view(self) -> None:
        self._set_view((self.view - 1) % self._view_count())

    def action_next_view(self) -> None:
        self._set_view((self.view + 1) % self._view_count())

    def action_view_0(self) -> None:
        self._set_view(0)

    def action_view_1(self) -> None:
        self._set_view(1)

    def action_view_2(self) -> None:
        self._set_view(2)

    def action_view_3(self) -> None:
        self._set_view(3)

    def action_view_4(self) -> None:
        self._set_view(4)

    def action_view_5(self) -> None:
        self._set_view(5)

    def _set_view(self, view: int) -> None:
        self.view = view
        self._paint()

    def action_help(self) -> None:
        self.error = f"{nav.keys()} / Left-Right views   t theme   Space freeze   r refresh   / filter   b browser   q quit"
        self._paint()
