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

The renderer never talks to hardware. `quotadeck render` works without a keyboard. The settings app picks accounts, language, and timings. Uploads rewrite the keyboard LCD SPI flash. Default: write at most every **10 minutes**. See **LCD flash life** below.

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

Each account owns the full LCD. Character bay on the left, remaining-% cards on the right. Cursor shows the same two dashboard bars: **AUTO** (Cursor Models) and **OTHER** (Other Models), not the spend-cents `used/limit` field.

- 50–100 idle · 20–49 busy · 10–19 caution · 1–9 critical · 0 exhausted
- plus offline / stale / reset

Original pixel art only. No official vendor mascots.

## Getting started (Windows 11)

### Windows exe (recommended)

No Python install required. Download [`dist/QuotaDeck.exe`](dist/QuotaDeck.exe) and double-click it.

1. Connect the F108 Pro over **USB-C** and press `Fn+4`.
2. Close official AULA software.
3. Sign in to the providers you want on this PC (CLI or app).
4. Run `QuotaDeck.exe`, pick accounts, then **Upload now**.
5. Switch language with **EN** / **한** in the top-right.

### From source

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
python tools\gen_sprites.py
quotadeck ui
```

In the settings app:

- **EN / 한** — switch the UI language. The choice is saved.
- **Detect** — rescan CLI/app logins on this PC.
- Checkboxes — only checked accounts rotate on the LCD. The alias is what the HUD shows.
- **Preview** — remaining % and HUD without writing flash. Preview uses the scene-hold interval.
- **Upload now** — write the selected accounts to the F108 Pro **user GIF slot**. Buttons lock while the transfer is running.

Default timings are **60 seconds / 10 seconds / 10 minutes**. They are not the same clock.

| Setting | Default | What it does |
| --- | --- | --- |
| **Usage poll** | 60 s | How often this PC re-reads APIs. Does not write keyboard flash. |
| **Scene hold** | 10 s | How long each account card stays on the LCD. |
| **Keyboard write** | 10 min | Minimum interval before rewriting onboard storage. |

### CLI

```powershell
quotadeck probe
quotadeck detect --apply
quotadeck usage
quotadeck run --once
quotadeck ui
```

## LCD flash life

Each keyboard upload erases and rewrites the LCD GIF slot on SPI flash. Consumer SPI NOR is typically rated around **100,000** program/erase cycles. AULA does not publish the F108 Pro chip rating, so the numbers below are estimates against that common rating.

The default **minimum upload interval is 10 minutes**. At 16 hours/day that is 6 writes/hour, **about 96 writes/day** (software cap 100/day).

| Minimum interval | Writes / day (16h) | Life at 100k cycles |
| --- | --- | --- |
| **10 min (default)** | ~96 | **~2.9 years** |
| 30 min | ~32 | ~8.6 years |
| 60 min | ~16 | ~17 years |
| 1 min | ~960 | ~3.4 months |

The app also caps writes at 100/day. Shorter intervals or repeated **Upload now** clicks still wear the same slot; raising or removing the cap follows the table above.

If the quantized usage snapshot does not change, QuotaDeck skips the upload, so real writes can be lower. The table is the upper bound if every interval writes.

> **Warning: refreshing too often shortens the keyboard's onboard storage life.** Keep the 10-minute default. Repeated **Upload now** clicks also wear the same slot.

## Rules

- Never copy tokens into the repo or `config.json`.
- Never upload more than 141 frames. The firmware does not enforce the slot; overflow corrupts menu graphics.
- Write the user GIF slot (`image_number = 1`). Slot 0 is the factory GIF.
- USB-C wired mode only (`Fn+4`).
- Do not write flash faster than needed. The default is 10 minutes. Faster intervals reduce lifespan.

## More

[Architecture](docs/ARCHITECTURE.md) · [Providers](docs/PROVIDERS.md) · [Security](docs/SECURITY.md) · [F108 protocol](docs/F108_PROTOCOL.md) · [Themes](docs/THEMES.md)

MIT. Protocol notes derived from [parsiya/f108-pro](https://github.com/parsiya/f108-pro) (MIT).
