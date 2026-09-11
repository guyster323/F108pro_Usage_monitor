# Theme and sprite pipeline

`themes/quotadeck-crew` is a compiled runtime theme. The maintainable masters
live in `artwork/sprite_sources` and `artwork/usage_sprite_sources`; do not
hand-edit the 104 derived PNGs.

```text
image-generation output
             │
             ├─ tools/extract_checker_alpha.py  # only for a painted checker
             └─ tools/normalize_sprite_sheet.py
                    │
                    ├─ artwork/sprite_sources/{provider}.png
                    │      # eight quota/status states in a transparent 4×4 master
                    └─ artwork/usage_sprite_sources/{provider}.png
                           # five cumulative reactions in the first ten cells
                                   │
                                   └─ tools/gen_sprites.py
                                          ├─ themes/quotadeck-crew/theme.json
                                          ├─ themes/quotadeck-crew/provider/{provider}/{state}_NN.png
                                          └─ artwork/sprite_previews/runtime_contact_sheet.png
```

## Runtime contract

- LCD canvas: `240×135`
- Runtime sprite: `88×108`, RGBA PNG
- Manifest anchor metadata: `(44, 103)`; actual placement is controlled by the
  renderer's `SPRITE_SLOT` and compiled bottom alignment
- Providers: `codex`, `claude`, `cursor`, `grok`
- Quota/status states: `idle`, `busy`, `caution`, `critical`, `exhausted`,
  `offline`, `stale`, `reset`
- Cumulative reaction states: `usage_below`, `usage_similar`, `usage_150`,
  `usage_200`, `usage_300`
- Frames: two non-identical poses per state
- Total: 4 providers × 13 states × 2 poses = **104 runtime PNGs**
- Alpha: only `0` or `255`
- Visible footprint: at least 48×56 bounding box and 1,200 opaque pixels. The
  deliberately horizontal collapsed `usage_300` pose uses a state-specific
  minimum of 64×40.
- Opaque baseline: exclusive y `106` (two transparent rows below)
- Palette: at most 48 visible opaque colours per compiled frame, no dithering,
  RGB565-snapped
- Paths in the manifest are basenames only; traversal is rejected

`load_theme()` performs this validation on the production render path as well
as the standalone CLI validator. An incomplete/corrupt theme fails with a
validation error; it cannot silently render a transparent fallback character.

The schema-v2 manifest also records the HUD revision, sprite size, anchor and
source compiler:

```json
{
  "schema_version": 2,
  "name": "QuotaDeck Crew",
  "id": "quotadeck-crew",
  "fps": 4,
  "canvas": { "width": 240, "height": 135 },
  "hud": "full-bars-v2",
  "sprite_size": { "w": 88, "h": 108 },
  "anchor": { "x": 44, "y": 103 },
  "asset_pipeline": {
    "sources": [
      "artwork/sprite_sources",
      "artwork/usage_sprite_sources"
    ],
    "compiler": "tools/gen_sprites.py",
    "pillow": "12.3.0",
    "layout": "4x4-state-pairs"
  }
}
```

`fps` is currently validated metadata only. It does not control playback
cadence; account duration and the one-shot secondary pose are driven by the
20ms frame delays in `renderer.budget` and `_sprite_index()`.

## Editing safely

1. Read the `README.md` and `PROMPTS.md` in both `artwork/sprite_sources` and
   `artwork/usage_sprite_sources`.
2. Normalize the image-generation output. Use checker extraction first only if
   the input is opaque RGB with a visibly painted transparency checker.
3. Replace only the relevant normalized 4×4 master sheet: the base source for
   quota/status art or the usage source for cumulative reactions.
4. Generate the theme and inspect the contact sheet at actual size and 4× zoom.
5. Run the non-writing freshness check and the strict theme validator.

```powershell
python tools\normalize_sprite_sheet.py generated.png artwork\sprite_sources\codex.png
python tools\normalize_sprite_sheet.py generated-usage.png artwork\usage_sprite_sources\codex.png
python tools\gen_sprites.py
python tools\gen_sprites.py --check
python -m quotadeck theme validate themes\quotadeck-crew
```

Each master must be a static `1280×1280` RGBA PNG with sixteen `320×320`
cells, at least 16 transparent pixels around every consumed cell, and a
substantial connected character component. The base sheet consumes all sixteen
cells as eight pose pairs. The cumulative sheet consumes its first ten cells in
row-major order as five pose pairs; its last six cells are reserved. `--check`
compiles both source sets into a temporary directory, validates the committed
runtime theme, and semantically compares canonical JSON, decoded visible RGBA
pixels, and the 13-state annotated contact sheet. PNG compression metadata and
invisible RGB do not create false mismatches.

The cumulative sequence is intentionally surprise rather than fatigue:

| State | Selected-period `THIS ÷ AVG` | Pose direction |
| --- | ---: | --- |
| `usage_below` | `< 1.0×` | Calm |
| `usage_similar` | `1.0× – < 1.5×` | Mild alert surprise |
| `usage_150` | `1.5× – < 2.0×` | Clearly startled |
| `usage_200` | `2.0× – < 3.0×` | Dramatically shocked |
| `usage_300` | `≥ 3.0×` | Comically collapsed on the floor |

The reviewed image-generation prompt and edit lineage are recorded in
`artwork/usage_sprite_sources/PROMPTS.md`. Image generation is nondeterministic;
the committed normalized provider PNGs are the reproducible source of truth.
Files named `*.alpha.png` are checker-extraction intermediates and must be
excluded from release archives.

The cumulative cost-row coin is a separate 7×7 transparent asset generated
from `artwork/coin-pixel-source.png` by `tools/gen_coin.py`. It is packaged as
`src/quotadeck/bundled/icons/coin-pixel.png` and is not one of the 104 runtime
character sprites.

The development dependency pins Pillow `12.3.0`, and the compiler records the
active version in `theme.json`, because resampling and palette quantization can
change between Pillow releases.

Normal generation stages and validates a complete theme before replacing a
previous compiler-managed theme. Resolved base source, usage source, theme, and
preview paths may not overlap; `--force` applies only to an unrelated non-empty
theme directory.
Preview and helper output files are atomically replaced without a backup, so
send image-generation output to a temporary filename, inspect it, and only then
choose the curated master/preview target. CI uses the non-writing check so
curated image-generated art is never overwritten during a build. Sprites must
be original; do not ship official provider logos or mascots.
