// Static check: load a pack's figure.js the way the figure frame loads it,
// with fake kit/host/DOM, and make sure it returns a usable body.
// Catches the mistakes a syntax check misses: wrong export name, sync factory,
// missing step/layout/do, a draw() that throws.
//
// usage: node tools/test_pack.mjs <pack dir>

import { pathToFileURL } from 'node:url';
import { readFileSync, existsSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { copyFileSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';

const dir = resolve(process.argv[2] || 'templates/minimal-pack');
const manifest = JSON.parse(readFileSync(join(dir, 'figure.json'), 'utf8'));

const fail = [];
const warn = [];
const ok = (m) => console.log('  ok   ' + m);
const bad = (m) => { fail.push(m); console.log('  FAIL ' + m); };
const note = (m) => { warn.push(m); console.log('  warn ' + m); };

console.log(`pack: ${dir}`);
console.log(`id: ${manifest.id}  entry: ${manifest.entry}  export: ${manifest.export}`);

// a DOM stub: enough for figure.draw() to run
function makeEl(tag) {
  const attrs = {};
  return {
    tagName: tag,
    attrs,
    children: [],
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return attrs[k]; },
    appendChild(c) { this.children.push(c); return c; },
    removeChild(c) { this.children = this.children.filter((x) => x !== c); },
    get innerHTML() { return ''; },
    set innerHTML(v) { if (typeof v !== 'string') throw new TypeError('innerHTML needs a string'); },
    ownerDocument: {
      createElementNS: (_ns, t) => makeEl(t),
      createElement: (t) => makeEl(t),
    },
  };
}

const bodyMethods = ['step', 'layout', 'do'];
let body = null;

try {
  // import as .mjs so node treats a bare .js as a module regardless of package.json
  const tmp = mkdtempSync(join(tmpdir(), 'pack-check-'));
  const abs = join(tmp, 'entry.mjs');
  copyFileSync(join(dir, manifest.entry), abs);
  const mod = await import(pathToFileURL(abs).href);
  rmSync(tmp, { recursive: true, force: true });

  if (typeof mod[manifest.export] !== 'function') {
    bad(`figure.js 没有导出 ${manifest.export}()。figure.json 的 export 要写函数名`);
    throw new Error('stop');
  }
  ok(`导出 ${manifest.export}() 存在`);

  const petG = makeEl('g');
  let figureOpts = null;

  const kit = {
    createBody(host, opts) {
      figureOpts = opts;
      return {
        step() {}, layout() { return {}; }, do() {},
        words: [],
      };
    },
  };
  const host = {
    root: makeEl('div'),
    bounds: () => ({ W: 900, H: 500, floorY: 480, S: 1 }),
    emit: () => {},
    sound: () => {},
    start: { x: 400, facing: 1, skin: {} },
  };

  const result = mod[manifest.export]('about:blank#/', {
    kit,
    host,
    asset: (p) => join(dir, p),
    loadImage: async (url) => {
      if (!url || !url.length) throw new Error('empty url');
      // 这里必须真的看磁盘：假装成功的话，包里没有的图也会被当成有
      if (!/^https?:/.test(url) && !existsSync(url)) throw new Error('missing: ' + url);
      return { width: 512, height: 512 };
    },
    model: manifest.model ? JSON.parse(readFileSync(join(dir, manifest.model), 'utf8')) : undefined,
    scheme: '',
  });

  body = result instanceof Promise ? await result : result;
  ok(result instanceof Promise ? '工厂是 async，已 await' : '工厂是同步的，也可以');

  if (!body || typeof body !== 'object') {
    bad('工厂没有返回 body 对象');
  } else {
    const missing = bodyMethods.filter((m) => typeof body[m] !== 'function');
    if (missing.length) bad(`body 少了必须的方法：${missing.join(', ')}`);
    else ok('body 有 step / layout / do');

    // run one frame through the figure's draw()
    if (figureOpts && figureOpts.figure) {
      const fig = figureOpts.figure;
      if (typeof fig.draw !== 'function') bad('opts.figure.draw 不是函数');
      else {
        try {
          fig.draw(petG, 'happy', {
            face: 'happy', mode: 'walk', modeT: 0.2, t: 1.23, talk: 0,
            drowse: 0, sit: 0, status: null, gesture: null,
          });
          const imgs = petG.children.filter((c) => c.tagName === 'image');
          if (!imgs.length) bad('draw() 没有往 petG 里加 <image>，宠物会看不见');
          else {
            const a = imgs[0].attrs;
            ok(`draw() 画了一张图：${a.width}x${a.height} href=${a.href}`);
            if (!a.href) bad('<image> 没有 href，图加载不出来');
            if (!a.preserveAspectRatio) note('建议给 <image> 加 preserveAspectRatio，否则非方图会被拉变形');
          }
          // second frame: must not throw when the element already exists
          fig.draw(petG, 'love', { face: 'love', mode: 'idle', modeT: 0, t: 2, talk: 0, drowse: 0, gesture: null });
          const count = petG.children.filter((c) => c.tagName === 'image').length;
          if (count > 1) note(`draw() 每帧都在加新元素（现在 ${count} 个），会拖慢并可能闪烁`);
          else ok('第二帧复用同一个元素，不会闪');

          // 行为抽样：不同状态下到底换没换图
          const node = petG.children.find((c) => c.tagName === 'image');
          const nameOf = (u) => String(u || '(空)').split(/[\\/]/).pop();
          const shot = (frame) => {
            fig.draw(petG, null, { talk: 0, drowse: 0, sit: 0, modeT: 0, gesture: null, ...frame });
            return nameOf(node.attrs.href);
          };

          const idle = shot({ mode: 'idle', face: 'neutral', t: 0.5 });
          const happy = shot({ mode: 'idle', face: 'happy', t: 0.5 });
          if (happy !== idle) ok(`换表情会换图：${idle} → ${happy}`);
          else note('换表情没换图。有 tex/face-happy.png 时应该换');

          const w1 = shot({ mode: 'walk', face: 'neutral', t: 0.00 });
          const w2 = shot({ mode: 'walk', face: 'neutral', t: 0.10 });
          const w3 = shot({ mode: 'walk', face: 'neutral', t: 0.20 });
          const wset = new Set([w1, w2, w3]);
          if (wset.size >= 2) ok(`走路会换帧：${[...wset].join(' / ')}`);
          else note('走路没有换帧。放 tex/walk-00.png 这类序列帧才会动');

          const sit = shot({ mode: 'sit', face: 'neutral', t: 1 });
          const sleep = shot({ mode: 'sleep', face: 'neutral', t: 1 });
          if (sit !== idle) ok(`坐下用专属图：${sit}`);
          else note('没有 tex/sit.png，坐下沿用站姿');
          if (sleep !== idle) ok(`睡觉用专属图：${sleep}`);
          else note('没有 tex/sleep.png，睡觉沿用站姿');
        } catch (e) {
          bad('draw() 抛错：' + e.message);
        }
      }
      if (typeof fig.extent !== 'undefined') {
        const ex = fig.extent;
        if (!Array.isArray(ex) || ex.length !== 4) bad('figure.extent 要是 [x0,y0,x1,y1]');
        else ok(`figure.extent = [${ex.join(', ')}]`);
      }
    } else {
      note('没有给 kit 传 opts.figure，套件不知道画什么');
    }
  }
} catch (e) {
  if (e.message !== 'stop') bad('加载失败：' + e.message);
}

console.log('');
if (fail.length) {
  console.log(`结果：${fail.length} 个问题必须改`);
  process.exit(1);
}
console.log(`结果：通过${warn.length ? `（${warn.length} 条提示）` : ''}`);
