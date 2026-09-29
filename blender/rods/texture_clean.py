"""Clean the owner's rod-skin textures before the skins are repaired in Blender.

    python blender/rods/texture_clean.py "Lava rod" "Void rod"

The skins come from an image-to-3D tool: their textures have speckle noise,
blotches and saw-tooth borders where the UV islands end (the renderer samples
the empty background next to an island's edge). For each folder this:

  1. rasterises the UV layout from output.obj (which pixels belong to the model);
  2. pads every island outward by PAD pixels with its own edge colours, so a
     seam can never sample the background (the saw-tooth goes away);
  3. removes speckles (a small median filter) and blotchy noise inside the
     islands (a mode filter, which also gives the flat, painted look the game
     uses), then sharpens a little and gives back colour and contrast;

and writes blender/rods/out/repaired/<folder>/textured_mesh.png, plus a
before/after strip for checking by eye.
"""

import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "blender" / "rods" / "out" / "repaired"
PAD = 12
MEDIAN = 3
MODE = 5
SATURATION = 1.18
CONTRAST = 1.08
SHARPEN = (1.4, 60, 2)  # radius, percent, threshold


def uv_mask(obj_path: Path, size: tuple[int, int]) -> Image.Image:
    uvs: list[tuple[float, float]] = []
    tris: list[list[int]] = []
    with obj_path.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            if line.startswith("vt "):
                parts = line.split()
                uvs.append((float(parts[1]), float(parts[2])))
            elif line.startswith("f "):
                corners = []
                for token in line.split()[1:]:
                    bits = token.split("/")
                    if len(bits) > 1 and bits[1]:
                        corners.append(int(bits[1]) - 1)
                if len(corners) >= 3:
                    for k in range(1, len(corners) - 1):
                        tris.append([corners[0], corners[k], corners[k + 1]])
    w, h = size
    mask = Image.new("L", size, 0)
    draw = ImageDraw.Draw(mask)
    for tri in tris:
        pts = [(uvs[i][0] * w, (1 - uvs[i][1]) * h) for i in tri]
        draw.polygon(pts, fill=255)
    # Grow by one pixel so triangle edges are covered.
    return mask.filter(ImageFilter.MaxFilter(3))


def pad_islands(image: np.ndarray, mask: np.ndarray, steps: int) -> np.ndarray:
    """Push the islands' colours outward into the empty pixels, one ring per step."""
    img = image.astype(np.float32)
    known = mask.copy()
    for _ in range(steps):
        total = np.zeros_like(img)
        count = np.zeros(known.shape, np.float32)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                shifted = np.roll(np.roll(img * known[..., None], dy, 0), dx, 1)
                shifted_known = np.roll(np.roll(known, dy, 0), dx, 1)
                total += shifted
                count += shifted_known
        grow = (~known) & (count > 0)
        img[grow] = total[grow] / count[grow][:, None]
        known = known | grow
    return img.clip(0, 255).astype(np.uint8)


def clean(folder: str) -> None:
    src_dir = ROOT / folder
    tex_path = src_dir / "textured_mesh.jpg"
    obj_path = src_dir / "output.obj"
    if not tex_path.exists() or not obj_path.exists():
        print("MISSING", folder)
        return
    original = Image.open(tex_path).convert("RGB")
    mask_img = uv_mask(obj_path, original.size)
    mask = np.array(mask_img) > 127

    # Denoise inside the islands first (the background would bleed in otherwise).
    work = original.filter(ImageFilter.MedianFilter(MEDIAN)).filter(ImageFilter.ModeFilter(MODE))
    arr = np.array(work)
    arr[~mask] = 0
    arr = pad_islands(arr, mask, PAD)
    cleaned = Image.fromarray(arr)
    cleaned = cleaned.filter(ImageFilter.UnsharpMask(*SHARPEN))
    cleaned = ImageEnhance.Color(cleaned).enhance(SATURATION)
    cleaned = ImageEnhance.Contrast(cleaned).enhance(CONTRAST)

    out_dir = OUT / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    cleaned.save(out_dir / "textured_mesh.png")
    # A strip to compare by eye: a 700 px crop of the busiest area, before | after.
    cx, cy = original.size[0] // 2, original.size[1] // 2
    box = (cx - 350, cy - 350, cx + 350, cy + 350)
    strip = Image.new("RGB", (1400, 700))
    strip.paste(original.crop(box), (0, 0))
    strip.paste(cleaned.crop(box), (700, 0))
    strip.save(out_dir / "texture_before_after.png")
    covered = mask.mean() * 100
    print(f"{folder}: {original.size[0]}x{original.size[1]}, UV islands cover {covered:.1f}% -> {out_dir}")


if __name__ == "__main__":
    for name in sys.argv[1:] or ["Lava rod", "Void rod"]:
        clean(name)
