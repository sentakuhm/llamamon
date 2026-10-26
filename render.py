"""The plain-text views (Overview fallback, Serving, Requests, Engine, Hardware, Logs).

Width-aware and read-only: every value is one the sampler or the server actually reported, and
anything unknown draws as `--` rather than a made-up zero. The interactive monitor paints these
plain lines and colours their headings itself; `llamamon status` prints them as they are.
"""

from __future__ import annotations

import time

from llamamon import nav

VIEWS = ("Overview", "Serving", "Requests", "Engine", "Hardware", "Logs")

MARKERS = {"connected": "LIVE", "stale": "STALE", "connecting": "CONNECTING", "disconnected": "DISCONNECTED"}


def safe(value, limit: int = 256) -> str:
    """One line of server text, with control characters dropped before it reaches the screen."""
    text = str(value) if value is not None else ""
    return "".join(ch for ch in text if ch.isprintable())[:limit]


def num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return float(value)


def fmt(v, digits: int = 0, unit: str = "") -> str:
    v = num(v)
    return "--" if v is None else f"{v:,.{digits}f}{unit}"


def k(v) -> str:
    v = num(v)
    if v is None:
        return "--"
    return f"{v / 1024:.0f}K" if v >= 1024 else f"{v:.0f}"


def gib(v) -> str:
    v = num(v)
    return "--" if v is None else f"{v / 2 ** 30:,.1f} GB"


def clock(ts) -> str:
    ts = num(ts)
    return time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "--"


def _marker(connection_state: str, frozen: bool, sampled_at, now) -> str:
    if frozen:
        return "FROZEN"
    marker = MARKERS.get(connection_state, "STALE")
    age = num(sampled_at)
    if marker == "LIVE" and age is not None and now - age > 6:
        marker = "STALE"
    return marker


def _head(view: str, connection: str, model_label: str, connection_state: str, frozen: bool,
          sampled_at, now: float, width: int) -> str:
    marker = _marker(connection_state, frozen, sampled_at, now)
    head = f"llamamon  {view}   {safe(connection, 64)}   {safe(model_label, 48)}".rstrip()
    tail = f"  [{marker}]"
    pad = max(2, width - len(head) - len(tail))
    return head + " " * pad + tail


def _tabs(view_index: int, width: int) -> str:
    return nav.plain(max(0, view_index), width)


def _state_line(live: dict) -> str:
    state = str(live.get("state") or "idle")
    label = {"reading": "reading a prompt", "generating": "generating a reply"}.get(state, "waiting for a request")
    read, total = num(live.get("prompt_read")), num(live.get("prompt_total"))
    gen, maxn = num(live.get("generated")), num(live.get("max_tokens"))
    if state == "reading" and total:
        label += f"  {read:,.0f}/{total:,.0f} prompt tokens read"
    elif state == "generating" and maxn:
        label += f"  {gen:,.0f}/{maxn:,.0f} tokens written"
    return f"Model state: {label}"


def _request_row(r: dict) -> str:
    finish = r.get("finish_reason") or r.get("finish")
    state = "Error" if r.get("error") else ("Stopped" if finish in ("cancelled", "error", "abort") else "Done")
    return (f"{clock(r.get('time'))}  {state:<8} {k(r.get('prompt_tokens')):>7} {k(r.get('reused')):>7}"
            f" {k(r.get('output_tokens')):>7} {fmt(r.get('decode_tok_s'), 1):>7} {fmt(r.get('duration_s'), 1):>6} s")


def request_lines(snapshot: dict, filter_text: str = "", limit: int = 12) -> list[str]:
    """The requests this monitor watched finish, newest first, matching the filter when there is one."""
    rows = []
    for r in (snapshot.get("requests") or [])[:limit]:
        if not isinstance(r, dict):
            continue
        line = _request_row(r)
        if filter_text and filter_text.lower() not in line.lower():
            continue
        rows.append(line)
    return rows


def _slot_lines(slots: list) -> list[str]:
    rows = []
    for s in slots[:16]:
        if not isinstance(s, dict):
            continue
        state = {"reading": "reading", "decoding": "decoding"}.get(s.get("state"), "idle")
        read = f"{k(s['prompt_read']):>8}" if num(s.get("prompt_read")) is not None else "      --"
        rows.append(f"{int(s['slot']):>3}  {state:<8} {k(s.get('prompt_tokens')):>8} {read:>8} {k(s.get('cached')):>8}"
                    f" {k(s.get('generated')):>8} {k(s.get('remaining')):>8} {fmt(s.get('tok_s'), 1):>8}")
    return rows


