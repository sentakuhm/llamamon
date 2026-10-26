"""Read-only transport for a llama.cpp server, and the numbers the views draw.

The server answers `GET /props` (the model and its capacity), `GET /slots` (one entry per slot:
what it is reading, decoding, or caching) and `GET /health`; `GET /v1/models` gives the file behind
the alias. Plain llama.cpp reports no history of its own: it answers one request at a time and a
busy slot delays the calls between requests, so the histories, rates and request log below are
built from this monitor's own samples, and a sample that timed out is a gap in the series, never
a zero. A server that answers `GET /metrics` (a llama.cpp fork that keeps its own live state,
series and request log) reports what plain llama.cpp cannot see while a request runs, so when it
answers, its numbers take priority over the samples.

`snapshot()` returns the numbers every renderer draws: `live` (what the model is doing now),
`engine` (the model's facts), `hardware` (the host sample), `hardware_static` (the machine's identity),
`history` (this monitor's series, oldest first, None where a poll timed out), `requests` (the finished
requests this monitor saw, newest first), `slots` (one row per slot, for the Serving view) and
`time` (when this was sampled).
"""

from __future__ import annotations

import json
import math
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from typing import Any

MAX_BODY = 4 * 1024 * 1024
HISTORY_LIMIT = 90
RECENT_LIMIT = 24
ACTIVITY_LIMIT = 24
SERIES = ("tok_s", "prefill_tok_s_mean", "gpu_util", "gpu_mem_used", "gpu_temp",
          "gpu_power", "gpu_pcie_rx_mb", "cpu", "disk_read_mb")


def server_url(raw: str) -> str:
    """Require a plain http(s) server root, without a path, query, fragment or credentials."""
    if not isinstance(raw, str):
        raise ValueError("URL must be an http(s) server root")
    u = urllib.parse.urlsplit(raw)
    if u.scheme not in ("http", "https") or not u.hostname or "@" in u.netloc:
        raise ValueError("URL must be an http(s) server root without embedded credentials")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise ValueError("URL must be the server root, without a path, query or fragment")
    try:
        port = u.port
    except ValueError as e:
        raise ValueError("invalid server port") from e
    host = u.hostname.lower()
    local = host in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not local:
        raise ValueError("a remote llama.cpp server needs https (or ssh to it and watch the tunnel)")
    authority = f"[{host}]" if ":" in host else host
    return f"{u.scheme}://{authority}{':' + str(port) if port is not None else ''}"


