# Cumulative-usage reaction sheets

These four 4×4 RGBA sheets were generated on 2026-09-11 by editing the
matching character in `artwork/sprite_sources/`.  The character design and
provider colour stay fixed; only the reaction to today's usage changes.

The first ten cells are consumed in row-major order by
`tools/gen_sprites.py`:

| Cells | Runtime state | Meaning |
|---|---|---|
| 1–2 | `usage_below` | Below the completed-day average |
| 3–4 | `usage_similar` | Similar to the average |
| 5–6 | `usage_150` | At least 1.5× the average |
| 7–8 | `usage_200` | At least 2× the average |
| 9–10 | `usage_300` | At least 3×; comically collapsed |

Each pair contains two distinct animation frames. The remaining six cells are
unused reserves. Generated checkerboard backgrounds were converted to alpha,
then the sheets were normalized to a safe 1280×1280 grid. Runtime files are
palette-limited, RGB565-snapped 88×108 PNGs under
`themes/quotadeck-crew/provider/`.

`*.alpha.png` files are intermediate extraction results and are intentionally
excluded from release archives; the curated provider-named PNGs are the
pipeline inputs.
