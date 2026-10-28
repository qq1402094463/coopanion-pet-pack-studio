"""Pet Pack Studio - local helper for building Coopanion figure packs.

Runs on 127.0.0.1 only. No third-party packages: Python's standard library
is enough. It does four jobs:

  1. project files: create a project, take uploaded images/videos, pick a main image
  2. AI: send your description and the asset list to any OpenAI-compatible model,
     get back a figure.json that passes Coopanion's own rules
  3. validate: check a pack the way Coopanion's importer checks it
  4. package: zip it under dist/ so it can be imported

The format rules here are copied from Coopanion v0.2.13:
  resources/app/node_modules/cortico-world-desktop-pet/src/packs.ts
"""

import base64
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import autogen

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PROJECTS = os.path.join(ROOT, 'projects')
DIST = os.path.join(ROOT, 'dist')
TEMPLATE = os.path.join(HERE, 'templates', 'minimal-pack')
CONFIG = os.path.join(HERE, 'ai-config.json')
# Key 不放 studio 里：这个文件夹整个会被 AI 工具扫描，明文 Key 会跟着进上下文。
CONFIG_HOME = os.path.join(
    os.environ.get('LOCALAPPDATA') or os.path.expanduser('~'), 'PetPackStudio', 'ai-config.json')
WEB = os.path.join(HERE, 'web')
PORT = 8848

# ---------------------------------------------------------------- rules

ID_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,31}$')
OPTION_ID_RE = re.compile(r'^[a-z0-9]{1,24}$')
NAME_BREAK = re.compile(r'[,，、\s【】<>＜＞]')
IMG_EXT = {'.png', '.jpg', '.jpeg', '.webp'}
# Coopanion 认不出的图片格式，提示用户换掉
UNSUPPORTED_EXT = {'.gif', '.svg', '.avif', '.bmp', '.tif', '.tiff', '.heic'}
AUDIO_EXT = {'.ogg', '.mp3', '.wav'}
SOUND_KINDS = {'move', 'touch', 'face', 'snore', 'talk'}
WORD_KINDS = {'expression', 'motion'}
IMPORT_MAX = 128 * 1024 * 1024
MANIFEST_VERSION = 2
FIGURE_API = 2

# 官方导入器的硬上限，超了整包会被拒，不是警告
MAX_ENTRIES = 2048                  # 文件条目数
MAX_FILE = 16 * 1024 * 1024         # 单个文件
MAX_MANIFEST = 256 * 1024           # figure.json 本身
RESERVED_IDS = {'coo', 'whale'}     # 内置形象专用 id
MAX_BODY = 96 * 1024 * 1024         # 单次 HTTP 请求体上限，防止一个大包把内存吃光


def names_of(v):
    if not isinstance(v, dict) or isinstance(v, list):
        return None
    out = {k: s.strip() for k, s in v.items() if isinstance(s, str) and s.strip()}
    return out or None


def inside_path(p):
    if not isinstance(p, str) or not p:
        return False
    if p.startswith('/') or re.match(r'^[a-z]+:', p, re.I):
        return False
    if '..' in re.split(r'[\\/]', p):
        return False
    return not re.search(r'[<>"\x00-\x1f]', p)


EXPR_IDS = {
    'neutral', 'happy', 'wink', 'love', 'shy', 'surprised', 'angry', 'sad',
    'sleepy', 'thinking', 'smug', 'pout', 'worried', 'determined',
    'flustered', 'scared', 'excited', 'cry', 'confused',
}
MOTION_IDS = {
    'stand', 'jump', 'hop', 'look', 'turn', 'nod', 'shake', 'spin', 'sit',
    'sleep', 'dizzy', 'walk', 'run', 'wave', 'bow', 'shiver', 'flap', 'dance',
}
# 模型常写的近义词，映射到 kit 真正认识的名字
WORD_ALIAS = {
    'idle': 'stand', 'standby': 'stand', 'surprise': 'surprised',
    'joy': 'happy', 'smile': 'happy', 'laugh': 'happy', 'sadness': 'sad',
    'anger': 'angry', 'tired': 'sleepy', 'sleeping': 'sleep',
    'sitdown': 'sit', 'lie': 'sleep', 'lying': 'sleep',
}
# 每个词自带中文叫法：模型只给了英文 id 也能直接用
DEFAULT_ZH = {
    'neutral': ['平静'], 'happy': ['开心', '大笑'], 'wink': ['眨眼'],
    'love': ['喜欢'], 'shy': ['害羞'], 'surprised': ['惊讶', '吃惊'],
    'angry': ['生气'], 'sad': ['难过'], 'sleepy': ['困', '打哈欠'],
    'thinking': ['思考'], 'smug': ['得意'], 'pout': ['鼓嘴', '不满'],
    'worried': ['担心'], 'determined': ['坚定'], 'flustered': ['慌张'],
    'scared': ['害怕'], 'excited': ['兴奋'], 'cry': ['哭'], 'confused': ['困惑'],
    'stand': ['站好'], 'jump': ['跳'], 'hop': ['小跳', '蹦'], 'look': ['看', '张望'],
    'turn': ['转身'], 'nod': ['点头'], 'shake': ['摇头'], 'spin': ['转圈'],
    'sit': ['坐下'], 'sleep': ['睡觉'], 'dizzy': ['转晕'], 'walk': ['走路', '走'],
    'run': ['跑', '小跑'], 'wave': ['招手', '挥手'], 'bow': ['鞠躬'],
    'shiver': ['发抖'], 'flap': ['扇动'], 'dance': ['跳舞'],
}


def _one_line(v):
    """把字符串 / 数组 / 语言对象压成一句中文，取不到就返回空串。"""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    if isinstance(v, list):
        parts = [_one_line(x) for x in v]
        return '、'.join(p for p in parts if p)
    if isinstance(v, dict):
        for k in ('zh', 'zh-CN', 'cn', 'en'):
            s = _one_line(v.get(k))
            if s:
                return s
    return ''


def _lang_map(v):
    """任何写法 -> {"zh": "一句话"}，拿不到就返回 {}。"""
    if isinstance(v, dict):
        out = {}
        for k, val in v.items():
            s = _one_line(val)
            if isinstance(k, str) and s:
                out[k] = s
        return out
    s = _one_line(v)
    return {'zh': s} if s else {}


def _name_lists(v):
    """名字字段 -> {"zh": ["a","b"]}，兼容字符串、数组、{lang:[...]} 三种写法。"""
    out = {}
    if isinstance(v, str):
        out['zh'] = [v]
    elif isinstance(v, list):
        out['zh'] = [x for x in v if isinstance(x, str)]
    elif isinstance(v, dict):
        for lang, val in v.items():
            if isinstance(val, str):
                out.setdefault(lang, []).append(val)
            elif isinstance(val, list):
                out.setdefault(lang, []).extend(x for x in val if isinstance(x, str))
    return out


def fix_manifest(raw):
    """把 AI 或手写的常见错误改成能通过导入的形状。

    只动清单字段，不动 figure.js。返回 (new_raw, changes, dropped)。
    dropped 里是被移出 vocab 的词，原文另存到项目的 backup 目录。
    """
    changes, dropped = [], []
    out = dict(raw) if isinstance(raw, dict) else {}

    for key in ('manifest', 'api'):
        if out.get(key) != 2:
            changes.append(f'{key} 改成 2')
        out[key] = 2

    pid = out.get('id')
    pid = pid.strip().lower() if isinstance(pid, str) else ''
    pid = re.sub(r'[^a-z0-9-]', '-', pid).strip('-')[:32]
    if not re.match(r'^[a-z0-9]', pid or ''):
        pid = 'my-pet'
        changes.append('id 不合法，先临时改成 my-pet，你自己再改一个')
    if pid == 'coo':
        pid = 'my-pet'
        changes.append('id 不能是 coo，改成 my-pet')
    if pid != out.get('id'):
        changes.append(f'id 改成 {pid}')
    out['id'] = pid

    name = _lang_map(out.get('name'))
    if not name:
        name = {'zh': pid}
        changes.append('name 是空的，先填成 id')
    elif not isinstance(out.get('name'), dict):
        changes.append('name 从字符串改成 {"zh": ...}')
    out['name'] = name

    about = _lang_map(out.get('about'))
    if not about:
        about = {'zh': f'{_one_line(name)}，一只自定义的桌面宠物。'}
        changes.append('about 是空的，先填一句占位描述，建议你自己改具体')
    elif not isinstance(out.get('about'), dict):
        changes.append('about 从字符串改成 {"zh": ...}')
    out['about'] = about

    ver = out.get('version')
    if not isinstance(ver, str) or not ver.strip():
        out['version'] = '1.0.0'
        changes.append('version 补成 1.0.0')

    if out.get('entry') != 'figure.js':
        out['entry'] = 'figure.js'
        changes.append('entry 设为 figure.js')
    if out.get('export') != 'createMyPet':
        out['export'] = 'createMyPet'
        changes.append('export 设为 createMyPet')

    for key, default in (('axes', []), ('presets', []), ('sounds', {}), ('can', {'walk': True})):
        if key not in out or out[key] is None:
            out[key] = default
            changes.append(f'{key} 补上默认值')

    words = out.get('vocab')
    if isinstance(words, dict):
        words = [dict(v, id=k) for k, v in words.items() if isinstance(v, dict)]
        changes.append('vocab 从对象改成数组')
    if not isinstance(words, list):
        words = []
        changes.append('vocab 不是数组，先清空')

    fixed, taken_ids, taken_names = [], set(), {}
    for w in words:
        if isinstance(w, str):
            w = {'id': w}
        if not isinstance(w, dict):
            dropped.append(str(w))
            continue
        wid = _one_line(w.get('id')).lower()
        wid = re.sub(r'[^a-z0-9-]', '-', wid).strip('-')[:32]
        if not wid:
            dropped.append(json.dumps(w, ensure_ascii=False)[:60])
            continue
        if wid in taken_ids:
            dropped.append(wid + '（重复的词 id）')
            continue

        notes = []

        alt = WORD_ALIAS.get(wid)
        if alt:
            notes.append(f'{wid} 改成 kit 认的 {alt}')
            wid = alt

        kind = w.get('kind')
        if kind not in WORD_KINDS:
            if wid in MOTION_IDS:
                kind = 'motion'
            elif wid in EXPR_IDS:
                kind = 'expression'
            else:
                dropped.append(wid + '（不是表情也不是动作，可能是配色或音效）')
                continue
            notes.append(f'补 kind={kind}')

        src = w.get('names') or w.get('aliases') or w.get('name') or w.get('label')
        raw_names = _name_lists(src)
        if not raw_names and wid in DEFAULT_ZH:
            raw_names = {'zh': list(DEFAULT_ZH[wid])}
            notes.append('这个 id 自带中文叫法，已补上')

        names = {}
        for lang, lst in raw_names.items():
            keep = []
            for n in lst:
                n = n.strip()
                if not n or NAME_BREAK.search(n) or n == wid:
                    if n:
                        notes.append(f'删掉不能用的别名 {n}')
                    continue
                if n in taken_names:
                    notes.append(f'别名 {n} 和 {taken_names[n]} 撞了，删掉')
                    continue
                if n in keep:
                    continue
                keep.append(n)
                taken_names[n] = wid
            if keep:
                names[lang] = keep
        if not names:
            dropped.append(wid + '（没有任何能用的中文别名）')
            continue
        if not isinstance(w.get('names'), dict):
            notes.append('名字收进 names（' + '/'.join(sum(names.values(), [])) + '）')
        elif w.get('names') != names:
            notes.append('names 整理过')

        about_w = _lang_map(w.get('about'))
        if not about_w:
            primary = names.get('zh', [wid])[0]
            about_w = {'zh': (f'露出「{primary}」的表情' if kind == 'expression'
                              else f'做「{primary}」的动作')}
            notes.append('补 about')

        sec = w.get('seconds')
        if isinstance(sec, str):
            try:
                sec = float(sec.strip())
            except ValueError:
                sec = None
        if not isinstance(sec, (int, float)) or isinstance(sec, bool) or not sec > 0:
            sec = 0.9 if kind == 'expression' else 1.2
            notes.append(f'补 seconds={sec}')

        if notes:
            changes.append(f'词 {wid}：' + '，'.join(notes))


        taken_ids.add(wid)
        entry = {k: v for k, v in w.items() if k not in ('aliases', 'name', 'label')}
        entry.update({'id': wid, 'kind': kind, 'names': names, 'about': about_w,
                      'seconds': round(float(sec), 2)})
        fixed.append(entry)

    if out.get('vocab') != fixed:
        out['vocab'] = fixed

    return out, changes, dropped


