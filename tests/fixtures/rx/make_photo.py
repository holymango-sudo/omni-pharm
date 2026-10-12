"""Turn a clean rendered document into a 'phone photo': tilt, perspective, uneven light, blur, JPEG.

    python tests/fixtures/rx/make_photo.py prescription.png prescription_photo.jpg
"""

import sys

from PIL import Image, ImageDraw, ImageFilter


def phone_photo(src: str, dst: str, angle: float = -3.5, tilt: float = 0.04, shade: float = 0.6, blur: float = 0.9,
                quality: int = 72) -> tuple[int, int]:
    im = Image.open(src).convert("RGB")
    w, h = im.size
    # a desk-coloured canvas a bit larger than the paper, paper rotated a few degrees
    canvas = Image.new("RGB", (int(w * 1.15), int(h * 1.15)), (176, 160, 140))
    paper = im.rotate(angle, expand=True, fillcolor=(176, 160, 140), resample=Image.BICUBIC)
    canvas.paste(paper, ((canvas.width - paper.width) // 2, (canvas.height - paper.height) // 2))
    # mild perspective (top edge narrower, as when the phone is tilted)
    cw, ch = canvas.size
    d = int(cw * tilt)
    canvas = canvas.transform((cw, ch), Image.QUAD, (d, 0, 0, ch, cw, ch, cw - d, 0), resample=Image.BICUBIC)
    # uneven light: darker towards the bottom-right corner
    mask = Image.new("L", (cw, ch), 0)
    dr = ImageDraw.Draw(mask)
    for i in range(0, 120, 4):
        dr.ellipse((cw * 0.55 - i * 6, ch * 0.55 - i * 6, cw * 1.6 + i * 6, ch * 1.6 + i * 6), fill=i)
    canvas = Image.composite(Image.new("RGB", (cw, ch), (60, 55, 50)), canvas, mask.point(lambda v: int(v * shade)))
    canvas = canvas.filter(ImageFilter.GaussianBlur(blur))
    canvas.save(dst, "JPEG", quality=quality)
    return canvas.size


if __name__ == "__main__":
    print(sys.argv[2], phone_photo(sys.argv[1], sys.argv[2]))
