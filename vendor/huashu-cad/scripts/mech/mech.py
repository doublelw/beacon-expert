"""机械装配登记、验证与导出（build123d）。

建模脚本里这样用：
    import sys; sys.path.insert(0, "<skill>/scripts/mech")
    from mech import Assembly
    asm = Assembly("行星减速器", units="mm")
    asm.add(sun_part, "太阳轮", material="steel", gear=dict(module=1.5, teeth=12), explode=(0, 0, 60))
    asm.mesh("太阳轮", "行星轮1")               # 声明啮合：按模数和齿数核中心距
    asm.planetary(sun="太阳轮", ring="内齿圈", planets=[...], carrier="行星架")  # 齿数条件＋运动
    asm.save("输出目录")                          # STEP＋网格＋assembly.json＋验证报告

save() 依次做：两两干涉、啮合正证据（质心距＝理论中心距）、同轴、行星条件、
质量属性、整机 STEP 导出并读回对账。报告里的每个数都来自几何，不来自声明。
"""
import json, math, time, base64
from pathlib import Path

import numpy as np
from build123d import Compound, export_step, import_step, CenterOf

# 密度 g/mm³ 与外观预设（viewer 用同名材质）
MATERIALS = {
    "steel":            {"density": 7.85e-3, "name": "合金钢"},
    "stainless":        {"density": 7.93e-3, "name": "不锈钢"},
    "aluminum":         {"density": 2.70e-3, "name": "铝合金"},
    "aluminum_black":   {"density": 2.70e-3, "name": "铝合金黑色阳极"},
    "aluminum_blue":    {"density": 2.70e-3, "name": "铝合金蓝色阳极"},
    "titanium":         {"density": 4.43e-3, "name": "钛合金"},
    "brass":            {"density": 8.50e-3, "name": "黄铜"},
    "copper":           {"density": 8.96e-3, "name": "铜（绕组）"},
    "magnet":           {"density": 7.50e-3, "name": "钕铁硼磁钢"},
    "electrical_steel": {"density": 7.65e-3, "name": "硅钢片"},
    "rubber":           {"density": 1.20e-3, "name": "橡胶"},
    "plastic":          {"density": 1.20e-3, "name": "工程塑料"},
    "pcb":              {"density": 1.85e-3, "name": "PCB"},
}

INTERFERENCE_LIMIT = 0.05   # mm³，超过记为干涉
CONTACT_LIMIT = 1e-3        # mm³，介于两者之间记为接触