FACE_ALIAS = {
    'surprise': 'surprised', 'surprised': 'surprised',
    'anger': 'angry', 'sadness': 'sad', 'fear': 'scared',
    'think': 'thinking', 'sleep': 'sleepy', 'tired': 'sleepy',
    'joy': 'happy', 'smile': 'happy', 'love2': 'love', 'pout2': 'pout',
}


def normalize_bottom(path):
    """把不透明部分移到画布最底边。站、走、坐、睡几张图脚才停在同一高度，换图不跳。

    需要 Pillow，没有就跳过（不影响出包，只是可能略微错位）。
    """
    try:
        from PIL import Image
    except Exception:
        return False
    try:
        im = Image.open(path).convert('RGBA')
    except Exception:
        return False
    bb = im.split()[3].getbbox()
    if not bb:
        return False
    w, h = im.size
    dy = h - bb[3]
    if dy <= 0:
        return False
    out = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    out.paste(im.crop((0, 0, w, bb[3])), (0, dy))
    try:
        out.save(path, 'PNG', optimize=True)
    except Exception:
        return False
    return True


def sync_raw_sounds(proj):
    """raw 里的 sound_step.wav -> pack/sounds/step.wav，并写进清单的 sounds。"""
    changes = []
    raw = os.path.join(proj, 'raw')
    pack = os.path.join(proj, 'pack')
    if not os.path.isdir(raw) or not os.path.isdir(pack):
        return changes
    files = [f for f in sorted(os.listdir(raw))
             if os.path.splitext(f)[1].lower() in AUDIO_EXT]
    if not files:
        return changes
    snd = os.path.join(pack, 'sounds')
    os.makedirs(snd, exist_ok=True)
    man_path = os.path.join(pack, 'figure.json')
    man = read_json(man_path, None)
    if not isinstance(man, dict):
        return changes
    table = man.get('sounds') if isinstance(man.get('sounds'), dict) else {}
    kind_of = {name: kind for name, _fn, kind, _v in autogen.SOUNDS}
    for fn in files:
        base, ext = os.path.splitext(fn)
        ext = ext.lower()
        name = re.sub(r'^sound[-_ ]*', '', base.lower()).strip() or base.lower()
        if not re.match(r'^[a-z0-9-]{1,24}$', name):
            continue
        dest = os.path.join(snd, name + ext)
        src = os.path.join(raw, fn)
        try:
            shutil.copyfile(src, dest)
        except OSError:
            continue
        table[name] = {'file': f'sounds/{name}{ext}',
                       'kind': kind_of.get(name, 'move'),
                       'volume': 0.45}
        changes.append(f'{fn} → sounds/{name}{ext}')
    if changes:
        man['sounds'] = table
        write_json(man_path, man)
    return changes


def sync_raw_textures(proj):
    """raw 里的 face_开心式命名 -> pack/tex/face-<id>.png，让模板真的用上这些图。"""
    changes = []
    raw = os.path.join(proj, 'raw')
    tex = os.path.join(proj, 'pack', 'tex')
    if not os.path.isdir(raw):
        return changes
    os.makedirs(tex, exist_ok=True)
    for fn in sorted(os.listdir(raw)):
        base, ext = os.path.splitext(fn)
        if ext.lower() not in IMG_EXT or not base.lower().startswith('face'):
            continue
        key = re.sub(r'^face[-_ ]*', '', base.lower()).strip()
        key = FACE_ALIAS.get(key, key)
        if key not in EXPR_IDS:
            continue
        src = os.path.join(raw, fn)
        dest = os.path.join(tex, f'face-{key}.png')
        try:
            if os.path.isfile(dest) and open(src, 'rb').read() == open(dest, 'rb').read():
                continue
        except OSError:
            pass
        ok = False
        if ext.lower() != '.png':
            try:
                from PIL import Image
                Image.open(src).convert('RGBA').save(dest, 'PNG')
                ok = True
            except Exception:
                ok = False
        if not ok:
            shutil.copyfile(src, dest)
        changes.append(f'{fn} → tex/face-{key}.png')
        normalize_bottom(dest)

    # 走路序列帧与坐/睡姿：walk_f00.png -> tex/walk-00.png，mode_sit.png -> tex/sit.png
    walk_seen = []
    for fn in sorted(os.listdir(raw)):
        base, ext = os.path.splitext(fn)
        if ext.lower() not in IMG_EXT:
            continue
        low = base.lower()
        dest_name = None
        m = re.match(r'^walk[-_ ]*f?(\d+)$', low)
        if m:
            dest_name = f'walk-{int(m.group(1)):02d}.png'
            walk_seen.append(int(m.group(1)))
        elif re.match(r'^(mode[-_ ]*)?sit$', low):
            dest_name = 'sit.png'
        elif re.match(r'^(mode[-_ ]*)?(sleep|sleepy|lie)$', low):
            dest_name = 'sleep.png'
        if not dest_name:
            continue
        src = os.path.join(raw, fn)
        dest = os.path.join(tex, dest_name)
        try:
            if os.path.isfile(dest) and open(src, 'rb').read() == open(dest, 'rb').read():
                continue
        except OSError:
            pass
        ok = False
        if ext.lower() != '.png':
            try:
                from PIL import Image
                Image.open(src).convert('RGBA').save(dest, 'PNG')
                ok = True
            except Exception:
                ok = False
        if not ok:
            shutil.copyfile(src, dest)
        changes.append(f'{fn} → tex/{dest_name}')
        normalize_bottom(dest)

    # 脚贴底边：连已经同步过的旧图也补一遍，换图时才不会一上一下
    for fn in sorted(os.listdir(tex)):
        if fn.lower().endswith('.png'):
            normalize_bottom(os.path.join(tex, fn))

    # 序列帧必须从 00 开始连续，断一帧后面就用不上了
    if walk_seen:
        missing = [i for i in range(max(walk_seen) + 1) if i not in walk_seen]
        if missing:
            changes.append('走路帧缺了 ' + '、'.join(f'{i:02d}' for i in missing) + '，只能用到第 ' + f'{min(missing) - 1:02d} 帧')

    # 音效：raw/sound_step.wav -> sounds/step.wav
    try:
        changes.extend(sync_raw_sounds(proj))
    except Exception:  # noqa: BLE001
        pass
    return changes


def same_pixels(a, b):
    """两张图是不是同一张（按像素比，不按字节比 —— 重新编码过字节就不同了）。"""
    try:
        from PIL import Image
        with Image.open(a) as ia, Image.open(b) as ib:
            if ia.size != ib.size:
                return False
            return ia.convert('RGBA').tobytes() == ib.convert('RGBA').tobytes()
    except Exception:  # noqa: BLE001
        try:
            return open(a, 'rb').read() == open(b, 'rb').read()
        except OSError:
            return False


def clean_template_faces(folder):
    """把还是模板原样的表情图挪到 tex/_unused/，不删，随时能拿回来。"""
    tpl_tex = os.path.join(TEMPLATE, 'tex')
    tex = os.path.join(folder, 'tex')
    moved = []
    if not os.path.isdir(tex) or not os.path.isdir(tpl_tex):
        return moved
    graveyard = os.path.join(tex, '_unused')
    for fn in sorted(os.listdir(tex)):
        if not fn.lower().startswith('face-') or not fn.lower().endswith('.png'):
            continue
        mine = os.path.join(tex, fn)
        theirs = os.path.join(tpl_tex, fn)
        if not os.path.isfile(theirs) or not os.path.isfile(mine):
            continue
        try:
            if not same_pixels(mine, theirs):
                continue
        except OSError:
            continue
        os.makedirs(graveyard, exist_ok=True)
        shutil.move(mine, os.path.join(graveyard, fn))
        moved.append('tex/' + fn)
    return moved


