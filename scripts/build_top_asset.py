"""Europcar-Dachansicht: invertieren, breit skalieren, kompaktes Canvas."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "public" / "assets" / "europcar" / "img-042.png"
OUT = ROOT / "public" / "assets" / "vehicles" / "top.png"
META = ROOT / "public" / "assets" / "vehicles" / "top.meta.txt"

CANVAS_W = 720
PAD_X = 16
PAD_Y = 20


def build_top_asset() -> None:
    src = Image.open(SRC).convert("L")
    line_mask = src.point(lambda p: 255 if p > 90 else 0)
    line_art = ImageOps.invert(line_mask)

    bbox = line_art.getbbox()
    if not bbox:
        raise RuntimeError("Keine Linien in Europcar-Dachgrafik gefunden")

    pad = 8
    x0, y0, x1, y1 = bbox
    cropped = line_art.crop(
        (
            max(0, x0 - pad),
            max(0, y0 - pad),
            min(line_art.width, x1 + pad),
            min(line_art.height, y1 + pad),
        )
    )

    cw, ch = cropped.size
    # Breite ausfuellen, Hoehe proportional – nichts abschneiden
    scale = (CANVAS_W - 2 * PAD_X) / cw
    nw, nh = max(1, int(cw * scale)), max(1, int(ch * scale))
    scaled = cropped.resize((nw, nh), Image.Resampling.LANCZOS)

    canvas_h = nh + 2 * PAD_Y
    canvas = Image.new("RGB", (CANVAS_W, canvas_h), "white")
    ox = (CANVAS_W - nw) // 2
    canvas.paste(scaled, (ox, PAD_Y))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(OUT, format="PNG")
    META.write_text(f"{CANVAS_W},{canvas_h}\n")
    print(f"Wrote {OUT} ({CANVAS_W}x{canvas_h}), vehicle {nw}x{nh}")


if __name__ == "__main__":
    build_top_asset()
