#!/usr/bin/env python3
"""Deterministic generator for Fancy QuotaDeck Crew sprites: Claude, Cursor, Grok.

Matches the high-detail pixel-art bar of Codex CRT-bay robot:
- Exact 72x72 RGBA canvas per sprite
- 8-16 color hand-tuned palettes with crisp nearest-neighbor aesthetics
- Distinct silhouettes:
    * Claude: Brass & warm copper scholar owl automaton with parchment mantle & quill
    * Cursor: Sleek stealth aero-drone navigator with HUD reticle & ion thruster
    * Grok: Cosmic astronaut with transparent bubble helmet, star face & lunar rover
- Expressive state acting across all 8 states (idle, busy, caution, critical, exhausted, reset, offline, stale)
"""

from __future__ import annotations

import math
from pathlib import Path
from PIL import Image, ImageDraw

CREW_DIR = Path(__file__).resolve().parent

# Common utility colors
C_TRANS = (0, 0, 0, 0)
C_WHITE = (248, 252, 255, 255)
C_SWEAT = (80, 185, 255, 255)
C_SWEAT_HI = (195, 235, 255, 255)
C_SPARK_GOLD = (255, 230, 85, 255)

# Provider states and frame counts (matches QuotaDeck contract)
STATES = {
    "idle": 4,
    "busy": 4,
    "caution": 2,
    "critical": 2,
    "exhausted": 2,
    "reset": 4,
    "offline": 2,
    "stale": 2,
}


# ==============================================================================
# 1. CLAUDE — Warm Paper Researcher (Scholar Owl Automaton)
# ==============================================================================
# Palette: Warm copper, burnished brass, antique vellum parchment, glowing amber
CLAUDE_OUTLINE = (22, 14, 10, 255)
CLAUDE_SHADOW = (44, 26, 18, 255)
CLAUDE_COPPER_DARK = (100, 56, 32, 255)
CLAUDE_COPPER_BASE = (156, 90, 48, 255)
CLAUDE_COPPER_MID = (202, 122, 66, 255)
CLAUDE_COPPER_HI = (236, 156, 96, 255)
CLAUDE_COPPER_SHEEN = (255, 198, 150, 255)

BRASS_DARK = (120, 88, 22, 255)
BRASS_BASE = (188, 142, 42, 255)
BRASS_HI = (242, 192, 68, 255)
BRASS_SHEEN = (255, 236, 145, 255)

PARCH_DARK = (168, 145, 112, 255)
PARCH_BASE = (226, 206, 170, 255)
PARCH_HI = (248, 238, 214, 255)
INK_LINE = (96, 68, 46, 255)


def _claude_leds(state: str):
    if state in {"caution", "exhausted"}:
        return (
            (32, 20, 8, 255), (55, 32, 12, 255), (180, 100, 20, 255),
            (230, 145, 30, 255), (255, 185, 50, 255), (255, 235, 160, 255)
        )
    if state in {"critical", "error"}:
        return (
            (35, 10, 14, 255), (60, 18, 24, 255), (170, 30, 45, 255),
            (230, 50, 70, 255), (255, 85, 105, 255), (255, 190, 205, 255)
        )
    if state in {"offline", "stale"}:
        return (
            (22, 18, 16, 255), (38, 30, 26, 255), (85, 72, 64, 255),
            (125, 110, 98, 255), (170, 155, 140, 255), (220, 210, 200, 255)
        )
    return (
        (36, 18, 8, 255), (62, 30, 12, 255), (195, 95, 22, 255),
        (245, 135, 38, 255), (255, 175, 75, 255), (255, 235, 170, 255)
    )