def generate_missing(proj, targets=None):
    """一张主图补齐缺的素材。已有的图一律不覆盖，说话要老实：走路是程序近似。"""
    targets = targets or ['sounds', 'poses', 'walk']
    pack = os.path.join(proj, 'pack')
    if not os.path.isdir(pack):
        return {'ok': False, 'error': '这个项目还没有 pack 目录'}
    tex = os.path.join(pack, 'tex')
    os.makedirs(tex, exist_ok=True)
    body = os.path.join(tex, 'body.png')
    man_path = os.path.join(pack, 'figure.json')
    changes, skipped = [], []

    has_body = os.path.isfile(body)
    if not autogen.HAS_PIL:
        skipped.append('没装 Pillow，图相关的都生成不了。执行 pip install pillow')

    # 走路帧
    if 'walk' in targets:
        have = [f for f in os.listdir(tex) if re.match(r'^walk-\d+\.png$', f)]
        if len(have) >= 3:
            skipped.append(f'已有 {len(have)} 张走路帧，没动')
        elif have:
            skipped.append(f'只有 {len(have)} 张走路帧，循环会短得看出来，建议补到 6 张')
        elif not has_body:
            skipped.append('还没有主图，走路帧跳过')
        elif autogen.HAS_PIL:
            made = autogen.make_walk(body, tex)
            changes.append(f'生成 {len(made)} 张走路帧（程序变形，不是重绘）')

    # 坐姿 / 睡姿
    if 'poses' in targets:
        for kind, fname, label in (('sit', 'sit.png', '坐姿'), ('sleep', 'sleep.png', '睡姿')):
            dest = os.path.join(tex, fname)
            if os.path.isfile(dest):
                skipped.append(f'已有{label}图，没动')
                continue
            if not has_body or not autogen.HAS_PIL:
                skipped.append(f'主图缺或没有 Pillow，{label}跳过')
                continue
            if autogen.make_pose(body, kind, dest):
                normalize_bottom(dest)
                changes.append(f'生成{label}图（程序近似，建议换成真图）')

    # 音效
    if 'sounds' in targets:
        man = read_json(man_path, None)
        if not isinstance(man, dict):
            skipped.append('清单还没有，音效跳过')
        else:
            table = man.get('sounds') if isinstance(man.get('sounds'), dict) else {}
            need = [n for n, _f, _k, _v in autogen.SOUNDS
                    if n not in table or not os.path.isfile(os.path.join(pack, table[n].get('file', '')))]
            if not need:
                skipped.append(f'已有 {len(table)} 条音效，没动')
            else:
                made, fresh = autogen.make_sounds(pack)
                for name in made:
                    if name not in table:
                        table[name] = fresh[name]
                man['sounds'] = table
                write_json(man_path, man)
                changes.append(f'合成 {len(need)} 条音效，清单已登记 {len(table)} 条')

    moved = clean_template_faces(pack)
    if moved:
        changes.append('模板占位表情挪到 tex/_unused/：' + '、'.join(moved))

    errors, warnings, info = validate_pack(pack)
    return {'ok': not errors, 'changes': changes, 'skipped': skipped,
            'errors': errors, 'warnings': warnings, 'info': info}


def fix_pack(folder):
    """就地修一份 figure.json，原件另存到 <项目>/backup/ 下。"""
    man_path = os.path.join(folder, 'figure.json')
    if not os.path.isfile(man_path):
        return {'ok': False, 'error': '这个项目里还没有 figure.json'}
    try:
        with open(man_path, encoding='utf-8-sig') as f:
            raw = json.load(f)
    except Exception as e:
        return {'ok': False, 'error': f'figure.json 解析不了：{e}'}

    fixed, changes, dropped = fix_manifest(raw)

    # 顺手把 raw 里的表情图同步成模板认的文件名
    synced = sync_raw_textures(os.path.dirname(folder))
    if synced:
        changes.extend('表情图同步：' + s for s in synced)

    backup_dir = os.path.join(os.path.dirname(folder), 'backup')
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')
    backup = os.path.join(backup_dir, f'figure-{stamp}.json')
    shutil.copyfile(man_path, backup)

    write_json(man_path, fixed)
    errors, warnings, info = validate_pack(folder)
    return {
        'ok': True, 'changes': changes, 'dropped': dropped,
        'backup': backup, 'errors': errors, 'warnings': warnings,
        'info': info, 'manifest': fixed,
    }


def validate_pack(folder):
    """Return (errors, warnings, info). Errors come straight from packs.ts."""
    errors, warnings, info = [], [], {}
    manifest_path = os.path.join(folder, 'figure.json')
    if not os.path.isfile(manifest_path):
        return ['缺少 figure.json'], warnings, info
    try:
        with open(manifest_path, encoding='utf-8-sig') as f:
            raw = json.load(f)
    except Exception as e:
        return [f'figure.json 不是合法 JSON：{e}'], warnings, info
    if not isinstance(raw, dict):
        return ['figure.json 的根必须是对象'], warnings, info

    for key, oldest, now in (('manifest', 2, MANIFEST_VERSION), ('api', 2, FIGURE_API)):
        v = raw.get(key)
        if not isinstance(v, int) or isinstance(v, bool):
            errors.append(f'{key} 必须是整数，现在是 {v!r}')
        elif v > now:
            errors.append(f'{key} 是 {v}，比这个版本支持的 {now} 还新')
        elif v < oldest:
            errors.append(f'{key} 是 {v}，太旧，最低 {oldest}')

    pid = raw.get('id')
    if not isinstance(pid, str) or not ID_RE.match(pid):
        errors.append(f'id 不合法：{pid!r}。只能小写字母数字和横杠，1-32 位，首字符不能是横杠')
    elif pid in RESERVED_IDS:
        errors.append(f'id 不能是 {pid}，官方内置形象占了这两个名字（coo、whale），换成自己的')
    else:
        info['id'] = pid

    if not names_of(raw.get('name')):
        errors.append('name 要有至少一个语言的名字，例如 {"zh":"小猫"}')
    if not names_of(raw.get('about')):
        errors.append('about 要有至少一个语言的描述。AI 靠这段知道自己的形象')

    entry = raw.get('entry')
    if not inside_path(entry):
        errors.append(f'entry 不合法：{entry!r}，要用包内相对路径')
    elif not os.path.isfile(os.path.join(folder, entry)):
        errors.append(f'entry 指向的文件不存在：{entry}')
        if isinstance(entry, str) and entry.endswith('.js'):
            errors.append('figure.js 一定要放在包里，没有它形象加载不出来')

    if not isinstance(raw.get('export'), str) or not raw.get('export'):
        errors.append('export 必须写工厂函数名，例如 createMyPet')

    for key in ('model', 'thumb'):
        if key in raw and not inside_path(raw[key]):
            errors.append(f'{key} 路径不合法：{raw[key]!r}')

    thumb = raw.get('thumb')
    if isinstance(thumb, str) and inside_path(thumb):
        full = os.path.join(folder, thumb)
        if not os.path.isfile(full):
            warnings.append(f'thumb 指向的图不存在：{thumb}，列表里会没有缩略图')
        elif os.path.getsize(full) > 1024 * 1024:
            warnings.append('缩略图超过 1 MB，界面会忽略它')
        elif os.path.splitext(thumb)[1].lower() not in IMG_EXT:
            warnings.append('缩略图只认 png/jpg/jpeg/webp')

    axes = raw.get('axes')
    axis_ids = []
    if axes is not None and not isinstance(axes, list):
        errors.append('axes 必须是数组')
        axes = []
    for a in axes or []:
        aid = a.get('id') if isinstance(a, dict) else None
        if not isinstance(aid, str) or not ID_RE.match(aid) or not names_of(a.get('name')):
            errors.append(f'轴不合法：{aid!r}，需要合法 id 和 name')
            continue
        opts = a.get('options')
        if not isinstance(opts, list) or not opts:
            errors.append(f'轴 {aid} 至少要有一个选项')
            continue
        seen = set()
        for o in opts:
            oid = o.get('id') if isinstance(o, dict) else None
            if not isinstance(oid, str) or not OPTION_ID_RE.match(oid):
                errors.append(f'轴 {aid} 的选项 id 不合法：{oid!r}。选项 id 只能小写字母数字，不能带横杠')
            elif oid in seen:
                errors.append(f'轴 {aid} 的选项 id 重复：{oid}')
            seen.add(oid)
            if not names_of(o.get('name')):
                errors.append(f'轴 {aid} 的选项 {oid} 缺 name')
        axis_ids.append(aid)

    presets = raw.get('presets')
    if presets is not None and not isinstance(presets, list):
        errors.append('presets 必须是数组')
        presets = []
    for p in presets or []:
        if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not ID_RE.match(p['id']):
            errors.append(f'预设 id 不合法：{p.get("id") if isinstance(p, dict) else p!r}')
            continue
        pick = p.get('pick')
        if not isinstance(pick, dict):
            errors.append(f'预设 {p["id"]} 缺 pick')
            continue
        for aid in axis_ids:
            if not isinstance(pick.get(aid), str):
                errors.append(f'预设 {p["id"]} 少了轴 {aid} 的选择')

    vocab = raw.get('vocab')
    if not isinstance(vocab, list):
        errors.append('vocab 必须是数组，可以是空数组 []')
        vocab = []
    if not vocab:
        warnings.append('vocab 是空的。AI 就没有表情和动作词可用，宠物只会走路和发呆')
    taken = {}
    for w in vocab:
        if not isinstance(w, dict):
            errors.append('vocab 里每一项都必须是对象')
            continue
        wid = w.get('id')
        if not isinstance(wid, str) or not ID_RE.match(wid):
            errors.append(f'词的 id 不合法：{wid!r}')
            continue
        kind = w.get('kind')
        if kind not in WORD_KINDS:
            warnings.append(f'词 {wid} 的 kind 是 {kind!r}，会被跳过（只认 expression 和 motion）')
            continue
        nm = w.get('names')
        if not isinstance(nm, dict) or not nm:
            errors.append(f'词 {wid} 缺 names')
            continue
        bad_names = []
        for lang, lst in nm.items():
            if not isinstance(lst, list):
                errors.append(f'词 {wid} 的 {lang} names 必须是数组')
                continue
            for n in lst:
                if not isinstance(n, str) or not n.strip():
                    errors.append(f'词 {wid} 有空的别名')
                elif NAME_BREAK.search(n):
                    errors.append(f'词 {wid} 的别名 {n!r} 含逗号/空格/【】这类字符，会让整包失败')
                else:
                    bad_names.append(n)
        for n in [wid] + bad_names:
            if n in taken:
                errors.append(f'名字 {n!r} 被词 {taken[n]} 和 {wid} 重复用了')
            taken[n] = wid
        if not names_of(w.get('about')):
            errors.append(f'词 {wid} 缺 about')
        sec = w.get('seconds')
        if not isinstance(sec, (int, float)) or isinstance(sec, bool) or not sec > 0:
            errors.append(f'词 {wid} 的 seconds 必须是大于 0 的数字')

    sounds = raw.get('sounds')
    if sounds is not None:
        if not isinstance(sounds, dict):
            errors.append('sounds 必须是对象，没有就写 {}')
        else:
            for name, d in sounds.items():
                if not re.match(r'^[a-z][a-zA-Z0-9-]{0,31}$', name):
                    errors.append(f'音效名不合法：{name!r}')
                    continue
                if not isinstance(d, dict) or not inside_path(d.get('file')):
                    errors.append(f'音效 {name} 缺合法 file')
                    continue
                ext = os.path.splitext(d['file'])[1].lower()
                if ext not in AUDIO_EXT:
                    warnings.append(f'音效 {name} 的类型 {ext} 不被支持，会被跳过')
                elif not os.path.isfile(os.path.join(folder, d['file'])):
                    warnings.append(f'音效 {name} 的文件不存在，会被跳过')
                if d.get('kind') not in SOUND_KINDS:
                    warnings.append(f'音效 {name} 的 kind 不认识，会被跳过')

    can = raw.get('can')
    if can is not None and not isinstance(can, dict):
        errors.append('can 必须是对象，例如 {"walk": true}')

    msize = os.path.getsize(manifest_path)
    if msize > MAX_MANIFEST:
        errors.append(f'figure.json 有 {msize / 1024:.0f} KB，超过 256 KB 上限，Coopanion 会拒绝导入')

    total = 0
    entries = 0
    big = []
    for dirpath, _, files in os.walk(folder):
        if dirpath.replace('\\', '/').find('/raw') >= 0:
            continue
        for fn in files:
            full = os.path.join(dirpath, fn)
            size = os.path.getsize(full)
            total += size
            entries += 1
            if size > MAX_FILE:
                big.append(f'{os.path.relpath(full, folder).replace(chr(92), "/")}（{size / 1048576:.1f} MB）')
    info['size'] = total
    info['entries'] = entries
    if total > IMPORT_MAX:
        errors.append(f'包有 {total / 1048576:.1f} MB，超过 128 MB 上限')
    if entries > MAX_ENTRIES:
        errors.append(f'包里 {entries} 个文件，超过 2048 个的上限（逐帧动画最容易超）')
    if big:
        errors.append('这些单个文件超过 16 MB，导入会被拒：' + '、'.join(big[:4]) +
                      '。转成 png 或缩小尺寸再放进来')

    bad_ext = []
    for dirpath, _, files in os.walk(folder):
        if dirpath.replace('\\', '/').find('/raw') >= 0:
            continue
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext in UNSUPPORTED_EXT:
                bad_ext.append(os.path.relpath(os.path.join(dirpath, fn), folder))
    if bad_ext:
        warnings.append('这些图 Coopanion 不认，用不到：' + ', '.join(bad_ext[:5]))

    # 模板自带的表情图没换：做表情时你的宠物会突然变成模板那个蓝色小生物
    tpl_tex = os.path.join(TEMPLATE, 'tex')
    tex_dir = os.path.join(folder, 'tex')
    if os.path.isdir(tex_dir) and os.path.isdir(tpl_tex) and \
            os.path.abspath(folder) != os.path.abspath(TEMPLATE):
        same = []
        for fn in sorted(os.listdir(tex_dir)):
            if not fn.lower().startswith('face-') or not fn.lower().endswith('.png'):
                continue
            mine = os.path.join(tex_dir, fn)
            theirs = os.path.join(tpl_tex, fn)
            if not os.path.isfile(theirs) or not os.path.isfile(mine):
                continue
            try:
                if same_pixels(mine, theirs):
                    same.append('tex/' + fn)
            except OSError:
                pass
        if same:
            warnings.append(
                '这几张表情图还是模板的示例图：' + '、'.join(same) +
                '。留着的话，你的宠物一做这个表情就会变成模板的蓝色小生物。'
                '换成自己的图，或者直接删掉这几个文件（没有对应表情图时会自动用主图）')

    info['words'] = len(vocab)
    info['axes'] = len(axis_ids)
    return errors, warnings, info


