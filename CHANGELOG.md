# Changelog

## 1.1.0 — 2026-10-26

### What's new

- **tokyonight theme** — the editor palette of the Tokyo Night theme
  (`#1a1b26` background, `#c0caf5` foreground, `#7aa2f7` blue, `#f7768e` red,
  `#9ece6a` green, `#e0af68` yellow, `#565f89` muted). Switch to it with
  `t`, like every other theme.
- **Error requests are now recorded** — a request that finishes in an error
  state shows up in the Requests view and the Overview's recent list with
  its `Error` state, instead of being dropped.
- **Hardware source is shown** — `status` and the Hardware view say where
  the numbers came from: `reported by the server` (when it answers
  `GET /metrics` with its own hardware block) or `sampled from this
  machine`.
- **Message bar hints** — the message bar now shows the key shortcuts
  (views, theme, freeze, refresh, filter, browser, quit) instead of a
  bare tagline.
- **Filter border follows the theme** — the `/` filter line takes its
  border colour from the active theme's accent (plain when
  `color = "never"`).

### What's fixed

- **Bad URLs fail cleanly** — an invalid server URL (no scheme, a port that
  is not a number, a remote `http://` host) now prints a short error and
  exits with code `2` instead of raising; `llamamon config set url`
  validates the URL against the same rules before saving it.
- **First CPU sample was fake** — psutil's very first `cpu_percent` call
  only primes its internal counter and returned `0.0`; the first sample
  now draws `--`, exactly like the `/proc` fallback path.
- **Config parse failure is visible** — a `config.toml` that does not parse
  as TOML now warns on stderr and falls back to the defaults, instead of
  failing silently.
- **STALE wording corrected** — the docs and in-app texts no longer claim
  every llama.cpp build delays the monitor's calls; some builds serialize
  reads behind a running request, and the texts now say that.
- **Dead states removed** — the `auth_required`, `incompatible` and
  `telemetry_unavailable` states could never be reached; they are gone from
  the markers, the monitor's message branch and the Overview.
- **Hardcoded colours removed** — the Overview's tile and bar colours are
  the theme's palette values directly, not hex sentinels mapped through a
  lookup; behaviour in solid mode is unchanged.
- **Mean rate labelled** — the request rate column is now `MEAN TOK/S` in
  the Requests view and the Overview, and the Requests view notes that
  requests shorter than one poll interval are not recorded.
- **`max_context` no longer prints as `None`** — the `status` Engine line
  shows the context as an integer, or `--` when the server reports none.
- **Duplicate `_text` helper removed** from `client.py`.
- **Dead `or 0` fallbacks removed** from the `/proc/diskstats` parse.
- **`requires-python` raised to 3.11** — the config reader uses the
  standard library's `tomllib`, which needs Python 3.11.

### Docs

- README: the 1.1.0 asset filenames, the Python 3.11 minimum, and the
  corrected wording about when a running request delays the monitor's
  calls.
