# QuotaDeck Architecture

QuotaDeck polls already-logged-in AI coding providers, normalizes usage into a
shared snapshot, renders 240×135 pixel-art scenes, and uploads RGB565 frames to
the AULA F108 Pro LCD.

```
Discovery (CLI / App) → account picker → Providers → UsageSnapshot
    → Severity → Scheduler → SceneEngine → Frames
    → RGB565 payload → F108 driver
                 ↘ GIF/PNG preview
```

The renderer never talks to hardware. `quotadeck render` works without a keyboard.

See [F108_PROTOCOL.md](F108_PROTOCOL.md), [PROVIDERS.md](PROVIDERS.md),
[THEMES.md](THEMES.md), and [SECURITY.md](SECURITY.md).
