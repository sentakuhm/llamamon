"""The host hardware behind the model: the graphics card, the CPU, the RAM, and the disks.

llama.cpp reports nothing about the machine it runs on, so the monitor samples the host itself:
* `rocm-smi` (AMD GPUs) or `nvidia-smi` (NVIDIA GPUs) for the card's temperature, power, load,
  VRAM and PCIe link — one subprocess per refresh on NVIDIA, two on AMD (the card info, then
  the VRAM bytes), parsed defensively from its JSON/CSV;
* `sensors` (lm_sensors) for the CPU's own temperature when it is installed, cached for a few
  seconds because it runs a subprocess;
* `psutil` for the CPU, the RAM and the disk read/write rates when it is installed, and a plain
  `/proc` fallback when it is not (psutil is optional: the monitor still draws, with `--` gaps).

Unknown readings are always `None`, never a made-up zero — the renderers show `--` for them.
"""

from __future__ import annotations

import math
import re
import shutil
import subprocess
import time
from typing import Any

GIB = 2 ** 30
PCIe_GEN = {32: 1, 64: 2, 128: 3, 160: 4, 320: 5, 640: 6, 1280: 7}   # link speed in 0.1 GT/s -> generation


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, str):                      # rocm-smi's JSON carries every number as a string
        try:
            v = float(v)
        except ValueError:
            return None
    elif not isinstance(v, (int, float)):
        return None
    try:
        f = float(v)
    except (ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _text(v: Any) -> str | None:
    return None if v is None else str(v).strip() or None


class Sampler:
    """A few seconds of host telemetry: one subprocess per GPU kind, cached until the next refresh."""

    def __init__(self, refresh_s: float = 1.5):
        self.refresh_s = refresh_s
        self._cards: list[dict] = []
        self._cards_at = 0.0
        self._cpu_temp: float | None = None
        self._cpu_temp_at = 0.0
        self._cpu_primed = False                                      # psutil's first cpu_percent call only primes its counter
        self._ticks: tuple[float, float] | None = None                # (/proc/stat: total, idle)
        self._io_bytes: tuple[float, int, int] | None = None          # psutil path: (when, read bytes, written bytes)
        self._io_sectors: tuple[float, int, int] | None = None        # /proc path: (when, read sectors, written sectors)

    # ---- the graphics cards -----------------------------------------------------------------------

    def _rocm(self) -> list[dict]:
        """One card per `rocm-smi` entry, merged from --showallinfo (temps/power/load/PCIe) and --showmeminfo vram (VRAM bytes)."""
        import json
        cards: dict[str, dict] = {}
        for flags in (("--showallinfo", "--json"), ("--showmeminfo", "vram", "--json")):
            if shutil.which("rocm-smi") is None:
                return []
            try:
                out = subprocess.run(("rocm-smi", *flags), capture_output=True, timeout=4.0,
                                     check=False).stdout.decode(errors="replace")
            except (OSError, subprocess.SubprocessError):
                continue
            try:
                data = json.loads(out)
            except (ValueError, SyntaxError):
                continue
            if not isinstance(data, dict):
                continue
            for card_id, fields in data.items():
                if isinstance(fields, dict):
                    cards.setdefault(card_id, {}).update(fields)
        results = []
        for index, (_card_id, fields) in enumerate(sorted(cards.items())):
            temp = next((_num(v) for k, v in fields.items() if "Temperature (Sensor" in k and "(C)" in k), None)
            power = next((_num(v) for k, v in fields.items() if "Package Power" in k and "Average" in k), None)
            power_limit = next((_num(v) for k, v in fields.items() if "Max Graphics Package Power" in k), None)
            util = next((_num(v) for k, v in fields.items() if "GPU use" in k), None)
            mem_total = next((_num(v) for k, v in fields.items() if "VRAM Total Memory (B)" in k), None)
            mem_used = next((_num(v) for k, v in fields.items() if "VRAM Total Used Memory (B)" in k), None)
            width = next((_num(v) for k, v in fields.items() if "pcie_link_width" in k), None)
            speed = next((_num(v) for k, v in fields.items() if "pcie_link_speed" in k), None)
            name = _text(next((v for k, v in fields.items() if "Device Name" in k), None))
            if util is None and temp is None and mem_total is None and name is None:
                continue
            results.append({"index": index, "name": name, "util": util, "mem_used": mem_used, "mem_total": mem_total,
                            "temp": temp, "power": power, "power_limit": power_limit,
                            "pcie_width": width,
                            "pcie_gen": PCIe_GEN.get(int(speed)) if speed is not None and int(speed) in PCIe_GEN else None})
        return results

    def _nvidia(self) -> list[dict]:
        """One card per row of a single nvidia-smi query (name, memory, temperature, power, load)."""
        if shutil.which("nvidia-smi") is None:
            return []
        try:
            done = subprocess.run(("nvidia-smi",
                                   "--query-gpu=name,memory.used,memory.total,temperature.gpu,utilization.gpu,power.draw,power.limit",
                                   "--format=csv,noheader,MiB,W,C,%"), capture_output=True, timeout=4.0, check=False)
        except (OSError, subprocess.SubprocessError):
            return []
        rows = done.stdout.decode(errors="replace").strip()
        if done.returncode != 0 or not rows:
            return []
        gib = 2 ** 20
        results = []
        for index, row in enumerate(rows.splitlines()):
            parts = [p.strip() for p in row.split(",")]
            if len(parts) < 5:
                continue
            used, total = _num(parts[1]), _num(parts[2])
            results.append({"index": index, "name": parts[0],
                            "util": _num(parts[4]),
                            "mem_used": used * gib if used is not None else None,
                            "mem_total": total * gib if total is not None else None,
                            "temp": _num(parts[3]),
                            "power": _num(parts[5]) if len(parts) > 5 else None,
                            "power_limit": _num(parts[6]) if len(parts) > 6 else None,
                            "pcie_width": None, "pcie_gen": None})   # NVIDIA reports no PCIe link: shown as --
        return results

    def devices(self) -> list[dict]:
        """The cards right now: util %, VRAM used/total in bytes, temperature, power and the PCIe link."""
        now = time.monotonic()
        if self._cards_at and now - self._cards_at < self.refresh_s:
            return self._cards
        self._cards = self._rocm() or self._nvidia()
        self._cards_at = now
        return self._cards

    def devices_static(self) -> dict:
        """The card identity and capacity, from the last sample (so nothing re-runs the tool)."""
        cards = self._cards
        if not cards:
            return {}
        totals = [c["mem_total"] for c in cards if c["mem_total"] is not None]
        return {"gpu_name": cards[0]["name"] or None, "gpu_count": float(len(cards)),
                "gpu_vram_total_mib": (sum(totals) / 2 ** 20) if totals else None,
                "pcie_width": next((c["pcie_width"] for c in cards if c["pcie_width"] is not None), None),
                "pcie_gen": next((c["pcie_gen"] for c in cards if c["pcie_gen"] is not None), None)}

    # ---- the cpu, the ram, and the disks -----------------------------------------------------------

    def _proc_cpu(self) -> float | None:
        """The cpu's share of the clock since the last call (/proc/stat: total and idle ticks)."""
        try:
            with open("/proc/stat") as handle:
                fields = handle.readline().split()[1:]
        except (OSError, IndexError, ValueError):
            return None
        if len(fields) < 4:
            return None
        total = sum(_num(f) or 0.0 for f in fields[:8])
        idle = _num(fields[3]) or 0.0
        previous = self._ticks
        self._ticks = (total, idle)
        if previous is None or total <= previous[0] or idle < previous[1]:
            return None
        busy = (total - previous[0]) - (idle - previous[1])
        return 100.0 * busy / (total - previous[0])

    def _proc_ram(self) -> tuple[float | None, float | None]:
        used = total = None
        try:
            with open("/proc/meminfo") as handle:
                want = {"MemTotal": None, "MemAvailable": None}
                for line in handle:
                    key, _, rest = line.partition(":")
                    if key in want and rest.split():
                        want[key] = _num(rest.split()[0])           # kilobytes
                    if want["MemTotal"] is not None and want["MemAvailable"] is not None:
                        break
            if want["MemTotal"] is not None:
                total = want["MemTotal"] * 1024
                if want["MemAvailable"] is not None:
                    used = (want["MemTotal"] - want["MemAvailable"]) * 1024
        except (OSError, IndexError, ValueError):
            pass
        return used, total

    def _proc_disk(self) -> tuple[float | None, float | None]:
        """(read MB/s, written MB/s) from whole disks in /proc/diskstats (sectors of 512 bytes, fields 6 and 9)."""
        reads = writes = 0
        seen = False
        try:
            with open("/proc/diskstats") as handle:
                for line in handle:
                    parts = line.split()
                    if len(parts) < 14:
                        continue
                    name = parts[2]
                    if not re.fullmatch(r"(?:sd|vd|hd)[a-z]|nvme\d+n\d+|mmcblk\d+|nokaport\d+t\d+", name):
                        continue                                  # partitions have their own line: whole disks only
                    reads += int(parts[5])
                    writes += int(parts[8])
                    seen = True
        except (OSError, IndexError, ValueError):
            return None, None
        if not seen:
            return None, None
        now = time.monotonic()
        previous = self._io_sectors
        self._io_sectors = (now, reads, writes)
        if previous is None or now - previous[0] <= 0.2 or reads < previous[1] or writes < previous[2]:
            return None, None
        elapsed = now - previous[0]
        return (max(0.0, (reads - previous[1]) * 512) / 2 ** 20 / elapsed,
                max(0.0, (writes - previous[2]) * 512) / 2 ** 20 / elapsed)

    def _psutil(self):
        """The installed psutil module, or None when it is not (the monitor keeps working with /proc)."""
        try:
            import psutil
            return psutil
        except ImportError:
            return None

    def _sensors_cpu_temp(self) -> float | None:
        """A CPU temperature from lm_sensors output: the k0temp/k10temp Tctl (AMD), an Intel cputemp Tdie."""
        if shutil.which("sensors") is None:
            return None
        try:
            out = subprocess.run(("sensors",), capture_output=True, timeout=4.0, check=False).stdout.decode(errors="replace")
        except (OSError, subprocess.SubprocessError):
            return None
        found: dict[str, float] = {}
        for block in out.split("\n\n"):        # sensors separates one probe group per blank line
            lines = block.splitlines()
            if not lines or not re.match(r"(k\d+temp|intel-\S+|cpu\w*temp|qos\d+)", lines[0]):
                continue
            for line in lines[1:]:
                name, _, rest = line.strip().partition(":")
                number = re.search(r"[-+]?\d+(?:\.\d+)?", rest)
                value = _num(number.group(0)) if number else None
                if value is not None and not name.endswith(("crit", "low", "high")):
                    found.setdefault(name.strip(), value)
        for want in ("Tctl", "Tdie", "Tmon", "Tccd", "temp1", "temp"):
            for name, value in found.items():
                if name.startswith(want):
                    return value
        return None

    def cpu_temp(self) -> float | None:
        """The cached CPU temperature, refreshed at most every few seconds (a `sensors` run per sample costs)."""
        now = time.monotonic()
        if self._cpu_temp_at and now - self._cpu_temp_at < 10.0:
            return self._cpu_temp
        self._cpu_temp_at = now
        self._cpu_temp = self._sensors_cpu_temp()
        return self._cpu_temp

    def sample(self) -> dict:
        """This moment's readings, merged with the cached card sample."""
        cards = self.devices()
        psutil = self._psutil()
        if psutil:
            try:
                if self._cpu_primed:
                    cpu_pct = float(psutil.cpu_percent(interval=None))
                else:
                    psutil.cpu_percent(interval=None)   # prime psutil's internal counter; the first real sample follows
                    self._cpu_primed = True
                    cpu_pct = None                      # like the /proc path: no reading yet, draw --
            except (OSError, ValueError, AttributeError):
                cpu_pct = self._proc_cpu()
            try:
                vm = psutil.virtual_memory()
                ram_used, ram_total = float(vm.used), float(vm.total)
            except (AttributeError, TypeError, ValueError):
                ram_used, ram_total = self._proc_ram()
        else:
            cpu_pct = self._proc_cpu()
            ram_used, ram_total = self._proc_ram()
        read_mb = write_mb = None
        if psutil:
            try:
                io = psutil.disk_io_counters()
                if io is not None:
                    now = time.monotonic()
                    counters = (now, int(io["read_bytes"]), int(io["write_bytes"]))
                    previous = self._io_bytes
                    self._io_bytes = counters
                    if previous is not None and now - previous[0] > 0.2:
                        elapsed = now - previous[0]
                        read_mb = max(0.0, counters[1] - previous[1]) / 2 ** 20 / elapsed
                        write_mb = max(0.0, counters[2] - previous[2]) / 2 ** 20 / elapsed
            except (KeyError, OSError, ValueError, TypeError):
                read_mb = write_mb = None
        if read_mb is None and write_mb is None:
            read_mb, write_mb = self._proc_disk()
        busiest = [c["util"] for c in cards if c["util"] is not None]
        used = [c["mem_used"] for c in cards if c["mem_used"] is not None]
        total = [c["mem_total"] for c in cards if c["mem_total"] is not None]
        temps = [c["temp"] for c in cards if c["temp"] is not None]
        powers = [c["power"] for c in cards if c["power"] is not None]
        limits = [c["power_limit"] for c in cards if c["power_limit"] is not None]
        return {
            "gpu_util": max(busiest) if busiest else None,
            "gpu_mem_used": sum(used) if used else None,
            "gpu_mem_total": sum(total) if total else None,
            "gpu_temp": max(temps) if temps else None,
            "gpu_power": sum(powers) if powers else None,
            "gpu_power_limit": sum(limits) if limits else None,
            "gpu_pcie_rx_mb": None,        # the monitor cannot see the host-to-card transfer rate: shown as --
            "gpu_pcie_gen": next((c["pcie_gen"] for c in cards if c["pcie_gen"] is not None), None),
            "gpu_pcie_gen_max": None,
            "gpu_pcie_width": next((c["pcie_width"] for c in cards if c["pcie_width"] is not None), None),
            "cpu_pct": cpu_pct, "cpu_temp": self.cpu_temp(),
            "disk_read_mb": read_mb, "disk_write_mb": write_mb,
            "ram_used": ram_used, "ram_total": ram_total,
            "gpus": cards,
        }

    def static(self) -> dict:
        """What the machine is: the card's name and capacity, the cpu's cores and threads."""
        cards = self.devices_static()
        psutil = self._psutil()
        if psutil:
            try:
                threads = float(psutil.cpu_count(logical=True) or 0) or None
            except (OSError, ValueError, AttributeError):
                threads = None
            try:
                cores = float(psutil.cpu_count(logical=False) or 0) or None
            except (OSError, ValueError, AttributeError):
                cores = threads
        else:
            threads = cores = None
            try:
                with open("/proc/cpuinfo") as handle:
                    threads = float(sum(1 for line in handle if line.startswith("processor"))) or None
            except OSError:
                pass
        return {"gpu_name": cards.get("gpu_name"), "gpu_count": cards.get("gpu_count"),
                "gpu_vram_total_mib": cards.get("gpu_vram_total_mib"),
                "cores": cores, "threads": threads, "sockets": None, "numa_nodes": None}

    def gap(self) -> dict:
        """One missing sample for every history series (an outage stays visible)."""
        return {key: None for key in ("gpu_util", "gpu_mem_used", "gpu_temp", "gpu_power",
                                      "gpu_pcie_rx_mb", "cpu", "disk_read_mb")}