def create_claude_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)

    # Bob and jitter
    bob_y = 0
    jitter_x = 0
    if state == "idle":
        bob_y = [0, 1, 0, 0][frame % 4]
    elif state == "busy":
        bob_y = [0, -1, 0, 1][frame % 4]
    elif state == "caution":
        jitter_x = [0, 1][frame % 2]
    elif state in {"critical", "exhausted"}:
        jitter_x = [1, -1][frame % 2]
        bob_y = [0, 1][frame % 2]
    elif state == "reset":
        bob_y = [1, -1, 0, -1][frame % 4]
    elif state == "offline":
        bob_y = 3
    elif state == "stale":
        bob_y = 1

    ox = 36 + jitter_x
    oy = 33 + bob_y
    lens_bg, lens_line, led_dark, led_mid, led_glow, led_bright = _claude_leds(state)

    # --- 1. Owl Ear Tufts / Horn Scrolls ---
    ear_wilt = 4 if state in {"exhausted", "offline"} else ( -2 if state == "reset" else 0 )
    # Left ear tuft
    lt_tip = (ox - 19, oy - 24 + ear_wilt)
    d.polygon([(ox - 11, oy - 14), (ox - 17, oy - 14), lt_tip], fill=CLAUDE_OUTLINE)
    d.polygon([(ox - 12, oy - 14), (ox - 16, oy - 14), (lt_tip[0] + 1, lt_tip[1] + 1)], fill=CLAUDE_COPPER_MID)
    d.point((lt_tip[0] + 1, lt_tip[1] + 2), fill=BRASS_HI)

    # Right ear tuft
    rt_tip = (ox + 19, oy - 24 + ear_wilt)
    d.polygon([(ox + 11, oy - 14), (ox + 17, oy - 14), rt_tip], fill=CLAUDE_OUTLINE)
    d.polygon([(ox + 12, oy - 14), (ox + 16, oy - 14), (rt_tip[0] - 1, rt_tip[1] + 1)], fill=CLAUDE_COPPER_MID)
    d.point((rt_tip[0] - 1, rt_tip[1] + 2), fill=BRASS_HI)

    if state == "critical" and frame % 2 == 0:
        d.line([(lt_tip[0] - 4, lt_tip[1] - 3), lt_tip], fill=led_glow)
        d.line([(rt_tip[0] + 4, rt_tip[1] - 3), rt_tip], fill=led_glow)
    elif state == "reset":
        d.point((lt_tip[0] - 2, lt_tip[1] - 3), fill=C_SPARK_GOLD)
        d.point((rt_tip[0] + 2, rt_tip[1] - 3), fill=C_SPARK_GOLD)

    # --- 2. Head & Scholar Helm ---
    head_l, head_r, head_t, head_b = ox - 20, ox + 20, oy - 15, oy + 10
    d.rounded_rectangle([head_l, head_t, head_r, head_b], radius=5, fill=CLAUDE_OUTLINE)
    d.rounded_rectangle([head_l + 1, head_t + 1, head_r - 1, head_b - 1], radius=4, fill=CLAUDE_COPPER_BASE)
    d.line([(head_l + 4, head_t + 1), (head_r - 4, head_t + 1)], fill=CLAUDE_COPPER_SHEEN)
    d.line([(head_l + 1, head_t + 4), (head_l + 1, head_b - 4)], fill=CLAUDE_COPPER_HI)
    d.line([(head_r - 1, head_t + 4), (head_r - 1, head_b - 4)], fill=CLAUDE_SHADOW)
    # Brass rivets on helm corners
    d.point((head_l + 3, head_t + 3), fill=BRASS_HI)
    d.point((head_r - 3, head_t + 3), fill=BRASS_DARK)
    d.point((head_l + 3, head_b - 3), fill=BRASS_HI)
    d.point((head_r - 3, head_b - 3), fill=BRASS_DARK)

    # --- 3. Dual Monocle Spectacles ---
    # Left Monocle
    d.ellipse([ox - 16, oy - 7, ox - 2, oy + 5], fill=CLAUDE_OUTLINE)
    d.ellipse([ox - 15, oy - 6, ox - 3, oy + 4], fill=BRASS_HI)
    d.ellipse([ox - 13, oy - 4, ox - 5, oy + 2], fill=lens_bg)
    # Right Monocle
    d.ellipse([ox + 2, oy - 7, ox + 16, oy + 5], fill=CLAUDE_OUTLINE)
    d.ellipse([ox + 3, oy - 6, ox + 15, oy + 4], fill=BRASS_HI)
    d.ellipse([ox + 5, oy - 4, ox + 13, oy + 2], fill=lens_bg)
    # Spectacle bridge
    d.line([(ox - 3, oy - 1), (ox + 3, oy - 1)], fill=CLAUDE_OUTLINE, width=2)
    d.line([(ox - 2, oy - 1), (ox + 2, oy - 1)], fill=BRASS_SHEEN)

    # Subtle scanline in lenses
    for sy in range(oy - 3, oy + 2, 2):
        d.line([(ox - 12, sy), (ox - 6, sy)], fill=lens_line)
        d.line([(ox + 6, sy), (ox + 12, sy)], fill=lens_line)

    # Monocle Eye Expressions
    eye_ly, eye_ry = oy - 1, oy - 1
    eye_lx, eye_rx = ox - 9, ox + 9

    if state in {"offline", "stale"}:
        # Dim offline horizontal dash
        d.line([(eye_lx - 2, eye_ly), (eye_lx + 2, eye_ly)], fill=led_mid)
        d.line([(eye_rx - 2, eye_ry), (eye_rx + 2, eye_ry)], fill=led_mid)
        if state == "stale":
            d.point((ox, oy - 5), fill=led_glow)
    elif state == "idle":
        if frame == 3:
            # Owl blink
            d.line([(eye_lx - 3, eye_ly), (eye_lx + 2, eye_ly)], fill=led_glow)
            d.line([(eye_rx - 2, eye_ry), (eye_rx + 3, eye_ry)], fill=led_glow)
        else:
            shift = 1 if frame == 1 else 0
            d.rectangle([eye_lx - 2 + shift, eye_ly - 2, eye_lx + 1 + shift, eye_ly + 1], fill=led_mid)
            d.rectangle([eye_lx - 1 + shift, eye_ly - 1, eye_lx + shift, eye_ly], fill=led_glow)
            d.point((eye_lx + 1 + shift, eye_ly - 1), fill=C_WHITE)

            d.rectangle([eye_rx - 2 + shift, eye_ry - 2, eye_rx + 1 + shift, eye_ry + 1], fill=led_mid)
            d.rectangle([eye_rx - 1 + shift, eye_ry - 1, eye_rx + shift, eye_ry], fill=led_glow)
            d.point((eye_rx + 1 + shift, eye_ry - 1), fill=C_WHITE)
    elif state == "busy":
        # Rapid reading eye scan
        f_shift = [-2, 0, 2, 0][frame % 4]
        d.rectangle([eye_lx - 1 + f_shift, eye_ly - 1, eye_lx + 1 + f_shift, eye_ly + 1], fill=led_bright)
        d.rectangle([eye_rx - 1 + f_shift, eye_ry - 1, eye_rx + 1 + f_shift, eye_ry + 1], fill=led_bright)
        # Magnifying reticle cross in right lens
        d.line([(eye_rx - 3, eye_ry), (eye_rx + 3, eye_ry)], fill=led_glow)
        d.line([(eye_rx, eye_ry - 3), (eye_rx, eye_ry + 3)], fill=led_glow)
    elif state in {"caution", "exhausted"}:
        # Angled worried eyes
        d.line([(eye_lx - 3, eye_ly + 1), (eye_lx + 2, eye_ly - 1)], fill=led_glow, width=2)
        d.line([(eye_rx - 2, eye_ry - 1), (eye_rx + 3, eye_ry + 1)], fill=led_glow, width=2)
        sw_y = head_t + 4 + (frame % 2) * 2
        d.ellipse([head_r - 5, sw_y, head_r - 1, sw_y + 4], fill=C_SWEAT)
        d.point((head_r - 4, sw_y + 1), fill=C_SWEAT_HI)
    elif state == "critical":
        if frame % 2 == 0:
            d.line([(eye_lx - 3, eye_ly - 2), (eye_lx + 2, eye_ly + 2)], fill=led_bright, width=2)
            d.line([(eye_lx + 2, eye_ly - 2), (eye_lx - 3, eye_ly + 2)], fill=led_bright, width=2)
            d.line([(eye_rx - 2, eye_ry - 2), (eye_rx + 3, eye_ry + 2)], fill=led_bright, width=2)
            d.line([(eye_rx + 3, eye_ry - 2), (eye_rx - 2, eye_ry + 2)], fill=led_bright, width=2)
        else:
            d.line([(eye_lx, eye_ly - 2), (eye_lx, eye_ly + 1)], fill=led_bright, width=2)
            d.point((eye_lx, eye_ly + 2), fill=led_bright)
            d.line([(eye_rx, eye_ry - 2), (eye_rx, eye_ry + 1)], fill=led_bright, width=2)
            d.point((eye_rx, eye_ry + 2), fill=led_bright)
    elif state == "reset":
        # Cheerful happy owl eyes ^ ^
        d.line([(eye_lx - 3, eye_ly + 1), (eye_lx, eye_ly - 2)], fill=led_bright, width=2)
        d.line([(eye_lx, eye_ly - 2), (eye_lx + 3, eye_ly + 1)], fill=led_bright, width=2)
        d.line([(eye_rx - 3, eye_ly + 1), (eye_rx, eye_ly - 2)], fill=led_bright, width=2)
        d.line([(eye_rx, eye_ry - 2), (eye_rx + 3, eye_ly + 1)], fill=led_bright, width=2)

    # --- 4. Automaton Brass Beak ---
    d.polygon([(ox - 3, oy + 3), (ox + 3, oy + 3), (ox, oy + 8)], fill=CLAUDE_OUTLINE)
    d.polygon([(ox - 2, oy + 3), (ox + 2, oy + 3), (ox, oy + 7)], fill=BRASS_HI)
    d.line([(ox, oy + 3), (ox, oy + 7)], fill=BRASS_DARK)

    # --- 5. Neck & Parchment Scholar Robe Chassis ---
    neck_t = head_b
    d.rectangle([ox - 6, neck_t, ox + 6, neck_t + 2], fill=CLAUDE_OUTLINE)
    d.rectangle([ox - 5, neck_t, ox + 5, neck_t + 1], fill=CLAUDE_SHADOW)

    body_l, body_r, body_t, body_b = ox - 15, ox + 15, neck_t + 2, neck_t + 16
    d.rounded_rectangle([body_l, body_t, body_r, body_b], radius=3, fill=CLAUDE_OUTLINE)
    d.rounded_rectangle([body_l + 1, body_t + 1, body_r - 1, body_b - 1], radius=2, fill=CLAUDE_COPPER_BASE)
    d.line([(body_l + 2, body_t + 1), (body_r - 2, body_t + 1)], fill=CLAUDE_COPPER_HI)

    # Vellum Parchment Bib / Mantle in center of chest
    parch_l, parch_r = ox - 7, ox + 7
    d.rectangle([parch_l, body_t + 2, parch_r, body_b - 2], fill=PARCH_BASE)
    d.rectangle([parch_l - 1, body_t + 1, parch_r + 1, body_b - 1], outline=CLAUDE_OUTLINE)
    d.line([(parch_l, body_t + 2), (parch_r, body_t + 2)], fill=PARCH_HI)
    # Handwritten rune lines on parchment
    d.line([(parch_l + 2, body_t + 4), (parch_r - 2, body_t + 4)], fill=INK_LINE)
    d.line([(parch_l + 2, body_t + 7), (parch_r - 3, body_t + 7)], fill=INK_LINE)
    d.line([(parch_l + 2, body_t + 10), (parch_r - 2, body_t + 10)], fill=INK_LINE)

    # Central Amber Chronometer / Rune Core
    core_y = body_t + 6
    d.ellipse([ox - 3, core_y - 3, ox + 3, core_y + 3], fill=CLAUDE_OUTLINE)
    d.ellipse([ox - 2, core_y - 2, ox + 2, core_y + 2], fill=led_dark)
    core_rad = 1 if (frame % 2 == 0 or state == "critical") else 0
    d.ellipse([ox - core_rad, core_y - core_rad, ox + core_rad, core_y + core_rad], fill=led_glow)
    d.point((ox, core_y), fill=C_WHITE if frame % 2 == 0 else led_bright)

    # --- 6. Articulated Wings & Props (Scroll & Quill) ---
    wing_t = body_t
    if state == "busy":
        # Left wing holds active glowing scroll
        scr_x1, scr_x2 = ox - 24, ox - 13
        scr_y1, scr_y2 = body_t + 1, body_b + 3
        # Scroll cylinder knobs
        d.line([(scr_x1 - 1, scr_y1 - 1), (scr_x1 + 3, scr_y1 - 1)], fill=BRASS_HI)
        d.line([(scr_x1 - 1, scr_y2 + 1), (scr_x1 + 3, scr_y2 + 1)], fill=BRASS_HI)
        # Scroll body
        d.rectangle([scr_x1, scr_y1, scr_x2, scr_y2], fill=PARCH_BASE, outline=CLAUDE_OUTLINE)
        for ry in range(scr_y1 + 2, scr_y2 - 1, 3):
            d.line([(scr_x1 + 2, ry), (scr_x2 - 2, ry)], fill=led_glow)
        # Left wing claw holding scroll
        d.line([(body_l, wing_t + 4), (scr_x2, wing_t + 8)], fill=CLAUDE_OUTLINE, width=2)
        d.line([(body_l + 1, wing_t + 4), (scr_x2, wing_t + 7)], fill=CLAUDE_COPPER_HI)

        # Right wing holds golden feather quill scribbling
        q_tip_y = body_t + 9 + (frame % 2) * 2
        d.line([(body_r, wing_t + 3), (ox + 20, wing_t - 2), (ox + 10, q_tip_y)], fill=BRASS_HI, width=2)
        d.polygon([(ox + 20, wing_t - 2), (ox + 24, wing_t - 6), (ox + 17, wing_t + 1)], fill=BRASS_SHEEN)
        d.point((ox + 9, q_tip_y), fill=C_SPARK_GOLD)
        d.point((ox + 7, q_tip_y - 1), fill=led_bright)
    elif state in {"caution", "exhausted"}:
        # Wings drooping or clutched inward
        d.line([(body_l, wing_t + 2), (body_l - 3, wing_t + 8), (body_l + 1, wing_t + 13)], fill=CLAUDE_OUTLINE, width=2)
        d.line([(body_l - 1, wing_t + 3), (body_l - 2, wing_t + 8), (body_l + 1, wing_t + 12)], fill=CLAUDE_COPPER_MID)
        d.line([(body_r, wing_t + 2), (body_r + 3, wing_t + 8), (body_r - 1, wing_t + 13)], fill=CLAUDE_OUTLINE, width=2)
        d.line([(body_r + 1, wing_t + 3), (body_r + 2, wing_t + 8), (body_r - 1, wing_t + 12)], fill=CLAUDE_COPPER_MID)
    elif state == "critical":
        # Wings flailing wide in panic
        flap = (frame % 2) * 3
        d.line([(body_l, wing_t + 2), (body_l - 7, wing_t - 3 - flap), (body_l - 10, wing_t + 4)], fill=CLAUDE_OUTLINE, width=2)
        d.line([(body_l - 1, wing_t + 3), (body_l - 6, wing_t - 2 - flap)], fill=CLAUDE_COPPER_HI)
        d.line([(body_r, wing_t + 2), (body_r + 7, wing_t - 3 - flap), (body_r + 10, wing_t + 4)], fill=CLAUDE_OUTLINE, width=2)
        d.line([(body_r + 1, wing_t + 3), (body_r + 6, wing_t - 2 - flap)], fill=CLAUDE_COPPER_HI)
        # Fluttering loose parchment leaf
        d.rectangle([ox + 18, oy - 2, ox + 22, oy + 3], fill=PARCH_BASE, outline=CLAUDE_OUTLINE)
    elif state == "reset":
        # Triumphant raised wings holding golden seal
        d.line([(body_l, wing_t + 2), (body_l - 6, wing_t - 4), (body_l - 3, wing_t + 8)], fill=CLAUDE_OUTLINE, width=2)
        d.line([(body_r, wing_t + 2), (body_r + 6, wing_t - 4), (body_r + 3, wing_t + 8)], fill=CLAUDE_OUTLINE, width=2)
        d.point((ox - 8, wing_t - 6), fill=C_SPARK_GOLD)
        d.point((ox + 8, wing_t - 6), fill=C_SPARK_GOLD)
    else:
        # Idle folded feathered wings
        d.polygon([(body_l, wing_t + 1), (body_l - 5, wing_t + 7), (body_l - 1, wing_t + 13), (body_l + 2, wing_t + 11)], fill=CLAUDE_OUTLINE)
        d.polygon([(body_l + 1, wing_t + 2), (body_l - 4, wing_t + 7), (body_l, wing_t + 12), (body_l + 2, wing_t + 10)], fill=CLAUDE_COPPER_MID)
        d.line([(body_l - 3, wing_t + 7), (body_l + 1, wing_t + 8)], fill=CLAUDE_COPPER_SHEEN)

        d.polygon([(body_r, wing_t + 1), (body_r + 5, wing_t + 7), (body_r + 1, wing_t + 13), (body_r - 2, wing_t + 11)], fill=CLAUDE_OUTLINE)
        d.polygon([(body_r - 1, wing_t + 2), (body_r + 4, wing_t + 7), (body_r, wing_t + 12), (body_r - 2, wing_t + 10)], fill=CLAUDE_COPPER_MID)
        d.line([(body_r + 3, wing_t + 7), (body_r - 1, wing_t + 8)], fill=CLAUDE_COPPER_SHEEN)

    # --- 7. Automaton Talons & Antique Scroll Perch ---
    base_t = body_b
    # Articulated brass talons
    d.line([(ox - 10, base_t), (ox - 10, base_t + 3)], fill=BRASS_HI, width=2)
    d.line([(ox - 6, base_t), (ox - 6, base_t + 3)], fill=BRASS_HI, width=2)
    d.line([(ox + 6, base_t), (ox + 6, base_t + 3)], fill=BRASS_HI, width=2)
    d.line([(ox + 10, base_t), (ox + 10, base_t + 3)], fill=BRASS_HI, width=2)

    # Scroll bar cylinder
    bar_l, bar_r = ox - 18, ox + 18
    bar_t, bar_b = base_t + 3, base_t + 8
    d.rectangle([bar_l, bar_t, bar_r, bar_b], fill=CLAUDE_OUTLINE)
    d.rectangle([bar_l + 1, bar_t + 1, bar_r - 1, bar_b - 1], fill=BRASS_BASE)
    d.line([(bar_l + 2, bar_t + 1), (bar_r - 2, bar_t + 1)], fill=BRASS_SHEEN)
    d.line([(bar_l + 2, bar_b - 1), (bar_r - 2, bar_b - 1)], fill=BRASS_DARK)

    # Knurled scroll-knob endcaps
    d.ellipse([bar_l - 4, bar_t - 1, bar_l, bar_b + 1], fill=CLAUDE_OUTLINE)
    d.ellipse([bar_l - 3, bar_t, bar_l - 1, bar_b], fill=BRASS_HI)
    d.point((bar_l - 2, bar_t + 2), fill=BRASS_SHEEN)

    d.ellipse([bar_r, bar_t - 1, bar_r + 4, bar_b + 1], fill=CLAUDE_OUTLINE)
    d.ellipse([bar_r + 1, bar_t, bar_r + 3, bar_b], fill=BRASS_HI)
    d.point((bar_r + 2, bar_t + 2), fill=BRASS_SHEEN)

    return img