def _hardware_lines(hw: dict, static: dict) -> list[str]:
    lines = []
    gpus = [g for g in (hw.get("gpus") or []) if isinstance(g, dict)]
    for card in gpus[:8]:
        lines.append(f"GPU {card.get('index')}: {safe(card.get('name'), 40) or 'graphics card'}"
                     f"  util {fmt(card.get('util'))}%"
                     f"  VRAM {gib(card.get('mem_used'))} / {gib(card.get('mem_total'))}"
                     f"  {fmt(card.get('temp'))} °C  {fmt(card.get('power'))} W")
    gen = f"Gen{int(hw['gpu_pcie_gen'])}" if num(hw.get("gpu_pcie_gen")) else "--"
    width = f"x{int(hw['gpu_pcie_width'])}" if num(hw.get("gpu_pcie_width")) else ""
    lines.append(f"PCIe link: {gen} {width} to GPU   (the card's bus: measured from the host, not llama.cpp)")
    lines.append(f"GPU load {fmt(hw.get('gpu_util'))}%   power {fmt(hw.get('gpu_power'))} / {fmt(hw.get('gpu_power_limit'), 0)} W"
                 f"   VRAM {gib(hw.get('gpu_mem_used'))} / {gib(hw.get('gpu_mem_total'))}   temp {fmt(hw.get('gpu_temp'))} °C")
    cores, threads = num(static.get("cores")), num(static.get("threads"))
    cpu_sub = f" ({cores:,.0f} cores · {threads:,.0f} threads)" if cores and threads else ""
    lines.append(f"CPU {fmt(hw.get('cpu_pct'))}%{cpu_sub}   RAM {gib(hw.get('ram_used'))} / {gib(hw.get('ram_total'))}")
    lines.append(f"Disk read {fmt(hw.get('disk_read_mb'), 1)} MB/s   write {fmt(hw.get('disk_write_mb'), 1)} MB/s (host sample)")
    return lines


# ------------------------------------------------------------------ the six views

