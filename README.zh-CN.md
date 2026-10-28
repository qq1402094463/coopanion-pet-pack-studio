# Pet Pack Studio · Coopanion 形象包制作台

**用你自己的画，做出 [Coopanion](https://github.com/Pal-AI-Lab/Coopanion) 形象包。不用手写 `figure.json`，也不用碰 `figure.js`。**

![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![依赖](https://img.shields.io/badge/只依赖-Pillow-blue)
![许可](https://img.shields.io/badge/license-MIT-green)
![形象接口](https://img.shields.io/badge/Coopanion%20figure%20API-2-7c9cff)

[English](README.md) · [安装说明](INSTALL.md) · [功能说明](docs/功能说明.md) · [格式规范](docs/02-形象包格式规范.md)

![制作台界面](docs/images/studio.png)

---

## 这是什么

形象包决定桌宠长什么样。默认是 Coo，导入一个包就换成你的角色。

一个包是一个文件夹。里面有清单 `figure.json`、渲染代码 `figure.js`、贴图和音效。
想让它被顺利导入，得同时满足一堆规则。词 id、`kind` 取值、贴图尺寸、音效名，还有
`draw(petG, face, frame)` 的参数约定，都要对。

这个工具在你自己机器上跑一个网页。它负责建目录、把素材放进对应分区、
写清单、按真实导入规则校验、不装进 Coopanion 也能预览、最后打成 zip。

整个过程不联网上传任何东西。只有你想让大模型写清单时，才会连你自己填的接口。

## 能做什么

| | |
|---|---|
| **按分区上传** | 主图、走路帧、表情图、坐姿、睡姿、音效。拖到卡片上就行，名字和尺寸它改好。 |
| **一张全身图出一个宠物** | 自动生成 6 帧走路、坐姿、睡姿，还有 20 条音效。只要一张 `body.png`。 |
| **脸部小图表情** | `facegen.py` 只重画眼睛和嘴。表情能贴在每一帧呼吸动画上，身体不闪。 |
| **视频抽走路帧** | 丢一段手机视频进去。自动抽帧、去背景、裁边、贴底边。容差可调，也能关掉抠图。 |
| **AI 写清单** | 任何 OpenAI 兼容接口。填地址、Key、模型名，它吐出 `figure.json`。 |
| **一键修复** | 修最常见的退包原因：`about` 写成字符串、缺 `kind`、英文别名撞 id、`entry` 写错。改之前先备份。 |
| **真规则校验** | 跟官方导入器同一套：id 格式、保留 id、128 MB、2048 个文件、单文件 16 MB、清单 256 KB。 |
| **预览** | 站、走、坐、睡、18 个表情、三种背景、音效试听。不用装进 Coopanion。 |
| **打包** | 一个 zip，目录结构正确，`raw/` 不会混进去。 |

## 三步跑起来

```bash
git clone https://github.com/<you>/coopanion-pet-pack-studio.git
cd coopanion-pet-pack-studio
python -m pip install -r requirements.txt
```

Windows 双击 **`start.bat`**。macOS / Linux 用 `./start.sh`。

网页会自己在 `http://127.0.0.1:8848/` 打开。端口被占它会往后找。

然后在页面里：

1. **第一步** 填模型接口。只想手写清单可以跳过。
2. **第二步** 新建项目，丢进 `body.png`，点「一键补齐：走路 + 坐睡 + 20 音效」。
3. **第三步** 用一句话描述你的角色，让 AI 写 `figure.json`。
4. **第四步** 点「检查这一包」，再点「打包成 zip」。
5. 随时点「预览效果」，看它走起来什么样。

装进 Coopanion：**装扮 → 导入形象包 → 选这个 zip**。
同一个 id 再导入一次，它会问你要不要替换。

## 两条路

**A. 逐帧做（质量最好）**
在动画软件里导出每个动作的每一帧。分别丢进走路、坐、睡、表情分区。
官方角色就是这么做的。

**B. 一张图加生成（最快）**
给一张透明背景的 `body.png`。
`autogen.py` 合成走路、坐姿、睡姿和 20 条音效。腿绕髋关节转，上身反向位移，落地时压扁。
`facegen.py` 重画眼睛和嘴，得到一套表情。

所以一张画就能走、能坐、能睡、能眨眼。
但合成出来的走路是纸片人走路。想要自然，走 A 路。

> 这个工具的 AI 只写清单，不画图。
> 要生图得用别的模型，再把结果丢进分区。提示词在 [docs/07](docs/07-素材清单与生图提示词.md)。

## 环境要求

| | |
|---|---|
| Python | 3.10 或更新 |
| Pillow | 必须。表情生成、抠图、缩略图、贴底边都靠它 |
| ffmpeg | 可选。只有视频抽帧要用 |
| Node 18+ | 可选。只有跑 `studio/tools/test_pack.mjs` 要用 |

启动时黑窗口会打印 `Pillow / ffmpeg / node` 三项。
缺东西时网页顶部会出现黄条。别忽略它，缺了功能是静默变差的。

## 目录

```
start.bat / start.sh        启动脚本
requirements.txt
studio/
  server.py                 服务端。校验、修清单、打包都在这里
  autogen.py                一张图生成走路、坐、睡、20 条音效
  facegen.py                一张图生成脸部表情小图
  web/index.html            制作台页面
  web/preview.html          预览页
  templates/minimal-pack/   一个能过的空白包
  tools/                    占位图生成、运行时模拟测试
docs/
  00-开始读我.md             从这里开始
  01-制作流程总览.md         完整流程
  02-形象包格式规范.md       格式规则，从运行时扒出来的
  03/04-AI提示词-*.md        指挥 AI 做包用的提示词
  05-测试验收清单.md         验收清单
  06-常见错误对照表.md       报错、原因、怎么改
  07-素材清单与生图提示词.md  要做什么图、提示词怎么写
  08-体检报告-2026-10.md     这一版的体检记录
  功能说明.md                每个按钮干什么
  开发说明.md                代码结构和 HTTP 接口
```

## 已知局限

- **合成走路是程序的。** 两条腿交替，不是从视频描出来的。要自然就去拍视频抽帧。
- **不带 AI 生图。** AI 那一步只写 `figure.json`。
- **表情是画出来的。** `facegen.py` 在 512 像素贴图上重画眼睛和嘴。近看不如图手绘。
- **只有正面。** 形象包不能真转身。运行时是把整组水平翻转。
- **只在 Windows 上测过。** 代码是标准库加 Pillow，理论上 macOS/Linux 能跑，但没实测。
- **只监听本机。** 服务只绑 `127.0.0.1`。外来的 Host 和 Origin 会被拒。

## 参与

欢迎提 issue 和 PR。最缺的是真实走路帧、成套表情，还有 macOS/Linux 的实测反馈。
代码结构和接口见 [docs/开发说明.md](docs/开发说明.md)。

## 许可

MIT，见 [LICENSE](LICENSE)。

这是独立社区工具，跟 Pal-AI-Lab 和 Coopanion 项目没有隶属关系。
名字只用来说明格式兼容。你导入的素材版权由你负责。
