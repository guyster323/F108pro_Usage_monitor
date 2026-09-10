# Image-generation prompts and provenance

The source sheets in this folder were created with OpenAI's built-in image
generation on 2026-09-10, normalized deterministically onto a `1280×1280`
transparent grid, then compiled by `tools/gen_sprites.py`. They are original
provider-inspired characters, not official logos or vendor mascots.

## Shared production prompt

> Create one transparent-background pixel-art sprite sheet for a tiny 240×135
> keyboard LCD. Use a clean 4×4 grid with exactly 16 isolated full-body sprites
> of the same original character, two animation poses per state. Row 1: idle A,
> idle B, busy A, busy B. Row 2: caution A, caution B, critical A, critical B.
> Row 3: exhausted A, exhausted B, offline A, offline B. Row 4: stale A, stale B,
> reset A, reset B. Keep every sprite in a consistent three-quarter view facing
> slightly right, with fixed feet, scale, silhouette, camera, palette and light
> direction. Make poses lively and readable after reduction to 88×108: subtle
> breathing/blink for idle, active tool motion for busy, guarded stance for
> caution, urgent low stance for critical, slumped for exhausted, powered-down
> for offline, clock-like hesitation for stale, and energetic recharge for
> reset. Crisp high-end pixel art, chunky 2–4 px-equivalent clusters, limited
> palette, no anti-aliased halo, no motion blur. No text, labels, numbers,
> borders, logos, watermarks, panel background, floor, or checkerboard. Output
> true alpha outside each sprite and leave generous transparent separation.

## Provider character additions

Append exactly one of these blocks to the shared prompt.

### Codex

> Character: a compact emerald-and-charcoal circuit engineer robot with a mint
> glass visor, expressive luminous eyes, sturdy boots, a tiny terminal slate and
> subtle code-rune particles. Friendly, ingenious and practical. Distinct
> rounded helmet and utility-jacket silhouette; dark graphite materials with
> bright emerald accents.

### Claude

> Character: a warm coral, copper and cream archive automaton with an owl-like
> face, thoughtful eyes, layered scarf/coat plates and a small glowing book
> terminal. Gentle scholarly energy, elegant rounded silhouette, parchment and
> amber highlights.

### Cursor

> Character: an indigo and cyan vector scout with an angular hood/helmet,
> luminous face panel, compact explorer suit and an original arrow-shaped energy
> tool. Agile navigation energy, crisp triangular silhouette, electric-blue
> highlights; do not reproduce the Cursor logo.

### Grok

> Character: a cobalt and magenta cosmic mechanic with asymmetric goggles,
> antenna details, tool belt and a small star-energy orb device. Curious,
> irreverent inventor energy, slightly asymmetric silhouette and violet nebula
> highlights; do not reproduce the xAI or X logo.

## Alpha refinement prompt

Some generated sheets contained a painted transparency checkerboard. This
narrowly scoped alpha-only edit was attempted before the guarded recovery path
below; the prompt alone was never treated as proof of real transparency:

> Preserve every sprite, pixel-art detail, colour, pose, scale, 4×4 placement
> and canvas dimensions exactly. Remove only the checkerboard/background and
> replace it with genuine transparent alpha. Do not redraw, relight, crop,
> rearrange, add text, or add outlines. The transparent areas must have alpha 0
> and the sprite interiors must remain opaque.

## Pose-stability and gutter refinement

The accepted second pass kept the provider identity while reducing full-body
flicker and reserving clear space around every cell:

> Refine this 4×4 sprite sheet without changing the character identity,
> three-quarter camera, core body proportions, palette, state order, or canvas
> layout. Keep each A/B pair at the same body scale and planted baseline. Make
> A/B changes local and readable—a blink, hand/tool motion, status light, or
> small energy effect—rather than a new costume or camera angle. Keep each of
> the 16 poses entirely isolated inside its cell with generous clear space on
> all four sides. Preserve crisp high-end pixel clusters and true transparent
> alpha. No text, grid, panel, floor, logo, watermark, or baked checkerboard.

The accepted Cursor refinement contained genuine alpha. The accepted Codex,
Claude, and Grok refinements painted a checkerboard despite the alpha request;
for those three only, edge-connected low-saturation checker pixels were
recovered with `tools/extract_checker_alpha.py`. All four were then passed
through `tools/normalize_sprite_sheet.py`, which uses one scale per state pair,
aligns the largest connected character silhouette to a shared baseline, and
creates the 16-pixel cell gutter.

The checker extraction is deliberately conservative and is not a universal
background-removal algorithm. Always inspect the normalized master and the
runtime contact sheet before accepting it.

After generation, acceptance is based on the compiled runtime contact sheet,
not the large source alone:

```powershell
python tools\extract_checker_alpha.py generated-with-checker.png recovered.png
python tools\normalize_sprite_sheet.py recovered.png artwork\sprite_sources\codex.png
python tools\gen_sprites.py
python tools\gen_sprites.py --check
python -m quotadeck theme validate themes\quotadeck-crew
```