# ---------------------------------------------------------------- helpers

def read_json(path, default):
    try:
        with open(path, encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return default


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def read_config():
    """环境变量 > 用户目录 > 老的 studio/ai-config.json。

    studio 整个文件夹是要给 AI 扫描的，Key 明文放在那儿等于写进提示词。
    新保存的配置一律写到用户目录。
    """
    cfg = {}
    for p in (CONFIG_HOME, CONFIG):
        if os.path.isfile(p):
            got = read_json(p, {})
            if isinstance(got, dict):
                cfg.update({k: v for k, v in got.items() if v})
    env_key = os.environ.get('PETSTUDIO_KEY')
    if env_key:
        cfg['key'] = env_key
    return cfg


def write_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_HOME), exist_ok=True)
    write_json(CONFIG_HOME, cfg)


def project_dir(pid):
    safe = os.path.basename(pid)
    return os.path.join(PROJECTS, safe)


_PROJ_CACHE = {}


def _pack_stamp(folder):
    """给包打个"指纹"，动过才重新校验。项目多了以后列表接口不至于卡几秒。"""
    try:
        parts = [os.path.getmtime(folder)]
        for name in ('figure.json', 'figure.js', 'thumb.png'):
            p = os.path.join(folder, name)
            if os.path.exists(p):
                parts.append(os.path.getmtime(p))
        for name in ('tex', 'sounds'):
            p = os.path.join(folder, name)
            if os.path.isdir(p):
                parts.append(os.path.getmtime(p))
                parts.append(len(os.listdir(p)))
        return tuple(parts)
    except OSError:
        return ()


def list_projects():
    out = []
    if not os.path.isdir(PROJECTS):
        return out
    for name in sorted(os.listdir(PROJECTS)):
        d = os.path.join(PROJECTS, name)
        if not os.path.isdir(d):
            continue
        meta = read_json(os.path.join(d, 'project.json'), {})
        pk = os.path.join(d, 'pack')
        stamp = _pack_stamp(pk)
        cached = _PROJ_CACHE.get(name)
        if cached and cached[0] == stamp:
            errors, warnings, info = cached[1], cached[2], cached[3]
        else:
            errors, warnings, info = validate_pack(pk)
            _PROJ_CACHE[name] = (stamp, errors, warnings, info)
        out.append({
            'id': name,
            'created': meta.get('created', ''),
            'main': meta.get('main', ''),
            'assets': sorted(os.listdir(os.path.join(d, 'raw'))) if os.path.isdir(os.path.join(d, 'raw')) else [],
            'errors': len(errors),
            'warnings': len(warnings),
            'words': info.get('words', 0),
        })
    return out


def apply_main(proj, name):
    """把 raw 里的一张图设成 tex/body.png（居中缩放到 512 方图，底部对齐）+ 缩略图。"""
    src = os.path.join(proj, 'raw', name)
    if not os.path.isfile(src):
        return False, '这张图不在项目的 raw 里'
    pack = os.path.join(proj, 'pack')
    os.makedirs(os.path.join(pack, 'tex'), exist_ok=True)
    dest = os.path.join(pack, 'tex', 'body.png')
    ok, note = fit_png(src, dest, 512)
    if not ok:
        shutil.copyfile(src, dest)
        note = '没有 Pillow，直接用了原图。非正方形的图会被等比缩放，不会变形'
    if not fit_png(src, os.path.join(pack, 'thumb.png'), 256)[0]:
        shutil.copyfile(src, os.path.join(pack, 'thumb.png'))
    meta = read_json(os.path.join(proj, 'project.json'), {})
    meta['main'] = 'tex/body.png'
    meta['mainSource'] = name
    write_json(os.path.join(proj, 'project.json'), meta)
    return True, note


def classify_assets(proj):
    """把 raw 里的文件按用途归类，前端用来显示每个分区放了几张。"""
    counts = {'body': 0, 'walk': 0, 'face': 0, 'sit': 0, 'sleep': 0, 'sound': 0, 'other': 0}
    raw = os.path.join(proj, 'raw')
    items = []
    if not os.path.isdir(raw):
        return {'counts': counts, 'items': items}
    for fn in sorted(os.listdir(raw)):
        base, ext = os.path.splitext(fn)
        low, ext = base.lower(), ext.lower()
        if ext in AUDIO_EXT:
            slot = 'sound'
        elif ext not in IMG_EXT:
            slot = 'other'
        elif low == 'body':
            slot = 'body'
        elif re.match(r'^walk[-_ ]*f?\d+$', low):
            slot = 'walk'
        elif low.startswith('face'):
            slot = 'face'
        elif re.match(r'^(mode[-_ ]*)?sit$', low):
            slot = 'sit'
        elif re.match(r'^(mode[-_ ]*)?(sleep|sleepy|lie)$', low):
            slot = 'sleep'
        else:
            slot = 'other'
        counts[slot] = counts.get(slot, 0) + 1
        items.append({'name': fn, 'slot': slot})
    return {'counts': counts, 'items': items}


