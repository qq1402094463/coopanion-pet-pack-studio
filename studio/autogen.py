"""从一张全身图，自动补齐形象包缺的素材。

三件事：
  1. 走路帧  —— 把身体切成上/下两段，按正弦相位错位，得到 6 帧循环
  2. 坐姿/睡姿 —— 压缩下半身，做出"蹲下去"和"趴着"的近似图
  3. 20 个音效 —— 纯代码合成 wav，不联网，不需要任何模型

它只补缺失的东西，已有的图一律不覆盖。
生成的走路和坐睡是"程序近似"，观感不如手绘，随时可以用真图替换。
"""

import json
import math
import os
import random
import struct
import wave

try:
    from PIL import Image
    HAS_PIL = True
except Exception:  # noqa: BLE001
    HAS_PIL = False

SR = 22050

# ---------------------------------------------------------------- 音效


def _fade(buf, ms_in=4, ms_out=12):
    """两头做淡入淡出，不然播放时会有"啪"的爆音。"""
    a = max(1, int(SR * ms_in / 1000))
    b = max(1, int(SR * ms_out / 1000))
    n = len(buf)
    for i in range(min(a, n)):
        buf[i] *= i / a
    for i in range(min(b, n)):
        buf[n - 1 - i] *= i / b
    return buf


def _write_wav(path, buf):
    peak = max(1e-9, max(abs(x) for x in buf))
    k = 0.88 / peak
    frames = bytearray()
    for x in buf:
        v = int(max(-1.0, min(1.0, x * k)) * 32767)
        frames += struct.pack('<h', v)
    with wave.open(path, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))


def _tone(f0, f1, dur, harm=0.0, shape='sine'):
    """扫频音。harm 加一点二次谐波，听起来不那么电子。"""
    n = int(SR * dur)
    out = []
    ph = 0.0
    for i in range(n):
        k = i / max(1, n - 1)
        f = f0 + (f1 - f0) * k
        ph += 2 * math.pi * f / SR
        if shape == 'square':
            v = 0.6 * (1 if math.sin(ph) >= 0 else -1)
        elif shape == 'tri':
            v = 2 / math.pi * math.asin(math.sin(ph))
        else:
            v = math.sin(ph)
        if harm:
            v += harm * math.sin(2 * ph)
        out.append(v * (1 - k) ** 1.4)
    return _fade(out)


def _noise(dur, decay=18.0, lowpass=0.35):
    n = int(SR * dur)
    out = []
    prev = 0.0
    for i in range(n):
        x = random.uniform(-1, 1)
        prev = prev + lowpass * (x - prev)  # 一阶低通，去掉刺耳的沙沙
        out.append(prev * math.exp(-decay * i / n))
    return _fade(out, 1, 20)


def _mix(*parts):
    n = max(len(p) for p in parts)
    out = [0.0] * n
    for p in parts:
        for i, v in enumerate(p):
            out[i] += v
    peak = max(abs(v) for v in out) or 1.0
    return [v / peak * 0.9 for v in out]


def _seq(*parts):
    out = []
    for p in parts:
        out.extend(p)
    return out


def _scale(buf, k):
    return [v * k for v in buf]


def _hit(freq, dur, noise=0.35, decay=22.0):
    n = int(SR * dur)
    body = _tone(freq, freq * 0.55, dur, 0.18)
    puff = _scale(_noise(dur, decay, 0.5)[:n], noise)
    return _mix(body, puff)


def _pad(buf, dur):
    n = int(SR * dur)
    return buf + [0.0] * max(0, n - len(buf))


