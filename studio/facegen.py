# -*- coding: utf-8 -*-
"""从一张正面全身图生成表情贴图。

原理：角色脸是平涂皮肤 + 黑色线稿，所以「盖掉原来的眼睛、用同样的黑线重画」
就能得到一套贴合原画风的表情，不需要重画角色。

用法：
    python facegen.py <body.png> <输出目录> [表情名...]
不给表情名就做全套。

输出：face-<id>.png，与原图同尺寸同位置，直接叠在身体上就能用。
"""
import os
import sys
import math
from PIL import Image, ImageDraw, ImageFilter

# 画线用的黑，与原图线稿同色（原图线稿是纯黑，不是深棕）
INK = (0, 0, 0, 255)
SS = 4  # 超采样倍数，画完缩回去，边缘才不毛

# 官方 kit 的表情名（web/kit/body.js 的 FACES），能对上的都做
ALL_FACES = [
    'happy', 'wink', 'love', 'shy', 'surprised', 'angry', 'sad', 'sleepy',
    'thinking', 'smug', 'pout', 'worried', 'determined', 'flustered',
    'scared', 'excited', 'cry', 'dizzy', 'confused', 'squeeze',
    'content', 'listening', 'dragged', 'waking', 'run',
]


def _near(p, c, tol):
    return p[3] > 200 and abs(p[0] - c[0]) <= tol and abs(p[1] - c[1]) <= tol and abs(p[2] - c[2]) <= tol


def find_face(im):
    """脸的范围：取头部区域里皮肤色像素的外框。"""
    w, h = im.size
    px = im.load()
    xs, ys = [], []
    for y in range(int(h * 0.10), int(h * 0.48)):
        for x in range(int(w * 0.25), int(w * 0.75)):
            if _near(px[x, y], (255, 243, 237), 9):
                xs.append(x)
                ys.append(y)
    if len(xs) < 200:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _blobs(im, box, pred, min_area=14):
    """box 内的连通块（4 邻域），返回 [{box, area, cx, cy}]，按面积降序。"""
    x0, y0, x1, y1 = box
    w, h = im.size
    px = im.load()
    seen = set()
    out = []
    for y in range(max(0, y0), min(h, y1)):
        for x in range(max(0, x0), min(w, x1)):
            if (x, y) in seen or not pred(px[x, y]):
                continue
            stack = [(x, y)]
            seen.add((x, y))
            pts = []
            while stack:
                cx, cy = stack.pop()
                pts.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < x0 or nx >= x1 or ny < y0 or ny >= y1 or (nx, ny) in seen:
                        continue
                    if pred(px[nx, ny]):
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            if len(pts) >= min_area:
                X = [p[0] for p in pts]
                Y = [p[1] for p in pts]
                out.append({'box': (min(X), min(Y), max(X), max(Y)), 'area': len(pts),
                            'cx': sum(X) / len(X), 'cy': sum(Y) / len(Y)})
    out.sort(key=lambda b: -b['area'])
    return out


def detect_features(im, face):
    """返回 (左眼, 右眼, 嘴)，每个是 (x0,y0,x1,y1)。认不出就返回 None。

    先看同目录有没有 face-layout.json（手标过坐标就用它，最稳）；
    否则按「虹膜是浅黄」来找眼睛——虹膜颜色比头发线稿更独特，不容易粘在一起。
    """
    fx0, fy0, fx1, fy1 = face
    fw, fh = fx1 - fx0, fy1 - fy0
    head = (fx0, fy0 + int(fh * 0.18), fx1, fy0 + int(fh * 0.72))
    iris = lambda p: p[3] > 200 and p[0] >= 245 and 190 <= p[1] <= 242 and 95 <= p[2] <= 198
    blobs = [b for b in _blobs(im, head, iris, 10) if b['area'] > 60]
    if len(blobs) < 2:
        return None
    # 两只眼睛的 y 应该差不多，取最像一对的两块
    blobs.sort(key=lambda b: -b['area'])
    blobs = sorted(blobs[:4], key=lambda b: b['cx'])
    best = None
    for i in range(len(blobs) - 1):
        a, b = blobs[i], blobs[i + 1]
        if abs(a['cy'] - b['cy']) > fh * 0.12:
            continue
        if not (fw * 0.15 < b['cx'] - a['cx'] < fw * 0.75):
            continue
        score = a['area'] + b['area']
        if not best or score > best[0]:
            best = (score, a['box'], b['box'])
    if not best:
        return None
    _, la, ra = best
    # 虹膜只是眼睛中间那块，眼睛框要比它大一圈
    def grow(b):
        x0, y0, x1, y1 = b
        gx, gy = (x1 - x0) * 0.30, (y1 - y0) * 0.26
        return (x0 - gx, y0 - gy, x1 + gx, y1 + gy)
    la, ra = grow(la), grow(ra)
    # 嘴巴在两眼之间偏下
    mb = (la[0], max(la[3], ra[3]), ra[2], fy0 + fh * 0.95)
    black = lambda p: p[3] > 180 and p[0] < 110 and p[1] < 110 and p[2] < 110
    mblobs = [b for b in _blobs(im, tuple(int(v) for v in mb), black, 4) if b['area'] >= 4]
    mouth = mblobs[0]['box'] if mblobs else None
    return la, ra, mouth