# ==============================================================================
# 2. CURSOR — Cool Blue Aero-Navigator (Cyber Hover Drone)
# ==============================================================================
# Palette: Deep stealth navy, cyber cyan, aero blue sheen, carbon fiber, ion plasma
CURSOR_OUTLINE = (10, 16, 26, 255)
CURSOR_SHADOW = (18, 28, 46, 255)
CURSOR_BODY_DARK = (30, 52, 84, 255)
CURSOR_BODY_BASE = (46, 80, 128, 255)
CURSOR_BODY_MID = (68, 118, 180, 255)
CURSOR_BODY_HI = (98, 164, 235, 255)
CURSOR_BODY_SHEEN = (165, 220, 255, 255)

CARBON_DARK = (20, 24, 34, 255)
CARBON_MID = (38, 46, 60, 255)
CARBON_HI = (64, 76, 96, 255)

ION_CORE = (235, 250, 255, 255)
ION_CYAN = (80, 210, 255, 255)
ION_DEEP = (25, 125, 225, 255)

NAV_RED = (255, 65, 80, 255)
NAV_GREEN = (45, 240, 140, 255)


def _cursor_leds(state: str):
    if state in {"caution", "exhausted"}:
        return (
            (30, 22, 10, 255), (55, 38, 14, 255), (190, 115, 25, 255),
            (240, 155, 35, 255), (255, 195, 65, 255), (255, 240, 170, 255)
        )
    if state in {"critical", "error"}:
        return (
            (32, 10, 14, 255), (58, 16, 22, 255), (180, 28, 42, 255),
            (235, 48, 68, 255), (255, 82, 102, 255), (255, 195, 205, 255)
        )
    if state in {"offline", "stale"}:
        return (
            (16, 20, 26, 255), (28, 34, 44, 255), (65, 78, 96, 255),
            (100, 118, 140, 255), (145, 165, 190, 255), (200, 215, 230, 255)
        )
    return (
        (8, 22, 38, 255), (16, 38, 64, 255), (25, 110, 195, 255),
        (50, 165, 240, 255), (90, 215, 255, 255), (220, 248, 255, 255)
    )


