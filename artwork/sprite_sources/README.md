# QuotaDeck sprite sources

These four transparent PNGs are the editable, image-generated master sheets for
the QuotaDeck crew. They are deliberately separate from the small runtime
sprites under `themes/quotadeck-crew`.

| Source | Character direction | Accent |
| --- | --- | --- |
| `codex.png` | emerald circuit engineer | `#19D79C` |
| `claude.png` | coral archive automaton | `#EF9A5A` |
| `cursor.png` | indigo vector scout | `#4AB6F7` |
| `grok.png` | cobalt cosmic mechanic | `#AD82EF` |

The sheets are normalized `1280×1280` RGBA PNGs on a fixed 4×4 grid. Each
`320×320` cell owns one pose and each state owns two adjacent poses.

| Row | Columns 1–2 | Columns 3–4 |
| ---: | --- | --- |
| 1 | `idle_00`, `idle_01` | `busy_00`, `busy_01` |
| 2 | `caution_00`, `caution_01` | `critical_00`, `critical_01` |
| 3 | `exhausted_00`, `exhausted_01` | `offline_00`, `offline_01` |
| 4 | `stale_00`, `stale_01` | `reset_00`, `reset_01` |

Compile and validate from the repository root:

```powershell
python tools\gen_sprites.py
python tools\gen_sprites.py --check
python -m quotadeck theme validate themes\quotadeck-crew
```

If image generation returns a genuine-alpha sheet with uneven placement,
normalize it before replacing a master:

```powershell
python tools\normalize_sprite_sheet.py generated.png artwork\sprite_sources\codex.png
```

If and only if the generator painted a gray transparency checker into an
opaque RGB image, first run the guarded recovery helper, inspect its recovered alpha, and
then normalize it:

```powershell
python tools\extract_checker_alpha.py generated-with-checker.png recovered.png
python tools\normalize_sprite_sheet.py recovered.png artwork\sprite_sources\codex.png
```

The checker helper is not a general background remover. It rejects existing
alpha and suspicious masks; enclosed background or bright gray foreground can
still need manual inspection. The compiled contact sheet is the acceptance
view.

The compiler alpha-trims each state pair, fits it into an `88×108` RGBA frame,
bottom-centres the character, reduces the palette without dithering, snaps RGB
channels to RGB565-representable values, and makes alpha binary. Palette
reduction is a FASTOCTREE clone that breaks equal-occupancy cubes by spatial
index; Pillow's `Image.quantize(FASTOCTREE)` is not used because libc `qsort`
is not a total order. Generated files are deterministic and may be committed.
CI uses `--check`; it never silently replaces curated source art.

Keep these invariants when revising a source:

- exact `1280×1280` static RGBA PNG, not a symlink or animated PNG;
- real transparency, not a painted checkerboard;
- at least 16 transparent pixels on every edge of every `320×320` cell;
- visible and primary-component width/height at least 25% of the cell;
- the same character identity, core proportions, light direction, and quarter-view across all cells;
- feet and body centre aligned between the two poses of a state;
- no captions, logos, grid lines, borders, shadows outside the cell, or official mascots;
- clear state changes at the silhouette level, not only through colour;
- important facial/tool details thick enough to survive an `88×108` reduction.

The complete image-generation prompt set and provenance are in
[`PROMPTS.md`](PROMPTS.md).
