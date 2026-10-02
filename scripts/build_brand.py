"""Rebuild the original project icon. Development dependency: Pillow.

This is a generic wallet with opposite-direction arrows, not a BSV or HA logo.
"""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
image = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
draw = ImageDraw.Draw(image)
draw.rounded_rectangle((72, 160, 952, 864), radius=144, fill="#123F42")
draw.rounded_rectangle((120, 208, 904, 300), radius=40, fill="#226267")
draw.rounded_rectangle((720, 390, 988, 632), radius=48, fill="#E9AD41")
draw.ellipse((790, 468, 872, 550), fill="#123F42")
draw.line([(236, 440), (612, 440)], fill="#FFFFFF", width=44)
draw.line([(522, 350), (612, 440), (522, 530)], fill="#FFFFFF", width=44, joint="curve")
draw.line([(236, 654), (612, 654)], fill="#E9AD41", width=44)
draw.line([(326, 564), (236, 654), (326, 744)], fill="#E9AD41", width=44, joint="curve")
for folder in (ROOT / "brand", ROOT / "custom_components/bsv_settlement/brand"):
    folder.mkdir(parents=True, exist_ok=True)
    image.resize((256, 256), Image.Resampling.LANCZOS).save(folder / "icon.png")
