"""Tileable pattern textures for the premium rod skins.

    python blender/rods/new/textures.py

Everything is drawn here (no downloaded art): each texture tiles seamlessly
(anything crossing an edge is drawn again on the other side), is drawn at 2x
and scaled down for clean edges, and goes to blender/rods/new/textures/.

    royal_filigree.png  crimson lacquer with a gold diamond lattice, quatrefoils
                        at the crossings and small gold fleurs in each diamond
    royal_panel.png     an iridescent violet-to-blue sheen with white flourishes
    frost_knot.png      ice blue with white interlaced knotwork bands (over and
                        under) between zigzag borders
    frost_bone.png      carved ivory: engraved spirals and dots
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parent / "textures"
OUT.mkdir(parents=True, exist_ok=True)
S = 1024  # drawn at 2x, saved at 512
HALF = S // 2


def canvas(color):
    return Image.new("RGB", (S, S), color)


def wrapped(draw_fn):
    """Draw once per neighbouring tile offset, so the result tiles."""
    for dx in (-S, 0, S):
        for dy in (-S, 0, S):
            draw_fn(dx, dy)


def save(img, name):
    img.resize((HALF, HALF), Image.LANCZOS).save(OUT / name)
    print("wrote", name)


# ------------------------------------------------------------------ royal filigree

def royal_filigree():
    img = canvas((96, 10, 24))
    # A soft lacquer mottle: a few big faint blotches, tiled.
    shade = Image.new("L", (S, S), 0)
    sd = ImageDraw.Draw(shade)
    for cx, cy, r in ((200, 300, 260), (700, 650, 300), (820, 150, 200), (350, 850, 240)):
        wrapped(lambda dx, dy: sd.ellipse((cx - r + dx, cy - r + dy, cx + r + dx, cy + r + dy), fill=40))
    shade = shade.filter(ImageFilter.GaussianBlur(90))
    img = Image.composite(Image.new("RGB", (S, S), (130, 20, 36)), img, shade)
    d = ImageDraw.Draw(img)
    gold, gold_dark = (222, 172, 78), (150, 104, 38)
    step = 256
    # Diamond lattice.
    for k in range(-4, 9):
        o = k * step
        for dx, dy in ((0, 0),):
            d.line((o, 0, o + S, S), fill=gold_dark, width=9)
            d.line((o, 0, o - S, S), fill=gold_dark, width=9)
            d.line((o, 0, o + S, S), fill=gold, width=5)
            d.line((o, 0, o - S, S), fill=gold, width=5)
    # Quatrefoils at the crossings (every step along x and y, offset half on alternate rows).
    def quatrefoil(cx, cy):
        for ox, oy in ((0, -13), (13, 0), (0, 13), (-13, 0)):
            d.ellipse((cx + ox - 10, cy + oy - 10, cx + ox + 10, cy + oy + 10), fill=gold, outline=gold_dark, width=2)
        d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=(255, 225, 150))

    def fleur(cx, cy):
        d.ellipse((cx - 7, cy - 26, cx + 7, cy + 4), fill=gold, outline=gold_dark, width=2)
        d.pieslice((cx - 30, cy - 16, cx + 2, cy + 16), 200, 340, fill=gold)
        d.pieslice((cx - 2, cy - 16, cx + 30, cy + 16), 200, 340, fill=gold)
        d.rectangle((cx - 12, cy + 6, cx + 12, cy + 11), fill=gold_dark)

    for gx in range(0, S + 1, step // 2):
        for gy in range(0, S + 1, step // 2):
            crossing = (gx // (step // 2) + gy // (step // 2)) % 2 == 0
            wrapped(lambda dx, dy: (quatrefoil if crossing else fleur)(gx + dx, gy + dy))
    save(img, "royal_filigree.png")


# ------------------------------------------------------------------ royal panel

def royal_panel():
    img = Image.new("RGB", (S, S))
    px = img.load()
    for x in range(S):
        t = 0.5 + 0.5 * math.sin(2 * math.pi * x / S)
        u = 0.5 + 0.5 * math.sin(4 * math.pi * x / S + 1.3)
        r = int(70 + 90 * t)
        g = int(30 + 60 * u * (1 - t))
        b = int(170 + 80 * (1 - t))
        for y in range(S):
            px[x, y] = (r, g, b)
    d = ImageDraw.Draw(img)
    white, glow = (250, 245, 255), (200, 180, 255)

    def scroll(cx, cy, size, flip):
        pts = []
        for i in range(90):
            a = i / 89 * 3.2 * math.pi
            rr = size * (1 - i / 110)
            pts.append((cx + flip * rr * math.cos(a), cy + rr * math.sin(a)))
        d.line(pts, fill=glow, width=9, joint="curve")
        d.line(pts, fill=white, width=4, joint="curve")

    def vine(y0):
        pts = [(x, y0 + 40 * math.sin(2 * math.pi * x / (S / 2))) for x in range(-20, S + 21, 8)]
        d.line(pts, fill=glow, width=10, joint="curve")
        d.line(pts, fill=white, width=5, joint="curve")

    for y0 in (HALF // 2, HALF + HALF // 2):
        vine(y0)
        for k in range(4):
            cx = k * S / 4 + S / 8
            cy = y0 + (48 if k % 2 else -48)
            wrapped(lambda dx, dy: scroll(cx + dx, cy + dy, 42, 1 if k % 2 else -1))
    save(img, "royal_panel.png")


# ------------------------------------------------------------------ frost knotwork

def frost_knot():
    """Braided knotwork: three strands wound like a rope (a helix seen side-on),
    drawn back to front so every crossing goes over and under by itself."""
    img = canvas((70, 142, 210))
    d = ImageDraw.Draw(img)
    white, edge, shine, deep = (246, 250, 255), (18, 44, 98), (205, 228, 250), (40, 92, 165)

    def braid(y0, amp, width, period, strands=3):
        segs = []
        step = 5
        for k in range(strands):
            ph = 2 * math.pi * k / strands
            for x in range(-60, S + 60, step):
                a0 = 2 * math.pi * x / period + ph
                a1 = 2 * math.pi * (x + step) / period + ph
                p0 = (x, y0 + amp * math.sin(a0))
                p1 = (x + step, y0 + amp * math.sin(a1))
                segs.append((math.cos((a0 + a1) / 2), p0, p1))
        segs.sort(key=lambda t: t[0])
        for depth, p0, p1 in segs:
            d.line((p0, p1), fill=edge, width=width + 12)
        # Fill in the same order so the edge of a front strand covers the one behind.
        for depth, p0, p1 in segs:
            d.line((p0, p1), fill=edge, width=width + 12)
            d.line((p0, p1), fill=white, width=width)
            d.line((p0, p1), fill=shine, width=max(2, width // 5))

    braid(S / 4, 64, 30, S / 2)
    braid(3 * S / 4, 64, 30, S / 2)
    # A thin two-strand cable and zigzag trims between the main braids.
    for yb in (0, S / 2, S):
        braid(yb, 16, 10, S / 8, strands=2)
        for off in (-40, 40):
            zig = [(x, yb + off + (12 if (x // 32) % 2 else -12)) for x in range(0, S + 33, 32)]
            d.line(zig, fill=deep, width=5)
    save(img, "frost_knot.png")


# ------------------------------------------------------------------ carved bone

def frost_bone():
    img = canvas((228, 218, 194))
    d = ImageDraw.Draw(img)
    groove, hi = (150, 132, 104), (248, 242, 226)

    def spiral(cx, cy, size, turns, flip):
        pts = []
        n = 120
        for i in range(n):
            a = i / (n - 1) * turns * 2 * math.pi
            rr = size * (i / (n - 1))
            pts.append((cx + flip * rr * math.cos(a), cy + rr * math.sin(a)))
        d.line([(x + 2, y + 2) for x, y in pts], fill=hi, width=6, joint="curve")
        d.line(pts, fill=groove, width=6, joint="curve")

    spots = [(160, 180, 110, 1), (560, 120, 90, -1), (820, 420, 120, 1), (330, 560, 100, -1),
             (700, 820, 115, -1), (120, 860, 95, 1), (480, 360, 60, 1), (900, 120, 55, -1)]
    for cx, cy, size, flip in spots:
        wrapped(lambda dx, dy: spiral(cx + dx, cy + dy, size, 2.6, flip))
    for k in range(60):
        x = (k * 173) % S
        y = (k * 311) % S
        d.ellipse((x - 5, y - 5, x + 5, y + 5), fill=groove)
    img = img.filter(ImageFilter.GaussianBlur(1.2))
    save(img, "frost_bone.png")


# ------------------------------------------------------------------ inferno honeycomb

def inferno_hex():
    """Red-chrome armour plates in a honeycomb: every cell is a recessed hex
    with a bevelled rim; about a third of them are glowing cells (white-pink),
    in clusters. Writes the colour and a matching glow mask (only the lit
    cells), so the plates stay red and only the cells shine."""
    import random

    rnd = random.Random(5)
    col_w, row_h, cols, rows = S / 8, S / 10, 8, 10
    r = col_w * 0.46
    img = canvas((150, 14, 14))
    glow = Image.new("RGB", (S, S), (0, 0, 0))
    # Chrome banding: soft light/dark stripes across the plate, tiled.
    band = Image.new("L", (S, S), 0)
    bd = ImageDraw.Draw(band)
    for y in range(S):
        v = int(60 + 60 * math.sin(2 * math.pi * y / S * 2) + 30 * math.sin(2 * math.pi * y / S * 5))
        bd.line((0, y, S, y), fill=max(0, v))
    img = Image.composite(Image.new("RGB", (S, S), (230, 70, 60)), img, band)
    d = ImageDraw.Draw(img)
    g = ImageDraw.Draw(glow)
    lit = set()
    for _ in range(6):
        c0, r0 = rnd.randrange(cols), rnd.randrange(rows)
        for dc, dr in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1), (1, 1 if r0 % 2 else -1)):
            if rnd.random() < 0.8:
                lit.add(((c0 + dc) % cols, (r0 + dr) % rows))

    def hexagon(cx, cy, rr):
        return [(cx + rr * math.cos(math.pi / 6 + k * math.pi / 3), cy + rr * math.sin(math.pi / 6 + k * math.pi / 3))
                for k in range(6)]

    for row in range(rows):
        for col in range(cols):
            cx = col * col_w + (col_w / 2 if row % 2 else 0)
            cy = row * row_h
            on = (col, row) in lit

            def draw_cell(dx, dy):
                x, y = cx + dx, cy + dy
                d.polygon(hexagon(x, y, r), fill=(90, 6, 10))
                d.polygon(hexagon(x, y, r * 0.86), fill=(255, 205, 215) if on else (175, 22, 26))
                if on:
                    d.polygon(hexagon(x, y, r * 0.55), fill=(255, 245, 250))
                    g.polygon(hexagon(x, y, r * 0.86), fill=(255, 120, 130))
                    g.polygon(hexagon(x, y, r * 0.55), fill=(255, 235, 240))
                else:
                    d.line(hexagon(x, y, r * 0.86)[:4], fill=(240, 110, 100), width=4)

            wrapped(draw_cell)
    save(img, "inferno_hex.png")
    save(glow, "inferno_hex_glow.png")


inferno_hex()
royal_filigree()
royal_panel()
frost_knot()
frost_bone()