class Assembly:
    def __init__(self, title, units="mm"):
        self.title, self.units = title, units
        self.parts = {}          # name -> dict
        self.meshes_decl = []    # (a, b)
        self.coaxial_decl = []   # (name, xy)
        self.planetary_decl = []  # 可多级串联
        self.gear_pairs = []     # 外啮合简单齿轮副运动
        self.notes = []
        self.allowed = set()     # 允许的过盈配合对（压装）

    # ---------- 登记 ----------
    def add(self, part, name, material="steel", role="", qty=1, explode=(0, 0, 0),
            gear=None, color=None, spin_axis=(0, 0, 1), spin_center=None, note=""):
        if name in self.parts:
            raise ValueError(f"零件名重复：{name}")
        if material not in MATERIALS:
            raise ValueError(f"未知材质 {material}，可选：{list(MATERIALS)}")
        if part is None or part.volume <= 0:
            raise ValueError(f"{name} 体积为零，检查建模")
        com = part.center(CenterOf.MASS)
        self.parts[name] = dict(part=part, name=name, code=f"P{len(self.parts) + 1:02d}", material=material, role=role, qty=qty,
                                explode=list(explode), gear=gear, color=color, note=note,
                                spin_axis=list(spin_axis),
                                spin_center=list(spin_center) if spin_center else [com.X, com.Y, com.Z],
                                motion={"spin": 0.0, "orbit": 0.0, "orbit_center": [0, 0, 0]})
        return part

    def mesh(self, a, b):
        """声明 a、b 两齿轮啮合（外啮合或内啮合由 gear['internal'] 判断）。"""
        self.meshes_decl.append((a, b))

    def coaxial(self, name, xy=(0, 0)):
        self.coaxial_decl.append((name, tuple(xy)))

    def allow_overlap(self, a, b, why):
        """压装/过盈等设计上允许的实体重叠，必须写理由，报告里单列。"""
        self.allowed.add(frozenset((a, b)))
        self.notes.append(f"允许重叠 {a}×{b}：{why}")

    def planetary(self, sun, ring, planets, carrier, input="sun", fixed="ring", input_speed=1.0, driven_by=None):
        """登记一级行星排：自动核齿数条件、计算传动比，并给零件写入运动（供 viewer 动画）。
        多级串联时对第二级写 driven_by="第一级行星架名"，输入转速取上一级行星架，总传动比相乘。
        上一级行星架与下一级太阳轮是同一个实体时，sun 与 driven_by 写同一个名字即可。"""
        self.planetary_decl.append(dict(sun=sun, ring=ring, planets=list(planets), carrier=carrier,
                                        input=input, fixed=fixed, input_speed=input_speed, driven_by=driven_by))
        for p in planets:
            self.mesh(sun, p)
            self.mesh(p, ring)

    def gear_pair(self, driver, driven, driver_speed=1.0):
        """登记定轴外啮合齿轮副的运动（转速比＝-z1/z2）。"""
        self.gear_pairs.append((driver, driven, driver_speed))
        self.mesh(driver, driven)

    def spin(self, name, speed):
        """给不在齿轮关系里的零件（电机转子、输出法兰等）直接指定转速。"""
        self.parts[name]["motion"]["spin"] = speed

    # ---------- 验证 ----------
    def _interference(self):
        names = list(self.parts)
        boxes = {n: self.parts[n]["part"].bounding_box() for n in names}
        rows = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                a, b = names[i], names[j]
                ba, bb = boxes[a], boxes[b]
                if (ba.max.X < bb.min.X or bb.max.X < ba.min.X or ba.max.Y < bb.min.Y or
                        bb.max.Y < ba.min.Y or ba.max.Z < bb.min.Z or bb.max.Z < ba.min.Z):
                    continue
                x = self.parts[a]["part"] & self.parts[b]["part"]
                v = float(x.volume) if x is not None else 0.0
                if v > CONTACT_LIMIT:
                    kind = "干涉" if v > INTERFERENCE_LIMIT else "接触"
                    if kind == "干涉" and frozenset((a, b)) in self.allowed:
                        kind = "允许重叠"
                    rows.append(dict(a=a, b=b, volume_mm3=round(v, 4), kind=kind))
        return rows

    def _gear(self, n):
        g = self.parts[n]["gear"]
        if not g:
            raise ValueError(f"{n} 没有登记 gear=dict(module=..., teeth=...)")
        return g

    def _mesh_checks(self):
        out = []
        for a, b in self.meshes_decl:
            ga, gb = self._gear(a), self._gear(b)
            if abs(ga["module"] - gb["module"]) > 1e-9:
                out.append(dict(pair=f"{a}↔{b}", ok=False, why="模数不同，无法啮合"))
                continue
            m = ga["module"]
            beta = math.radians(ga.get("helix_deg", 0))
            mt = m / math.cos(beta) if beta else m
            ia, ib = ga.get("internal", False), gb.get("internal", False)
            if ia or ib:
                theory = mt * (max(ga["teeth"], gb["teeth"]) - min(ga["teeth"], gb["teeth"])) / 2
            else:
                theory = mt * (ga["teeth"] + gb["teeth"]) / 2
            ca, cb = self.parts[a]["part"].center(CenterOf.MASS), self.parts[b]["part"].center(CenterOf.MASS)
            measured = math.hypot(ca.X - cb.X, ca.Y - cb.Y)
            ok = abs(measured - theory) < max(0.02, 0.002 * theory)
            out.append(dict(pair=f"{a}↔{b}", theory_mm=round(theory, 4), measured_mm=round(measured, 4),
                            ok=ok, why="" if ok else "质心距≠理论中心距：位置或啮合方向错了"))
        return out

    def _coaxial_checks(self):
        out = []
        for n, (x, y) in self.coaxial_decl:
            c = self.parts[n]["part"].center(CenterOf.MASS)
            d = math.hypot(c.X - x, c.Y - y)
            out.append(dict(part=n, offset_mm=round(d, 4), ok=d < 0.05))
        return out

    def _planetary(self):
        stages, speed = [], {}
        for k, d in enumerate(self.planetary_decl, 1):
            st = self._stage(d, speed.get(d["driven_by"], d["input_speed"]) if d["driven_by"] else d["input_speed"])
            st["stage"] = k
            if st["ratio"]:
                speed[d["carrier"]] = st["carrier_speed"]
            stages.append(st)
        if not stages:
            return None
        total = 1.0
        for st in stages:
            total = total * st["ratio"] if (st["ratio"] and total) else None
        return dict(stages=stages, ratio=total, sun=stages[0]["sun"], planets=stages[0]["planets"],
                    zs=stages[0]["zs"], zp=stages[0]["zp"], zr=stages[0]["zr"], n=stages[0]["n"],
                    conditions=[c for st in stages for c in st["conditions"]],
                    note="；".join(f"第{st['stage']}级 {st['note']}" for st in stages))

    def _stage(self, d, ws):
        zs, zr = self._gear(d["sun"])["teeth"], self._gear(d["ring"])["teeth"]
        zp_set = {self._gear(p)["teeth"] for p in d["planets"]}
        n = len(d["planets"])
        zp = zp_set.pop() if len(zp_set) == 1 else None
        cond = []
        if zp is None:
            cond.append(dict(rule="行星轮齿数一致", ok=False))
        else:
            cond.append(dict(rule=f"同心条件 Zr = Zs + 2Zp：{zr} = {zs} + 2×{zp}", ok=zr == zs + 2 * zp))
            cond.append(dict(rule=f"装配条件 (Zs+Zr)/N 为整数：({zs}+{zr})/{n} = {(zs + zr) / n:g}",
                             ok=(zs + zr) % n == 0))
            m = self._gear(d["sun"])["module"]
            a = m * (zs + zp) / 2
            gap = 2 * a * math.sin(math.pi / n) - m * (zp + 2)
            cond.append(dict(rule=f"邻接条件 相邻行星齿顶间隙 {gap:.2f} mm > 0", ok=gap > 0))
        ratio = wc = None
        if d["input"] == "sun" and d["fixed"] == "ring" and zp:
            ratio = 1 + zr / zs
            wc = ws / ratio
            wp_rel = -(zs / zp) * (ws - wc)
            if not d["driven_by"]:
                self.parts[d["sun"]]["motion"]["spin"] = ws
            elif d["driven_by"] != d["sun"]:
                self.parts[d["sun"]]["motion"]["spin"] = ws
            self.parts[d["carrier"]]["motion"]["spin"] = wc
            for p in d["planets"]:
                mo = self.parts[p]["motion"]
                mo["spin"] = wc + wp_rel
                mo["orbit"] = wc
            self.parts[d["ring"]]["motion"]["spin"] = 0.0
        return dict(zs=zs, zr=zr, zp=zp, n=n, ratio=ratio, carrier_speed=wc, conditions=cond,
                    sun=d["sun"], ring=d["ring"], planets=d["planets"], carrier=d["carrier"],
                    note=f"Zs{zs}/Zp{zp}/Zr{zr}×{n}，i={ratio:g}" if ratio else "非标准驱动方式，未计算")

    def _gear_pair_motion(self):
        for drv, dvn, w in self.gear_pairs:
            z1, z2 = self._gear(drv)["teeth"], self._gear(dvn)["teeth"]
            self.parts[drv]["motion"]["spin"] = w
            self.parts[dvn]["motion"]["spin"] = -w * z1 / z2

    def _mass(self):
        rows, tot = [], 0.0
        for n, p in self.parts.items():
            v = float(p["part"].volume)
            mass = v * MATERIALS[p["material"]]["density"] * p["qty"]
            tot += mass
            rows.append(dict(name=n, code=p["code"], material=MATERIALS[p["material"]]["name"], qty=p["qty"],
                             volume_mm3=round(v, 2), mass_g=round(mass, 2)))
        return rows, round(tot, 2)

    # ---------- 导出 ----------
    def _tessellate(self, part):
        bb = part.bounding_box()
        diag = math.dist((bb.min.X, bb.min.Y, bb.min.Z), (bb.max.X, bb.max.Y, bb.max.Z))
        verts, tris = part.tessellate(max(0.005, diag * 4e-4), 0.15)
        v = np.array([[p.X, p.Y, p.Z] for p in verts], dtype=np.float32)
        t = np.array(tris, dtype=np.uint32)
        return dict(v=base64.b64encode(v.tobytes()).decode(), i=base64.b64encode(t.tobytes()).decode(),
                    nv=int(len(v)), nt=int(len(t)))

    def save(self, out_dir, step_name=None):
        t0 = time.time()
        out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
        self._gear_pair_motion()
        plan = self._planetary()
        inter = self._interference()
        meshes = self._mesh_checks()
        coax = self._coaxial_checks()
        mass_rows, mass_total = self._mass()

        step = out / (step_name or f"{self.title}.step")
        # STEP 零件名用 ASCII 编号：中文标签写入后读回是乱码（UTF-8 被按 Latin-1 解析），
        # 其它 CAD 打开同样可能乱；中文名对照写在 assembly.json 与报告里。
        children = []
        for p in self.parts.values():
            p["part"].label = p["code"]
            children.append(p["part"])
        export_step(Compound(children=children, label="ASSEMBLY"), str(step))
        back = import_step(str(step))
        solids = back.solids()
        vol_in = sum(float(p["part"].volume) for p in self.parts.values())
        vol_back = sum(float(s.volume) for s in solids)
        n_in = sum(len(p["part"].solids()) for p in self.parts.values())
        step_ok = len(solids) == n_in and abs(vol_back - vol_in) < 1e-4 * vol_in + 1e-3

        bad_inter = [r for r in inter if r["kind"] == "干涉"]
        checks_ok = (not bad_inter and all(m["ok"] for m in meshes) and all(c["ok"] for c in coax)
                     and step_ok and (plan is None or all(c["ok"] for c in plan["conditions"])))
        bb = Compound(children=children).bounding_box()

        manifest = dict(
            title=self.title, units=self.units,
            bbox=dict(min=[bb.min.X, bb.min.Y, bb.min.Z], max=[bb.max.X, bb.max.Y, bb.max.Z]),
            planetary=plan, mass_total_g=mass_total, checks_ok=checks_ok,
            parts=[dict(name=n, code=p["code"], material=p["material"], material_name=MATERIALS[p["material"]]["name"],
                        role=p["role"], qty=p["qty"], explode=p["explode"], color=p["color"],
                        gear=p["gear"], note=p["note"], spin_axis=p["spin_axis"],
                        spin_center=p["spin_center"], motion=p["motion"],
                        mass_g=next(r["mass_g"] for r in mass_rows if r["name"] == n),
                        mesh=self._tessellate(p["part"]))
                   for n, p in self.parts.items()])
        (out / "assembly.json").write_text(json.dumps(manifest, ensure_ascii=False))

        report = dict(title=self.title, checks_ok=checks_ok, seconds=round(time.time() - t0, 1),
                      parts=len(self.parts), solids_in=n_in, step=dict(file=step.name, solids_back=len(solids),
                      volume_in=round(vol_in, 3), volume_back=round(vol_back, 3), ok=step_ok),
                      interference=inter, mesh=meshes, coaxial=coax, planetary=plan,
                      mass=mass_rows, mass_total_g=mass_total, notes=self.notes)
        (out / "验证报告.json").write_text(json.dumps(report, ensure_ascii=False, indent=1))
        (out / "验证报告.md").write_text(_report_md(report))
        print(_report_md(report))
        return report


