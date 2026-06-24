#!/usr/bin/env python3
"""Generate the C-Bus integration icon (for home-assistant/brands).

Draws a rounded teal->blue badge with a white light bulb wired to a 3-node
"C-Bus" line, and writes the brands-required sizes. Requires Pillow.

    python3 brands/make_icon.py
"""

import os

from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(__file__), "custom_integrations", "cbus")
S = 512
SS = 4  # supersample for smooth edges
W = S * SS


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))

    # gradient background (teal -> blue)
    top, bot = (35, 173, 196), (37, 99, 200)
    grad = Image.new("RGB", (W, W))
    gd = ImageDraw.Draw(grad)
    for y in range(W):
        t = y / W
        gd.line(
            [(0, y), (W, y)],
            fill=tuple(int(top[i] * (1 - t) + bot[i] * t) for i in range(3)),
        )
    mask = Image.new("L", (W, W), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, W - 1, W - 1], radius=112 * SS, fill=255
    )
    img.paste(grad, (0, 0), mask)

    d = ImageDraw.Draw(img)
    white, soft = (255, 255, 255, 255), (255, 255, 255, 235)
    groove = (37, 120, 198, 255)

    # bulb glass + base
    cx, cy, rad = W // 2, int(W * 0.40), int(W * 0.20)
    d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=white)
    bw, by0 = int(rad * 0.62), cy + int(rad * 0.78)
    d.rounded_rectangle(
        [cx - bw, by0, cx + bw, by0 + int(rad * 0.55)], radius=18 * SS, fill=soft
    )
    for i in range(2):
        yy = by0 + int(rad * 0.16) + i * int(rad * 0.22)
        d.line([(cx - bw, yy), (cx + bw, yy)], fill=groove, width=6 * SS)

    # C-Bus network: 3 nodes, middle wired up to the bulb
    ny, nr = int(W * 0.80), int(W * 0.045)
    xs = [int(W * 0.30), int(W * 0.50), int(W * 0.70)]
    d.line([(xs[0], ny), (xs[2], ny)], fill=white, width=10 * SS)
    d.line([(xs[1], ny), (cx, by0 + int(rad * 0.55))], fill=white, width=10 * SS)
    for x in xs:
        d.ellipse([x - nr, ny - nr, x + nr, ny + nr], fill=white)
        d.ellipse([x - nr // 2, ny - nr // 2, x + nr // 2, ny + nr // 2], fill=groove)

    icon = img.resize((S, S), Image.LANCZOS)
    icon.save(os.path.join(OUT, "icon@2x.png"))
    icon.save(os.path.join(OUT, "logo.png"))
    icon.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "icon.png"))
    print("wrote icon.png, icon@2x.png, logo.png to", OUT)


if __name__ == "__main__":
    main()
