# Theme and sprite pipeline

`themes/quotadeck-crew` is a compiled runtime theme. The maintainable masters
live in `artwork/sprite_sources`; do not hand-edit the 64 derived PNGs.

```text
image-generation output
             │
             ├─ tools/extract_checker_alpha.py  # only for a painted checker
             └─ tools/normalize_sprite_sheet.py
                    │
artwork/sprite_sources/{provider}.png  # 1280×1280 transparent 4×4 masters
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
- States: `idle`, `busy`, `caution`, `critical`, `exhausted`, `offline`, `stale`, `reset`
- Frames: two non-identical poses per state
- Alpha: only `0` or `255`
- Visible footprint: at least 48×56 bounding box and 1,200 opaque pixels
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
    "source": "artwork/sprite_sources",
    "compiler": "tools/gen_sprites.py",
    "pillow": "12.3.0",
    "layout": "4x4-state-pairs"
  }
}
```

`fps` is currently validated metadata only. It does not control playback
cadence; account duration and the one-shot secondary pose are driven by the
2ms frame delays in `renderer.budget` and `_sprite_index()`.

## Editing safely

1. Read `artwork/sprite_sources/README.md` and `PROMPTS.md`.
2. Normalize the image-generation output. Use checker extraction first only if
   the input is opaque RGB with a visibly painted transparency checker.
3. Replace only the relevant normalized 4×4 master sheet.
4. Generate the theme and inspect the contact sheet at actual size and 4× zoom.
5. Run the non-writing freshness check and the strict theme validator.

```powershell
python tools\normalize_sprite_sheet.py generated.png artwork\sprite_sources\codex.png
python tools\gen_sprites.py
python tools\gen_sprites.py --check
python -m quotadeck theme validate themes\quotadeck-crew
```

Each master must be a static `1280×1280` RGBA PNG with sixteen `320×320`
cells, at least 16 transparent pixels around every cell, and a substantial
connected character component. `--check` compiles into a temporary directory,
validates the committed runtime theme, and semantically compares canonical JSON,
decoded visible RGBA pixels, and the annotated contact sheet. PNG compression
metadata and invisible RGB do not create false mismatches.

The development dependency pins Pillow `12.3.0`, and the compiler records the
active version in `theme.json`, because resampling and palette quantization can
change between Pillow releases.

Normal generation stages and validates a complete theme before replacing a
previous compiler-managed theme. Resolved source, theme, and preview paths may
not overlap; `--force` applies only to an unrelated non-empty theme directory.
Preview and helper output files are atomically replaced without a backup, so
send image-generation output to a temporary filename, inspect it, and only then
choose the curated master/preview target. CI uses the non-writing check so
curated image-generated art is never overwritten during a build. Sprites must
be original; do not ship official provider logos or mascots.