SOUNDS = [
    # name, 生成函数, 时长, kind, 音量
    ('step', lambda: _hit(180, 0.09, 0.5, 26), 'move', 0.45),
    ('jump', lambda: _seq(_tone(300, 760, 0.14, 0.1), _noise(0.05, 12)), 'move', 0.45),
    ('land', lambda: _mix(_hit(110, 0.13, 0.7, 20), _noise(0.06, 14)), 'move', 0.5),
    ('grab', lambda: _seq(_tone(520, 900, 0.07, 0.12), _tone(880, 640, 0.06)), 'touch', 0.4),
    ('poke', lambda: _seq(_tone(760, 1180, 0.055, 0.15), _pad(_noise(0.03, 30), 0.02)), 'touch', 0.42),
    ('purr', lambda: [math.sin(2 * math.pi * 62 * i / SR) * (0.5 + 0.5 * math.sin(2 * math.pi * 22 * i / SR)) * (1 - i / (SR * 0.85)) for i in range(int(SR * 0.85))], 'touch', 0.3),
    ('squeak', lambda: _seq(_tone(1250, 1750, 0.06, 0.2), _tone(1600, 1050, 0.07, 0.2)), 'touch', 0.38),
    ('snore', lambda: [math.sin(2 * math.pi * (78 + 16 * math.sin(2 * math.pi * 0.7 * i / SR)) * i / SR) * (0.55 + 0.45 * math.sin(2 * math.pi * 1.1 * i / SR)) * math.exp(-1.2 * (i / (SR * 1.25))) for i in range(int(SR * 1.25))], 'snore', 0.32),
    ('chirps', lambda: _seq(_tone(1500, 2100, 0.05, 0.15), _pad([], 0.02), _tone(1700, 2300, 0.05, 0.15), _pad([], 0.02), _tone(1900, 2500, 0.05, 0.15)), 'move', 0.32),
    ('dance', lambda: _seq(_hit(210, 0.08, 0.4, 24), _tone(320, 480, 0.07, 0.1), _hit(190, 0.09, 0.45, 24)), 'move', 0.42),
    ('nod', lambda: _seq(_tone(430, 300, 0.08, 0.1), _pad(_tone(380, 260, 0.07, 0.1), 0.01)), 'move', 0.3),
    ('shake', lambda: _seq(_noise(0.05, 22, 0.6), _pad(_noise(0.05, 22, 0.6), 0.03), _pad(_noise(0.05, 22, 0.6), 0.03)), 'move', 0.3),
    ('spin', lambda: _tone(600, 1600, 0.22, 0.12), 'move', 0.34),
    ('skid', lambda: _noise(0.16, 8, 0.25), 'move', 0.34),
    ('shiver', lambda: _seq(*[_pad(_noise(0.022, 30, 0.7), 0.012) for _ in range(7)]), 'move', 0.26),
    ('look', lambda: _seq(_tone(700, 760, 0.05, 0.1), _pad(_tone(760, 700, 0.05, 0.1), 0.03)), 'move', 0.26),
    ('tick', lambda: _seq(_tone(1300, 1300, 0.018), _pad(_tone(1050, 1050, 0.018), 0.02)), 'move', 0.24),
    ('whoosh', lambda: [random.uniform(-1, 1) * (1 - i / (SR * 0.3)) ** 2.2 * math.sin(math.pi * i / (SR * 0.3)) for i in range(int(SR * 0.3))], 'move', 0.3),
    ('surprised', lambda: _seq(_tone(500, 1100, 0.1, 0.14), _pad(_tone(900, 700, 0.09, 0.1), 0.02)), 'face', 0.4),
    ('yawn', lambda: _tone(420, 210, 0.42, 0.08), 'face', 0.32),
]


def make_sounds(pack_dir, manifest=None):
    """合成 20 个音效，返回 (写出的名字列表, 清单 sounds 字段)。"""
    snd_dir = os.path.join(pack_dir, 'sounds')
    os.makedirs(snd_dir, exist_ok=True)
    random.seed(20261028)  # 每次生成都一样，方便复现
    made, table = [], {}
    for name, fn, kind, vol in SOUNDS:
        path = os.path.join(snd_dir, name + '.wav')
        try:
            buf = fn()
            if not buf:
                continue
            _write_wav(path, buf)
        except Exception:  # noqa: BLE001
            continue
        made.append(name)
        table[name] = {'file': 'sounds/%s.wav' % name, 'kind': kind, 'volume': vol}
    return made, table


# ---------------------------------------------------------------- 图像


def _load_body(path):
    im = Image.open(path).convert('RGBA')
    bb = im.split()[3].getbbox()
    if bb:
        im = im.crop(bb)
    return im


def _canvas(w, h):
    return Image.new('RGBA', (max(1, w), max(1, h)), (0, 0, 0, 0))


def _alpha_ramp(size, width, horizontal=False):
    """一条从 0 到 255 的渐变，用来把拼接处的硬边化开。"""
    m = Image.new('L', (size, width) if not horizontal else (width, size))
    px = m.load()
    for i in range(width):
        v = int(255 * i / max(1, width - 1))
        for j in range(size):
            if horizontal:
                px[i, j] = v
            else:
                px[j, i] = v
    return m


def _paste_soft(dst, src, xy):
    dst.alpha_composite(src, (max(0, xy[0]), max(0, xy[1])))