def save_data_url(data_url, dest):
    m = re.match(r'^data:([^;]+);base64,(.*)$', data_url, re.S)
    if not m:
        raise ValueError('上传的数据不是 base64 data URL')
    blob = base64.b64decode(m.group(2))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, 'wb') as f:
        f.write(blob)
    return len(blob)


def image_ok(path):
    """确认落盘的东西真是一张图。

    以前只看扩展名，一段 base64 垃圾也会被改名成 walk_f00.png 写进 tex，
    打包后才发现整包贴图是坏的。先看文件头，再让 Pillow 真解一遍。
    """
    ext = os.path.splitext(path)[1].lower()
    with open(path, 'rb') as f:
        head = f.read(16)
    if not head:
        return False, '文件是空的，上传可能中断了'
    sig = {'.png': b'\x89PNG\r\n\x1a\n', '.jpg': b'\xff\xd8\xff',
           '.jpeg': b'\xff\xd8\xff', '.webp': b'RIFF'}.get(ext, b'')
    if sig and not head.startswith(sig):
        return False, f'内容不像 {ext} 图片：可能选错了文件，或者上传时被截断'
    try:
        from PIL import Image
    except Exception:
        return True, ''
    try:
        with Image.open(path) as im:
            im.verify()
        return True, ''
    except Exception as e:
        return False, f'这张图解不开：{e}'


def safe_filename(name):
    name = os.path.basename(name.replace('\\', '/'))
    name = re.sub(r'[<>:"|?*\x00-\x1f]', '_', name)
    return name or 'file'


def ffmpeg_exe():
    p = shutil.which('ffmpeg')
    return p


def env_report():
    """依赖体检。

    缺 Pillow 时整条图片流水线会静默降级：表情做不了、视频帧抠不了、
    缩略图只是复制原图。用户看不出来是哪一步坏了，所以启动就要说清楚。
    """
    rep = {'python': sys.version.split()[0], 'exe': sys.executable,
           'pillow': '', 'ffmpeg': '', 'node': ''}
    try:
        import PIL  # noqa: F401
        rep['pillow'] = getattr(PIL, '__version__', 'yes')
    except Exception:
        pass
    rep['ffmpeg'] = ffmpeg_exe() or ''
    rep['node'] = shutil.which('node') or ''
    return rep


# ---------------------------------------------------------------- AI

SYSTEM_PROMPT = """你是 Coopanion 桌面宠物的词表作者。只输出一个 JSON 对象，不要解释，不要代码块。

输出格式：
{"name":"豆丁","about":"一两句外形描述","faces":[{"id":"happy","zh":["开心","大笑"]},{"id":"sad","zh":["难过"]}],"moves":[{"id":"jump","zh":["跳"]}]}

规则：
- faces 是表情清单，moves 是动作清单。这两个是给桌宠用的能力标签，不是台词，绝对不要写"抱抱我嘛"这种句子
- 每一项必须是对象，必须有 id 和 zh 两个键
- id 只能从下面的清单里挑，英文小写，不要自己编
- zh 是这个词的中文叫法，1 到 3 个，彼此不能重复，不能带逗号顿号空格
- faces 给 8 到 10 项，moves 给 4 到 6 项
- name 是宠物的中文短名
- about 写给看不见图的人：外形、颜色、多大、走路什么样，150 字以内
- 不要输出 vocab、assets、sounds、axes、entry 这些字段

表情 id：neutral, happy, wink, love, shy, surprised, angry, sad, sleepy, thinking, smug, pout, worried, determined, flustered, scared, excited, cry, confused
动作 id：stand, jump, hop, look, turn, nod, shake, spin, sit, sleep, dizzy, walk, run, wave, bow, shiver, flap, dance"""


def open_req(req, timeout=60):
    """访问本机地址时绕开系统代理，否则 Clash 这类会把它转发出去然后失败。"""
    host = urllib.parse.urlparse(req.full_url).hostname or ''
    if host in ('127.0.0.1', 'localhost', '::1', '0.0.0.0'):
        return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def call_llm(messages, cfg, timeout=180):
    base = cfg['base'].rstrip('/')
    if not base.endswith('/chat/completions'):
        base = base + '/chat/completions'
    payload = {
        'model': cfg['model'],
        'messages': messages,
        'temperature': 0.4,
        'stream': False,
        # 不写上限时不少中转只给 1024 或 4096，词表一长就被截断
        'max_tokens': 2000,
    }
    req = urllib.request.Request(
        base,
        data=json.dumps(payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + cfg['key'],
        },
        method='POST',
    )
    with open_req(req, timeout=timeout) as r:
        data = json.loads(r.read().decode('utf-8', 'replace'))
    return data['choices'][0]['message']['content']


def _close_partial(s):
    """把被截断的 JSON 补成完整的：砍到最后一个完整的逗号，再把括号补齐。"""
    stack = []
    in_str = esc = False
    cut = None
    cut_stack = None
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch in '{[':
            stack.append(ch)
        elif ch in '}]':
            if stack:
                stack.pop()
            if not stack:
                return s[:i + 1]
        elif ch == ',' and stack:
            cut = i
            cut_stack = list(stack)
    if cut is None:
        return None
    body = s[:cut]
    tail = ''.join(']' if b == '[' else '}' for b in reversed(cut_stack))
    return body + tail


def extract_json(text):
    """Pull the first JSON object out of a model reply. 截断了也尽量救回来。"""
    if not isinstance(text, str):
        raise ValueError('模型没有返回文字')
    fence = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
    body = fence.group(1) if fence else text
    start = body.find('{')
    if start < 0:
        raise ValueError('回复里没有 JSON')
    body = body[start:]

    for piece in (body, _close_partial(body)):
        if not piece:
            continue
        try:
            return json.loads(piece)
        except Exception:
            pass

    # 逐层往外砍，直到能解析为止
    probe = body
    for _ in range(6):
        probe = _close_partial(probe)
        if not probe:
            break
        try:
            return json.loads(probe)
        except Exception:
            probe = probe[:probe.rfind(',')] if ',' in probe else ''
    raise ValueError('JSON 不完整，可能是模型输出被截断了')


WORD_ONLY_PROMPT = """只输出一个 JSON 对象，不要解释。
格式是这样的，照抄结构，只换里面的 id 和中文：
{"faces":[{"id":"happy","zh":["开心"]},{"id":"sad","zh":["难过"]},{"id":"shy","zh":["害羞"]}],
 "moves":[{"id":"jump","zh":["跳"]},{"id":"wave","zh":["招手"]}]}

要求：
- faces 挑 8 个表情，moves 挑 5 个动作，id 只能用下面给的
- 每一项必须有两个键：id 和 zh。zh 是中文叫法，1 到 3 个字
- 不要写句子，不要写台词，不要写"抱抱我"这种

表情 id：neutral, happy, wink, love, shy, surprised, angry, sad, sleepy, thinking, smug, pout, worried, excited, cry
动作 id：stand, jump, hop, look, turn, nod, shake, spin, sit, sleep, walk, run, wave, bow, dance"""


REFUSAL_HINTS = ('没法接', '不能帮', '无法帮', '不提供', '拒绝', '不在我能', '换个话题', '做不到')


def default_motions():
    return [dict(w) for w in default_vocab() if w['kind'] == 'motion']


def add_missing_motions(vocab):
    """模型只给了表情没给动作时，补上标准动作，顺便躲开已有的名字。"""
    taken = set()
    for w in vocab:
        taken.add(w['id'])
        for lst in w.get('names', {}).values():
            taken.update(lst)
    added = []
    for w in default_motions():
        if w['id'] in taken or any(x in taken for x in w['names']['zh']):
            continue
        vocab.append(w)
        taken.add(w['id'])
        taken.update(w['names']['zh'])
        added.append(w['id'])
    return added


def explain_no_json(text, err):
    """模型没给 JSON 时，把真正的原因说清楚，别只丢一句解析失败。"""
    text = (text or '').strip()
    if not text:
        return ('模型返回了空内容。多半是上游把这个描述拦掉了，'
                '换一句描述再试，别用「小女孩」「幼童」这类词，改成「Q 版卡通角色」。')
    if any(h in text for h in REFUSAL_HINTS):
        return ('模型拒了这次描述，不是格式问题。'
                '换一句描述再试，比如把「巴掌大的小女孩」改成「Q 版卡通角色，圆脸短发」。'
                '它原话：' + text[:120])
    return f'模型没给出能解析的 JSON：{err}。它原话：{text[:120]}'


def default_vocab():
    """模型给的词全用不上时的保底词表，保证包一定能用。"""
    ids = ['happy', 'sad', 'angry', 'surprised', 'shy', 'sleepy', 'love', 'thinking',
           'walk', 'run', 'sit', 'sleep', 'jump', 'wave', 'dance']
    out = []
    for wid in ids:
        kind = 'expression' if wid in EXPR_IDS else 'motion'
        zh = list(DEFAULT_ZH.get(wid, [wid]))
        about = (f'露出「{zh[0]}」的表情' if kind == 'expression' else f'做「{zh[0]}」的动作')
        out.append({'id': wid, 'kind': kind, 'names': {'zh': zh},
                    'about': {'zh': about}, 'seconds': 0.9 if kind == 'expression' else 1.2})
    return out