def load_layout(tex_dir):
    """手标的坐标表：{'eyes': [[x0,y0,x1,y1], [x0,y0,x1,y1]], 'mouth': [x0,y0,x1,y1]}"""
    import json
    p = os.path.join(tex_dir, 'face-layout.json')
    if not os.path.exists(p):
        return None
    try:
        d = json.load(open(p, encoding='utf-8'))
        e = d['eyes']
        return tuple(e[0]), tuple(e[1]), (tuple(d['mouth']) if d.get('mouth') else None)
    except Exception as err:
        print('face-layout.json 读不了，改用自动识别：', err)
        return None



def hair_mask(im, min_area=1200, above_y=None):
    """灰发的区域掩码。用连通块来认头发，而不是按颜色判断——
    眼睛边缘的抗锯齿像素也是灰的，按颜色判断会被当成头发留下来，闭眼后就有一圈残影。"""
    w, h = im.size
    px = im.load()
    grey = lambda p: p[3] > 180 and 150 <= p[0] <= 228 and abs(p[0] - p[1]) < 16 and abs(p[1] - p[2]) < 16
    seen = bytearray(w * h)
    mask = bytearray(w * h)
    for sy in range(h):
        for sx in range(w):
            i = sy * w + sx
            if seen[i] or not grey(px[sx, sy]):
                continue
            stack = [(sx, sy)]
            seen[i] = 1
            pts = []
            while stack:
                cx, cy = stack.pop()
                pts.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if nx < 0 or nx >= w or ny < 0 or ny >= h:
                        continue
                    j = ny * w + nx
                    if seen[j] or not grey(px[nx, ny]):
                        continue
                    seen[j] = 1
                    stack.append((nx, ny))
            # 大块算头发；细发丝只要往上连着刘海（越过 above_y）也算，
            # 这样压在眼睛上的发丝不会被抹掉，而眼睛自己那圈抗锯齿边不会误判。
            top = min(p[1] for p in pts)
            if len(pts) >= min_area or (above_y is not None and top < above_y - 1):
                for cx, cy in pts:
                    mask[cy * w + cx] = 1
    return mask


def erase(im, box, mask=None, pad=3):
    """把 box 里的眼睛抹成皮肤色。mask 里是灰发的地方跳过，免得刘海被抠出洞。"""
    x0, y0, x1, y1 = box
    w, h = im.size
    px = im.load()
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    rx, ry = (x1 - x0) / 2.0 + pad, (y1 - y0) / 2.0 + pad
    for y in range(max(0, int(cy - ry - 1)), min(h, int(cy + ry + 2))):
        for x in range(max(0, int(cx - rx - 1)), min(w, int(cx + rx + 2))):
            if ((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2 > 1.0:
                continue
            if px[x, y][3] < 40:
                continue
            if mask is not None and mask[y * w + x]:
                continue
            px[x, y] = (255, 243, 237, 255)


# ---------- 画笔 ----------

def _w(px):
    """PIL 的线宽只能是整数。"""
    return max(1, int(round(px)))


def _curve(d, pts, width, fill=INK):
    width = _w(width)
    d.line(pts, fill=fill, width=width, joint='curve')
    r = width / 2.0
    for p in (pts[0], pts[-1]):
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=fill)


def arc(d, cx, cy, half, lift, width, down=False):
    """一条眼皮弧线。down=True 是向下弯（困、懒），否则向上弯（笑）。"""
    pts = []
    for i in range(21):
        k = i / 20.0
        x = cx - half + 2 * half * k
        y = cy + (lift if down else -lift) * math.sin(math.pi * k)
        pts.append((x, y))
    _curve(d, pts, width)


def flat(d, cx, cy, half, width):
    """放松的闭眼：一点点往下弯，不是一条直杠。"""
    pts = []
    for i in range(15):
        k = i / 14.0
        pts.append((cx - half + 2 * half * k, cy + half * 0.18 * math.sin(math.pi * k)))
    _curve(d, pts, width)


def tight(d, cx, cy, half, width, flip=False):
    """紧闭：> < 那种尖角。"""
    s = -1 if flip else 1
    _curve(d, [(cx - half * s, cy - width * 1.1), (cx + half * s, cy), (cx - half * s, cy + width * 1.1)], width)


def heart(d, cx, cy, r, width):
    pts = []
    for i in range(41):
        t = 2 * math.pi * i / 40
        x = 16 * math.sin(t) ** 3
        y = -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))
        pts.append((cx + x * r / 16.0, cy + y * r / 16.0))
    d.line(pts, fill=INK, width=_w(width), joint='curve')


