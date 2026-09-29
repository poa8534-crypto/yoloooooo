"""Join compare_skins.py's renders into one labelled sheet per skin.

    python blender/rods/compare_sheet.py

Writes blender/rods/out/inspect/<folder>_before_after.png:
BEFORE front | BEFORE side | AFTER front | AFTER side, each labelled.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[2] / "blender" / "rods" / "out" / "inspect"

for folder in ("Lava rod", "Void rod"):
    tag = folder.replace(" ", "_")
    tiles = [
        ("BEFORE (raw) - front", f"{tag}_before_front.png"),
        ("BEFORE (raw) - side", f"{tag}_before_side.png"),
        ("AFTER (repaired) - front", f"{tag}_after_front.png"),
        ("AFTER (repaired) - side", f"{tag}_after_side.png"),
    ]
    images = [Image.open(OUT / name).convert("RGB") for _, name in tiles]
    w, h = images[0].size
    sheet = Image.new("RGB", (w * 4 + 30, h + 60), (40, 44, 52))
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("arialbd.ttf", 30)
    except OSError:
        font = ImageFont.load_default()
    for i, ((label, _), img) in enumerate(zip(tiles, images)):
        x = i * w + (30 if i >= 2 else 0)
        sheet.paste(img, (x, 60))
        draw.text((x + 14, 14), label, fill=(255, 220, 120) if i >= 2 else (230, 230, 230), font=font)
    sheet.save(OUT / f"{tag}_before_after.png")
    print("sheet", tag)
