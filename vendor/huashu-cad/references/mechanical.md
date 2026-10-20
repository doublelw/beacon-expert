# 机械装配：齿轮、减速器、关节模组

适用：齿轮副、行星/多级减速器、电机＋减速器关节模组、带轴承和紧固件的回转类装配，以及要出爆炸图、剖视、运转动画、装配工程图的任务。单个零件仍按 `geometry.md`。

## 一条完整链路

1. **定参数表**：先把需求写成一张表——模数、齿数、齿宽、压力角、传动比、外形尺寸、轴径、材料。需求没给的写成「演示假设」并在交付说明里列出。行星排先用下面的条件算一遍再建模。
2. **建模**：build123d 建实体；齿轮用 `py_gearworks`（`SpurGear`、`SpurRingGear`、`HelicalGear(herringbone=True)`、`PlanetaryGearset`），用 `mesh_to(other, target_dir=...)` 摆位；轴承、螺钉、螺母用 `bd_warehouse`。所有尺寸是具名参数，脚本可重跑。
3. **登记装配**：`scripts/mech/mech.py` 的 `Assembly`：

   ```python
   import sys; sys.path.insert(0, "<skill>/scripts/mech")
   from mech import Assembly
   asm = Assembly("关节模组")
   asm.add(part, "太阳轮", material="steel", gear=dict(module=1, teeth=15), explode=(0, 0, 60))
   asm.coaxial("太阳轮")                         # 应在轴线上的零件
   asm.planetary(sun=..., ring=..., planets=[...], carrier=...)   # 或 asm.gear_pair(主动, 从动)
   asm.spin("电机转子", 1.0)                      # 不在齿轮关系里的转动件
   asm.allow_overlap("轴承内圈", "轴", "过盈压装")  # 设计上允许的重叠，必须写理由
   asm.save("产物目录")
   ```
   `save()` 写出整机 STEP（零件名为 ASCII 编号 P01…，中文名对照在报告里）、`assembly.json`、`验证报告.md/json`。材质可选：steel stainless aluminum aluminum_black aluminum_blue titanium brass copper magnet electrical_steel rubber plastic pcb。斜齿在 gear 里写 `helix_deg`，内齿写 `internal=True`。
4. **看报告，修到全绿**：总判定 ❌ 不进入下一步。
5. **出图出片**：
   - `python scripts/mech/viewer.py 产物/assembly.json` → 单文件交互网页（爆炸、运转、剖切、标签、BOM），双击即开、不联网。
   - `python scripts/mech/render_media.py 网页.html --stills 目录 [--clean] [--dark]` → 装配/爆炸/剖切/爆炸剖切四张；`--video 输出.mp4 --seconds 10` → 环绕＋运转＋展开＋收回。
   - `python scripts/mech/drawing.py 产物/assembly.json 产物/X.step --out 装配图.dxf --png 装配图.png` → A3 装配图（全剖主视图＋俯视图、齿轮简化画法、尺寸、序号、明细栏、齿轮参数表、技术要求）。只支持轴线为 Z 的回转类装配；其它构型按 `geometry.md` 自行投影。
6. **自己打开每张图和视频抽帧检查**，再交付。

## 齿轮与行星排的硬条件（建模前算）

- 中心距：外啮合 a = m(z1+z2)/2；内啮合 a = m(z2−z1)/2；斜齿用端面模数 mt = m/cos β。
- 单级行星排（齿圈固定、太阳轮入、行星架出）：传动比 i = 1 + Zr/Zs。
  - 同心条件：Zr = Zs + 2Zp。
  - 装配条件：(Zs + Zr)/N 为整数（N 为行星轮数）。
  - 邻接条件：2a·sin(π/N) > 行星轮齿顶圆直径。
- 两级串联：总传动比相乘；第二级太阳轮与第一级行星架同轴相连。
- 小齿轮齿数低于约 17（压力角 20°）会根切，py_gearworks 默认开启根切建模；要避免就加齿数或变位。

## 让它看起来专业的设计习惯

- **零件之间留间隙**：行星销与行星轮孔、轴与盖板孔留 0.1–0.5 mm；外壳内径必须大于内齿圈齿根圆，否则会把齿槽填满（实测：三个行星轮各干涉 201 mm³）。
- **材质分工**：齿轮和轴用 steel；外壳用 aluminum_black 或 aluminum_blue；行星架、端盖用 aluminum；电机绕组 copper、磁钢 magnet、定子铁芯 electrical_steel；编码器板 pcb。三到四种材质就够，颜色过多会显得廉价。
- **爆炸方向**：沿轴线分层，越靠里的件飞得越远；行星轮在轴向位移之外再加径向位移（约 0.6–1 倍齿顶圆直径），让三个行星轮散开而不是挤在一起；外壳通常不动，作为参照。位移量取装配高度的 0.5–1.5 倍。
- **有内部结构的装配要配剖切图**：剖切面默认为 XZ（y=0），零件摆位时让主要零件切得到。

## 验证：什么算证明，什么不算

- 干涉为零 **不能** 证明啮合——齿轮放偏到远处同样不干涉（实测：斜齿内齿圈 `mesh_to` 方向与直齿相反，放偏到 47.9 mm，干涉矩阵却全是 0）。报告里的「啮合正证据」用质心距对理论中心距，这一节必须全绿。
- 齿面接触时求交体积会有 10⁻⁴ mm³ 量级的数值，报告记为「接触」，属正常；超过 0.05 mm³ 记为干涉。
- STEP 读回的实体数和体积一致，只证明文件完整，不证明设计正确。
- 运转动画里各件转速来自齿数公式（Willis），不是手调；动画里齿不打架是运动学正确的旁证，不是强度证明。
- 交付说明必须写：强度、疲劳、温升、齿面接触应力、回差、公差配合、润滑与制造工艺均未验证；只能称「几何层面的参数化装配」，不能称可量产。

## 已知的坑

- build123d 依赖的 OCP 只支持到 Python 3.12/3.13 一类版本，新版 Python 装不上时按 `environment.md` 建 3.12 隔离环境；首次 import 约 20–30 秒。
- STEP 写中文零件名，读回会变乱码（UTF-8 被按 Latin-1 解析），其它 CAD 也可能乱码；`mech.py` 已改为 ASCII 编号。
- 无头浏览器出图：macOS 先试 Metal 后端，失败自动退回 SwiftShader 软件渲染（更慢，但画面一致）。
- `render_media.py` 依赖 playwright 的 chromium（`python -m playwright install chromium`）和 ffmpeg。

## 开源库与许可

build123d、py-gearworks、bd_warehouse：Apache-2.0；ezdxf：MIT；three.js（`assets/viewer` 内置打包）：MIT。
