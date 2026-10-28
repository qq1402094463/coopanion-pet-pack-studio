# 参与贡献

欢迎 PR。这个项目不需要你先读懂全部代码。

---

## 最有用的几种贡献

### 1. 真实素材

比代码更缺的是素材。

- **一段正面走路的视频**，3 秒以上，背景干净。抽出来的帧比程序合成了自然得多
- **成套表情图**，18 个表情每个一张
- **侧身、背面的图**。现在只能整组水平翻转，做不了真转身

素材请确认你有权分享。

### 2. macOS / Linux 实测

代码只用了标准库加 Pillow，理论上跨平台。
但只在 Windows 上跑过。装上跑一遍，有问题提 issue 就是贡献。

### 3. 报错信息

遇到 `导不进去` 的时候，把这几样贴上来：

- Coopanion 弹出的原文
- `检查这一包` 输出的红字
- `figure.json` 的相关片段

比「用不了」有用一百倍。

### 4. 代码

改之前先看 [开发说明](开发说明.md)。里面有代码地图和接口表。

---

## 提 PR 之前

跑一下这三条：

```bash
python -m py_compile studio/server.py studio/autogen.py studio/facegen.py
python -c "import sys;sys.path.insert(0,'studio');import server;print(server.validate_pack('studio/templates/minimal-pack'))"
node --check studio/templates/minimal-pack/figure.js
```

最后一条如果报 `export` 语法错，说明 `node --check` 按 CommonJS 解析了。
复制成 `.mjs` 再查：

```bash
cp studio/templates/minimal-pack/figure.js /tmp/f.mjs && node --check /tmp/f.mjs
```

## 代码约定

- 中文注释，讲为什么，不讲是什么
- 报错信息写给人看，带上怎么改
- 不引第三方依赖。Pillow 是唯一的例外
- 前端不引框架。一个 HTML 文件带内联 CSS 和 JS
- 新功能要能在没有大模型、没有 ffmpeg 的情况下降级运行

## 不要提交的东西

`.gitignore` 已经挡住这些。手动检查一遍：

- `projects/` —— 你的形象包工程
- `dist/` —— 打出来的 zip
- `studio/ai-config.json` —— 有明文 Key

## 提 issue

带上系统版本、Python 版本、复现步骤。
启动横幅那三行（Pillow / ffmpeg / node）也贴上。