def create_cursor_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)

    # Hover dynamics
    bob_y = 0
    jitter_x = 0
    if state == "idle":
        bob_y = [0, -2, 0, 1][frame % 4]
    elif state == "busy":
        bob_y = [-1, 0, -2, 0][frame % 4]
    elif state == "caution":
        jitter_x = [0, 1][frame % 2]
        bob_y = [0, 1][frame % 2]
    elif state == "critical":
        jitter_x = [2, -2][frame % 2]
        bob_y = [1, -2][frame % 2]
    elif state == "exhausted":
        bob_y = 3
    elif state == "reset":
        bob_y = [2, -3, -1, 0][frame % 4]
    elif state == "offline":
        bob_y = 5
    elif state == "stale":
        bob_y = 0

    ox = 36 + jitter_x
    oy = 31 + bob_y
    hud_bg, hud_line, led_dark, led_mid, led_glow, led_bright = _cursor_leds(state)

    # --- 1. Swept Hover Fins / Stabilizer Canards ---
    fin_tilt = -1 if state == "busy" else ( 2 if state in {"exhausted", "offline"} else (frame % 2) )
    # Left Wing Fin
    l_fin_pts = [(ox - 14, oy - 4), (ox - 26, oy + 4 + fin_tilt), (ox - 24, oy + 9 + fin_tilt), (ox - 14, oy + 6)]
    d.polygon(l_fin_pts, fill=CURSOR_OUTLINE)
    d.polygon([(p[0] + 1, p[1]) for p in l_fin_pts[:3]], fill=CARBON_MID)
    d.line([(ox - 14, oy - 3), (ox - 25, oy + 4 + fin_tilt)], fill=CURSOR_BODY_SHEEN)
    # Left port nav beacon (red)
    d.point((ox - 26, oy + 4 + fin_tilt), fill=NAV_RED if state != "offline" else CARBON_DARK)

    # Right Wing Fin
    r_fin_pts = [(ox + 14, oy - 4), (ox + 26, oy + 4 + fin_tilt), (ox + 24, oy + 9 + fin_tilt), (ox + 14, oy + 6)]
    d.polygon(r_fin_pts, fill=CURSOR_OUTLINE)
    d.polygon([(p[0] - 1, p[1]) for p in r_fin_pts[:3]], fill=CARBON_MID)
    d.line([(ox + 14, oy - 3), (ox + 25, oy + 4 + fin_tilt)], fill=CURSOR_BODY_SHEEN)
    # Right starboard nav beacon (green)
    d.point((ox + 26, oy + 4 + fin_tilt), fill=NAV_GREEN if state != "offline" else CARBON_DARK)

    # --- 2. Aero-Drone Helm & Chevron Fuselage ---
    prow_apex = (ox, oy - 19)
    helm_poly = [
        prow_apex,
        (ox + 9, oy - 12),
        (ox + 18, oy - 4),
        (ox + 14, oy + 8),
        (ox - 14, oy + 8),
        (ox - 18, oy - 4),
        (ox - 9, oy - 12),
    ]
    d.polygon(helm_poly, fill=CURSOR_OUTLINE)
    inner_poly = [
        (prow_apex[0], prow_apex[1] + 1),
        (ox + 8, oy - 11),
        (ox + 16, oy - 4),
        (ox + 13, oy + 7),
        (ox - 13, oy + 7),
        (ox - 16, oy - 4),
        (ox - 8, oy - 11),
    ]
    d.polygon(inner_poly, fill=CURSOR_BODY_BASE)
    d.line([(prow_apex[0], prow_apex[1] + 1), (ox - 8, oy - 11), (ox - 16, oy - 4)], fill=CURSOR_BODY_SHEEN)
    d.line([(ox + 13, oy + 7), (ox - 13, oy + 7)], fill=CURSOR_SHADOW)

    # Top apex sensor beacon crystal
    d.point(prow_apex, fill=led_bright if state != "offline" else CARBON_HI)
    d.point((ox, oy - 17), fill=led_glow)

    # Air cooling gills
    d.line([(ox - 5, oy - 11), (ox - 2, oy - 11)], fill=CURSOR_SHADOW)
    d.line([(ox + 2, oy - 11), (ox + 5, oy - 11)], fill=CURSOR_SHADOW)

    # --- 3. Panoramic HUD Visor & Targeting Reticle ---
    vis_l, vis_r = ox - 14, ox + 14
    vis_t, vis_b = oy - 6, oy + 5
    d.rectangle([vis_l - 1, vis_t - 1, vis_r + 1, vis_b + 1], fill=CURSOR_SHADOW)
    d.rectangle([vis_l, vis_t, vis_r, vis_b], fill=hud_bg)
    for sy in range(vis_t + 1, vis_b, 2):
        d.line([(vis_l, sy), (vis_r, sy)], fill=hud_line)
    d.point((vis_l + 1, vis_t + 1), fill=CURSOR_BODY_SHEEN)

    # HUD Navigation & Reticle Acting
    cx, cy = ox, oy - 1
    if state in {"offline", "stale"}:
        # Dormant flatline or static
        d.line([(vis_l + 3, cy), (vis_r - 3, cy)], fill=led_mid)
        if state == "stale":
            d.point((cx, cy - 2), fill=led_glow)
            d.point((cx + 4, cy + 2), fill=led_glow)
    elif state == "idle":
        # Holographic crosshair cursor
        shift_x = 1 if frame == 1 else 0
        d.line([(cx - 4 + shift_x, cy), (cx + 4 + shift_x, cy)], fill=led_glow)
        d.line([(cx + shift_x, cy - 3), (cx + shift_x, cy + 3)], fill=led_glow)
        d.point((cx + shift_x, cy), fill=C_WHITE)
        # Radar corner brackets
        if frame % 2 == 0:
            d.point((cx - 7 + shift_x, cy - 3), fill=led_bright)
            d.point((cx + 7 + shift_x, cy + 3), fill=led_bright)
    elif state == "busy":
        # Fast rotating navigator reticle and speed vector lines
        angle = (frame % 4) * 45
        dx = int(3 * math.cos(math.radians(angle)))
        dy = int(3 * math.sin(math.radians(angle)))
        d.line([(cx - dx, cy - dy), (cx + dx, cy + dy)], fill=led_bright, width=2)
        d.line([(cx + dy, cy - dx), (cx - dy, cy + dx)], fill=led_glow)
        d.point((cx, cy), fill=C_WHITE)
        # Streaming vector arrows >>>
        v_offset = (frame % 3) * 2
        d.point((vis_r - 4 + v_offset, cy), fill=led_bright)
        d.line([(vis_l + 2, cy - 2), (vis_l + 5, cy - 2)], fill=led_mid)
    elif state in {"caution", "exhausted"}:
        # Warning diamond & exclamation HUD
        d.line([(cx - 3, cy), (cx, cy - 3), (cx + 3, cy), (cx, cy + 3), (cx - 3, cy)], fill=led_glow)
        d.point((cx, cy), fill=led_bright)
        sw_y = prow_apex[1] + 6 + (frame % 2) * 2
        d.ellipse([ox + 16, sw_y, ox + 20, sw_y + 4], fill=C_SWEAT)
        d.point((ox + 17, sw_y + 1), fill=C_SWEAT_HI)
    elif state == "critical":
        # Glitched broken crosshair & alert strobe
        if frame % 2 == 0:
            d.line([(cx - 5, cy - 3), (cx + 5, cy + 3)], fill=led_bright, width=2)
            d.line([(cx + 5, cy - 3), (cx - 5, cy + 3)], fill=led_bright, width=2)
        else:
            d.rectangle([cx - 4, cy - 3, cx + 4, cy + 3], outline=led_bright)
            d.point((cx, cy), fill=C_WHITE)
    elif state == "reset":
        # Expanding circular radar lock-on sequence
        rad = (frame % 4) + 2
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=led_bright)
        d.point((cx, cy), fill=C_WHITE)

    # --- 4. Lower Fuselage & Ion Reactor Core ---
    fus_l, fus_r = ox - 11, ox + 11
    fus_t, fus_b = oy + 8, oy + 18
    d.rounded_rectangle([fus_l, fus_t, fus_r, fus_b], radius=3, fill=CURSOR_OUTLINE)
    d.rounded_rectangle([fus_l + 1, fus_t + 1, fus_r - 1, fus_b - 1], radius=2, fill=CURSOR_BODY_BASE)
    d.line([(fus_l + 2, fus_t + 1), (fus_r - 2, fus_t + 1)], fill=CURSOR_BODY_HI)

    # Gyroscopic ion reactor sphere
    core_cy = oy + 13
    d.ellipse([ox - 4, core_cy - 4, ox + 4, core_cy + 4], fill=CURSOR_OUTLINE)
    d.ellipse([ox - 3, core_cy - 3, ox + 3, core_cy + 3], fill=led_dark)
    core_rad = 2 if (frame % 2 == 0 or state == "critical") else 1
    d.ellipse([ox - core_rad, core_cy - core_rad, ox + core_rad, core_cy + core_rad], fill=led_glow)
    d.point((ox, core_cy), fill=C_WHITE if frame % 2 == 0 else led_bright)

    # --- 5. Dual Ion Thrusters & Levitation Plasma Exhaust ---
    noz_y1, noz_y2 = fus_b, fus_b + 4
    # Dual thruster nozzles
    d.rectangle([ox - 8, noz_y1, ox - 3, noz_y2], fill=CARBON_DARK)
    d.rectangle([ox + 3, noz_y1, ox + 8, noz_y2], fill=CARBON_DARK)
    d.line([(ox - 7, noz_y2), (ox - 4, noz_y2)], fill=CARBON_HI)
    d.line([(ox + 4, noz_y2), (ox + 7, noz_y2)], fill=CARBON_HI)

    # Exhaust Flames / Levitation Rings
    flame_top = noz_y2 + 1
    if state == "offline":
        # Landed carbon skids resting on the ground
        d.line([(ox - 8, flame_top), (ox - 10, flame_top + 6)], fill=CARBON_DARK, width=2)
        d.line([(ox + 8, flame_top), (ox + 10, flame_top + 6)], fill=CARBON_DARK, width=2)
        d.line([(ox - 13, flame_top + 6), (ox - 7, flame_top + 6)], fill=CARBON_MID, width=2)
        d.line([(ox + 7, flame_top + 6), (ox + 13, flame_top + 6)], fill=CARBON_MID, width=2)
    elif state == "busy":
        # Full afterburner jet flare!
        flame_len = 12 + (frame % 2) * 3
        d.polygon([(ox - 7, flame_top), (ox - 4, flame_top), (ox - 5, flame_top + flame_len)], fill=ION_CYAN)
        d.polygon([(ox + 4, flame_top), (ox + 7, flame_top), (ox + 6, flame_top + flame_len)], fill=ION_CYAN)
        d.line([(ox - 6, flame_top), (ox - 5, flame_top + flame_len - 3)], fill=ION_CORE)
        d.line([(ox + 5, flame_top), (ox + 6, flame_top + flame_len - 3)], fill=ION_CORE)
        # Downward particle sparks
        d.point((ox - 3, flame_top + flame_len + 1), fill=led_bright)
        d.point((ox + 4, flame_top + flame_len + 2), fill=led_bright)
    elif state == "critical":
        # Misfiring red/orange stall combustion
        burst = (frame % 2) * 3
        d.ellipse([ox - 8, flame_top, ox - 3, flame_top + 5 + burst], fill=(245, 50, 40, 255))
        d.ellipse([ox + 3, flame_top, ox + 8, flame_top + 5 - burst], fill=(255, 140, 30, 255))
        d.point((ox, flame_top + 8), fill=C_SPARK_GOLD)
    elif state in {"caution", "exhausted"}:
        # Sputtering small flame puff
        d.ellipse([ox - 7, flame_top, ox - 4, flame_top + 3], fill=led_glow)
        d.ellipse([ox + 4, flame_top, ox + 7, flame_top + 3], fill=led_glow)
    elif state == "reset":
        # Concentric shockwave burst rings
        d.ellipse([ox - 10, flame_top, ox + 10, flame_top + 6], outline=led_bright)
        d.ellipse([ox - 6, flame_top + 4, ox + 6, flame_top + 10], outline=led_glow)
    else:
        # Idle levitation ion pulse
        f_len = 5 + (frame % 2) * 2
        d.polygon([(ox - 7, flame_top), (ox - 4, flame_top), (ox - 5, flame_top + f_len)], fill=ION_CYAN)
        d.polygon([(ox + 4, flame_top), (ox + 7, flame_top), (ox + 6, flame_top + f_len)], fill=ION_CYAN)
        d.point((ox - 5, flame_top + 1), fill=ION_CORE)
        d.point((ox + 5, flame_top + 1), fill=ION_CORE)
        # Levitation plasma ripple ring
        d.line([(ox - 9, flame_top + f_len + 2), (ox + 9, flame_top + f_len + 2)], fill=led_glow)

    return img