class ClientError(Exception):
    """One failed round of collection; the monitor turns it into CONNECTING / STALE / DISCONNECTED."""

    def __init__(self, message: str, exit_code: int = 3, *, http_status: int | None = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.http_status = http_status


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return float(v)


def _text(v: Any) -> str | None:
    return None if v is None else str(v).strip() or None


def _merge_live(local: dict, server: Any) -> dict:
    """A server /metrics live block knows what the model is doing better than the slots array can."""
    if not isinstance(server, dict):
        return local
    merged = dict(local)
    state = _text(server.get("state"))
    if state in ("decoding", "processing"):
        state = "generating"
    if state in ("idle", "reading", "generating"):
        merged["state"] = state
    for key in ("queued", "prompt_read", "prompt_total", "generated", "max_tokens",
                "prompt_tokens", "tok_s", "prefill_tok_s_mean"):
        value = _num(server.get(key))
        if value is not None:
            merged[key] = value
    return merged


def _merge_engine(local: dict, server: Any) -> dict:
    """Engine facts from a server /metrics engine block fill what /props left out."""
    if not isinstance(server, dict):
        return local
    merged = dict(local)
    for key in ("model", "version", "ftype", "path"):
        if not merged.get(key):
            merged[key] = _text(server.get(key))
    if _num(merged.get("max_context")) is None:
        merged["max_context"] = _num(server.get("max_context"))
    if _num(merged.get("slots")) is None:
        merged["slots"] = _num(server.get("slots"))
    return merged


def _merge_hardware(local: dict, server: Any) -> dict:
    """A server that samples its own host (gpu, cpu, ram, disk) reports it in /metrics; the sampler fills the rest."""
    if not isinstance(server, dict):
        return local
    merged = dict(local)
    for key in ("gpu_util", "gpu_mem_used", "gpu_mem_total", "gpu_temp", "gpu_power", "gpu_power_limit",
                "gpu_pcie_gen", "gpu_pcie_gen_max", "gpu_pcie_width", "gpu_pcie_rx_mb",
                "ram_used", "ram_total", "disk_read_mb", "disk_write_mb"):
        value = _num(server.get(key))
        if value is not None:
            merged[key] = value
    cpu = _num(server.get("cpu"))
    if cpu is not None:
        merged["cpu_pct"] = cpu
    return merged


def _merge_static(local: dict, server: Any) -> dict:
    """The machine's identity from a server /metrics hardware_static block, under what the sampler saw."""
    if not isinstance(server, dict):
        return local
    merged = dict(local)
    for key in ("cores", "threads", "sockets", "numa_nodes", "gpu_count", "gpu_vram_total_mib"):
        if _num(merged.get(key)) is None:
            merged[key] = _num(server.get(key))
    for key in ("gpu_name", "cpu_name"):
        if not merged.get(key):
            merged[key] = _text(server.get(key))
    return merged


def _server_requests(raw: Any) -> list[dict]:
    """A server /metrics request log, newest first, in the shape the views draw."""
    if not isinstance(raw, list):
        return []
    rows = []
    for r in raw[:RECENT_LIMIT]:
        if not isinstance(r, dict):
            continue
        mapped = {"time": _num(r.get("time")), "error": False,
                  "finish_reason": _text(r.get("finish")) or "stop",
                  "prompt_tokens": _num(r.get("prompt_tokens")), "reused": _num(r.get("reused")),
                  "output_tokens": _num(r.get("output_tokens")),
                  "decode_tok_s": _num(r.get("decode_tok_s")), "duration_s": _num(r.get("duration_s"))}
        if any(v is not None for v in (mapped["prompt_tokens"], mapped["output_tokens"], mapped["duration_s"])):
            rows.append(mapped)
    rows.sort(key=lambda r: r["time"] or 0.0, reverse=True)
    return rows


class SlotState:
    """What llama.cpp's slot array says one slot is doing, and the last request it saw."""

    def __init__(self, slot_id: int):
        self.slot_id = slot_id
        self.request_id: int | None = None      # llama.cpp's id_task
        self.active = False
        self.since = None                       # first sample that saw this request
        self.decoding_since = None              # first sample that saw it decoding
        self.prompt_total = self.prompt_read = self.cached = None
        self.prompt_size = None                 # the prompt's own size; n_prompt_tokens grows to hold prompt + reply
        self.decoded = self.remaining = None

    def observe(self, row: dict, now: float) -> dict:
        """Update with one sample of this slot; return its display row."""
        task = _num(row.get("id_task"))
        task = int(task) if task is not None and task >= 0 else None
        processing = bool(row.get("is_processing"))
        finished = None
        if self.active and (task != self.request_id or not processing):
            finished = self.complete(now, release_total=_num(row.get("n_prompt_tokens")))
            # finalize from the last busy sample; the release row already reset the counters
            self.request_id = task
            self.since = self.decoding_since = None
            self.prompt_total = self.prompt_read = self.cached = None
            self.prompt_size = None
            self.decoded = self.remaining = None
        self.prompt_total = _num(row.get("n_prompt_tokens"))
        self.prompt_read = _num(row.get("n_prompt_tokens_processed"))
        self.cached = _num(row.get("n_prompt_tokens_cache"))
        nxt = row.get("next_token")
        if isinstance(nxt, list):
            nxt = next((item for item in nxt if isinstance(item, dict)), {})
        elif not isinstance(nxt, dict):
            nxt = {}
        self.decoded = _num(nxt.get("n_decoded"))
        self.remaining = _num(nxt.get("n_remain"))
        self.active = processing and task is not None
        if self.active:
            if self.since is None:
                self.since = now
            decoding = (self.decoded or 0.0) > 0
            if decoding and self.decoding_since is None:
                self.decoding_since = now
            if decoding:
                self.prompt_size = max(self.prompt_size or 0.0, self.prompt_read or 0.0)
            else:
                self.prompt_size = max(self.prompt_size or 0.0, self.prompt_total or 0.0)
        decoding = self.active and (self.decoded or 0.0) > 0
        state = "decoding" if decoding else "reading" if self.active else "idle"
        tok_s = None
        if decoding and self.decoding_since is not None and self.decoded and now > self.decoding_since:
            tok_s = self.decoded / (now - self.decoding_since)
        return {"slot": self.slot_id, "state": state, "request_id": self.request_id,
                "prompt_tokens": self.prompt_size, "prompt_read": self.prompt_read,
                "cached": self.cached, "generated": self.decoded if decoding else None,
                "remaining": self.remaining if decoding else None,
                "tok_s": round(tok_s, 1) if tok_s is not None else None,
                "since": self.since, "finished": finished}

    def complete(self, now: float, release_total: float | None = None) -> dict | None:
        """The finished request this slot just stopped running, or None if it never ran here."""
        if not self.since or not self.prompt_size:
            return None
        duration = now - self.since
        decode_s = now - self.decoding_since if self.decoding_since else None
        output = self.decoded
        if output is None or (output == 0 and decode_s is None):
            output = None            # we never saw a decoding sample; the release row had reset the counter
        if release_total is not None:
            # the release row keeps the final KV size (prompt + reply), so it can hand back
            # the tokens that finished between the last sample and the release
            final = release_total - self.prompt_size
            if final > (output or 0):
                output = final
        return {"time": now, "error": False, "finish_reason": "stop",
                "prompt_tokens": self.prompt_size, "reused": self.cached,
                "output_tokens": output,
                "decode_tok_s": (round(output / decode_s, 1) if output and decode_s and decode_s > 0 else None),
                "duration_s": round(duration, 1)}


class Endpoint:
    """One llama.cpp server: its endpoints, its host sampler, and the series built from its samples."""

    def __init__(self, root: str, *, timeout: float = 4.0):
        self.base_url = server_url(root)
        self.timeout = min(max(timeout, 0.5), 30.0)
        self._slots: dict[int, SlotState] = {}
        self._recent: list[dict] = []
        self._activity: deque = deque(maxlen=ACTIVITY_LIMIT)
        self._history: dict[str, deque] = {key: deque(maxlen=HISTORY_LIMIT) for key in SERIES}
        self._last_props: dict = {}
        self._last_requests: list[dict] = []

    def get(self, path: str, timeout: float | None = None):
        """GET one of the server's read-only endpoints; a slot array is a list, everything else an object."""
        if path not in ("/props", "/slots", "/health", "/v1/models", "/metrics"):
            raise ValueError("endpoint is not part of the read-only llama.cpp contract")
        req = urllib.request.Request(self.base_url + path, headers={"Accept": "application/json",
                                                                    "User-Agent": "llamamon/0.1"})
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=timeout or self.timeout) as response:
                body = response.read(MAX_BODY + 1)
                if len(body) > MAX_BODY:
                    raise ClientError("server response exceeds the 4 MiB limit")
        except urllib.error.HTTPError as e:
            if e.code in (301, 302, 303, 307, 308):
                raise ClientError("the server redirected; use the final server root", http_status=e.code) from e
            raise ClientError(f"llama.cpp request failed: HTTP {e.code}", http_status=e.code) from e
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as e:
            raise ClientError(f"cannot reach llama.cpp server: {getattr(e, 'reason', type(e).__name__)}") from e
        try:
            data = json.loads(body)
        except (ValueError, UnicodeDecodeError, RecursionError) as e:
            raise ClientError("server returned malformed JSON") from e
        if not isinstance(data, (dict, list)):
            raise ClientError("server returned a non-object response")
        return data

    def get_metrics(self) -> dict | None:
        """GET /metrics for a fork that keeps its own live state and request log; plain llama.cpp answers 501/404."""
        try:
            data = self.get("/metrics")
        except ClientError:
            return None
        if not isinstance(data, dict):
            return None
        return data if any(key in data for key in ("live", "requests", "engine", "history")) else None

    def probe(self) -> bool:
        """Is the server there at all? (a long generation delays /slots; /health answers between requests)"""
        try:
            data = self.get("/health", timeout=min(self.timeout, 2.0))
        except ClientError:
            return False
        return isinstance(data, dict) and data.get("status") == "ok"

    def note_gap(self) -> None:
        """A round that timed out: keep the outage visible in every series."""
        for series in self._history.values():
            series.append(None)

    def add_history(self, live: dict, hardware: dict, now: float) -> None:
        """One sample into every series, so the tiles' sparklines keep a minute of the monitor's own polls."""
        reading = live.get("state") == "reading"
        speed = live.get("tok_s") if not reading else None
        prefill = live.get("prefill_tok_s_mean") if reading else None
        values = {"tok_s": _num(speed), "prefill_tok_s_mean": _num(prefill),
                  "gpu_util": _num(hardware.get("gpu_util")), "gpu_mem_used": _num(hardware.get("gpu_mem_used")),
                  "gpu_temp": _num(hardware.get("gpu_temp")), "gpu_power": _num(hardware.get("gpu_power")),
                  "gpu_pcie_rx_mb": _num(hardware.get("gpu_pcie_rx_mb")), "cpu": _num(hardware.get("cpu_pct")),
                  "disk_read_mb": _num(hardware.get("disk_read_mb"))}
        for key, series in self._history.items():
            series.append(values[key])

    def snapshot(self, sampler) -> dict:
        """The numbers every renderer draws, sampled now. Raises ClientError when the server cannot be read."""
        now = time.time()
        props = self.get("/props")
        if not isinstance(props, dict):
            props = {}
        if not props:
            props = self._last_props
        else:
            self._last_props = props
        metrics = self.get_metrics()
        try:
            slots_raw = self.get("/slots")
            if not isinstance(slots_raw, list):
                raise ClientError("the slots endpoint returned a non-array response")
        except ClientError:
            if metrics is None:
                raise
            slots_raw = []            # a fork that delayed /slots still told us everything through /metrics

        rows: list[dict] = []
        seen: set[int] = set()
        for raw in slots_raw[:64]:
            if not isinstance(raw, dict):
                continue
            index = _num(raw.get("id"))
            if index is None or index < 0 or int(index) in seen:
                continue
            index = int(index)
            seen.add(index)
            state = self._slots.get(index) or SlotState(index)
            before_decoded = state.decoded
            row = state.observe(raw, now)
            finished = row.pop("finished")
            if finished and (finished["output_tokens"] or finished["prompt_tokens"]):   # this request just finished
                self._recent.insert(0, finished)
                del self._recent[RECENT_LIMIT:]
                self._activity.appendleft({"type": "request finished", "time": now,
                                           "prompt_tokens": finished["prompt_tokens"],
                                           "output_tokens": finished["output_tokens"],
                                           "tok_s": finished["decode_tok_s"],
                                           "duration_s": finished["duration_s"]})
            elif state.active and before_decoded is not None and (state.decoded or 0) > before_decoded and \
                    state.decoded is not None and state.decoded - before_decoded > 512:
                self._activity.appendleft({"type": "decoding burst", "time": now, "tokens": int(state.decoded - before_decoded)})
            self._slots[index] = state
            rows.append(row)

        reading = any(r["state"] == "reading" for r in rows)
        decoding = any(r["state"] == "decoding" for r in rows)
        state = "reading" if reading else "generating" if decoding else "idle"
        lead = next((r for r in rows if r["state"] != "idle"), None)
        live = {
            "state": state, "queued": 0,
            "prompt_read": lead["prompt_read"] if lead and lead["state"] == "reading" else None,
            "prompt_total": lead["prompt_tokens"] if lead and lead["state"] == "reading" else None,
            "generated": lead["generated"] if lead and lead["state"] == "decoding" else None,
            "max_tokens": (lead["generated"] + lead["remaining"]) if lead and lead["state"] == "decoding"
                          and lead["generated"] is not None and lead["remaining"] is not None else None,
            "prompt_tokens": lead["prompt_tokens"] if lead else None,
            "tok_s": lead["tok_s"] if lead and lead["state"] == "decoding" else None,
            "prefill_tok_s_mean": (lead["prompt_read"] / (now - lead["since"])) if lead and lead["state"] == "reading"
                                   and lead["prompt_read"] and lead["since"] and now > lead["since"] else None,
        }
        max_ctx = _num(props.get("n_ctx"))
        if max_ctx is None:                           # this fork carries n_ctx only under the generation defaults
            defaults = props.get("default_generation_settings")
            if isinstance(defaults, dict):
                max_ctx = _num(defaults.get("n_ctx"))
        engine = {"model": _text(props.get("model_alias")), "error": False, "max_context": max_ctx,
                  "slots": _num(props.get("total_slots")), "ftype": _text(props.get("model_ftype")),
                  "path": _text(props.get("model_path")), "version": _text(props.get("version")),
                  "endpoints": {key: props.get(key) for key in ("endpoint_slots", "endpoint_props", "endpoint_metrics")
                                if isinstance(props.get(key), bool)}}
        if metrics:
            engine = _merge_engine(engine, metrics.get("engine"))
            live = _merge_live(live, metrics.get("live"))
        hardware = sampler.sample()
        static = sampler.static()
        if metrics:
            hardware = _merge_hardware(hardware, metrics.get("hardware"))
            static = _merge_static(static, metrics.get("hardware_static"))
        self.add_history(live, hardware, now)
        history = {key: list(series) for key, series in self._history.items()}
        if metrics and isinstance(metrics.get("history"), dict):
            for key, series in metrics["history"].items():
                if isinstance(series, list):
                    history[key] = [_num(x) for x in series[-HISTORY_LIMIT:]]
        requests = _server_requests(metrics.get("requests")) if metrics else []
        if not requests:
            requests = list(self._recent)
        self._last_requests = requests
        return {"live": live, "engine": engine, "hardware": hardware, "hardware_static": static,
                "history": history, "requests": requests, "slots": rows, "time": now,
                "activity": list(self._activity)}

    def recent(self, limit: int = 12) -> list[dict]:
        """The finished requests: the server's own log when it keeps one, this monitor's otherwise, newest first."""
        return (self._last_requests or self._recent)[:limit]

    def activity(self, limit: int = 12) -> list[dict]:
        """What this monitor watched happen: requests finishing on their own."""
        return list(self._activity)[-limit:]


def _text(v: Any) -> str | None:
    return None if v is None else str(v).strip() or None
