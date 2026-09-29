"""One contact sheet per clip from the visual review's screenshots.

Usage: python pipeline/review_sheets.py [CLIP ...]   (default: every clip in the manifest)
Reads clips/visual_review/manifest.json (web/scripts/visual_review.mjs) and
writes clips/visual_review/<clip>.png: one row per moment, the broadcast
shot on the left and the one behind the player on the right, each with its
label (time, expected action, camera) burnt in.
"""

import json
import sys
from pathlib import Path

from PIL import Image

OUT = Path("clips/visual_review")
TILE = (480, 270)


def sheet(name, entry):
    rows = sorted({s["i"] for s in entry["shots"]})
    img = Image.new("RGB", (TILE[0] * 2, TILE[1] * len(rows)), "black")
    for s in entry["shots"]:
        tile = Image.open(OUT / name / s["file"]).convert("RGB").resize(TILE, Image.LANCZOS)
        img.paste(tile, ((0 if s["cam"] == "broadcast" else 1) * TILE[0], rows.index(s["i"]) * TILE[1]))
    path = OUT / f"{name}.png"
    img.save(path, optimize=True)
    return path


def main():
    manifest = json.loads((OUT / "manifest.json").read_text())
    names = [a.removesuffix(".json") for a in sys.argv[1:]] or list(manifest)
    for name in names:
        print(sheet(name, manifest[name]))


if __name__ == "__main__":
    main()
