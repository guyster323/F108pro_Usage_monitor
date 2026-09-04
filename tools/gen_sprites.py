#!/usr/bin/env python3
"""Generate QuotaDeck Crew sprites — Gemini CRT-bay characters for production."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
THEME = ROOT / "themes" / "quotadeck-crew"

C_TRANS = (0, 0, 0, 0)
C_OUTLINE = (12, 16, 22, 255)
C_DARK_SHADOW = (22, 28, 38, 255)
C_BODY_BASE = (38, 48, 62, 255)
C_BODY_MID = (54, 68, 88, 255)
C_BODY_HI = (78, 98, 124, 255)
C_SHEEN = (125, 150, 185, 255)
C_BOLT = (160, 180, 205, 255)
C_WHITE = (245, 255, 250, 255)
C_SPARK = (255, 235, 90, 255)
C_SWEAT = (80, 180, 255, 255)
C_SWEAT_HI = (190, 230, 255, 255)

TEAL = ((10, 24, 20, 255), (15, 36, 30, 255), (16, 120, 95, 255), (20, 180, 140, 255), (61, 220, 151, 255), (160, 255, 215, 255))
AMB = ((28, 20, 8, 255), (42, 30, 12, 255), (160, 95, 15, 255), (220, 145, 25, 255), (255, 185, 45, 255), (255, 230, 150, 255))
RED = ((30, 8, 12, 255), (48, 14, 20, 255), (160, 25, 40, 255), (225, 45, 65, 255), (255, 75, 95, 255), (255, 180, 195, 255))
GRAY = ((18, 20, 24, 255), (28, 32, 38, 255), (70, 78, 88, 255), (110, 118, 128, 255), (160, 168, 176, 255), (210, 216, 220, 255))

STATES = ["idle", "busy", "caution", "critical", "exhausted", "reset", "offline", "stale"]
PROVIDERS = {
    "codex": {"accent": "#00F5B4", "kind": "codex"},
    "claude": {"accent": "#FF9F5A", "kind": "claude"},
    "cursor": {"accent": "#50B4FF", "kind": "cursor"},
    "grok": {"accent": "#B48CFF", "kind": "grok"},
}


def _leds(state: str):
    if state in {"caution", "exhausted"}:
        return AMB
    if state in {"critical", "error"}:
        return RED
    if state in {"offline", "stale"}:
        return GRAY
    return TEAL


def create_codex_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)
    bob_y = 0
    jitter_x = 0
    if state == "idle":
        bob_y = [0, 1, 0, 0][frame % 4]
    elif state == "busy":
        bob_y = [0, 1, 0, 1][frame % 4]
    elif state == "caution":
        jitter_x = [0, 1][frame % 2]
    elif state in {"critical", "exhausted"}:
        jitter_x = [1, -1][frame % 2]
        bob_y = [0, 1][frame % 2]
    elif state == "reset":
        bob_y = [1, -1, 0, -1][frame % 4]
    elif state == "offline":
        bob_y = 2
    ox = 36 + jitter_x
    oy = 34 + bob_y
    crt_bg, crt_line, led_dark, led_mid, led_glow, led_bright = _leds(state)

    ant_x = ox
    ant_top_y = oy - 26
    d.rectangle([ant_x - 1, ant_top_y + 4, ant_x + 1, oy - 16], fill=C_OUTLINE)
    d.line([(ant_x, ant_top_y + 5), (ant_x, oy - 17)], fill=C_BODY_HI)
    d.ellipse([ant_x - 4, ant_top_y - 4, ant_x + 4, ant_top_y + 4], fill=C_OUTLINE)
    d.ellipse([ant_x - 3, ant_top_y - 3, ant_x + 3, ant_top_y + 3], fill=led_mid)
    d.point((ant_x - 1, ant_top_y - 1), fill=led_bright)
    if state == "critical" and frame % 2 == 0:
        d.line([(ant_x - 7, ant_top_y - 5), (ant_x - 4, ant_top_y - 3)], fill=led_glow)
        d.line([(ant_x + 7, ant_top_y - 5), (ant_x + 4, ant_top_y - 3)], fill=led_glow)
    elif state == "reset":
        d.point((ant_x - 6, ant_top_y - 6 + (frame % 2) * 2), fill=C_SPARK)
        d.point((ant_x + 6, ant_top_y - 8), fill=led_bright)

    head_l, head_r, head_t, head_b = ox - 22, ox + 22, oy - 16, oy + 12
    d.rounded_rectangle([head_l, head_t, head_r, head_b], radius=4, fill=C_OUTLINE)
    d.rounded_rectangle([head_l + 1, head_t + 1, head_r - 1, head_b - 1], radius=3, fill=C_BODY_BASE)
    d.line([(head_l + 3, head_t + 1), (head_r - 3, head_t + 1)], fill=C_SHEEN)
    d.line([(head_l + 1, head_t + 3), (head_l + 1, head_b - 4)], fill=C_BODY_HI)
    d.line([(head_r - 1, head_t + 3), (head_r - 1, head_b - 3)], fill=C_DARK_SHADOW)
    for vx in (ox - 10, ox - 5, ox, ox + 5, ox + 10):
        d.point((vx, head_t + 3), fill=C_DARK_SHADOW)

    scr_l, scr_r, scr_t, scr_b = ox - 17, ox + 17, oy - 11, oy + 8
    d.rectangle([scr_l - 1, scr_t - 1, scr_r + 1, scr_b + 1], fill=C_DARK_SHADOW)
    d.rectangle([scr_l, scr_t, scr_r, scr_b], fill=crt_bg)
    for sy in range(scr_t + 1, scr_b + 1, 2):
        d.line([(scr_l, sy), (scr_r, sy)], fill=crt_line)
    d.point((scr_l + 1, scr_t + 1), fill=C_SHEEN)

    eye_y, eye_lw, eye_rw = oy - 4, ox - 9, ox + 9
    if state in {"offline", "stale"}:
        d.line([(eye_lw - 4, eye_y - 2), (eye_lw + 2, eye_y + 2)], fill=led_mid)
        d.line([(eye_lw + 2, eye_y - 2), (eye_lw - 4, eye_y + 2)], fill=led_mid)
        d.line([(eye_rw - 2, eye_y - 2), (eye_rw + 4, eye_y + 2)], fill=led_mid)
        d.line([(eye_rw + 4, eye_y - 2), (eye_rw - 2, eye_y + 2)], fill=led_mid)
        if state == "stale":
            d.point((ox, eye_y + 4), fill=led_glow)
    elif state == "idle":
        if frame == 3:
            d.line([(eye_lw - 4, eye_y), (eye_lw + 2, eye_y)], fill=led_glow)
            d.line([(eye_rw - 2, eye_y), (eye_rw + 4, eye_y)], fill=led_glow)
        else:
            shift = 1 if frame == 1 else 0
            d.rectangle([eye_lw - 4 + shift, eye_y - 3, eye_lw + 2 + shift, eye_y + 3], fill=led_mid)
            d.rectangle([eye_lw - 3 + shift, eye_y - 2, eye_lw + 1 + shift, eye_y + 2], fill=led_glow)
            d.point((eye_lw + 1 + shift, eye_y - 2), fill=C_WHITE)
            d.rectangle([eye_rw - 2 + shift, eye_y - 3, eye_rw + 4 + shift, eye_y + 3], fill=led_mid)
            d.rectangle([eye_rw - 1 + shift, eye_y - 2, eye_rw + 3 + shift, eye_y + 2], fill=led_glow)
            d.point((eye_rw + 3 + shift, eye_y - 2), fill=C_WHITE)
    elif state == "busy":
        d.line([(eye_lw - 4, eye_y - 3), (eye_lw + 1, eye_y)], fill=led_bright)
        d.line([(eye_lw + 1, eye_y), (eye_lw - 4, eye_y + 3)], fill=led_bright)
        d.line([(eye_rw - 2, eye_y + 3), (eye_rw + 4, eye_y + 3)], fill=led_glow if frame % 2 == 0 else led_dark)
    elif state in {"caution", "exhausted"}:
        d.line([(eye_lw - 4, eye_y - 3), (eye_lw + 2, eye_y + 2)], fill=led_glow, width=2)
        d.line([(eye_rw + 4, eye_y - 3), (eye_rw - 2, eye_y + 2)], fill=led_glow, width=2)
        sw_y = head_t + 4 + (frame % 2) * 2
        d.ellipse([head_r - 5, sw_y, head_r - 1, sw_y + 4], fill=C_SWEAT)
        d.point((head_r - 4, sw_y + 1), fill=C_SWEAT_HI)
    elif state == "critical":
        if frame % 2 == 0:
            d.line([(eye_lw - 4, eye_y - 3), (eye_lw + 2, eye_y + 3)], fill=led_bright, width=2)
            d.line([(eye_lw + 2, eye_y - 3), (eye_lw - 4, eye_y + 3)], fill=led_bright, width=2)
            d.line([(eye_rw - 2, eye_y - 3), (eye_rw + 4, eye_y + 3)], fill=led_bright, width=2)
            d.line([(eye_rw + 4, eye_y - 3), (eye_rw - 2, eye_y + 3)], fill=led_bright, width=2)
        else:
            d.line([(ox - 5, eye_y - 3), (ox - 5, eye_y + 1)], fill=led_bright, width=2)
            d.point((ox - 5, eye_y + 3), fill=led_bright)
            d.line([(ox + 5, eye_y - 3), (ox + 5, eye_y + 1)], fill=led_bright, width=2)
            d.point((ox + 5, eye_y + 3), fill=led_bright)
    elif state == "reset":
        d.line([(eye_lw - 4, eye_y + 1), (eye_lw - 1, eye_y - 2)], fill=led_bright, width=2)
        d.line([(eye_lw - 1, eye_y - 2), (eye_lw + 2, eye_y + 1)], fill=led_bright, width=2)
        d.line([(eye_rw - 2, eye_y + 1), (eye_rw + 1, eye_y - 2)], fill=led_bright, width=2)
        d.line([(eye_rw + 1, eye_y - 2), (eye_rw + 4, eye_y + 1)], fill=led_bright, width=2)
        d.line([(ox - 2, eye_y + 4), (ox + 2, eye_y + 4)], fill=led_glow)

    neck_t = head_b
    d.rectangle([ox - 5, neck_t, ox + 5, neck_t + 3], fill=C_OUTLINE)
    d.rectangle([ox - 4, neck_t, ox + 4, neck_t + 2], fill=C_DARK_SHADOW)

    body_l, body_r, body_t, body_b = ox - 15, ox + 15, neck_t + 3, neck_t + 16
    d.rounded_rectangle([body_l, body_t, body_r, body_b], radius=3, fill=C_OUTLINE)
    d.rounded_rectangle([body_l + 1, body_t + 1, body_r - 1, body_b - 1], radius=2, fill=C_BODY_BASE)
    d.line([(body_l + 2, body_t + 1), (body_r - 2, body_t + 1)], fill=C_BODY_HI)
    d.point((body_l + 2, body_t + 2), fill=C_BOLT)
    d.point((body_r - 2, body_t + 2), fill=C_BOLT)
    reac_cy = body_t + 6
    d.ellipse([ox - 4, reac_cy - 4, ox + 4, reac_cy + 4], fill=C_OUTLINE)
    d.ellipse([ox - 3, reac_cy - 3, ox + 3, reac_cy + 3], fill=led_dark)
    core = 2 if (frame % 2 == 0 or state == "critical") else 1
    d.ellipse([ox - core, reac_cy - core, ox + core, reac_cy + core], fill=led_glow)
    d.point((ox, reac_cy), fill=C_WHITE if frame % 2 == 0 else led_bright)

    arm_y = body_t + 2
    if state == "busy":
        tap = frame % 2
        d.line([(body_l, arm_y), (ox - 10, arm_y + 4 + tap)], fill=C_OUTLINE, width=3)
        d.line([(body_r, arm_y), (ox + 10, arm_y + 5 - tap)], fill=C_OUTLINE, width=3)
        d.line([(ox - 14, body_b - 2), (ox + 14, body_b - 2)], fill=led_glow)
    elif state in {"caution", "exhausted"}:
        d.line([(body_l, arm_y), (body_l - 2, arm_y + 5), (body_l + 2, arm_y + 7)], fill=C_OUTLINE, width=2)
        d.line([(body_r, arm_y), (body_r + 2, arm_y + 5), (body_r - 2, arm_y + 7)], fill=C_OUTLINE, width=2)
    elif state == "critical":
        flap = (frame % 2) * 2
        d.line([(body_l, arm_y + 2), (body_l - 6, arm_y - 2 - flap)], fill=C_OUTLINE, width=3)
        d.line([(body_r, arm_y + 2), (body_r + 6, arm_y - 2 - flap)], fill=C_OUTLINE, width=3)
    else:
        d.line([(body_l, arm_y), (body_l - 3, arm_y + 6), (body_l - 1, arm_y + 9)], fill=C_OUTLINE, width=2)
        d.line([(body_r, arm_y), (body_r + 3, arm_y + 6), (body_r + 1, arm_y + 9)], fill=C_OUTLINE, width=2)
        d.point((body_l, arm_y + 9), fill=C_BOLT)
        d.point((body_r, arm_y + 9), fill=C_BOLT)

    base_l, base_r, base_t, base_b = ox - 14, ox + 14, body_b, body_b + 7
    d.rounded_rectangle([base_l, base_t, base_r, base_b], radius=3, fill=C_OUTLINE)
    d.rounded_rectangle([base_l + 1, base_t + 1, base_r - 1, base_b - 1], radius=2, fill=C_DARK_SHADOW)
    for wx in (ox - 9, ox, ox + 9):
        d.ellipse([wx - 2, base_t + 1, wx + 2, base_b - 2], fill=C_BODY_MID)
        d.point((wx, base_t + 3), fill=C_BOLT)
    return img


def create_claude_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)
    bob = [0, 1, 0, 0][frame % 4] if state != "offline" else 2
    ox, oy = 36, 36 + bob
    _, _, led_dark, led_mid, led_glow, _ = _leds(state)
    c_out, c_base, c_hi = (20, 14, 10, 255), (180, 110, 70, 255), (215, 145, 100, 255)
    d.rounded_rectangle([ox - 18, oy - 16, ox + 18, oy + 8], radius=8, fill=c_out)
    d.rounded_rectangle([ox - 17, oy - 15, ox + 17, oy + 7], radius=7, fill=c_base)
    d.line([(ox - 12, oy - 14), (ox + 12, oy - 14)], fill=c_hi)
    d.ellipse([ox - 12, oy - 8, ox - 2, oy + 2], fill=c_out)
    d.ellipse([ox - 11, oy - 7, ox - 3, oy + 1], fill=led_glow)
    d.ellipse([ox + 2, oy - 7, ox + 10, oy + 1], fill=c_out)
    d.ellipse([ox + 3, oy - 6, ox + 9, oy], fill=led_dark)
    d.polygon([(ox - 2, oy + 1), (ox + 2, oy + 1), (ox, oy + 5)], fill=(240, 180, 60, 255))
    d.rounded_rectangle([ox - 14, oy + 9, ox + 14, oy + 23], radius=4, fill=c_out)
    d.rounded_rectangle([ox - 13, oy + 10, ox + 13, oy + 22], radius=3, fill=c_base)
    d.rectangle([ox - 5, oy + 11, ox + 5, oy + 21], fill=(245, 235, 210, 255))
    d.line([(ox - 3, oy + 14), (ox + 3, oy + 14)], fill=led_mid)
    d.rectangle([ox - 10, oy + 23, ox - 4, oy + 26], fill=(220, 150, 40, 255))
    d.rectangle([ox + 4, oy + 23, ox + 10, oy + 26], fill=(220, 150, 40, 255))
    if state == "busy":
        d.rectangle([ox + 16, oy + 4, ox + 22, oy + 18], fill=(245, 235, 210, 255))
    return img


def create_cursor_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)
    bob = [0, 1, 0, 1][frame % 4]
    ox, oy = 36, 36 + bob
    _, _, _, led_mid, led_glow, led_bright = _leds(state)
    c_out, c_base, c_hi = (10, 16, 26, 255), (30, 90, 160, 255), (70, 150, 230, 255)
    d.polygon([(ox - 18, oy - 4), (ox, oy - 20), (ox + 18, oy - 4), (ox + 12, oy + 10), (ox - 12, oy + 10)], fill=c_out)
    d.polygon([(ox - 16, oy - 4), (ox, oy - 18), (ox + 16, oy - 4), (ox + 10, oy + 8), (ox - 10, oy + 8)], fill=c_base)
    d.rectangle([ox - 12, oy - 4, ox + 12, oy + 4], fill=c_out)
    d.rectangle([ox - 11, oy - 3, ox + 11, oy + 3], fill=(15, 30, 50, 255))
    d.polygon([(ox - 4, oy - 2), (ox + 4, oy), (ox - 2, oy + 2)], fill=led_bright)
    d.polygon([(ox - 16, oy - 12), (ox - 24, oy - 16), (ox - 18, oy)], fill=c_hi)
    d.polygon([(ox + 16, oy - 12), (ox + 24, oy - 16), (ox + 18, oy)], fill=c_hi)
    d.rounded_rectangle([ox - 11, oy + 10, ox + 11, oy + 24], radius=3, fill=c_out)
    d.rounded_rectangle([ox - 10, oy + 11, ox + 10, oy + 23], radius=2, fill=c_base)
    d.rectangle([ox - 3, oy + 13, ox + 3, oy + 19], fill=led_glow)
    d.ellipse([ox - 10, oy + 22, ox + 10, oy + 27], fill=led_mid)
    return img


def create_grok_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)
    bob = [0, 1, 0, 0][frame % 4]
    ox, oy = 36, 36 + bob
    _, _, _, led_mid, led_glow, led_bright = _leds(state)
    c_out, c_base, c_hi = (18, 12, 28, 255), (70, 45, 105, 255), (120, 85, 175, 255)
    d.ellipse([ox - 20, oy - 18, ox + 20, oy + 10], fill=c_out)
    d.ellipse([ox - 18, oy - 16, ox + 18, oy + 8], fill=c_base)
    d.ellipse([ox - 14, oy - 12, ox + 14, oy + 4], fill=(25, 15, 45, 255))
    d.point((ox - 7, oy - 4), fill=led_bright)
    d.point((ox + 6, oy - 4), fill=led_bright)
    d.point((ox, oy - 8), fill=led_glow)
    d.ellipse([ox + 16, oy - 16, ox + 24, oy - 6], fill=c_hi)
    d.rounded_rectangle([ox - 13, oy + 10, ox + 13, oy + 23], radius=3, fill=c_out)
    d.rounded_rectangle([ox - 12, oy + 11, ox + 12, oy + 22], radius=2, fill=c_base)
    d.ellipse([ox - 4, oy + 13, ox + 4, oy + 19], fill=led_glow)
    d.ellipse([ox - 15, oy + 20, ox - 7, oy + 27], fill=(40, 30, 55, 255))
    d.ellipse([ox + 7, oy + 20, ox + 15, oy + 27], fill=(40, 30, 55, 255))
    if state == "busy":
        d.point((ox - 10 + frame, oy - 14), fill=led_mid)
    return img


def _crew_drawers() -> dict:
    import importlib.util

    path = ROOT / "tools" / "crew_sprites.py"
    spec = importlib.util.spec_from_file_location("quotadeck_crew_sprites", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module.DRAWERS


try:
    DRAWERS = {"codex": create_codex_sprite, **_crew_drawers()}
    DRAWERS = {name: DRAWERS[name] for name in ("codex", "claude", "cursor", "grok")}
except Exception:
    DRAWERS = {
        "codex": create_codex_sprite,
        "claude": create_claude_sprite,
        "cursor": create_cursor_sprite,
        "grok": create_grok_sprite,
    }


def write_theme() -> None:
    THEME.mkdir(parents=True, exist_ok=True)
    providers_meta: dict = {}
    for name, spec in PROVIDERS.items():
        folder = THEME / "provider" / name
        folder.mkdir(parents=True, exist_ok=True)
        drawer = DRAWERS[spec["kind"]]
        states: dict[str, list[str]] = {}
        for state in STATES:
            files = []
            count = 4 if state in {"idle", "busy", "reset"} else 2
            for i in range(count):
                filename = f"{state}_{i:02d}.png"
                drawer(state, i).save(folder / filename)
                files.append(filename)
            states[state] = files
        providers_meta[name] = {"accent": spec["accent"], "states": states}
    theme = {
        "name": "QuotaDeck Crew",
        "id": "quotadeck-crew",
        "fps": 4,
        "canvas": {"width": 240, "height": 135},
        "hud": "hybrid-v1",
        "sprite_size": {"w": 72, "h": 72},
        "palette": {"bg": "#070C14", "text": "#E4F9F6", "accent": "#1EE2B0"},
        "providers": providers_meta,
    }
    (THEME / "theme.json").write_text(json.dumps(theme, indent=2), encoding="utf-8")
    print(f"wrote {THEME}")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT / "src"))
    write_theme()
