# 安装说明 · Installation

> English quick version is at the bottom.

---

## 一分钟版

已经装好 Python 3.10+ 的话，执行这三步就够：

```bash
git clone https://github.com/qq1402094463/coopanion-pet-pack-studio.git
cd coopanion-pet-pack-studio
python -m pip install -r requirements.txt
```

然后 Windows 双击 `start.bat`，macOS / Linux 执行 `./start.sh`。
浏览器会自己打开。

---

## 一、装 Python

这个工具是用 Python 写的。没有 Python 就跑不起来。

### Windows

1. 打开 <https://www.python.org/downloads/>，下载 3.10 或更新版本。
2. 运行安装包。
3. **第一页最下面勾上「Add python.exe to PATH」。** 这一步漏了会找不到命令。
4. 点 Install Now。

装完打开「命令提示符」验证：

```bat
python --version
```

能看到 `Python 3.12.x` 之类就对了。

> 从微软商店装的 Python 也能用，但有时会跑到 WindowsApps 里搞出路径问题。
> 官方安装包最稳。

### macOS

```bash
brew install python@3.12
```

或者去 <https://www.python.org/downloads/macos/> 下安装包。

### Linux

```bash
sudo apt install python3 python3-pip python3-venv    # Debian / Ubuntu
sudo dnf install python3 python3-pip                 # Fedora
sudo pacman -S python python-pip                     # Arch
```

## 二、装依赖

只有一件：Pillow。它负责图片处理。

```bash
cd coopanion-pet-pack-studio
python -m pip install -r requirements.txt
```

装不上就试镜像：

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

> `start.bat` 和 `start.sh` 会自己检查 Pillow。缺了它会自动装一次。
> 手动装一遍更稳，因为你能看到报错。

## 三、启动

### Windows

双击项目根目录的 **`start.bat`**。

会弹一个黑色命令行窗口。窗口里会打印：

```
========================================================
  Pet Pack Studio for Coopanion
  http://127.0.0.1:8848/
  关掉这个窗口就停止服务
========================================================
  Pillow 10.4.0  ·  ffmpeg 有  ·  node v22
```

最后一行是环境体检。三项都正常就说明功能全开。

### macOS / Linux

```bash
chmod +x start.sh     # 第一次要加执行权限
./start.sh
```

### 打开页面

浏览器会自动打开 `http://127.0.0.1:8848/`。
没自动开就手动复制这个地址。

**这个窗口一直开着，工具才在跑。** 关掉窗口服务就停了。

## 四、可选组件

### ffmpeg（要从视频抽走路帧才需要）

没有它也能用。只是「上传视频抽帧」那个按钮会报错。

Windows：

```bat
winget install Gyan.FFmpeg
```

macOS：

```bash
brew install ffmpeg
```

Linux：

```bash
sudo apt install ffmpeg
```

装完**重开**命令行窗口，再启动工具。不然找不到新装的东西。

### Node 18+（只有跑测试脚本才需要）

`studio/tools/test_pack.mjs` 模拟 Coopanion 装载你的包。
不跑测试不需要 Node。

`packs.ts` 的校验逻辑我是照着官方运行时对的，脚本只是帮你再确认一遍贴图能不能选中。

## 五、第一次打开看到什么

页面顶部右侧有三个按钮：

- **接口已配置** / **接口未配置** —— 大模型接口状态
- **预览效果** —— 打开预览页
- **打开工具文件夹** —— 在资源管理器里打开项目根目录

左边是项目列表。中间从上到下是四个步骤。

界面里每个按钮的作用，见 [功能说明](功能说明.md)。

> 如果顶部出现黄色提示条，说明缺 Pillow 或 ffmpeg。
> 点提示条右边会告诉你装什么。

## 六、配置存在哪

大模型接口的地址、Key、模型名，存成一个 json。位置：

| 系统 | 路径 |
|---|---|
| Windows | `%LOCALAPPDATA%\PetPackStudio\ai-config.json` |
| macOS | `~/PetPackStudio/ai-config.json` |
| Linux | `~/PetPackStudio/ai-config.json` |

**故意不放在项目目录里。** 因为这个文件夹常常会被 AI 工具整个扫描，
Key 明文放在里面就等于写进提示词里了。

也可以用环境变量：

```bash
export PETSTUDIO_KEY=sk-xxxx
```

环境变量的优先级最高。

## 七、端口被占了怎么办

默认从 8848 开始找。被占就试 8849、8850，最多往后 14 个。
窗口标题栏下面会打印实际用的地址。

如果 14 个都被占（很少见），关掉占端口的程序再启动。
Coopanion 自己的形象包工具也可能用 8848，两个同时开就会撞。

## 八、卸载

1. 关掉命令行窗口。
2. 删掉整个项目文件夹。
3. 删掉 `%LOCALAPPDATA%\PetPackStudio\`（里面有你的 Key）。
4. 如果装了 Pillow 想清掉：`python -m pip uninstall pillow`。

**装进 Coopanion 的形象包不会跟着删。** 那是 Coopanion 的事。
去 Coopanion 的 `data/figures/` 里删，或者在「装扮」页里删。

## 九、装不上怎么办

| 现象 | 原因 | 怎么办 |
|---|---|---|
| `python 不是内部或外部命令` | 装 Python 时没勾 PATH | 重装一遍勾上，或手动把 Python 目录加进 PATH |
| `No module named PIL` | Pillow 没装 / 装到别的 Python 里 | `python -m pip install pillow`，用同一个 `python` |
| `pip 不是内部或外部命令` | 用了 `pip` 而不是 `python -m pip` | 一律用 `python -m pip` |
| 页面打不开，窗口一闪就没 | 启动报错 | 用命令行手动跑 `python studio/server.py`，看完整报错 |
| 页面能开但图片全是灰块 | 素材没进 `raw/` | 回「功能说明」第二步重新上传 |
| `Address already in use` | 端口被占 | 会自动换端口；全占了就关掉占用的程序 |
| 中文文件名乱码 | 命令行编码不对 | `start.bat` 里有 `chcp 65001`，用脚本启动就行 |

更多运行期报错见 [06-常见错误对照表](06-常见错误对照表.md)。

---

# Installation (English)

**Requirements:** Python 3.10+, Pillow. Optional: ffmpeg (video frame extraction), Node 18+ (test script).

```bash
git clone https://github.com/qq1402094463/coopanion-pet-pack-studio.git
cd coopanion-pet-pack-studio
python -m pip install -r requirements.txt
```

- **Windows:** double-click `start.bat`.
- **macOS / Linux:** `chmod +x start.sh && ./start.sh`.

The browser opens `http://127.0.0.1:8848/` (it walks forward if the port is busy).
Keep the console window open — closing it stops the server.

The startup banner prints `Pillow / ffmpeg / node`. Anything missing is listed there, and a yellow
banner appears at the top of the page. Missing Pillow will not crash the tool, but expression
generation, video cutout, thumbnails and bottom alignment will silently stop working.

Your model endpoint, key and model name are stored at:

- Windows: `%LOCALAPPDATA%\PetPackStudio\ai-config.json`
- macOS / Linux: `~/PetPackStudio/ai-config.json`

They are deliberately kept out of the project folder, because that folder is often scanned whole
by AI coding agents. `PETSTUDIO_KEY` overrides the stored key.

To uninstall: close the console, delete the project folder and the config folder above.
Packs you already imported stay inside Coopanion.
