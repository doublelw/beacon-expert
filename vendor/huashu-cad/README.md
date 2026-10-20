<div align="center">

# huashu-cad

> *「说清楚要画什么，拿回一份能在 CAD 里接着改的文件。」*

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Agent-Agnostic](https://img.shields.io/badge/Agent-Agnostic-blueviolet)](https://skills.sh)
[![Formats](https://img.shields.io/badge/输出-DXF%20·%20STEP-c2410c)](#能做什么)

<br>

**用自然语言出可编辑 CAD：户型与平面布置（DXF）· 齿轮、减速器、关节模组等机械装配（STEP）**

装配会自动查干涉和啮合，出交互爆炸图网页、运转视频和 A3 装配工程图。

```
npx skills add alchaincyf/huashu-cad
```

Claude Code、Codex、Cursor、WorkBuddy 等能读 skill 的 agent 都能用。

</div>

---

## 看效果

下面两个案例都是在 WorkBuddy 里用 Step 5 Preview 加载本 skill 跑出来的，画面全部由代码生成。

### 人形机器人膝关节一体化关节模组（34 个零件）

无框力矩电机 ＋ 两级行星减速（总减速比 25）＋ 交叉滚子轴承 ＋ 磁编码器，外径 118 mm、轴向 74.3 mm。整机 STEP、可重跑的建模脚本、验证报告全绿。

![爆炸图](docs/joint-exploded.jpg)

交互爆炸图网页：拖滑块把零件一层层拆开，点「运转」整机转起来。

![爆炸图网页操作](docs/joint-viewer.gif)

剖切看内部结构：

![爆炸剖切](docs/joint-section.jpg)

A3 装配工程图（全剖主视图、俯视图、序号、明细栏、齿轮参数表、技术要求），同时出 DXF：

![装配图](docs/joint-drawing.jpg)

这是演示级设计。干涉、啮合中心距、行星装配条件由脚本校验过；强度、寿命、公差配合和制造工艺没有校核，不能直接当加工图用。

### 咖啡工作室平面布置图（DXF）

一段文字需求：外轮廓 12000×8000、墙厚 200、南侧入口 1200、北侧操作台、东北角储藏间、4 张圆桌、入口到操作台留 1 米通道。出来的是原生尺寸标注、按用途分图层的 DXF，未给定的尺寸写成「演示假设」放在图里。

![咖啡工作室平面图](docs/floorplan.jpg)

## 能做什么

| 场景 | 输出 | 怎么验 |
|---|---|---|
| 新画平面图、户型、车位、布置 | DXF（原生 DIMENSION、图层、图块） | 回读文件核尺寸与数量，渲染预览自己看图 |
| 改已有 DXF、批量出图 | 改后的副本＋修改说明 | 前后对比，范围外对象保持不动 |
| 户型图片／PDF 复刻成 CAD | DXF＋叠图对照 | 叠图来自最终 DXF；门洞、通道等空间语义单独验 |
| 参数化零件 | STEP＋Python 参数源 | STEP 读回核实体数、体积、孔位 |
| 齿轮、减速器、关节模组等装配 | 整机 STEP、验证报告、爆炸图网页、静帧、运转视频、A3 装配图 | 两两布尔求交查干涉；质心距对理论中心距查啮合 |

DWG 需要本机有原生 CAD 或转换器，skill 会先检测，没有就如实说明，不会把 DXF 改个扩展名交给你。

## 装上就能用

**一行安装：**

```
npx skills add alchaincyf/huashu-cad
```

**或者手动放进 agent 的技能目录：**

```sh
git clone https://github.com/alchaincyf/huashu-cad.git ~/.claude/skills/huashu-cad      # Claude Code
git clone https://github.com/alchaincyf/huashu-cad.git ~/.workbuddy/skills/huashu-cad   # WorkBuddy
```

访问不了 GitHub 的话，可以到 https://www.huasheng.ai/cad/ 下载压缩包，解压后把 `huashu-cad` 文件夹放进上面的目录。

**Python 环境由 agent 自己搭**：第一次用时它会读 `references/environment.md`，在你的项目里建隔离环境（Python 3.12 ＋ ezdxf；机械装配另装 build123d、py-gearworks、bd_warehouse、playwright，出视频需要系统里有 ffmpeg）。

**然后直接说需求：**

```
用 huashu-cad 画一张咖啡工作室平面图：外轮廓 12000×8000 毫米，墙厚 200，南面居中入口净宽 1200……
输出可编辑 DXF、PNG 预览和验证记录，尺寸缺失处列明演示假设。
```

```
用 huashu-cad 做一个人形机器人膝关节的一体化关节模组（演示级）：外径不超过 120 mm，
无框力矩电机＋两级行星减速，总减速比 20 到 30……交付整机 STEP、验证报告、爆炸图网页和 A3 装配图。
分步推进，每步落盘再往下走。
```

尺寸、对象和结果你来说；Python、依赖、脚本 agent 来处理。

## 文件结构

```
SKILL.md                    入口：确定交付 → 选路线 → 制图 → 必须完成的验证
references/
  environment.md            环境搭建
  geometry.md               参数化零件
  mechanical.md             机械装配：登记、验证、出图出片
  floorplans.md             户型复刻与改图
  experience.md             实战踩过的坑（每条带修法和适用边界）
scripts/
  cad_io.py                 doctor / inspect / render / preserve
  overlay_saved_dxf.py      最终 DXF 与原图叠图
  mech/                     mech.py 装配验证 · viewer.py 爆炸图网页 · render_media.py 静帧与视频 · drawing.py 装配图
examples/planetary_reducer.py   用法示范（新任务按需求重新设计）
assets/viewer/              爆炸图网页用的 three.js 打包（MIT）
```

## 边界

- 测试环境是 macOS arm64 ＋ Python 3.12；Windows 原生 CAD 连接没实测过。
- 通过的是文件解析、几何读回和预览检查，没有在 AutoCAD 等商业软件里逐一实测，交付说明里会写清哪些验过、哪些没验。
- 机械案例是演示级，不替代工程校核与专业审图。

## 许可

MIT。`assets/viewer/three-cad-bundle.min.js` 打包自 three.js（MIT，见 `assets/viewer/THREE_LICENSE.txt`）。

---

<details>
<summary><b>English</b></summary>

**huashu-cad** turns plain-language requests into editable CAD files for coding agents (Claude Code, Codex, Cursor, WorkBuddy, …).

- **2D (DXF)**: floor plans, layouts, batch drawings, edits to existing DXF, and floor-plan image/PDF reconstruction — with native DIMENSION entities, purpose-based layers and blocks.
- **Mechanical assemblies (STEP)**: gears, planetary reducers, robot joint modules. Assemblies are registered and checked automatically (pairwise boolean interference, gear center distances, planetary assembly conditions, STEP read-back), then rendered to an interactive exploded-view web page, stills, a running video and an A3 assembly drawing (DXF + PNG).
- Every deliverable is read back from disk and checked against the request; the skill reports what was verified and what was not.

Install: `npx skills add alchaincyf/huashu-cad`, or clone this repo into your agent's skills folder. The agent sets up an isolated Python env per `references/environment.md`.

The joint module above is a demo-level design: strength, life, tolerances and manufacturing were not analyzed. Tested on macOS arm64 / Python 3.12. MIT licensed.

</details>
