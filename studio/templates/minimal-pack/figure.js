/**
 * Coopanion 形象包入口 —— 会换帧、会换表情的那种。
 *
 * 官方 kit 负责物理和动作（走、跳、坐、睡、被拎起、粒子、阴影、朝向翻转），
 * 这里只负责一件事：根据当前状态，把对应的一张图画到 256×256 的格子里。
 *
 * 认这些图，有就用，没有就退回上一级：
 *   tex/body.png        主图，必填
 *   tex/face-<id>.png   表情图，可选，id 见下面的 EXPRS
 *   tex/walk-00.png …   走路序列帧，可选，有几张用几张
 *   tex/sit.png         坐姿，可选
 *   tex/sleep.png       睡姿，可选
 *
 * 坐标系：256×256，脚在 y=256，默认朝右。
 * 朝向翻转由 kit 做（它会翻转整个 group），这里不要再翻一次。
 */

const EXPRS = [
  'neutral', 'happy', 'wink', 'love', 'shy', 'surprised', 'angry', 'sad',
  'sleepy', 'thinking', 'smug', 'pout', 'worried', 'determined',
  'flustered', 'scared', 'excited', 'cry', 'confused',
];

/** 走路序列帧最多试几张。少几张也照跑，只是循环更短。 */
const WALK_FRAMES = 8;
/** 每秒过几格。跑起来比走路快。 */
const FPS_WALK = 10;
const FPS_RUN = 16;
const FPS_DANCE = 12;

const SVG_NS = 'http://www.w3.org/2000/svg';

/** 建一个"只会画"的 figure，交给 kit。 */
function makeFigure(urls) {
  let img = null;
  let curUrl = '';

  /** 当前该显示哪张图。返回空串表示还没有可用的图。 */
  function pick(frame) {
    const mode = frame.mode;
    const t = frame.t || 0;

    // 睡觉、坐下：有专属图就用专属图
    if (mode === 'sleep' && urls.sleep) return urls.sleep;
    if (mode === 'sit' && urls.sit) return urls.sit;

    // 走 / 跑 / 跳舞：按时间切序列帧，这是"活起来"的关键
    if (urls.walk.length) {
      if (mode === 'walk') return urls.walk[Math.floor(t * FPS_WALK) % urls.walk.length];
      if (mode === 'run') return urls.walk[Math.floor(t * FPS_RUN) % urls.walk.length];
      if (mode === 'dance') return urls.walk[Math.floor(t * FPS_DANCE) % urls.walk.length];
    }

    // 其它情况：有表情图就用表情图
    const f = frame.face && urls.faces[frame.face];
    if (f) return f;

    return urls.main || '';
  }

  /** 走路时腿在动，上半身跟着颠；站着时轻轻晃。幅度都按 256 的格子算。 */
  function pose(frame) {
    const mode = frame.mode;
    const t = frame.t || 0;

    if (mode === 'walk' || mode === 'run' || mode === 'dance') {
      // 和换帧同一个频率，脚落地时才压低，看起来才不飘
      const w = mode === 'run' ? 22 : (mode === 'dance' ? 16 : 10.5);
      const ph = t * w;
      const bob = -Math.abs(Math.sin(ph)) * 6;
      const rot = Math.sin(ph) * 2.2;
      return `translate(0 ${bob.toFixed(2)}) rotate(${rot.toFixed(2)} 128 256)`;
    }

    if (mode === 'sleep') {
      // 睡着的呼吸：极慢，几乎看不出来，但就是这份"几乎看不出"让人不觉得是贴纸
      const k = 1 + Math.sin(t * 1.5) * 0.008;
      return `translate(0 0) scale(${k.toFixed(4)} ${k.toFixed(4)}) translate(${(128 * (1 - k)).toFixed(2)} ${(256 * (1 - k)).toFixed(2)})`;
    }

    if (mode === 'sit') {
      const rot = Math.sin(t * 1.2) * 0.7;
      return `translate(0 0) rotate(${rot.toFixed(2)} 128 256)`;
    }

    // 站着：只加一点点摇摆。呼吸和挤压是 kit 做的，这里再来一次会重
    const rot = Math.sin(t * 0.85) * 0.8;
    const bob = Math.sin(t * 0.85) * 1.2;
    return `translate(0 ${bob.toFixed(2)}) rotate(${rot.toFixed(2)} 128 256)`;
  }

  return {
    draw(petG, _face, frame) {
      // 元素只建一次，之后只改属性 —— 每帧重写 innerHTML 会让图反复重载、画面闪
      if (!img) {
        img = petG.ownerDocument.createElementNS(SVG_NS, 'image');
        img.setAttribute('x', '0');
        img.setAttribute('y', '0');
        img.setAttribute('width', '256');
        img.setAttribute('height', '256');
        // 等比缩放、底部对齐：任何比例的图都不会被拉变形
        img.setAttribute('preserveAspectRatio', 'xMidYMax meet');
        petG.appendChild(img);
      }

      const url = pick(frame);
      if (url && url !== curUrl) {
        img.setAttribute('href', url);
        curUrl = url;
      }

      img.setAttribute('transform', pose(frame));
    },

    /** 眼泪、气泡的位置，按 256 格子算 */
    get anchors() {
      return { tears: [[104, 140], [152, 140]], bubble: [128, 30] };
    },

    /** 身体占的框，kit 用它算阴影和命中 */
    get extent() {
      return [16, 8, 240, 256];
    },

    /** 点击判定圆：圆心和半径 */
    get hits() {
      return [[128, 136, 104]];
    },
  };
}

/**
 * 工厂函数。figure.json 的 export 必须写这个名字。
 * 这是 async 的：先把所有图预热好，第一帧就不会空着。
 */
export async function createMyPet(base, ctx) {
  const { kit, host, asset, loadImage } = ctx;

  const load = async (p) => {
    const url = asset(p);
    try {
      await loadImage(url);
      return url;
    } catch {
      return null; // 没有这张图，正常
    }
  };

  const main = await load('tex/body.png');

  // 表情图：没几张也能跑
  const faces = {};
  await Promise.all(EXPRS.map(async (name) => {
    const url = await load(`tex/face-${name}.png`);
    if (url) faces[name] = url;
  }));

  // 走路序列帧：从 00 开始，遇到第一张缺的就停
  const walk = [];
  for (let i = 0; i < WALK_FRAMES; i += 1) {
    const url = await load(`tex/walk-${String(i).padStart(2, '0')}.png`);
    if (!url) break;
    walk.push(url);
  }

  const sit = await load('tex/sit.png');
  const sleep = await load('tex/sleep.png');

  const urls = { main, faces, walk, sit, sleep };

  const start = host.start || {};
  const body = kit.createBody(
    { ...host, start },
    { figure: makeFigure(urls) }
  );

  return body;
}