# ==============================================================================
# 3. GROK — Violet Star Explorer (Cosmic Rover Astronaut)
# ==============================================================================
# Palette: Deep cosmic violet, midnight nebula, star gold, lunar rover titanium
GROK_OUTLINE = (18, 10, 30, 255)
GROK_SHADOW = (32, 18, 52, 255)
GROK_ROVER_DARK = (62, 36, 96, 255)
GROK_ROVER_BASE = (94, 58, 142, 255)
GROK_ROVER_MID = (135, 88, 195, 255)
GROK_ROVER_HI = (175, 126, 235, 255)
GROK_ROVER_SHEEN = (220, 185, 255, 255)

GOLD_FOIL_DARK = (160, 120, 20, 255)
GOLD_FOIL_BASE = (225, 175, 35, 255)
GOLD_FOIL_HI = (255, 218, 75, 255)

DOME_GLASS_EDGE = (75, 48, 115, 255)
DOME_GLASS_SHEEN = (245, 235, 255, 255)
DOME_NEBULA_DARK = (24, 12, 42, 255)
DOME_NEBULA_MID = (48, 22, 78, 255)

STAR_GOLD = (255, 225, 90, 255)
STAR_WHITE = (255, 255, 255, 255)
TIRE_RUBBER = (28, 24, 36, 255)
TIRE_TREAD = (44, 38, 56, 255)
RIM_METAL = (150, 135, 175, 255)


