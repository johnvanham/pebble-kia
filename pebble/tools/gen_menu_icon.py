# Regenerate: uv run --with cairosvg --with pillow python tools/gen_menu_icon.py
import io
import pathlib

import cairosvg
from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).parent
OUT = HERE.parent / "resources" / "images" / "menu_icon.png"

SIZE = 25    # SDK-3 launcher icon size
SCALE = 16   # render big, downscale with Lanczos for clean antialiasing
RADIUS = 5
MARK_FRACTION = 0.8

hi = SIZE * SCALE
badge = Image.new("RGBA", (hi, hi), (0, 0, 0, 0))
ImageDraw.Draw(badge).rounded_rectangle(
    [0, 0, hi - 1, hi - 1], radius=RADIUS * SCALE, fill=(0, 0, 0, 255))

# Wikimedia Commons KIA_logo3.svg (PD-textlogo), refilled white for the badge.
svg = (HERE / "kia_logo.svg").read_text().replace("#131E29", "#FFFFFF")
mark_w = round(hi * MARK_FRACTION)
png = cairosvg.svg2png(bytestring=svg.encode(), output_width=mark_w)
mark = Image.open(io.BytesIO(png)).convert("RGBA")
badge.alpha_composite(mark, ((hi - mark.width) // 2, (hi - mark.height) // 2))

badge.resize((SIZE, SIZE), Image.LANCZOS).save(OUT)
print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")
