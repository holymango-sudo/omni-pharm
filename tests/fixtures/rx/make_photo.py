"""Turn a clean rendered document into a 'phone photo': tilt, perspective, uneven light, blur, JPEG.

    python tests/fixtures/rx/make_photo.py prescription.png prescription_photo.jpg
"""

import sys

from PIL import Image, ImageDraw, ImageFilter

src, dst = sys.argv[1], sys.argv[2]
im = Image.open(src).convert("RGB")
w, h = im.size
# a desk-coloured canvas a bit larger than the paper, paper rotated a few degrees
canvas = Image.new("RGB", (int(w * 1.15), int(h * 1.15)), (176, 160, 140))
paper = im.rotate(-3.5, expand=True, fillcolor=(176, 160, 140), resample=Image.BICUBIC)
canvas.paste(paper, ((canvas.width - paper.width) // 2, (canvas.height - paper.height) // 2))
# mild perspective (top edge narrower, as when the phone is tilted)
cw, ch = canvas.size
d = int(cw * 0.04)
coeffs = Image.Image.transform  # keep linters quiet about the import style
canvas = canvas.transform((cw, ch), Image.QUAD, (d, 0, 0, ch, cw, ch, cw - d, 0), resample=Image.BICUBIC)
# uneven light: darker towards the bottom-right corner
shade = Image.new("L", (cw, ch), 0)
dr = ImageDraw.Draw(shade)
for i in range(0, 120, 4):
    dr.ellipse((cw * 0.55 - i * 6, ch * 0.55 - i * 6, cw * 1.6 + i * 6, ch * 1.6 + i * 6), fill=i)
canvas = Image.composite(Image.new("RGB", (cw, ch), (60, 55, 50)), canvas, shade.point(lambda v: int(v * 0.6)))
canvas = canvas.filter(ImageFilter.GaussianBlur(0.9))
canvas.save(dst, "JPEG", quality=72)
print(dst, canvas.size)