def _grok_leds(state: str):
    if state in {"caution", "exhausted"}:
        return (
            (30, 20, 10, 255), (55, 36, 15, 255), (185, 105, 22, 255),
            (235, 148, 32, 255), (255, 190, 58, 255), (255, 238, 165, 255)
        )
    if state in {"critical", "error"}:
        return (
            (34, 10, 16, 255), (60, 16, 24, 255), (180, 25, 45, 255),
            (235, 46, 72, 255), (255, 80, 110, 255), (255, 195, 210, 255)
        )
    if state in {"offline", "stale"}:
        return (
            (18, 14, 26, 255), (30, 24, 42, 255), (70, 60, 90, 255),
            (110, 95, 135, 255), (155, 140, 185, 255), (210, 200, 230, 255)
        )
    return (
        (22, 12, 38, 255), (42, 22, 70, 255), (125, 65, 195, 255),
        (170, 105, 240, 255), (205, 155, 255, 255), (245, 225, 255, 255)
    )


def create_grok_sprite(state: str, frame: int) -> Image.Image:
    img = Image.new("RGBA", (72, 72), C_TRANS)
    d = ImageDraw.Draw(img)

    # Suspension dynamics
    bob_y = 0
    jitter_x = 0
    if state == "idle":
        bob_y = [0, 1, 0, 0][frame % 4]
    elif state == "busy":
        bob_y = [0, -1, 0, 1][frame % 4]
    elif state == "caution":
        jitter_x = [0, 1][frame % 2]
    elif state in {"critical", "exhausted"}:
        jitter_x = [1, -1][frame % 2]
        bob_y = [0, 1][frame % 2]
    elif state == "reset":
        bob_y = [1, -2, 0, -1][frame % 4]
    elif state == "offline":
        bob_y = 3
    elif state == "stale":
        bob_y = 1

    ox = 36 + jitter_x
    oy = 32 + bob_y
    neb_bg, neb_line, led_dark, led_mid, led_glow, led_bright = _grok_leds(state)

    # --- 1. Parabolic Satellite Communications Dish (Top Right) ---
    dish_pivot = (ox + 13, oy - 14)
    if state == "reset":
        # Pointed straight UP to cosmos!
        dish_center = (ox + 14, oy - 23)
        d.line([dish_pivot, dish_center], fill=GROK_OUTLINE, width=2)
        d.ellipse([dish_center[0] - 5, dish_center[1] - 3, dish_center[0] + 5, dish_center[1] + 3], fill=GROK_ROVER_MID, outline=GROK_OUTLINE)
        d.line([(dish_center[0], dish_center[1] - 2), (dish_center[0], dish_center[1] - 7)], fill=led_bright, width=2)
        d.point((dish_center[0], dish_center[1] - 8), fill=C_SPARK_GOLD)
    elif state in {"exhausted", "offline"}:
        # Drooped down against helmet
        d.line([dish_pivot, (ox + 20, oy - 10)], fill=GROK_OUTLINE, width=2)
        d.ellipse([ox + 17, oy - 13, ox + 23, oy - 7], fill=GROK_ROVER_DARK, outline=GROK_OUTLINE)
    else:
        # Active tracking dish
        d_tilt = 1 if frame % 2 == 0 else -1
        dish_center = (ox + 20, oy - 20 + d_tilt)
        d.line([dish_pivot, dish_center], fill=GROK_OUTLINE, width=2)
        d.ellipse([dish_center[0] - 4, dish_center[1] - 5, dish_center[0] + 4, dish_center[1] + 5], fill=GROK_ROVER_MID, outline=GROK_OUTLINE)
        d.line([(dish_center[0] - 1, dish_center[1]), (dish_center[0] + 4, dish_center[1] - 2)], fill=GOLD_FOIL_HI)
        d.point((dish_center[0] + 5, dish_center[1] - 2), fill=led_bright)
        if state == "busy":
            # Emitting radio wave arcs
            d.arc([dish_center[0] + 3, dish_center[1] - 8, dish_center[0] + 11, dish_center[1]], start=-60, end=40, fill=led_glow, width=2)
            d.arc([dish_center[0] + 7, dish_center[1] - 12, dish_center[0] + 17, dish_center[1] + 4], start=-60, end=40, fill=led_bright)
        elif state == "critical":
            d.line([(dish_center[0] + 4, dish_center[1] - 4), (dish_center[0] + 9, dish_center[1] - 7)], fill=led_glow)
            d.line([(dish_center[0] + 2, dish_center[1] + 4), (dish_center[0] + 8, dish_center[1] + 6)], fill=led_glow)

    # --- 2. Bubble Helmet Dome ---
    dome_l, dome_r = ox - 20, ox + 20
    dome_t, dome_b = oy - 17, oy + 11
    d.ellipse([dome_l, dome_t, dome_r, dome_b], fill=GROK_OUTLINE)
    d.ellipse([dome_l + 1, dome_t + 1, dome_r - 1, dome_b - 1], fill=DOME_GLASS_EDGE)
    # Interior Nebula Atmosphere
    d.ellipse([dome_l + 3, dome_t + 3, dome_r - 3, dome_b - 3], fill=neb_bg)
    # Stardust gradient & twinkling micro-stars
    d.ellipse([dome_l + 5, dome_t + 6, dome_r - 5, dome_b - 5], fill=DOME_NEBULA_MID if state != "offline" else neb_bg)
    if state != "offline":
        d.point((ox - 13, oy - 10), fill=led_mid)
        d.point((ox + 11, oy - 9), fill=led_mid)
        d.point((ox - 1, oy - 12), fill=C_WHITE)

    # Glass reflection sheen on upper-left quadrant
    d.arc([dome_l + 3, dome_t + 2, ox, oy - 2], start=170, end=270, fill=DOME_GLASS_SHEEN, width=2)
    d.point((dome_l + 6, dome_t + 4), fill=STAR_WHITE)

    # --- 3. Visor Face: Star-Eyes & Celestial Expressions ---
    eye_y = oy - 2
    lx, rx = ox - 8, ox + 6

    def _draw_star_eye(cx: int, cy: int, col_center, col_arms):
        d.point((cx, cy), fill=col_center)
        d.point((cx - 1, cy), fill=col_arms)
        d.point((cx + 1, cy), fill=col_arms)
        d.point((cx, cy - 1), fill=col_arms)
        d.point((cx, cy + 1), fill=col_arms)

    if state in {"offline", "stale"}:
        # Dim dormant star points
        d.point((lx, eye_y), fill=led_mid)
        d.point((rx, eye_y), fill=led_mid)
        if state == "stale":
            d.point((ox, oy - 5), fill=led_glow)
    elif state == "idle":
        # 4-point golden star eyes
        if frame == 3:
            # Right eye wink into single bright diamond
            _draw_star_eye(lx, eye_y, STAR_WHITE, STAR_GOLD)
            d.point((rx, eye_y), fill=STAR_WHITE)
            d.point((rx - 1, eye_y), fill=STAR_GOLD)
        else:
            _draw_star_eye(lx, eye_y, STAR_WHITE, STAR_GOLD)
            _draw_star_eye(rx, eye_y, STAR_WHITE, STAR_GOLD)
        # Cute violet smile
        d.line([(ox - 2, eye_y + 6), (ox + 2, eye_y + 6)], fill=led_glow)
        d.point((ox - 3, eye_y + 5), fill=led_glow)
        d.point((ox + 3, eye_y + 5), fill=led_glow)
    elif state == "busy":
        # Shooting stars with streaming comet tails
        tail_shift = [-2, 0, 2, 0][frame % 4]
        _draw_star_eye(lx + tail_shift, eye_y, STAR_WHITE, led_bright)
        _draw_star_eye(rx + tail_shift, eye_y, STAR_WHITE, led_bright)
        # Comet tails
        d.line([(lx + tail_shift - 4, eye_y), (lx + tail_shift - 1, eye_y)], fill=led_glow)
        d.line([(rx + tail_shift - 4, eye_y), (rx + tail_shift - 1, eye_y)], fill=led_glow)
    elif state in {"caution", "exhausted"}:
        # Swirling spiral galaxy eyes or sleepy crescent moons
        if state == "caution":
            d.line([(lx - 2, eye_y - 2), (lx + 2, eye_y + 2)], fill=led_glow, width=2)
            d.line([(rx - 2, eye_y + 2), (rx + 2, eye_y - 2)], fill=led_glow, width=2)
            sw_y = dome_t + 5 + (frame % 2) * 2
            d.ellipse([dome_r - 5, sw_y, dome_r - 1, sw_y + 4], fill=C_SWEAT)
            d.point((dome_r - 4, sw_y + 1), fill=C_SWEAT_HI)
        else:
            # Sleepy crescent moons
            d.arc([lx - 3, eye_y - 1, lx + 3, eye_y + 4], start=0, end=180, fill=led_glow, width=2)
            d.arc([rx - 3, eye_y - 1, rx + 3, eye_y + 4], start=0, end=180, fill=led_glow, width=2)
    elif state == "critical":
        # Red supernova burst alarm
        if frame % 2 == 0:
            _draw_star_eye(lx, eye_y, STAR_WHITE, led_bright)
            _draw_star_eye(rx, eye_y, STAR_WHITE, led_bright)
            d.ellipse([lx - 4, eye_y - 4, lx + 4, eye_y + 4], outline=led_bright)
            d.ellipse([rx - 4, eye_y - 4, rx + 4, eye_y + 4], outline=led_bright)
        else:
            d.line([(lx - 3, eye_y), (lx + 3, eye_y)], fill=led_bright, width=2)
            d.line([(rx - 3, eye_y), (rx + 3, eye_y)], fill=led_bright, width=2)
    elif state == "reset":
        # Supernova celebration starburst in center
        _draw_star_eye(ox, eye_y - 2, STAR_WHITE, STAR_GOLD)
        d.line([(ox - 5, eye_y - 2), (ox + 5, eye_y - 2)], fill=STAR_GOLD)
        d.line([(ox, eye_y - 7), (ox, eye_y + 3)], fill=STAR_GOLD)
        d.point((ox - 4, eye_y - 6), fill=C_SPARK_GOLD)
        d.point((ox + 4, eye_y - 6), fill=C_SPARK_GOLD)
        d.line([(ox - 3, eye_y + 6), (ox + 3, eye_y + 6)], fill=led_bright)

    # --- 4. Neck Collar & Lunar Rover Chassis ---
    neck_t = dome_b - 2
    d.rectangle([ox - 10, neck_t, ox + 10, neck_t + 3], fill=GROK_OUTLINE)
    d.rectangle([ox - 9, neck_t + 1, ox + 9, neck_t + 2], fill=GROK_ROVER_DARK)
    d.point((ox - 7, neck_t + 1), fill=GOLD_FOIL_HI)
    d.point((ox + 7, neck_t + 1), fill=GOLD_FOIL_HI)

    body_l, body_r, body_t, body_b = ox - 14, ox + 14, neck_t + 3, neck_t + 15
    d.rounded_rectangle([body_l, body_t, body_r, body_b], radius=3, fill=GROK_OUTLINE)
    d.rounded_rectangle([body_l + 1, body_t + 1, body_r - 1, body_b - 1], radius=2, fill=GROK_ROVER_BASE)
    d.line([(body_l + 2, body_t + 1), (body_r - 2, body_t + 1)], fill=GROK_ROVER_HI)

    # Gold foil thermal insulation patches on corners
    d.rectangle([body_l + 2, body_t + 2, body_l + 4, body_b - 2], fill=GOLD_FOIL_BASE)
    d.rectangle([body_r - 4, body_t + 2, body_r - 2, body_b - 2], fill=GOLD_FOIL_BASE)
    d.point((body_l + 3, body_t + 2), fill=GOLD_FOIL_HI)
    d.point((body_r - 3, body_t + 2), fill=GOLD_FOIL_HI)

    # Central Cosmic Pulsar Battery Core
    core_cy = body_t + 6
    d.ellipse([ox - 4, core_cy - 4, ox + 4, core_cy + 4], fill=GROK_OUTLINE)
    d.ellipse([ox - 3, core_cy - 3, ox + 3, core_cy + 3], fill=led_dark)
    core_rad = 2 if (frame % 2 == 0 or state == "critical") else 1
    d.ellipse([ox - core_rad, core_cy - core_rad, ox + core_rad, core_cy + core_rad], fill=led_glow)
    d.point((ox, core_cy), fill=C_WHITE if frame % 2 == 0 else led_bright)

    # --- 5. All-Terrain Lunar Rover Wheels ---
    whl_y1, whl_y2 = body_b - 3, body_b + 10
    left_whl = [ox - 17, whl_y1, ox - 7, whl_y2]
    right_whl = [ox + 7, whl_y1, ox + 17, whl_y2]

    # Wheel tread pattern rotation
    tread_shift = (frame % 3) * 2 if state == "busy" else 0

    for w_box in (left_whl, right_whl):
        d.rounded_rectangle(w_box, radius=4, fill=GROK_OUTLINE)
        d.rounded_rectangle([w_box[0] + 1, w_box[1] + 1, w_box[2] - 1, w_box[3] - 1], radius=3, fill=TIRE_RUBBER)
        # Deep tread lugs
        for ty in range(w_box[1] + 2 + tread_shift, w_box[3] - 1, 3):
            d.line([(w_box[0], ty), (w_box[0] + 2, ty)], fill=TIRE_TREAD)
            d.line([(w_box[2] - 2, ty), (w_box[2], ty)], fill=TIRE_TREAD)
        # Metallic hubcap
        hx = (w_box[0] + w_box[2]) // 2
        hy = (w_box[1] + w_box[3]) // 2
        d.ellipse([hx - 2, hy - 2, hx + 2, hy + 2], fill=RIM_METAL)
        d.point((hx, hy), fill=GOLD_FOIL_HI)

    # Busy state: tire kicking up cosmic dust particles behind wheels
    if state == "busy":
        d.point((left_whl[0] - 2, whl_y2 - 1), fill=led_glow)
        d.point((left_whl[0] - 4, whl_y2 - 3), fill=led_mid)
        d.point((right_whl[2] + 2, whl_y2 - 1), fill=led_glow)
        d.point((right_whl[2] + 4, whl_y2 - 3), fill=led_mid)

    return img