def spiral(d, cx, cy, r, width):
    pts = []
    for i in range(46):
        t = i / 45.0
        a = t * math.pi * 3.4
        rr = r * (0.25 + 0.75 * t)
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a) * 0.8))
    d.line(pts, fill=INK, width=_w(width), joint='curve')


def pupil(d, cx, cy, rx, ry, width):
    """睁眼：椭圆眼白 + 浅黄瞳孔 + 高光，照原画的层次来。"""
    width = _w(width)
    d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=(255, 250, 244, 255), outline=INK, width=width)
    d.ellipse([cx - rx * .52, cy - ry * .58, cx + rx * .52, cy + ry * .58], fill=(255, 224, 158, 255))
    d.ellipse([cx - rx * .38, cy - ry * .42, cx + rx * .28, cy + ry * .2], fill=(255, 208, 110, 255))
    d.ellipse([cx - rx * .50, cy - ry * .54, cx - rx * .18, cy - ry * .24], fill=(255, 255, 255, 255))


def blush(im, face, k=1.0):
    """腮红：在脸两侧加一层软色，和原画的脸颊红同色。"""
    fx0, fy0, fx1, fy1 = face
    fw, fh = fx1 - fx0, fy1 - fy0
    lay = Image.new('RGBA', im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    col = (255, 170, 165, int(90 * k))
    for cx in (fx0 + fw * .12, fx1 - fw * .12):
        d.ellipse([cx - fw * .10, fy0 + fh * .60, cx + fw * .10, fy0 + fh * .78], fill=col)
    lay = lay.filter(ImageFilter.GaussianBlur(max(1.5, fw * .018)))
    im.alpha_composite(lay)


def marks(im, face, kind):
    """眼外的记号：汗、泪、怒气、爱心、星光。"""
    fx0, fy0, fx1, fy1 = face
    fw, fh = fx1 - fx0, fy1 - fy0
    lay = Image.new('RGBA', im.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    if kind == 'sweat':
        cx, cy = fx1 - fw * .02, fy0 + fh * .40
        s = fw * .045
        d.polygon([(cx, cy - s * 1.6), (cx + s, cy + s * .3), (cx, cy + s * .9), (cx - s, cy + s * .3)],
                  fill=(140, 200, 245, 235))
    elif kind == 'tear':
        for cx in (fx0 + fw * .30, fx1 - fw * .30):
            cy = fy0 + fh * .70
            s = fw * .035
            d.polygon([(cx, cy), (cx + s, cy + s * 1.7), (cx, cy + s * 2.4), (cx - s, cy + s * 1.7)],
                      fill=(150, 205, 245, 240))
    elif kind == 'vein':
        cx, cy = fx1 - fw * .12, fy0 + fh * .18
        s = fw * .035
        for a in (0, math.pi / 2):
            d.line([(cx - s * math.cos(a), cy - s * math.sin(a)), (cx + s * math.cos(a), cy + s * math.sin(a))],
                   fill=(230, 90, 90, 255), width=int(max(2, s * .55)))
    elif kind == 'drops':
        for i, (dx, dy) in enumerate(((.24, .80), (.34, .86), (.76, .82), (.66, .88))):
            cx, cy = fx0 + fw * dx, fy0 + fh * dy
            s = fw * .030
            d.polygon([(cx, cy), (cx + s, cy + s * 1.6), (cx, cy + s * 2.2), (cx - s, cy + s * 1.6)],
                      fill=(150, 205, 245, 240))
    im.alpha_composite(lay)


# ---------- 各个表情 ----------

def build(body_path, out_dir, only=None):
    src = Image.open(body_path).convert('RGBA')
    face = find_face(src)
    if not face:
        raise SystemExit('找不到脸（皮肤色像素太少），换个正面图试试')
    feat = load_layout(os.path.dirname(os.path.abspath(body_path)))
    if not feat:
        feat = detect_features(src, face)
    if not feat:
        raise SystemExit('找不到眼睛，可能是侧脸或分辨率太低；可以在同目录放 face-layout.json 手标坐标')
    lbox, rbox, mbox = feat
    HAT = hair_mask(src, above_y=min(lbox[1], rbox[1]))   # 灰发掩码，抹眼睛时绕开
    fw = face[2] - face[0]
    W = max(2.6, fw * 0.043)          # 线宽跟着脸的大小走
    lcx, lcy = (lbox[0] + lbox[2]) / 2, (lbox[1] + lbox[3]) / 2
    rcx, rcy = (rbox[0] + rbox[2]) / 2, (rbox[1] + rbox[3]) / 2
    ehalf = (lbox[2] - lbox[0]) / 2
    eyes = [(lcx, lcy, ehalf), (rcx, rcy, ehalf)]
    mcx, mcy = ((mbox[0] + mbox[2]) / 2, (mbox[1] + mbox[3]) / 2) if mbox else (face[0] + fw / 2, face[3] - (face[3] - face[1]) * .26)

    def mouth(d, style):
        ry = W * 1.15
        if style == 'smile':
            arc(d, mcx, mcy - ry * .5, W * 1.7, W * .95, W, down=True)
        elif style == 'o':
            d.ellipse([mcx - W, mcy - W, mcx + W, mcy + W], outline=INK, width=_w(W))
        elif style == 'flat':
            flat(d, mcx, mcy, W * 1.5, W)
        elif style == 'w':
            pts = [(mcx - W * 1.8, mcy), (mcx - W * .9, mcy - W * .8), (mcx, mcy),
                   (mcx + W * .9, mcy - W * .8), (mcx + W * 1.8, mcy)]
            _curve(d, pts, W)
        elif style == 'open':
            d.ellipse([mcx - W * 1.35, mcy - W * .5, mcx + W * 1.35, mcy + W * 1.5], fill=(150, 72, 78, 255),
                      outline=INK, width=_w(W))
        elif style == 'puff':
            d.ellipse([mcx - W * 1.2, mcy - W * .8, mcx + W * 1.2, mcy + W * .8], outline=INK, width=_w(W))
        elif style == 'small':
            d.ellipse([mcx - W * .5, mcy - W * .3, mcx + W * .5, mcy + W * .7], fill=INK)

    def render(eye_fn=None, mouth_style='small', erase_all=True, marks_kind=None, blush_k=0.0):
        # 先原尺寸抹掉原眼和原嘴（灰发掩码在这里算一次就够，放大后再算太慢）
        base = src.copy()
        if erase_all and eye_fn is not None:
            for b in (lbox, rbox):
                erase(base, b, HAT, pad=5)
            # 嘴也要抹：新嘴可能和原来的位置形状不一样，不抹会露出两层
            if mbox:
                erase(base, mbox, HAT, pad=3)
        im = base.resize((src.width * SS, src.height * SS), Image.LANCZOS)
        big = [(c * SS, y * SS, h * SS) for (c, y, h) in eyes]
        d = ImageDraw.Draw(im)
        if eye_fn:
            for (cx, cy, h), flip in zip(big, (False, True)):
                eye_fn(d, cx, cy, h, W * SS, flip)
        mouth(d, mouth_style)
        im = im.resize(src.size, Image.LANCZOS)
        if blush_k:
            blush(im, face, blush_k)
        if marks_kind:
            marks(im, face, marks_kind)
        return im

    def happy_eyes(d, cx, cy, h, w, flip):
        arc(d, cx, cy + h * .12, h * .82, h * .62, w * 1.25)

    def flat_eyes(d, cx, cy, h, w, flip):
        flat(d, cx, cy, h * .80, w * 1.25)

    def tight_eyes(d, cx, cy, h, w, flip):
        tight(d, cx, cy, h * .78, w * 1.2, flip)

    def heart_eyes(d, cx, cy, h, w, flip):
        heart(d, cx, cy, h * .92, w * .95)

    def dizzy_eyes(d, cx, cy, h, w, flip):
        spiral(d, cx, cy, h * .92, w * .80)

    def open_eyes(d, cx, cy, h, w, flip):
        pupil(d, cx, cy, h * .80, h * .90, w)

    def wide_eyes(d, cx, cy, h, w, flip):
        pupil(d, cx, cy, h * .86, h * .96, w)

    def half_eyes(d, cx, cy, h, w, flip):
        # 半睁：先睁着，再压一条眼皮线
        pupil(d, cx, cy, h * .80, h * .90, w)
        arc(d, cx, cy - h * .26, h * .86, h * .14, w)

    plans = {
        'happy':    dict(eye=happy_eyes, mouth='smile', blush=0.5),
        'wink':     dict(eye=None, mouth='smile', blush=0.4, wink=True),
        'love':     dict(eye=heart_eyes, mouth='smile', blush=0.9),
        'shy':      dict(eye=half_eyes, mouth='w', blush=1.0),
        'surprised': dict(eye=wide_eyes, mouth='o'),
        'angry':    dict(eye=tight_eyes, mouth='open', blush=0.35, mark='vein'),
        'sad':      dict(eye=half_eyes, mouth='flat', mark='tear'),
        'sleepy':   dict(eye=flat_eyes, mouth='small'),
        'thinking': dict(eye=half_eyes, mouth='small'),
        'smug':     dict(eye=flat_eyes, mouth='flat', blush=0.3),
        'pout':     dict(eye=None, mouth='puff', blush=0.8),
        'worried':  dict(eye=None, mouth='w', mark='sweat'),
        'determined': dict(eye=open_eyes, mouth='flat', mark='vein'),
        'flustered': dict(eye=tight_eyes, mouth='w', blush=1.0, mark='sweat'),
        'scared':   dict(eye=wide_eyes, mouth='open', mark='sweat'),
        'excited':  dict(eye=open_eyes, mouth='open', blush=0.5),
        'cry':      dict(eye=tight_eyes, mouth='open', mark='drops'),
        'dizzy':    dict(eye=dizzy_eyes, mouth='w'),
        'confused': dict(eye=half_eyes, mouth='w', mark='sweat'),
        'squeeze':  dict(eye=tight_eyes, mouth='small'),
        'content':  dict(eye=flat_eyes, mouth='smile', blush=0.6),
        'listening': dict(eye=open_eyes, mouth='small'),
        'dragged':  dict(eye=tight_eyes, mouth='open', mark='sweat'),
        'waking':   dict(eye=flat_eyes, mouth='small', mark='sweat'),
        'run':      dict(eye=open_eyes, mouth='open', mark='sweat'),
    }
    want = [f for f in (only or ALL_FACES) if f in plans]
    os.makedirs(out_dir, exist_ok=True)
    made = []
    for name in want:
        p = plans[name]
        if p.get('wink'):
            # 眨眼：只闭左眼，右眼保持原样
            base = src.copy()
            erase(base, lbox, HAT, pad=5)
            im = base.resize((src.width * SS, src.height * SS), Image.LANCZOS)
            d = ImageDraw.Draw(im)
            happy_eyes(d, lcx * SS, lcy * SS, ehalf * SS, W * SS, False)
            mouth(d, p['mouth'])
            im = im.resize(src.size, Image.LANCZOS)
            blush(im, face, p.get('blush', 0))
            out = im
        else:
            out = render(eye_fn=p['eye'], mouth_style=p['mouth'], erase_all=True,
                         marks_kind=p.get('mark'), blush_k=p.get('blush', 0))
        path = os.path.join(out_dir, 'face-%s.png' % name)
        out.save(path, optimize=True)
        made.append(name)
    return made, face, feat


def preview(tex_dir, out_png, names=None, cols=5):
    """把生成的表情拼成一张对照图，方便一眼看画风对不对。"""
    names = names or ALL_FACES
    files = [os.path.join(tex_dir, 'face-%s.png' % n) for n in names]
    files = [f for f in files if os.path.exists(f)]
    if not files:
        return None
    ims = [Image.open(f).convert('RGBA') for f in files]
    # 只截脸上半部，省地方
    w, h = ims[0].size
    crop = (int(w * .28), int(h * .10), int(w * .72), int(h * .48))
    tw = 200
    th = int(tw * (crop[3] - crop[1]) / (crop[2] - crop[0]))
    rows = (len(ims) + cols - 1) // cols
    sheet = Image.new('RGBA', (cols * tw, rows * (th + 4)), (40, 40, 46, 255))
    for i, im in enumerate(ims):
        sheet.alpha_composite(im.crop(crop).resize((tw, th), Image.LANCZOS), ((i % cols) * tw, (i // cols) * (th + 4)))
    sheet.convert('RGB').save(out_png)
    return out_png


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    made, face, feat = build(sys.argv[1], sys.argv[2], sys.argv[3:] or None)
    print('脸', face, '眼', feat[0], feat[1], '嘴', feat[2])
    print('生成 %d 个表情：%s' % (len(made), ' '.join(made)))
