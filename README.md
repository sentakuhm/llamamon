# llamamon

A terminal monitor for a local [llama.cpp](https://github.com/ggml-org/llama.cpp)
inference server.

![llamamon](llmon.png)

## What is this tool?

llamamon watches one llama.cpp server — by default `http://127.0.0.1:9931` —
and draws, in the same one-screen look as the [Strata Monitor](https://github.com/Niko1221/Strata):

- the model state bar (Idle / Reading / Generating / Queued / Error) and what
  the model is doing right now
- six tiles with sparklines: speed, GPU load (with the card's temperature in
  the tile's top-right corner), VRAM, power, CPU (with the CPU's own
  temperature), disk
- bars for context fill, KV cache reuse and system RAM
- the recent requests, and the Serving / Requests / Engine / Hardware / Logs views

It is read-only: llamamon never sends the server anything.

## How it works

- It polls the server every `interval` seconds (default `1.0`).
- llama.cpp answers one request at a time, so a busy server delays the
  monitor's own calls: the monitor shows that as `STALE` (a generation is
  running), not as a lost connection.
- Plain llama.cpp keeps no history and reports nothing about the machine it
  runs on — every series, rate and the request log come from the monitor's
  own samples, and the GPU/CPU/RAM/disk numbers are sampled from the host
  (`rocm-smi` for AMD, `nvidia-smi` for NVIDIA, `sensors`, `psutil`,
  `/proc` fallbacks).
- A server that answers `GET /metrics` reports its own live state, series and
  request log, and then those numbers take priority.
- Unknown readings always draw as `--`, never a made-up zero.

## Install

### Arch Linux

From the [releases](https://github.com/sentakuhm/llamamon/releases):

```
sudo pacman -U llamamon-0.1.0-1-x86_64.pkg.tar.zst
```

### Debian & Ubuntu

The same `.deb` works on both:

```
sudo apt install ./llamamon_0.1.0_all.deb
```

The full-screen monitor also needs `rich`, `textual` and `psutil`:

```
sudo apt install python3-rich python3-textual python3-psutil
```

### Windows 10/11

Download `llamamon-0.1.0-src.zip` from the [releases](https://github.com/sentakuhm/llamamon/releases),
unzip it, and run it with Python 3.10 or newer:

```
pip install -r requirements.txt
python -m llamamon
```

### From source

```
git clone https://github.com/sentakuhm/llamamon
python3 -m pip install -e llamamon
python3 -m pip install -r llamamon/requirements.txt
llamamon
```

## Use

```
llamamon                        full-screen monitor (needs rich + textual)
llamamon status                 one snapshot, printed and done (stdlib only)
llamamon config show            the settings as they take effect
llamamon config set url http://127.0.0.1:9931
llamamon config init            write the default settings file
```

`python -m llamamon` works the same from a source checkout. Pass `--url`
or `--port` to watch another server (a fork on `--port 8080`, say).

Inside the monitor: `1-6` or left/right switch views (Overview, Serving,
Requests, Engine, Hardware, Logs) · `t` cycles the theme · `Space` freezes the
display · `r` refreshes now · `/` filters the request rows · `b` opens the
server's own web UI · `q` quits. It is read-only: llamamon never sends the
server anything.

## Settings

`~/.config/llamamon/config.toml`:

```toml
# llamamon settings
# the llama.cpp server to watch
url = "http://127.0.0.1:9931"
# seconds between polls
interval = 1.0
# seconds one HTTP request may take
timeout = 4.0
# recent requests the views keep
requests = 12
# dark | light | terminal | tokyonight
theme = "dark"
# auto | always | never (NO_COLOR wins)
color = "auto"
# auto | unicode | ascii
glyphs = "auto"
```

`theme = "tokyonight"` is the editor palette of the Tokyo Night theme
(background `#16161e`/`#1a1b26`, foreground `#c0caf5`, with its red `#f7768e`,
green `#9ece6a`, yellow `#e0af68` and blue `#7aa2f7` for the tiles).

## Credits

llamamon is inspired by the browser monitor of the
[Strata](https://github.com/Niko1221/Strata) project — it brings that
one-screen look to the terminal and points it at a plain llama.cpp server.
