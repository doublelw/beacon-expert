"""示范：单级行星减速器（5:1）。只用来理解 mech.py 的用法，新任务按需求重新设计。

    python planetary_reducer.py 输出目录
"""
import sys
from pathlib import Path

import numpy as np
from build123d import Cylinder, Pos, Align
from py_gearworks import SpurGear, SpurRingGear, PlanetaryGearset

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "mech"))
from mech import Assembly

out = sys.argv[1] if len(sys.argv) > 1 else "out_planetary"
M, H = 1.5, 12            # 模数、齿宽
ZS, ZP, ZR, N = 12, 18, 48, 3
A = M * (ZS + ZP) / 2     # 中心距 22.5
BASE = (Align.CENTER, Align.CENTER, Align.MIN)

pg = PlanetaryGearset(ZS, ZR, ZP, N)
dirs = pg.get_planet_directions_distributed()
sun_g = SpurGear(number_of_teeth=ZS, module=M, height=H)
planets_g = []
for d in dirs:
    p = SpurGear(number_of_teeth=ZP, module=M, height=H)
    p.mesh_to(sun_g, target_dir=np.array(d))
    planets_g.append(p)
ring_g = SpurRingGear(number_of_teeth=ZR, module=M, height=H)
ring_g.mesh_to(planets_g[0], target_dir=np.array(dirs[0]))

asm = Assembly("行星减速器5比1")

sun = sun_g.build_part() + Pos(0, 0, H) * Cylinder(4, 22, align=BASE)
asm.add(sun, "太阳轮输入轴", material="steel", role="输入", explode=(0, 0, 70),
        gear=dict(module=M, teeth=ZS))
asm.coaxial("太阳轮输入轴")

names = []
for i, (pgr, d) in enumerate(zip(planets_g, dirs)):
    c = (A * d[0], A * d[1], 0)
    g = pgr.build_part() - Pos(*c) * Cylinder(2.6, H, align=BASE)
    n = f"行星轮{i + 1}"
    asm.add(g, n, material="steel", role="行星轮", explode=(d[0] * 25, d[1] * 25, 38),
            gear=dict(module=M, teeth=ZP))
    names.append(n)

# 外壳内径必须大于内齿圈齿根圆，否则会填满齿槽（实测踩过）
ring = ring_g.build_part()
housing = Cylinder(48, H, align=BASE) - Cylinder(ZR * M / 2 + 1.25 * M + 0.6, H, align=BASE)
ring = ring + housing
asm.add(ring, "内齿圈壳体", material="aluminum_black", role="固定", explode=(0, 0, 0),
        gear=dict(module=M, teeth=ZR, internal=True))
asm.coaxial("内齿圈壳体")

pt = 3
carrier = (Pos(0, 0, -pt - 0.5) * Cylinder(34, pt, align=BASE)
           + Pos(0, 0, H + 0.5) * (Cylinder(34, pt, align=BASE) - Cylinder(5, pt, align=BASE))
           + Pos(0, 0, -pt - 0.5 - 20) * Cylinder(7, 20, align=BASE))
for d in dirs:
    carrier += Pos(A * d[0], A * d[1], -pt - 0.5) * Cylinder(2.5, H + 2 * pt + 1, align=BASE)
asm.add(carrier, "行星架输出轴", material="aluminum", role="输出", explode=(0, 0, -60))
asm.coaxial("行星架输出轴")

asm.planetary(sun="太阳轮输入轴", ring="内齿圈壳体", planets=names, carrier="行星架输出轴")
asm.save(out)
