# AI 提示词 · AutoClaw 5.3 专用

这份是给 AutoClaw 5.3 用的。
每个代码块都可以整段复制，直接粘进去。

---

## 怎么用

三种用法，按你的情况选一种。

| 用法 | 适合 | 积分消耗 |
|---|---|---|
| A. 一次跑完 | 想省事，信任 AI | 多，一次把全流程做完 |
| B. 分步跑 | 想控制质量，每步检查 | 中，能及时叫停 |
| C. 只做单点 | 卡在某一步 | 少 |

**A 和 B 不要混着用。** 混用会让 AI 重复劳动，白烧积分。

---

## 用法 A · 一次跑完

先确保四件事，再粘：

1. `E:\AI\Coopanion的宠物制作工具` 已存在。
2. 你已经有一张主图，路径知道。
3. 工具的 API 已经配好（`studio\ai-config.json` 里有 key）。
4. 你已经在工具里建好了项目（有新项目 id）。

```
你要帮我做完一只 Coopanion 形象包，从素材到能导入，一次做完。

第一步，先读这三个文件。读不完不要动手：
E:\AI\Coopanion的宠物制作工具\docs\02-形象包格式规范.md
E:\AI\Coopanion的宠物制作工具\docs\01-制作流程总览.md
E:\AI\Coopanion的宠物制作工具\studio\templates\minimal-pack\figure.json

第二步，认清目录。所有产出都必须在 E:\AI\Coopanion的宠物制作工具 里面。
项目在 projects\<项目id>\pack\，打包输出在 dist\。

我的主图：<填你的图片完整路径>
项目 id：<填项目 id，小写字母数字横杠>
宠物设定：<两三句话，说外形、颜色、性格、动作习惯>

铁律，违反任何一条都算失败：
1. 绝对不要改 figure.js 的渲染逻辑。它是验证过的模板。只允许换 tex 里的图片。
2. 不要动 figure.json 的 manifest、api、entry、export 这四个字段，
   必须是 2、2、"figure.js"、"createMyPet"。
3. vocab 里的 id 只能用这些，不许发明：
   expression: neutral happy wink love shy surprised angry sad sleepy thinking smug pout worried determined flustered scared excited cry confused
   motion: stand jump hop look turn nod shake spin sit sleep dizzy walk run wave bow shiver flap dance
4. vocab 每一项的 names 里只用中文别名，不要写英文别名。
   英文别名很容易和 id 撞成重名，整包会被拒绝。
5. 中文别名不许含逗号、顿号、空格、【】<> 这些字符。
6. 所有 id 和别名全局不能重复。
7. 每改一次 figure.json，必须跑一次校验，通过才算完成：
   cd /d E:\AI\Coopanion的宠物制作工具\studio
   node tools\test_pack.mjs ..\projects\<项目id>\pack
8. 不确定的地方直接说不确定，不要编。编了会浪费时间。

第三步，按顺序做，每做完一步告诉我结果：
1) 把主图复制成 projects\<id>\pack\tex\body.png
2) 删掉 pack\tex\face-happy.png、face-love.png、face-sleepy.png
   这三张是模板自带的示例，画的是蓝色小生物，留着会让我的宠物出戏
3) 按我的设定改写 figure.json 的 name 和 about，写具体
4) 写 16 到 20 个 vocab 词，表情和动作都要有，每个带中文别名和 about
5) 跑校验，直到 0 错误
6) 打包，告诉我 zip 的完整路径

最后给我一份三行的验收清单，写清我在 Coopanion 里该看哪三件事。
```

---

## 用法 B · 分步跑

六步。每步一条，做完看结果再发下一条。

### B1 · 侦察

```
先只做侦察，不要改任何文件。

读这三个文件，然后回答我四个问题：
1. E:\AI\Coopanion的宠物制作工具\docs\02-形象包格式规范.md 里，
   figure.json 哪几个字段是必填的？列出字段名。
2. vocab 里每一项必须有哪几个字段？
3. 为什么英文别名容易导致整包被拒？
4. 内置包 coo 的 id 有什么特殊限制？

第四问的回答如果来自文件原文，指出是哪一节。

回答完就停，不要开始做包。
```

这一步花不了多少积分，但能确认 AI 真的读了文件。
它要是答得含糊，后面一定出问题，别继续。

### B2 · 主图

