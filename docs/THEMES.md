# Theme packs

A theme is a folder with `theme.json` plus PNG sprites.

```text
themes/quotadeck-crew/
  theme.json
  provider/
    codex/idle_00.png …
    claude/…
    cursor/…
    grok/…
```

## theme.json

```json
{
  "name": "QuotaDeck Crew",
  "id": "quotadeck-crew",
  "fps": 4,
  "canvas": { "width": 240, "height": 135 },
  "palette": { "bg": "#101418", "text": "#E8F0F4", "accent": "#3DDC97" },
  "providers": {
    "codex": {
      "accent": "#10A37F",
      "states": {
        "idle": ["idle_00.png", "idle_01.png"],
        "busy": ["busy_00.png", "busy_01.png"]
      }
    }
  }
}
```

States: `idle`, `busy`, `caution`, `critical`, `exhausted`, `reset`, `offline`, `stale`.

Validate with `quotadeck theme validate <folder>`.
Sprites must be original pixel art (or procedural stand-ins). Do not ship official vendor mascots.