def render_view(snapshot: dict, view: str, *, width: int, height: int, selected: int = 0,
                frozen: bool = False, filter_text: str = "", glyphs: str = "ascii",
                connection: str = "", connection_state: str = "connecting") -> str:
    """One view as plain lines, within `width` columns and `height` rows."""
    now = time.time()
    width = max(30, min(width, 200))
    height = max(6, height)
    live = snapshot.get("live") if isinstance(snapshot.get("live"), dict) else {}
    engine = snapshot.get("engine") if isinstance(snapshot.get("engine"), dict) else {}
    hw = snapshot.get("hardware") if isinstance(snapshot.get("hardware"), dict) else {}
    static = snapshot.get("hardware_static") if isinstance(snapshot.get("hardware_static"), dict) else {}
    label = f"{safe(engine.get('model'), 40) or 'model'} {k(engine.get('max_context'))}".strip()
    lines = [_head(view, safe(connection, 64), label, connection_state, frozen, snapshot.get("time"), now, width),
             _tabs(VIEWS.index(view) if view in VIEWS else 0, width)]
    body: list[str] = []
    view_index = VIEWS.index(view) if view in VIEWS else 0

    if view_index == 0:                                    # the Overview's text fallback (the Rich Overview is the monitor's own)
        body.append(_state_line(live))
        requests = request_lines(snapshot, filter_text, 6)
        body.append("Recent requests (what this monitor watched finish):" if requests else "Recent requests: none yet")
        body += requests[: max(0, height - len(lines) - len(body) - 2)]

    elif view_index == 1:                                  # Serving: the slots and what each one is doing
        body.append(_state_line(live))
        body.append(f"Slots ({fmt(engine.get('slots'), 0)}):  SLOT  STATE    PROMPT     READ   CACHED   OUTPUT  REMAIN   TOK/S")
        slots_lines = _slot_lines(snapshot.get("slots") or [])
        if not slots_lines:
            body.append("  -- the slots endpoint is delayed by a running request on some builds")
        body += slots_lines
        if hw.get("gpus"):
            body.append("Cards:")
            body += _hardware_lines(hw, static)[:4]

    elif view_index == 2:                                  # Requests: the recent ones this monitor saw
        body.append("TIME       STATUS    PROMPT   REUSED   OUTPUT   MEAN TOK/S   DURATION")
        body.append("  (requests shorter than one poll interval are not recorded; the rate is a mean over the samples seen)")
        requests = request_lines(snapshot, filter_text, 50)
        body += requests[: max(0, height - len(lines) - len(body) - 3)]
        if not requests:
            body.append("  no finished request reached this monitor yet (a running request can delay the server's answers)")
        live_rows = _slot_lines(snapshot.get("slots") or [])
        body.append("Now running:" if any("reading" in r or "decoding" in r for r in live_rows) else "Now running: nothing")

    elif view_index == 3:                                  # Engine: the model and its capacity
        body.append(f"Model: {safe(engine.get('model'), 48) or '--'}   file type {safe(engine.get('ftype'), 32) or '--'}")
        body.append(f"File: {safe(engine.get('path'), 100)}" if engine.get("path") else "File: --")
        ver = safe(engine.get("version"), 24)
        body.append(f"Context: {k(engine.get('max_context'))} tokens   slots: {fmt(engine.get('slots'), 0)}   server: llama.cpp{f' {ver}' if ver else ''}")
        used = num(live.get("prompt_tokens"))
        if used is not None:
            body.append(f"Context in use now: {k(used)} of {k(engine.get('max_context'))}")
        endpoints = engine.get("endpoints") or {}
        parts = [f"{name.replace('endpoint_', '')}={'on' if endpoints.get(name) else 'off'}"
                 for name in ("endpoint_slots", "endpoint_props", "endpoint_metrics")]
        body.append("Endpoints: " + " ".join(parts))

    elif view_index == 4:                                  # Hardware: the host sample, or the server's own report when it answers /metrics
        source = "reported by the server" if snapshot.get("hardware_source") == "server" else "sampled from this machine"
        body.append(f"Hardware ({source}):")
        body.append(f"Graphics card: {safe(static.get('gpu_name'), 40) or '--'}   VRAM total {fmt(static.get('gpu_vram_total_mib') and num(static.get('gpu_vram_total_mib')) / 1024, 1)} GB   {fmt(static.get('gpu_count'), 0)} card(s)")
        body += _hardware_lines(hw, static)

    elif view_index == 5:                                  # Logs: what this monitor watched happen
        body.append("Activity (this monitor's own samples; llama.cpp keeps no log):")
        for ev in list(snapshot.get("activity") or [])[-(max(0, height - len(lines) - len(body) - 3)):]:
            if isinstance(ev, dict):
                when = clock(ev.get("time"))
                if ev.get("type") == "request finished":
                    body.append(f"{when}  request finished  {k(ev.get('prompt_tokens'))} prompt → {k(ev.get('output_tokens'))} output @ {fmt(ev.get('tok_s'), 1)} tok/s in {fmt(ev.get('duration_s'), 1)} s")
                else:
                    body.append(f"{when}  {safe(ev.get('type'), 32)}")

    body.append("Read only — llamamon never talks to the server while you watch.")
    lines += body[: max(0, height - len(lines))]
    return "\n".join(line[:width] for line in lines)


def _detail_lines(snapshot: dict) -> list[str]:
    """The one thing selected with the arrow keys, expanded: a live slot or a recent request."""
    rows = [s for s in (snapshot.get("slots") or []) if isinstance(s, dict) and s.get("state") != "idle"]
    requests = [r for r in (snapshot.get("requests") or []) if isinstance(r, dict)]
    if rows:
        r = rows[0]
        return [f"Slot {r['slot']}: {r['state']}  prompt {k(r.get('prompt_tokens'))} tokens"
                f"  read {k(r.get('prompt_read'))}  cached {k(r.get('cached'))}  output {k(r.get('generated'))}"
                f"  left {k(r.get('remaining'))}  {fmt(r.get('tok_s'), 1)} tok/s"]
    if requests:
        r = requests[0]
        return [f"Request at {clock(r.get('time'))}: prompt {k(r.get('prompt_tokens'))}, reused {k(r.get('reused'))},"
                f" output {k(r.get('output_tokens'))} tokens at {fmt(r.get('decode_tok_s'), 1)} tok/s, {fmt(r.get('duration_s'), 1)} s long"]
    return []
