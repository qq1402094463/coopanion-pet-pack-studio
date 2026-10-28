"""Generate placeholder art for the minimal figure pack. Pure stdlib, no deps.

Draws a round little creature so the template pack has something visible
before you replace tex/body.png with your own artwork.
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'templates', 'minimal-pack')

INK = (43, 58, 71)
BODY = (126, 200, 232)
BELLY = (232, 247, 255)
EYE = (35, 48, 59)
WHITE = (255, 255, 255)
BLUSH = (255, 155, 179)


def write_png(path, w, h, rows):
    raw = b''.join(b'\x00' + bytes(r) for r in rows)

    def chunk(tag, data):
        body = tag + data
        return struct.pack('>I', len(data)) + body + struct.pack('>I', zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n')
        f.write(chunk(b'IHDR', ihdr))
        f.write(chunk(b'IDAT', zlib.compress(raw, 9)))
        f.write(chunk(b'IEND', b''))


def ell(x, y, cx, cy, rx, ry):
    dx = (x - cx) / rx
    dy = (y - cy) / ry
    return dx * dx + dy * dy <= 1.0


def seg(x, y, x1, y1, x2, y2, w):
    vx, vy = x2 - x1, y2 - y1
    wx, wy = x - x1, y - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, (wx * vx + wy * vy) / L2))
    px, py = x1 + t * vx, y1 + t * vy
    return (x - px) ** 2 + (y - py) ** 2 <= w * w


def heart(x, y, cx, cy, r):
    # two lobes + a diamond, reads as a heart at this size
    if ell(x, y, cx - r * 0.45, cy - r * 0.30, r * 0.52, r * 0.52):
        return True
    if ell(x, y, cx + r * 0.45, cy - r * 0.30, r * 0.52, r * 0.52):
        return True
    dx, dy = abs(x - cx) / (r * 1.15), (y - cy) / (r * 1.15)
    return dx + dy <= 0.95


def sample(x, y, S, expr):
    """Colour at a point, in normalised 0..1 space."""
    c = None

    def put(col):
        nonlocal c
        c = col

    edge = 2.4 / S  # outline thickness in normalised units

    # feet
    for fx in (0.40, 0.60):
        if ell(x, y, fx, 0.895, 0.075, 0.050):
            put(INK)
        if ell(x, y, fx, 0.895, 0.075 - edge, 0.050 - edge):
            put(BODY)

    # body outline + fill
    if ell(x, y, 0.5, 0.60, 0.345, 0.305):
        put(INK)
    if ell(x, y, 0.5, 0.60, 0.345 - edge, 0.305 - edge):
        put(BODY)

    # belly
    if ell(x, y, 0.5, 0.665, 0.215, 0.185):
        put(BELLY)

    # ears
    for ex in (0.335, 0.665):
        if ell(x, y, ex, 0.355, 0.058, 0.070):
            put(INK)
        if ell(x, y, ex, 0.355, 0.058 - edge, 0.070 - edge):
            put(BODY)

    # eyes
    lx, rx_, ey = 0.395, 0.605, 0.545
    if expr == 'happy':
        for cx in (lx, rx_):
            if seg(x, y, cx - 0.042, ey + 0.018, cx, ey - 0.020, 0.0115):
                put(EYE)
            if seg(x, y, cx, ey - 0.020, cx + 0.042, ey + 0.018, 0.0115):
                put(EYE)
    elif expr == 'love':
        for cx in (lx, rx_):
            if heart(x, y, cx, ey, 0.055):
                put((226, 74, 105))
    elif expr == 'sleepy':
        for cx in (lx, rx_):
            if seg(x, y, cx - 0.040, ey, cx + 0.040, ey, 0.011):
                put(EYE)
    else:
        for cx in (lx, rx_):
            if ell(x, y, cx, ey, 0.048, 0.052):
                put(EYE)
            if ell(x, y, cx + 0.016, ey - 0.018, 0.016, 0.016):
                put(WHITE)

    # blush
    for cx in (0.295, 0.705):
        if ell(x, y, cx, 0.645, 0.052, 0.028):
            put(BLUSH)

    # mouth
    if expr == 'happy':
        if ell(x, y, 0.5, 0.635, 0.030, 0.024):
            put(EYE)
    elif expr == 'sleepy':
        if ell(x, y, 0.5, 0.640, 0.020, 0.014):
            put(EYE)
    else:
        if seg(x, y, 0.470, 0.628, 0.500, 0.643, 0.0075) or seg(x, y, 0.500, 0.643, 0.530, 0.628, 0.0075):
            put(EYE)

    return c


def render(size, expr='neutral'):
    S = 3  # supersampling
    rows = []
    for py in range(size):
        row = bytearray()
        for px in range(size):
            r = g = b = a = 0
            for sy in range(S):
                for sx in range(S):
                    x = (px + (sx + 0.5) / S) / size
                    y = (py + (sy + 0.5) / S) / size
                    col = sample(x, y, size, expr)
                    if col:
                        r += col[0]
                        g += col[1]
                        b += col[2]
                        a += 255
            n = S * S
            if a:
                k = a / 255.0
                row += bytes((int(r / k), int(g / k), int(b / k), int(a / n)))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(row)
    return rows


def main():
    tex = os.path.join(OUT, 'tex')
    os.makedirs(tex, exist_ok=True)

    write_png(os.path.join(tex, 'body.png'), 512, 512, render(512, 'neutral'))
    print('tex/body.png')

    for expr in ('happy', 'love', 'sleepy'):
        write_png(os.path.join(tex, f'face-{expr}.png'), 512, 512, render(512, expr))
        print(f'tex/face-{expr}.png')

    write_png(os.path.join(OUT, 'thumb.png'), 256, 256, render(256, 'happy'))
    print('thumb.png')


if __name__ == '__main__':
    main()