def manifest_from_ai(ai, pid, base):
    """模型的简短回复 -> 完整清单。缺的字段交给 fix_manifest 兜。"""
    out = dict(base)
    out['id'] = pid
    if 'name' in ai:
        out['name'] = ai['name']
    if 'about' in ai:
        out['about'] = ai['about']
    raw_words = ai.get('faces') or ai.get('moves')
    if raw_words is None:
        raw_words = ai.get('words') or ai.get('vocab') or []
    if isinstance(raw_words, dict):
        raw_words = [dict(v, id=k) for k, v in raw_words.items() if isinstance(v, dict)]
    words = list(raw_words) if isinstance(raw_words, list) else []
    for key in ('moves', 'faces'):
        extra = ai.get(key)
        if isinstance(extra, list) and extra is not raw_words:
            words.extend(extra)
    words = []
    for w in raw_words if isinstance(raw_words, list) else []:
        if isinstance(w, str):
            words.append({'id': w})
        elif isinstance(w, dict):
            words.append({
                'id': w.get('id'),
                'kind': w.get('kind'),
                'names': (w.get('names') or w.get('aliases') or w.get('zh')
                          or w.get('name') or w.get('label')),
                'about': w.get('about'),
                'seconds': w.get('seconds'),
            })
    out['vocab'] = words
    out['manifest'] = 2
    out['api'] = 2
    out['entry'] = 'figure.js'
    out['export'] = 'createMyPet'
    out.setdefault('axes', [])
    out.setdefault('presets', [])
    out.setdefault('sounds', {})
    out.setdefault('can', {'walk': True})
    return out


def merge_manifest(base_manifest, ai_manifest):
    """Keep the verified skeleton, take the AI's words and text."""
    out = dict(base_manifest)
    for key in ('id', 'version', 'name', 'about', 'author', 'license', 'vocab', 'thumb'):
        if key in ai_manifest:
            out[key] = ai_manifest[key]
    out['manifest'] = 2
    out['api'] = 2
    out['entry'] = 'figure.js'
    out['export'] = 'createMyPet'
    out.setdefault('axes', [])
    out.setdefault('presets', [])
    out.setdefault('sounds', {})
    out.setdefault('can', {'walk': True})
    return out


# ---------------------------------------------------------------- server

