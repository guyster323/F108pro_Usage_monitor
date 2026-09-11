# Image-generation prompt record

Each provider's existing 4×4 source sheet was supplied as the reference image.
The same edit prompt was used with the provider name substituted:

> Edit the supplied provider character sprite sheet into a production-ready
> 4 by 4 pixel-art sprite sheet. Preserve the exact character identity,
> silhouette, costume, palette, accessory, camera angle, and scale. The first
> ten row-major cells contain two animation frames each for: below average
> (calm), similar to average (mild alert surprise), 1.5× (clearly startled),
> 2× (dramatically shocked), and 3× (comically collapsed on the floor). The
> progression is surprise rather than fatigue, sadness, anger, damage, or
> aging. No text, numbers, labels, borders, UI, or background; keep every full
> character centred with safe transparent padding and crisp small-LCD forms.

Image generation is not deterministic. The committed normalized PNGs are the
reviewed source of truth for reproducible runtime compilation.
