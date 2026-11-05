from pathlib import Path
from PIL import Image, ImageDraw
import math

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'app' / 'web'
S = 1024
img = Image.new('RGBA', (S, S), (0, 0, 0, 0))
px = img.load()
stops = [(0.0, (58, 123, 240)), (0.38, (35, 169, 162)), (0.72, (245, 160, 74)), (1.0, (58, 123, 240))]
for y in range(S):
    for x in range(S):
        dx, dy = x - S / 2, y - S / 2
        if dx * dx + dy * dy > (S * 0.49) ** 2:
            continue
        a = (math.atan2(dy, dx) / (2 * math.pi) + 1.0) % 1.0
        for i in range(len(stops) - 1):
            p0, c0 = stops[i]
            p1, c1 = stops[i + 1]
            if p0 <= a <= p1:
                t = (a - p0) / (p1 - p0)
                r = int(c0[0] + (c1[0] - c0[0]) * t)
                g = int(c0[1] + (c1[1] - c0[1]) * t)
                b = int(c0[2] + (c1[2] - c0[2]) * t)
                px[x, y] = (r, g, b, 255)
                break

# subtle white highlight and rings
d = ImageDraw.Draw(img)

def sc(v):
    return int(round(v * S / 64))

def ring(cx, cy, r, color, width):
    d.ellipse([sc(cx-r), sc(cy-r), sc(cx+r), sc(cy+r)], outline=color, width=max(1, sc(width)))

ring(32, 32, 20, (255, 255, 255, 175), 1.4)
ring(32, 32, 12, (255, 255, 255, 135), 1.15)
# center and satellite
d.ellipse([sc(32-4.6), sc(32-4.6), sc(32+4.6), sc(32+4.6)], fill=(255, 249, 239, 255))
d.ellipse([sc(43-2.2), sc(21-2.2), sc(43+2.2), sc(21+2.2)], fill=(255, 214, 120, 255))
# soft outer edge
ring(32, 32, 24.2, (255, 255, 255, 80), 0.8)

icon256 = img.resize((256, 256), Image.LANCZOS)
icon256.save(OUT / 'safelens.png')
icon256.save(OUT / 'safelens.ico', sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print(OUT / 'safelens.ico')