```
现在处理主图。

源文件：<你的图片路径>
项目 id：<项目 id>

要做三件事：
1. 检查这张图是不是正方形、背景是否透明、脚底有没有贴边。
   不达标就告诉我哪里不达标，不要自己改图。
2. 达标的，复制到 E:\AI\Coopanion的宠物制作工具\projects\<id>\pack\tex\body.png
   用 Python + Pillow 居中放到 512x512 透明画布上，底部对齐。
3. 同样处理一份 256x256，存成 pack\thumb.png

做完告诉我处理后的实际尺寸和是否透明。
```

### B3 · 清掉模板表情

```
删除模板自带的三张示例表情：
projects\<id>\pack\tex\face-happy.png
projects\<id>\pack\tex\face-love.png
projects\<id>\pack\tex\face-sleepy.png

原因：它们画的是模板的蓝色小生物，不是我的宠物。
留着会导致我的宠物做表情时变成别的东西。

删完列一下 pack\tex\ 现在还剩什么文件。
```

注意：这一步是删文件。
如果你的宠物本来就是蓝的、长得像模板，可以跳过。

### B4 · 清单

```
改 figure.json，只改 name、about、vocab 三个字段，
其它字段一个字节都不要动。

我的设定：<两三句话>

要求：
- name 至少给 zh，可以再加 en
- about 至少给 zh，100 字左右。写清外形、颜色、体型、走路姿势、性格。
  这段是给 Coopanion 里的 AI 读的，它靠这段理解自己的形象，所以要具体
- vocab 给 16 到 20 个词
- 每个词：id / kind / names / about / seconds
- kind 只能是 expression 或 motion
- names 只写中文，格式 {"zh":["别名"]}
- 别名不要含逗号、顿号、空格、【】<>
- id 只能用官方套件已实现的那些，不要发明
- seconds：表情 0.9 到 1.5，动作 1.0 到 2.0
- 中文别名要口语化，像我平时会说的话

改完先自己检查一遍：
有没有两个词用了同一个中文名？
有没有别名带标点？
有没有 id 不在官方列表里？
检查完再写文件。
```

### B5 · 校验

```
跑校验，把完整输出贴给我。

cd /d E:\AI\Coopanion的宠物制作工具\studio
node tools\test_pack.mjs ..\projects\<id>\pack

如果有 FAIL，逐条告诉我原因和改法。先别改，等我确认。
```

### B6 · 打包

```
校验通过了，打包：

cd /d E:\AI\Coopanion的宠物制作工具
python -c "import sys; sys.path.insert(0,'studio'); import server; print(server.validate_pack(r'projects\<id>\pack'))"

确认 0 错误后，把 projects\<id>\pack 压成 dist\<id>-1.0.0.zip，
压缩包里 figure.json 必须在最外层，不能套一层文件夹。

压完列一下 zip 里的文件清单给我。
```

---

## 用法 C · 只做单点

### 卡住了，让它诊断

```
我的形象包导入 Coopanion 后 <描述现象，比如：桌面上是个白方块 / 一闪就没了 / 表情不变>。

相关文件在 E:\AI\Coopanion的宠物制作工具\projects\<id>\pack\

不要猜。按这个顺序查，每步告诉我结果：
1. figure.json 是否是合法 JSON，字段是否齐全
2. figure.js 能不能通过加载测试（node tools\test_pack.mjs）
3. tex\body.png 是否真的透明（读像素，角落 alpha 是不是 0）
4. tex\ 里有没有画着别的东西的示例图
5. vocab 里有没有重名或非法字符

查完给出最可能的两个原因，按可能性排序。先别改。
```

### 只写词表

```
给这个形象包写词表，只输出 JSON 数组，别的什么都不要：

<把 figure.json 的 about 贴进来>

要求：
- 18 个词，12 个 expression，6 个 motion
- id 只能用 neutral happy wink love shy surprised angry sad sleepy thinking smug pout excited cry confused worried flustered scared determined jump nod shake spin sit sleep wave dance
- 每项字段：id / kind / names / about / seconds
- names 只写中文，{"zh":["别名"]}，别名口语化
- 别名不含逗号、顿号、空格、【】<>
- 所有别名互不相同，也不能等于任何 id
- about 写具体，描述这个表情看起来什么样
```

### 只做表情图提示词