DRAWERS = {
    "claude": create_claude_sprite,
    "cursor": create_cursor_sprite,
    "grok": create_grok_sprite,
}


def generate_all():
    print("Generating Fancy QuotaDeck crew sprites for claude, cursor, grok...")
    total_generated = 0

    for provider, drawer in DRAWERS.items():
        prov_dir = CREW_DIR / provider
        prov_dir.mkdir(parents=True, exist_ok=True)
        print(f"\nProcessing provider: {provider}")

        for state, count in STATES.items():
            for f in range(count):
                img = drawer(state, f)
                assert img.size == (72, 72), f"Invalid size {img.size} for {provider}/{state}_{f:02d}.png"
                out_path = prov_dir / f"{state}_{f:02d}.png"
                img.save(out_path)
                total_generated += 1
            print(f"  - {state:10s} ({count} frames)")

    print(f"\nTotal sprites generated: {total_generated} across 3 providers.")
    create_preview_contact_sheet()


def create_preview_contact_sheet():
    """Create a 240x135 pixel contact sheet matching QuotaDeck LCD dimensions."""
    bg_color = (7, 12, 20, 255)  # #070C14
    sheet = Image.new("RGBA", (240, 135), bg_color)
    d = ImageDraw.Draw(sheet)

    # Outer subtle cyberdeck bezel
    d.rectangle([1, 1, 238, 133], outline=(20, 34, 52, 255))
    d.line([(1, 22), (238, 22)], fill=(24, 42, 64, 255))
    d.line([(1, 106), (238, 106)], fill=(24, 42, 64, 255))

    # Place 3 providers side-by-side in idle_00 pose
    # Widths: 6 + 72 + 6 + 72 + 6 + 72 + 6 = 240
    placements = [
        ("claude", 6, (255, 159, 90, 255), "CLAUDE", "SCHOLAR OWL"),
        ("cursor", 84, (80, 180, 255, 255), "CURSOR", "AERO DRONE"),
        ("grok", 162, (180, 140, 255, 255), "GROK", "STAR ROVER"),
    ]

    for prov, x, acc_col, title, subtitle in placements:
        # Header title
        d.text((x + 12, 6), title, fill=acc_col)
        # LED indicator dot
        d.ellipse([x + 2, 8, x + 7, 13], fill=acc_col)

        # Sprite image
        sprite_path = CREW_DIR / prov / "idle_00.png"
        sprite_img = Image.open(sprite_path)
        sheet.alpha_composite(sprite_img, (x, 28))

        # Footer subtitle
        d.text((x + 2, 114), subtitle, fill=(160, 185, 210, 255))

    preview_path = CREW_DIR / "preview_strip.png"
    sheet.save(preview_path)
    print(f"Wrote preview contact sheet to {preview_path} ({sheet.size[0]}x{sheet.size[1]})")


if __name__ == "__main__":
    generate_all()