def _split_legs(body, hip):
    """把下半身从中间分成左右腿两块，中间留一点重叠。"""
    w, h = body.size
    mid = w // 2
    over = max(2, w // 40)
    left = body.crop((0, hip, mid + over, h))
    right = body.crop((mid - over, hip, w, h))
    return left, right, mid, over


def make_walk(body_path, out_dir, frames=6, swing=0.10, lift=0.025):
    """生成走路序列帧。

    swing 是每条腿绕髋关节摆动的幅度（占身宽比例），lift 是抬脚高度（占身高比例）。
    腿越靠下越明显，所以髋的分界线放在 72% 处 —— 裙子以下才是腿。
    """
    if not HAS_PIL:
        return []
    body = _load_body(body_path)
    w, h = body.size
    if w < 8 or h < 8:
        return []

    hip = int(h * 0.72)
    legs = body.crop((0, hip, w, h))
    upper = body.crop((0, 0, w, min(h, hip + max(2, h // 70))))
    lw, lh = legs.size
    mid = lw // 2
    over = max(2, lw // 30)
    leg_l = legs.crop((0, 0, mid + over, lh))
    leg_r = legs.crop((mid - over, 0, lw, lh))

    pad_x = int(w * swing * 1.6) + 6
    pad_y = int(h * lift) + 8
    cw, ch = w + pad_x * 2, h + pad_y
    os.makedirs(out_dir, exist_ok=True)
    made = []
    theta = math.degrees(math.atan2(w * swing, max(1, h - hip)))  # 摆动角

    for i in range(frames):
        ph = 2 * math.pi * (i + 0.5) / frames  # 错半帧，避免有两帧完全一样
        s = math.sin(ph)
        bob = -abs(s) * h * 0.014
        base_y = ch - h

        layer = _canvas(cw, ch)

        # 两条腿反相绕髋关节转，抬起来的那侧再往上收一点
        for src, sign, x0 in ((leg_l, +1, 0), (leg_r, -1, mid - over)):
            piv = (src.width / 2, 0)
            rot = src.rotate(theta * s * sign, resample=Image.BICUBIC, center=piv, fillcolor=(0, 0, 0, 0))
            up = int(h * lift * max(0.0, s * sign))
            layer.alpha_composite(rot, (pad_x + x0, base_y + hip - up))

        # 上半身：反向小幅摆动，加一点上下颠
        up_dx = int(-w * swing * 0.18 * s)
        layer.alpha_composite(upper, (pad_x + up_dx, int(base_y + bob)))

        # 重心左右移动：这一项让前后半周期看起来不一样，不然六帧里只有三张能区分
        shift = int(w * 0.020 * math.cos(ph))
        moved = _canvas(cw, ch)
        moved.alpha_composite(layer, (shift, 0))
        layer = moved

        # 整体绕脚底中心侧倾，落地那一下再压一点
        compress = 1 - 0.015 * abs(math.cos(ph))
        squashed = layer.resize((cw, max(1, int(ch * compress))), Image.LANCZOS)
        frame = _canvas(cw, ch)
        frame.alpha_composite(squashed, (0, ch - squashed.height))
        frame = frame.rotate(s * 1.8, resample=Image.BICUBIC, center=(cw // 2, ch), fillcolor=(0, 0, 0, 0))

        name = 'walk-%02d.png' % i
        frame.save(os.path.join(out_dir, name))
        made.append(name)
    return made


def make_pose(body_path, kind, out_path):
    """坐姿 / 睡姿的近似图。kind: 'sit' 或 'sleep'。"""
    if not HAS_PIL:
        return False
    body = _load_body(body_path)
    w, h = body.size
    if w < 8 or h < 8:
        return False

    hip = int(h * 0.58)
    upper = body.crop((0, 0, w, hip))
    lower = body.crop((0, hip, w, h))

    if kind == 'sit':
        # 下半身压扁、上半身跟着降下来，等于蹲下去
        lh = max(2, int(lower.height * 0.42))
        lower2 = lower.resize((w, lh), Image.LANCZOS)
        new_h = upper.height + lh
        out = _canvas(w, new_h)
        out.alpha_composite(upper, (0, 0))
        out.alpha_composite(lower2, (0, upper.height))
        # 稍微压扁整体，看起来重心更低
        out = out.resize((max(1, int(w * 1.04)), max(1, int(new_h * 0.97))), Image.LANCZOS)
    elif kind == 'sleep':
        # 躺平：先蹲，再整体转 90 度，头朝左
        lh = max(2, int(lower.height * 0.3))
        lower2 = lower.resize((w, lh), Image.LANCZOS)
        new_h = upper.height + lh
        tmp = _canvas(w, new_h)
        tmp.alpha_composite(upper, (0, 0))
        tmp.alpha_composite(lower2, (0, upper.height))
        tmp = tmp.resize((int(w * 0.86), int(new_h * 0.86)), Image.LANCZOS)
        out = tmp.rotate(90, expand=True, resample=Image.BICUBIC, fillcolor=(0, 0, 0, 0))
    else:
        return False

    bb = out.split()[3].getbbox()
    if bb:
        out = out.crop(bb)
    out.save(out_path)
    return True