class BodyTooLarge(Exception):
    pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'PetPackStudio/1.0'

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        elif isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            raise ValueError('Content-Length 不是数字')
        if n <= 0:
            return {}
        if n > MAX_BODY:
            raise BodyTooLarge(n)
        return json.loads(self.rfile.read(n).decode('utf-8'))

    def _local_only(self):
        """只接受本机网页发来的 JSON 请求。

        不加这一层的话，任何网页都能用 text/plain 往这个端口发一个简单请求，
        借 /api/open 打开本机任意程序 —— 不需要读返回值，副作用照样发生。
        """
        host = urllib.parse.urlsplit('//' + (self.headers.get('Host') or '')).hostname or ''
        if host.lower() not in ('127.0.0.1', 'localhost', '::1'):
            return False
        origin = self.headers.get('Origin')
        if origin:
            netloc = urllib.parse.urlparse(origin).hostname or ''
            if netloc.lower() not in ('127.0.0.1', 'localhost', '::1'):
                return False
        if self.command == 'POST':
            ctype = (self.headers.get('Content-Type') or '').split(';')[0].strip().lower()
            if ctype != 'application/json':
                return False
        return True

    def do_GET(self):
        path = self.path.split('?')[0]
        if not self._local_only():
            return self._send(403, {'error': '只接受本机访问'})
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if path in ('/', '/index.html'):
            f = os.path.join(WEB, 'index.html')
            if os.path.isfile(f):
                with open(f, 'rb') as fh:
                    return self._send(200, fh.read(), 'text/html; charset=utf-8')
            return self._send(404, 'index.html 不见了', 'text/plain; charset=utf-8')
        if path == '/preview':
            f = os.path.join(WEB, 'preview.html')
            if os.path.isfile(f):
                with open(f, 'rb') as fh:
                    return self._send(200, fh.read(), 'text/html; charset=utf-8')
            return self._send(404, 'preview.html 不见了', 'text/plain; charset=utf-8')
        if path == '/api/preview-data':
            return self._preview_data((qs.get('id') or [''])[0])
        if path == '/api/packfile':
            return self._packfile(qs)
        if path == '/api/projects':
            return self._send(200, list_projects())
        if path == '/api/assets':
            pid = qs.get('id', [''])[0]
            return self._send(200, classify_assets(project_dir(pid)))
        if path == '/api/config':
            cfg = read_config()
            return self._send(200, {
                'base': cfg.get('base', ''),
                'model': cfg.get('model', ''),
                'hasKey': bool(cfg.get('key')),
            })
        if path == '/api/ffmpeg':
            return self._send(200, {'ok': bool(ffmpeg_exe())})
        if path == '/api/env':
            return self._send(200, env_report())
        if path == '/api/thumb':
            return self._thumb()
        return self._send(404, {'error': 'not found'})

    def _packfile(self, qs):
        """给预览页读包里的贴图。只能读这个包内部的图，挡掉 ../。"""
        pid = (qs.get('id') or [''])[0]
        rel = (qs.get('p') or [''])[0]
        pack = os.path.abspath(os.path.join(project_dir(pid), 'pack'))
        if not inside_path(rel):
            return self._send(400, {'error': '路径不合法'})
        full = os.path.abspath(os.path.join(pack, rel))
        if not full.startswith(pack + os.sep):
            return self._send(400, {'error': '只能读包里的文件'})
        ext = os.path.splitext(full)[1].lower()
        ctype = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
                 '.webp': 'image/webp', '.wav': 'audio/wav', '.mp3': 'audio/mpeg',
                 '.ogg': 'audio/ogg'}.get(ext)
        if not ctype or not os.path.isfile(full):
            return self._send(404, {'error': '没有这个文件'})
        with open(full, 'rb') as fh:
            return self._send(200, fh.read(), ctype)

    def _preview_data(self, pid):
        pack = os.path.join(project_dir(pid), 'pack')
        man = read_json(os.path.join(pack, 'figure.json'), {})
        if not man:
            return self._send(404, {'error': '这个项目还没有 figure.json，先让 AI 写一份'})
        tex = os.path.join(pack, 'tex')
        files = sorted(os.listdir(tex)) if os.path.isdir(tex) else []
        walk = [f for f in files if re.match(r'^walk-\d+\.png$', f)]
        faces = [m.group(1) for m in (re.match(r'^face-(.+)\.png$', f) for f in files) if m]
        vocab = {w.get('id'): w for w in (man.get('vocab') or []) if isinstance(w, dict)}

        def zh(wid, fallback):
            w = vocab.get(wid) or {}
            n = names_of(w.get('names')) or {}
            return n.get('zh') or fallback

        snd = man.get('sounds') if isinstance(man.get('sounds'), dict) else {}
        return self._send(200, {
            'id': man.get('id'),
            'name': (names_of(man.get('name')) or {}).get('zh', ''),
            'about': (names_of(man.get('about')) or {}).get('zh', ''),
            'walk': walk,
            'faces': [{'id': f, 'name': zh(f, f)} for f in faces],
            'has': {'body': 'body.png' in files, 'sit': 'sit.png' in files,
                    'sleep': 'sleep.png' in files},
            'sounds': sorted(snd.keys()),
            'soundFiles': {k: (v or {}).get('file') for k, v in snd.items() if isinstance(v, dict)},
            'canWalk': bool((man.get('can') or {}).get('walk', True)),
        })

    def _thumb(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        pid = (q.get('id') or [''])[0]
        name = safe_filename((q.get('n') or [''])[0])
        full = os.path.join(project_dir(pid), 'raw', name)
        ext = os.path.splitext(name)[1].lower()
        ctype = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}.get(ext)
        if not os.path.isfile(full) or not ctype:
            return self._send(404, {'error': 'no such image'})
        with open(full, 'rb') as f:
            return self._send(200, f.read(), ctype)

    def do_POST(self):
        path = self.path.split('?')[0]
        if not self._local_only():
            return self._send(403, {'error': '只接受本机网页发来的 JSON 请求'})
        try:
            req = self._body()
        except BodyTooLarge as e:
            mb = int(str(e)) / 1048576
            return self._send(413, {'error': f'这次请求有 {mb:.0f} MB，超过上限。'
                                             f'大视频请先压小或切成几段，图片请先转成 png'})
        except Exception as e:
            return self._send(400, {'error': f'请求不是合法 JSON：{e}'})
        try:
            if path == '/api/project/create':
                return self._create(req)
            if path == '/api/project/upload':
                return self._upload(req)
            if path == '/api/project/video':
                return self._video(req)
            if path == '/api/project/main':
                return self._set_main(req)
            if path == '/api/config':
                return self._save_config(req)
            if path == '/api/ping':
                return self._ping(req)
            if path == '/api/ai/generate':
                return self._ai_generate(req)
            if path == '/api/validate':
                return self._validate(req)
            if path == '/api/fix':
                return self._fix(req)
            if path == '/api/clean-faces':
                return self._clean_faces(req)
            if path == '/api/autogen':
                return self._autogen(req)
            if path == '/api/package':
                return self._package(req)
            if path == '/api/open':
                return self._open(req)
            if path == '/api/delete':
                return self._delete(req)
            return self._send(404, {'error': 'not found'})
        except Exception as e:
            return self._send(500, {'error': f'{type(e).__name__}: {e}'})

    # -- project

    def _create(self, req):
        pid = (req.get('id') or '').strip()
        if not ID_RE.match(pid):
            return self._send(400, {'error': 'id 只能小写字母数字和横杠，1-32 位，例如 my-cat'})
        d = project_dir(pid)
        if os.path.isdir(d):
            return self._send(400, {'error': '这个 id 已经有项目了，换一个'})
        pack = os.path.join(d, 'pack')
        os.makedirs(os.path.join(d, 'raw'), exist_ok=True)
        shutil.copytree(TEMPLATE, pack)
        # 模板那三张示例表情是蓝色小生物，留在新项目里只会让你的宠物做表情时变身
        clean_template_faces(pack)
        man = read_json(os.path.join(pack, 'figure.json'), {})
        man['id'] = pid
        man['name'] = {'zh': req.get('name') or pid, 'en': pid}
        write_json(os.path.join(pack, 'figure.json'), man)
        write_json(os.path.join(d, 'project.json'), {
            'created': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
            'main': 'tex/body.png',
        })
        return self._send(200, {'ok': True, 'id': pid})

    def _upload(self, req):
        pid = req.get('id')
        d = project_dir(pid)
        if not os.path.isdir(d):
            return self._send(400, {'error': '项目不存在'})
        files = req.get('files') or []
        raw = os.path.join(d, 'raw')
        os.makedirs(raw, exist_ok=True)
        saved = []

        def next_walk_index():
            used = []
            for f in os.listdir(raw):
                m = re.match(r'^walk[-_ ]*f?(\d+)\.', f.lower())
                if m:
                    used.append(int(m.group(1)))
            return (max(used) + 1) if used else 0

        for item in files:
            slot = (item.get('slot') or '').strip().lower()
            name = safe_filename(item.get('name', 'image.png'))
            base, ext = os.path.splitext(name)
            ext = ext.lower()

            # 音效
            if slot == 'sound':
                if ext not in AUDIO_EXT:
                    saved.append({'name': name, 'error': f'音效只认 wav/mp3/ogg，这个是 {ext}'})
                    continue
                key = re.sub(r'[^a-z0-9-]', '', base.lower()) or 'sound'
                dest_name = f'sound_{key}{ext}'
                size = save_data_url(item['data'], os.path.join(raw, dest_name))
                saved.append({'name': dest_name, 'size': size, 'ok': True, 'slot': slot})
                continue

            if ext not in IMG_EXT:
                saved.append({'name': name, 'error': f'不支持的格式 {ext}，只认 png/jpg/jpeg/webp'})
                continue

            # 图片按分区改名，落盘时就带上用途
            if slot == 'body':
                dest_name = 'body' + ext
            elif slot == 'walk':
                dest_name = 'walk_f%02d' % next_walk_index() + ext
            elif slot.startswith('face'):
                key = slot.split(':', 1)[1] if ':' in slot else base.lower()
                key = re.sub(r'^face[-_ ]*', '', key.strip())
                key = FACE_ALIAS.get(key, key)
                if key not in EXPR_IDS:
                    saved.append({'name': name, 'error': f'不认识的表情 {key}'})
                    continue
                dest_name = f'face_{key}{ext}'
            elif slot == 'sit':
                dest_name = 'mode_sit' + ext
            elif slot == 'sleep':
                dest_name = 'mode_sleep' + ext
            else:
                dest_name = name

            size = save_data_url(item['data'], os.path.join(raw, dest_name))
            ok_img, why = image_ok(os.path.join(raw, dest_name))
            if not ok_img:
                try:
                    os.remove(os.path.join(raw, dest_name))
                except OSError:
                    pass
                saved.append({'name': name, 'error': why})
                continue
            saved.append({'name': dest_name, 'size': size, 'ok': True, 'slot': slot})
            if slot == 'body':
                ok, note = apply_main(d, dest_name)
                saved[-1]['main'] = ok
                saved[-1]['note'] = note
        # 上传完立刻同步进 tex / sounds，不用等下一步
        returned = sync_raw_textures(d)
        return self._send(200, {'ok': True, 'files': saved, 'synced': returned})

    def _set_main(self, req):
        """Copy a raw image into the pack as tex/body.png, letterboxed to 512x512."""
        pid = req.get('id')
        name = safe_filename(req.get('name', ''))
        d = project_dir(pid)
        ok, note = apply_main(d, name)
        if not ok:
            return self._send(400, {'error': note})
        return self._send(200, {'ok': True, 'note': note})

    def _video(self, req):
        pid = req.get('id')
        d = project_dir(pid)
        if not os.path.isdir(d):
            return self._send(400, {'error': '项目不存在'})
        name = safe_filename(req.get('name', 'clip.mp4'))
        data = req.get('data')
        try:
            count = int(req.get('count') or 6)
        except (TypeError, ValueError):
            count = 6
        count = max(2, min(12, count))
        cutout = (req.get('bg') or 'auto') != 'none'   # 背景和衣服颜色太像时可以关掉抠图，只裁边
        try:
            tol = max(4, min(60, int(req.get('tol') or 12)))
        except (TypeError, ValueError):
            tol = 12
        exe = ffmpeg_exe()
        if not exe:
            return self._send(400, {'error':
                '没找到 ffmpeg，视频抽不了帧。三种办法：\n'
                '1. 在终端跑 winget install Gyan.FFmpeg 装上，重开这个工具\n'
                '2. 装好后重启工具，这里会自动认出来\n'
                '3. 不想装：用剪映/微信截图，把走路的每一帧截成图片，直接拖进「走路帧」'})

        tmpdir = tempfile.mkdtemp()
        tmp = os.path.join(tmpdir, name)
        save_data_url(data, tmp)
        outdir = os.path.join(d, 'frames')
        os.makedirs(outdir, exist_ok=True)
        try:
            probe = subprocess.run([exe, '-i', tmp], capture_output=True, text=True, timeout=60)
            dur = 0.0
            m = re.search(r'Duration:\s(\d+):(\d+):(\d+\.\d+)', probe.stderr or '')
            if m:
                dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            if dur <= 0:
                dur = 4.0

            raw = os.path.join(d, 'raw')
            os.makedirs(raw, exist_ok=True)
            used = [int(x) for x in re.findall(r'^walk[-_ ]*f?(\d+)\.', '\n'.join(os.listdir(raw)), re.M)]
            start = (max(used) + 1) if used else 0

            made, skipped = [], []
            for i in range(count):
                at = dur * (i + 0.5) / count
                shot = os.path.join(tmpdir, 'f%02d.png' % i)
                subprocess.run([exe, '-y', '-ss', f'{at:.2f}', '-i', tmp, '-frames:v', '1', shot],
                               capture_output=True, timeout=120)
                if not os.path.isfile(shot) or os.path.getsize(shot) == 0:
                    skipped.append(i)
                    continue
                keep = os.path.join(outdir, f'{os.path.splitext(name)[0]}_{i + 1:02d}.png')
                shutil.copyfile(shot, keep)
                dest = os.path.join(raw, 'walk_f%02d.png' % (start + len(made)))
                ok, why = frame_to_walk(shot, dest, tol=tol, cutout=cutout)
                if ok:
                    made.append(os.path.basename(dest))
                else:
                    skipped.append(i)
                    keep_err = why
            if not made:
                return self._send(500, {'error': '一帧都没抽出来。视频可能没有画面轨道，或编码 ffmpeg 不认'})
            synced = sync_raw_textures(d)
            return self._send(200, {
                'ok': True, 'frames': made, 'duration': round(dur, 2),
                'skipped': skipped, 'synced': synced,
                'note': '抽好的帧已经当成走路帧用上了（去背景、裁到人、底部对齐）。'
                        '原来那 6 张原图留在项目的 frames 目录里，需要重来可以用。',
            })
        except Exception as e:
            return self._send(500, {'error': f'抽帧失败：{e}'})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _delete(self, req):
        pid = req.get('id')
        if req.get('confirm') != pid:
            return self._send(400, {'error': '要确认才能删：把 id 再填一遍'})
        d = project_dir(pid)
        if os.path.isdir(d):
            shutil.rmtree(d)
        return self._send(200, {'ok': True})

    def _open(self, req):
        """在资源管理器里打开某个文件夹。

        只允许打开工具自己的目录。以前还能传任意 path，等于给本机开了个
        「打开任意程序」的后门，网页里一句请求就能启动 exe。
        """
        kind = req.get('kind')
        pid = req.get('id')
        if kind == 'root':
            target = ROOT
        elif kind == 'dist':
            target = DIST
        elif pid:
            d = project_dir(pid)
            if not os.path.isdir(d):
                return self._send(400, {'error': '项目不存在'})
            sub = req.get('sub') or ''
            target = os.path.abspath(os.path.join(d, sub)) if sub else d
        else:
            return self._send(400, {'error': '要么给 kind，要么给项目 id'})
        base = os.path.abspath(ROOT)
        if not (os.path.abspath(target) == base or os.path.abspath(target).startswith(base + os.sep)):
            return self._send(400, {'error': '只能打开工具目录里的文件夹'})
        if not os.path.exists(target):
            return self._send(400, {'error': '路径不存在'})
        if os.name == 'nt':
            os.startfile(target)  # noqa: S606
        return self._send(200, {'ok': True})

    # -- config

    def _ping(self, req):
        """One tiny round trip, to prove the key and model name actually work."""
        cfg = read_config()
        if not cfg.get('key'):
            return self._send(400, {'error': '还没有填 API Key'})
        t0 = time.time()
        try:
            text = call_llm([{'role': 'user', 'content': '只回复两个字：在的'}], cfg, timeout=60)
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:400]
            return self._send(502, {'error': f'接口返回 {e.code}。检查地址、Key、模型名。\n{detail}'})
        except Exception as e:
            return self._send(502, {'error': f'连不上：{type(e).__name__}: {e}'})
        return self._send(200, {'ok': True, 'ms': int((time.time() - t0) * 1000),
                                'reply': (text or '').strip()[:40]})

    def _save_config(self, req):
        cfg = read_config()
        for key in ('base', 'model'):
            if req.get(key):
                cfg[key] = req[key].strip()
        if req.get('key'):
            cfg['key'] = req['key'].strip()
        if not cfg.get('base') or not cfg.get('model'):
            return self._send(400, {'error': '接口地址和模型名都要填'})
        write_config(cfg)
        return self._send(200, {'ok': True})

    # -- AI

    def _ai_generate(self, req):
        cfg = read_config()
        if not cfg.get('key'):
            return self._send(400, {'error': '先填 API Key'})
        pid = req.get('id')
        d = project_dir(pid)
        pack = os.path.join(d, 'pack')
        base_man = read_json(os.path.join(pack, 'figure.json'), {})
        assets = []
        raw_dir = os.path.join(d, 'raw')
        if os.path.isdir(raw_dir):
            assets = sorted(os.listdir(raw_dir))
        brief = (req.get('brief') or '').strip()
        if not brief:
            return self._send(400, {'error': '先写一句你想要的宠物长什么样'})

        user = f"""{SYSTEM_PROMPT}

宠物描述：
{brief}

已有素材：{', '.join(assets) if assets else '（还没上传图，按文字描述来）'}

现在只输出那个 JSON，不要解释，不要代码块。"""

        try:
            # 不用 system 角色：不少中转会把 system 换掉或让模型跑题
            text = call_llm([{'role': 'user', 'content': user}], cfg)
        except urllib.error.HTTPError as e:
            detail = e.read().decode('utf-8', 'replace')[:400]
            return self._send(502, {'error': f'模型返回 {e.code}：{detail}'})
        except Exception as e:
            return self._send(502, {'error': f'调用失败：{type(e).__name__}: {e}'})

        try:
            ai_man = extract_json(text)
        except Exception as e:
            return self._send(502, {'error': explain_no_json(text, e), 'raw': text[:800]})
        if not isinstance(ai_man, dict):
            return self._send(502, {'error': '模型返回的不是一个对象', 'raw': text[:800]})

        merged = manifest_from_ai(ai_man, pid, base_man)
        # 模型经常漏 kind、把 about 写成字符串，这里统一兜一遍
        merged, changes, dropped = fix_manifest(merged)

        # 词表是空的：再问一次，只问词表，模型这时通常能给对
        if not merged.get('vocab'):
            try:
                t2 = call_llm([
                    {'role': 'user', 'content': WORD_ONLY_PROMPT + '\n\n宠物描述：' + brief},
                ], cfg)
                o2 = extract_json(t2)
                m2 = manifest_from_ai(o2, pid, merged)
                m2, _, _ = fix_manifest(m2)
                if m2.get('vocab'):
                    merged['vocab'] = m2['vocab']
                    changes.append('第二次请求补上了词表')
            except Exception:
                pass

        if not merged.get('vocab'):
            merged['vocab'] = default_vocab()
            changes.append('模型两次都没给出能用的词，先用标准 15 个词顶上')
        elif not any(w.get('kind') == 'motion' for w in merged['vocab']):
            added = add_missing_motions(merged['vocab'])
            if added:
                changes.append('模型没给动作词，已补 ' + '、'.join(added))

        write_json(os.path.join(pack, 'figure.json'), merged)
        sync_raw_textures(d)
        errors, warnings, info = validate_pack(pack)
        return self._send(200, {
            'ok': True, 'manifest': merged, 'changes': changes, 'dropped': dropped,
            'errors': errors, 'warnings': warnings, 'words': info.get('words', 0),
        })

    # -- check / package

    def _fix(self, req):
        pid = req.get('id')
        if not pid:
            return self._send(400, {'error': '先选一个项目'})
        folder = os.path.join(project_dir(pid), 'pack')
        result = fix_pack(folder)
        if not result.get('ok'):
            return self._send(400, result)
        return self._send(200, result)

    def _clean_faces(self, req):
        pid = req.get('id')
        if not pid:
            return self._send(400, {'error': '先选一个项目'})
        folder = os.path.join(project_dir(pid), 'pack')
        moved = clean_template_faces(folder)
        errors, warnings, _ = validate_pack(folder)
        return self._send(200, {'ok': True, 'moved': moved,
                                'errors': errors, 'warnings': warnings})

    def _autogen(self, req):
        pid = req.get('id')
        if not pid:
            return self._send(400, {'error': '先选一个项目'})
        targets = req.get('targets')
        if not isinstance(targets, list) or not targets:
            targets = ['sounds', 'poses', 'walk']
        result = generate_missing(project_dir(pid), targets)
        if not result.get('ok') and result.get('error'):
            return self._send(400, result)
        return self._send(200, result)

    def _validate(self, req):
        pid = req.get('id')
        pack = os.path.join(project_dir(pid), 'pack')
        errors, warnings, info = validate_pack(pack)
        js = os.path.join(pack, 'figure.js')
        syntax = None
        node = shutil.which('node')
        if os.path.isfile(js) and node:
            tmpdir = tempfile.mkdtemp()
            try:
                tmp = os.path.join(tmpdir, 'check.mjs')
                shutil.copyfile(js, tmp)
                r = subprocess.run([node, '--check', tmp], capture_output=True, text=True, timeout=60)
                syntax = 'ok' if r.returncode == 0 else (r.stderr or '').strip()[:600]
            finally:
                shutil.rmtree(tmpdir, ignore_errors=True)
        if syntax and syntax != 'ok':
            errors.append('figure.js 有语法错误，Coopanion 加载会失败：' + syntax)

        # deeper check: load it like the figure frame does
        contract = None
        runner = os.path.join(HERE, 'tools', 'test_pack.mjs')
        if syntax == 'ok' and node and os.path.isfile(runner):
            try:
                r = subprocess.run([node, runner, pack], capture_output=True, text=True, timeout=90)
                contract = r.stdout.strip()
                if r.returncode != 0:
                    errors.append('形象包加载测试没通过，看下面的输出')
            except Exception as e:
                warnings.append(f'加载测试没跑起来：{e}')

        return self._send(200, {
            'ok': not errors, 'errors': errors, 'warnings': warnings,
            'syntax': syntax, 'contract': contract, 'info': info,
        })

    def _package(self, req):
        pid = req.get('id')
        pack = os.path.join(project_dir(pid), 'pack')
        errors, _, _ = validate_pack(pack)
        if errors and not req.get('force'):
            return self._send(400, {'error': '还有错误，先修好再打包', 'errors': errors})
        man = read_json(os.path.join(pack, 'figure.json'), {})
        ver = str(man.get('version') or '1.0.0')
        os.makedirs(DIST, exist_ok=True)
        out = os.path.join(DIST, f'{pid}-{ver}.zip')
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
            for dirpath, dirnames, files in os.walk(pack):
                dirnames[:] = [x for x in dirnames if x not in ('raw', '_unused', '.git', '__pycache__')]
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, pack).replace('\\', '/')
                    z.write(full, rel)
        return self._send(200, {'ok': True, 'zip': out, 'size': os.path.getsize(out)})


