# QuotaDeck architecture

QuotaDeck polls AI coding providers already signed in on the PC, normalizes
usage into a shared snapshot, renders 240×135 pixel-art frames, and uploads an
RGB565 playlist to the AULA F108 Pro LCD.

```text
Discovery → account selection → Providers → UsageSnapshot
          → Severity → Scheduler → equal-slot SceneEngine → Frames
          → RGB565 payload → F108 driver
                               ↘ GIF/PNG preview
```

## Display contract

Every selected account owns one full-screen slot. Provider is only a visual
theme and never changes duration. SMART mode performs a stable severity sort;
it does not duplicate urgent accounts. FIXED mode preserves the selected order.
There is no provider grouping, transition card, reset card or final summary.

`scene_hold_seconds` means total time per account, including every animation
pose. The default is 5 seconds. `renderer.budget.allocate()` converts this to
20 ms logical/GIF ticks, gives each account the same frame count and exact
tick sum, and rejects impossible budgets before rendering. Payload delay
bytes use an observed 2 ms playback unit (this user's `/4` encode played a
5 s setting in about 2.5 s). Preview/GIF keep the configured logical
milliseconds. A stored budget below the feasible requirement is raised up
to the 141-frame hard limit before save; only combinations that need more
than 141 frames fail closed. No final slice may silently remove later
accounts. The constraints are:

- default hidden budget: 80 frames (8 accounts × 5 s; legacy hidden 32 and v7 default 48 migrate)
- feasible 81–141 combinations persist (8×6 s=96, 9×5 s=90, 3×20 s=120)
- absolute device limit: 141 frames
- padded payload size grows with frames (80≈5.2MB, 96≈6.2MB, 141≈9.1MB)
- logical/GIF frame delay: 20–500 ms (255 × 2 ms = 510 ms, snapped down to the 20 ms grid)
- frame dimensions: exactly 240×135
- payload pixel data: RGB565, 64,800 bytes per frame

The renderer never talks to hardware. `quotadeck render` works without a
keyboard and uses the saved account duration unless `--hold-seconds` overrides
it. Polling cadence and keyboard-flash write cadence are independent clocks.

## UI and asset layers

`renderer.layout` owns fixed LCD geometry. `renderer.canvas` owns deterministic
bitmap glyphs, full-height bars and fill-boundary text clipping.
`renderer.scenes` only orders accounts and expands the equal timing budget.
`renderer.sprites` loads and validates compiled theme assets. Large editable
source sheets and their deterministic compiler are documented in
[THEMES.md](THEMES.md).

See [UX_REFRESH_REVIEW.md](UX_REFRESH_REVIEW.md),
[F108_PROTOCOL.md](F108_PROTOCOL.md), [PROVIDERS.md](PROVIDERS.md),
[COLLECTORS.md](COLLECTORS.md), and [SECURITY.md](SECURITY.md).
