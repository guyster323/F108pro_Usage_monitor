<p align="center">
  <a href="README.md">🇰🇷 한국어</a> · <strong>🇺🇸 English</strong>
</p>

<p align="center">
  <img alt="Python" src="https://img.shields.io/badge/Python-%3E%3D3.11-3776AB?logo=python&logoColor=white">
  <img alt="Windows" src="https://img.shields.io/badge/Windows-11-0078D6?logo=windows&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/License-MIT-green">
  <img alt="LCD" src="https://img.shields.io/badge/AULA%20F108%20Pro-240%C3%97135-1EE2B0">
</p>
<p align="center">
  <img src="docs/assets/crew-lineup.png" alt="QuotaDeck crew: Codex, Claude, Cursor, Grok" width="720">
</p>

<p align="center">
  <img src="docs/hud-preview.png" alt="F108 Pro HUD — character bay and remaining-percent cards" width="480">
</p>

---
# QuotaDeck

QuotaDeck finds AI coding accounts already signed in on this PC (CLI or app), lets you pick which ones to show, and uploads a 240×135 RGB565 playlist to the AULA F108 Pro LCD. Tokens stay with the official CLI or app. Remaining percent is the hero number.

> New here? Follow **Getting started**. Protocol, themes, and security live under [docs/](docs/).
## Architecture

```
Discovery (CLI / App) → account picker → Providers → UsageSnapshot
    → Severity → Scheduler → SceneEngine → Frames
    → RGB565 payload → F108 driver
                 ↘ PNG preview (settings app)
```

The renderer never talks to hardware. `quotadeck render` works without a keyboard. The settings app picks accounts, language, and timings, then keeps polling while it is hidden in the tray. The CLI `quotadeck run` uses the same scheduler and flash-budget policy. Uploads rewrite the keyboard LCD SPI flash. Default: write at most every **30 minutes**. See **LCD flash life** below.
## Providers

| Provider | Source | Credential | Status |
| --- | --- | --- | --- |
| OpenAI Codex | CLI | `~/.codex`, `CODEX_HOME` | Live |
| Cursor | App / CLI | `state.vscdb` or `auth.json` | Live |
| Anthropic Claude | CLI | `~/.claude/.credentials.json` | Community |
| xAI Grok | CLI | `~/.grok/auth.json` | Community |

The same Cursor user found in both App and CLI is shown once (App first).
<p align="center">
  <img src="docs/assets/crew-codex.png" alt="Codex" width="160">
  <img src="docs/assets/crew-claude.png" alt="Claude" width="160">
  <img src="docs/assets/crew-cursor.png" alt="Cursor" width="160">
  <img src="docs/assets/crew-grok.png" alt="Grok" width="160">
</p>
Each account owns the full LCD for exactly the same duration. An 88×108 quarter-view character fills the left bay; full-height quota bars fill the right. Only **5H / WEEKLY / AUTO / OTHER** and a large remaining percentage stay inside each bar. Text switches from white over the filled side to black over the depleted side at the live boundary. Cursor prefers **AUTO** and **OTHER**, falling back to a spend-cents `used/limit` **PLAN** bar only when those fields are absent.

- 50–100 idle · 20–49 busy · 10–19 caution · 1–9 critical · 0 exhausted
- plus offline / stale / reset

Original pixel art only. No official vendor mascots.
## Getting started (Windows 11)

### Windows exe

This checkout includes `dist/QuotaDeck.exe`, rebuilt from the current source
after the automated validation below, so it can run without Python. Physical
F108 Pro LCD upload and brightness/viewing-angle checks still require manual
hardware QA. Build again from `QuotaDeck.spec` when needed.