def frame_to_walk(src, dest, size=512, tol=12, cutout=True):
    """把视频里抽出来的一帧变成能用的贴图：去背景、裁到人、底部对齐。

    视频帧没有透明通道，直接拿来当贴图就是一块带背景的方形图。
    这里从画面四边往里灌同色，把背景清成透明，再裁到人物再放大对齐。
    """
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except Exception:
        return False, '没装 Pillow，抽出来的帧只能原样用'
    try:
        im = Image.open(src)
    except Exception as e:
        return False, f'这一帧解不开：{e}'

    has_alpha = im.mode in ('RGBA', 'LA') and im.convert('RGBA').split()[-1].getextrema()[0] < 250
    if has_alpha or not cutout:
        rgba = im.convert('RGBA')
    else:
        im = im.convert('RGB')
        w, h = im.size
        seed = (255, 0, 255)
        # 四角 + 四条边中点 + 两条边上再各取一点，背景有渐变也能灌干净
        for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
                   (w // 2, 0), (w // 2, h - 1), (0, h // 2), (w - 1, h // 2),
                   (w // 4, 0), (3 * w // 4, h - 1)):
            try:
                ImageDraw.floodfill(im, xy, seed, thresh=tol)
            except Exception:
                pass
        r, g, b = im.split()
        hit = ImageChops.multiply(
            ImageChops.multiply(r.point(lambda v: 255 if v >= 250 else 0),
                                g.point(lambda v: 255 if v <= 5 else 0)),
            b.point(lambda v: 255 if v >= 250 else 0))
        alpha = ImageChops.invert(hit).filter(ImageFilter.MinFilter(3))  # 收 1 像素，去紫边
        rgba = im.convert('RGBA')
        rgba.putalpha(alpha)

    bb = rgba.split()[-1].getbbox()
    if not bb:
        return False, '这一帧去背景后什么也没剩下，可能背景和人物颜色太接近'
    rgba = rgba.crop(bb)
    rgba.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    canvas.alpha_composite(rgba, ((size - rgba.width) // 2, size - rgba.height))
    parent = os.path.dirname(dest)
    if parent:
        os.makedirs(parent, exist_ok=True)
    canvas.save(dest, 'PNG')
    return True, ''


def fit_png(src, dest, size):
    """Center a picture on a transparent square canvas. Needs Pillow; says so when missing."""
    try:
        from PIL import Image
    except Exception:
        return False, 'no Pillow'
    try:
        im = Image.open(src).convert('RGBA')
        im.thumbnail((size, size), Image.LANCZOS)
        canvas = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        canvas.paste(im, ((size - im.width) // 2, size - im.height), im)
        canvas.save(dest, 'PNG')
        return True, 'fit to square'
    except Exception as e:
        return False, str(e)


class LocalServer(ThreadingHTTPServer):
    # 默认的 allow_reuse_address 在 Windows 上会让两个程序抢同一个端口而不报错，
    # 请求随机落到别人那里。关掉它，端口被占就直接失败，好往后换一个。
    allow_reuse_address = False


def bind_server():
    """找一个真正空闲的端口。8848 常被别的工具占用，占住了就往后挪。"""
    last = None
    for i in range(0, 14):
        port = PORT + i
        try:
            return LocalServer(('127.0.0.1', port), Handler), port
        except OSError as e:
            last = e
    raise SystemExit(f'8848-8861 全被占用，起不来：{last}')


def main():
    os.makedirs(PROJECTS, exist_ok=True)
    os.makedirs(DIST, exist_ok=True)
    srv, port = bind_server()
    url = f'http://127.0.0.1:{port}/'
    env = env_report()
    print('=' * 56)
    print('  Pet Pack Studio for Coopanion')
    print(f'  {url}')
    print(f'  Python {env["python"]}  {env["exe"]}')
    print(f'  Pillow {env["pillow"] or "缺"}   ffmpeg {env["ffmpeg"] or "缺"}   node {env["node"] or "缺"}')
    if not env['pillow']:
        print('  ! 没装 Pillow：表情生成、视频抠图、缩略图都用不了')
        print('    装一个： python -m pip install pillow')
    if not env['ffmpeg']:
        print('  ! 没装 ffmpeg：视频抽帧用不了（可以用截图代替）')
    print('  关掉这个窗口就停止服务')
    print('=' * 56)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start() if '--no-browser' not in sys.argv else None
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