```
给我的宠物做表情图，我要拿去 AI 画图工具生成。

先读：E:\AI\Coopanion的宠物制作工具\projects\<id>\pack\tex\body.png

按这个格式给我 6 条英文提示词，一条一个表情：
文件名 | 中文表情 | 英文提示词

需要这六个：face-happy、face-love、face-sleepy、face-surprised、face-cry、face-angry

每个提示词必须包含这些约束：
- transparent background
- same character, same pose, same body position, same scale
- only the eyes and mouth change
- no change to hair, ears, body, limbs, or outline
- output 1024x1024 png

最后加一句提醒我：AI 画的图通常对不齐，要手动贴回原图。
```

---

## 想把积分花在有用的地方

你说积分太多要消耗。下面这些是真有用，不是灌水。

### 1. 把 about 写成多语言版

Coopanion 支持 11 种语言。
`about` 写全了，AI 陪聊时不管用什么语言都认得自己。

```
把 projects\<id>\pack\figure.json 的 about 扩成 11 种语言：
zh / zh-Hant / en / ja / ko / fr / de / es-419 / pt-BR / it / ru

每种语言都要翻译到位，不要机翻腔。
内容保持一致：外形、颜色、体型、走路姿势、性格。
只改 about，其它字段不动。改完跑一次校验。
```

### 2. 给每张表情图写制作说明

```
给 projects\<id>\pack\tex\ 里每个 face-*.png 写一句说明，
放进 projects\<id>\表情图说明.md：
文件名 | 表情 | 这只宠物做这个表情时，眼睛和嘴具体什么样

按这个宠物的性格来写，不要用通用模板话术。
```

### 3. 做一份配色方案清单

想以后加配色轴，先把方案想清楚。

```
读 projects\<id>\pack\tex\body.png 的主色，
给我 5 套配色方案，每套：名字（中文+英文）、主色 hex、辅色 hex、适用场合。
做成表格，存到 projects\<id>\配色方案.md。
先只出方案，不要改 figure.json。
```

### 4. 批量写动作设计

```
给这只宠物设计 8 个自定义动作：
<贴 about>

每个动作写：名字、触发场景、身体怎么动、表情怎么变、大约几秒。
存到 projects\<id>\动作设计.md。

注意：这些是设计稿，不实现。模板只支持官方套件已有的动作。
实现自定义动作要改 figure.js，风险高，先不做。
```

### 5. 让它把整个流程写成你自己的检查表

```
读 E:\AI\Coopanion的宠物制作工具\docs\05-测试验收清单.md，
结合我们刚才做的这只宠物，改写一份只针对它的检查表，
存到 projects\<id>\我的检查表.md。

只留这次真正要检查的项，删掉用不上的。
每条要能明确判断"过"或"不过"。
```

---

## 换平台时怎么交接

积分用完了要换平台。别重新开始，把下面这段带过去。

更完整的版本在 `04-AI提示词-备用平台.md`。

```
接着做一只 Coopanion 形象包，前一个助手做到一半。

工作目录：E:\AI\Coopanion的宠物制作工具
项目：projects\<项目id>\
已完成：<填，比如：主图已放好，模板表情已删，词表写了 18 个>
卡在：<填，比如：导入后一闪就没了>

先做三件事，不要动任何文件：
1. 读 docs\02-形象包格式规范.md
2. 读 projects\<项目id>\pack\figure.json，告诉我里面 vocab 有几个词、id 是什么
3. 跑 node studio\tools\test_pack.mjs projects\<项目id>\pack，把输出贴给我

三件事都做完，再告诉我你判断卡在哪，以及打算怎么改。
等我确认再动手。
```

---

## 禁忌清单

把这个贴给任何 AI 之前都值得看一眼。

| 别做 | 为什么 |
|---|---|
| 让它从零重写 `figure.js` | 白屏概率极高，报错只有一句 `error`，查不出来 |
| 让它改 `manifest` / `api` 的版本号 | 只能写 2，写别的整包被拒 |
| 让它用英文别名 | 极易和 id 撞名，整包被拒 |
| 让它发明新的 `kind` | 只认 `expression` 和 `motion` |
| 让它把图放进 `schemes/` 却不填 `axes` | 那些图不会被读到 |
| 让它改 `D:\AIGJ\Coopanion\resources\` 下的任何文件 | 那是程序本体，改了下次更新就没了，还可能导致启动失败 |
| 相信它说"应该可以了" | 只信 `test_pack.mjs` 的输出和 Coopanion 的实际表现 |
| 一次让它改五个地方 | 出错分不清是哪个改坏的，一次一个 |