1. Connect the F108 Pro over **USB-C** and press `Fn+4`.
2. Close official AULA software.
3. Sign in to the providers you want on this PC (CLI or app).
4. Run `QuotaDeck.exe`, pick accounts, then **Upload now**.
5. Switch language with **EN** / **한** in the top-right.
### From source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe tools\gen_sprites.py
.\.venv\Scripts\python.exe -m quotadeck ui
```

In the settings app:
- **EN / 한** — switch the UI language. The choice is saved.
- **Detect** — rescan CLI/app logins on this PC.
- Checkboxes — only checked accounts rotate on the LCD. The alias is what the HUD shows.
- **Preview** — remaining % and HUD without writing flash. Every account rotates for the configured time per account.
- **Upload now** — write the selected accounts to the F108 Pro **user GIF slot**. Buttons lock while the transfer is running.

The tray menu can open the app, refresh usage without writing flash, or upload now.
With saved accounts, the background scheduler evaluates automatic uploads. Enable
**Launch at Windows startup** to resume the app after boot.
Default timings are **60 seconds / 5 seconds / 30 minutes**. They are not the same clock.

The F108 Pro delay byte is measured in 2 ms ticks. QuotaDeck expands a 5-second
account slot into ten 500 ms frames, so the saved GUI value matches the time
played by the keyboard. Existing settings files are migrated automatically.

| Setting | Default | What it does |
| --- | --- | --- |
| **Usage poll** | 60 s | How often this PC re-reads APIs. Does not write keyboard flash. |
| **Time per account** | 5 s | Total full-screen time, including character animation. Configurable in the app. |
| **Flash interval** | 30 min | Minimum interval before rewriting onboard storage. |

All three timing fields accept direct keyboard input. Save, preview, and upload
show a popup and stop if the value is not an integer in its allowed range:
usage poll 15–3,600 seconds, time per account 2–20 seconds, and flash interval
1–720 minutes. New settings use 5 seconds and 30 minutes. Upgrades preserve
existing choices and migrate the old 10-minute default to 30 minutes.
### CLI

```powershell
.\.venv\Scripts\python.exe -m quotadeck probe
.\.venv\Scripts\python.exe -m quotadeck detect --apply
.\.venv\Scripts\python.exe -m quotadeck usage
.\.venv\Scripts\python.exe -m quotadeck render --fixture tests\fixtures\usage.json --out preview.gif --hold-seconds 5 --mode fixed
.\.venv\Scripts\python.exe -m quotadeck run --once
.\.venv\Scripts\python.exe -m quotadeck ui
```
## LCD flash life

Each keyboard upload erases and rewrites the LCD GIF slot on SPI flash. Consumer SPI NOR is typically rated around **100,000** program/erase cycles. AULA does not publish the F108 Pro chip rating, so the numbers below are estimates against that common rating.

The default **flash interval is 30 minutes**. At 16 hours/day that is 2 writes/hour, **about 32 writes/day**.
| Minimum interval | Writes / day (16h) | Life at 100k cycles |
| --- | --- | --- |
| **30 min (default)** | ~32 | **~8.6 years** |
| 60 min | ~16 | ~17 years |
| 10 min | ~96 | ~2.9 years |
| 1 min | ~960 | ~3.4 months |

The 100-write counter and last-write timestamp are shared by the settings app and
`quotadeck run`, and persist across restarts. Use the interval estimates above and
do not repeat manual uploads in quick succession.
If the quantized usage snapshot does not change, QuotaDeck skips the upload, so real writes can be lower. The table is the upper bound if every interval writes.

> **Warning: refreshing too often shortens the keyboard's onboard storage life.** Keep the 30-minute default. Repeated **Upload now** clicks also wear the same slot.
## Rules

- Never copy tokens into the repo or `config.json`.
- Never upload more than 141 frames. The firmware does not enforce the slot; overflow corrupts menu graphics.
- Write the user GIF slot (`image_number = 1`). Slot 0 is the factory GIF.
- USB-C wired mode only (`Fn+4`).
- Do not write flash faster than needed. The default is 30 minutes. Faster intervals reduce lifespan.
## More

[UX refresh and code review](docs/UX_REFRESH_REVIEW.md) · [Architecture](docs/ARCHITECTURE.md) · [Providers](docs/PROVIDERS.md) · [Security](docs/SECURITY.md) · [F108 protocol](docs/F108_PROTOCOL.md) · [Themes](docs/THEMES.md)

MIT. Protocol notes derived from [parsiya/f108-pro](https://github.com/parsiya/f108-pro) (MIT).
