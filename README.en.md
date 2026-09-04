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

The renderer never talks to hardware. `quotadeck render` works without a keyboard. Uploads rewrite SPI flash. Default policy: 10 minute minimum interval, 60 minute max age, 100 writes/day.

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

Each account owns the full LCD. Character bay on the left, remaining % / week / reset on the right.

- 50–100 idle · 20–49 busy · 10–19 caution · 1–9 critical · 0 exhausted
- plus offline / stale / reset

Original pixel art only. No official vendor mascots.

## Getting started (Windows 11)

1. Connect the F108 Pro over **USB-C** and press `Fn+4`.
2. Close official AULA software.
3. Sign in to the providers you want on this PC (CLI or app).

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
python tools\gen_sprites.py
quotadeck ui
```

In the settings app: **Detect** signed-in accounts, check the ones to show, **Preview** without writing flash, then **Upload now**. The custom GIF lives in **slot 1**, not the factory slot.

### CLI

```powershell
quotadeck probe
quotadeck detect --apply
quotadeck usage
quotadeck run --once
quotadeck ui
```

## Rules

- Never copy tokens into the repo or `config.json`.
- Never upload more than 141 frames. The firmware does not enforce the slot; overflow corrupts menu graphics.
- Write the user GIF slot (`image_number = 1`). Slot 0 is the factory GIF.
- USB-C wired mode only (`Fn+4`).

## More

[Architecture](docs/ARCHITECTURE.md) · [Providers](docs/PROVIDERS.md) · [Security](docs/SECURITY.md) · [F108 protocol](docs/F108_PROTOCOL.md) · [Themes](docs/THEMES.md)

MIT. Protocol notes derived from [parsiya/f108-pro](https://github.com/parsiya/f108-pro) (MIT).