def _report_md(r):
    ok = lambda b: "✅" if b else "❌"
    L = [f"# 装配验证报告：{r['title']}", "",
         f"总判定：{ok(r['checks_ok'])}　零件 {r['parts']} 个　实体 {r['solids_in']} 个　总质量 {r['mass_total_g']} g　用时 {r['seconds']} s", "",
         "## STEP 读回", f"{ok(r['step']['ok'])} {r['step']['file']}：实体 {r['solids_in']}→{r['step']['solids_back']}，"
         f"体积 {r['step']['volume_in']}→{r['step']['volume_back']} mm³", "", "## 两两干涉（布尔求交）"]
    bad = [x for x in r["interference"] if x["kind"] != "接触"]
    con = [x for x in r["interference"] if x["kind"] == "接触"]
    L.append("无干涉。" if not bad else "")
    for x in bad:
        L.append(f"- {'❌' if x['kind']=='干涉' else '⚠️'} {x['a']} × {x['b']}：{x['volume_mm3']} mm³（{x['kind']}）")
    if con:
        L.append(f"接触（<{INTERFERENCE_LIMIT} mm³，齿面贴合属正常）：" + "；".join(f"{x['a']}×{x['b']} {x['volume_mm3']}" for x in con))
    L += ["", "## 啮合正证据（质心距 vs 理论中心距）", "干涉为零不能证明啮合，这一节才是。"]
    for m in r["mesh"]:
        L.append(f"- {ok(m['ok'])} {m['pair']}：理论 {m.get('theory_mm','-')} mm，实测 {m.get('measured_mm','-')} mm {m['why']}")
    if r["coaxial"]:
        L += ["", "## 同轴"] + [f"- {ok(c['ok'])} {c['part']} 偏心 {c['offset_mm']} mm" for c in r["coaxial"]]
    p = r["planetary"]
    if p:
        L += ["", "## 行星排（齿圈固定、太阳轮输入、行星架输出）"]
        for st in p["stages"]:
            L.append(f"第{st['stage']}级：{st['note']}")
            L += [f"- {ok(c['ok'])} {c['rule']}" for c in st["conditions"]]
        if p["ratio"]:
            L.append(f"总传动比 {p['ratio']:g}:1" + ("（各级相乘）" if len(p["stages"]) > 1 else ""))
    L += ["", "## 质量属性", "| STEP编号 | 零件 | 材料 | 数量 | 体积 mm³ | 质量 g |", "|---|---|---|---|---|---|"]
    L += [f"| {m['code']} | {m['name']} | {m['material']} | {m['qty']} | {m['volume_mm3']} | {m['mass_g']} |" for m in r["mass"]]
    if r["notes"]:
        L += ["", "## 说明"] + [f"- {n}" for n in r["notes"]]
    L += ["", "## 未验证", "强度、疲劳、温升、齿面接触应力、公差链与制造工艺均未验证；本报告只证明几何装配关系。"]
    return "\n".join(L) + "\n"
