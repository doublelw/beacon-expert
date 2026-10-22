#!/usr/bin/env python3
"""装配拆件+爆炸 (纯OCC, 不经Part.Shape): STEP → parts.json + 逐件单件STEP.

全用 OCC 底层 (STEPControl/Bnd_Box/brepbndlib/BRepGProp/HLR), 避开 FreeCAD
Part.Shape 包装器在本环境触发 OpenBLAS 段错误的路径.

输入输出全走环境变量 (避免 argv 里任何路径被 FreeCAD try import):
  TMP_IN=<装配STEP> TMP_OUT=<parts.json> [TMP_SCALE=1.5] freecadcmd 本脚本
输出 (parts.json 同目录): P01.step.../Pnn.step / labels.txt / <parts.json>
"""
import sys, os, json, math
from OCC.Core.STEPControl import STEPControl_Reader, STEPControl_Writer, STEPControl_AsIs
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_SOLID
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.GProp import GProp_GProps
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCC.Core.HLRAlgo import HLRAlgo_Projector
from OCC.Core.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCC.Core.BRepAdaptor import BRepAdaptor_Curve


def subview(shape, vdir, xdir):
    """HLR 可见锐边投影 → 模型坐标2D线/整圆 (u,v)."""
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(HLRAlgo_Projector(gp_Ax2(gp_Pnt(0, 0, 0),
                                             gp_Dir(*vdir), gp_Dir(*xdir))))
    algo.Update(); algo.Hide()
    hls = HLRBRep_HLRToShape(algo)
    lines, circles = [], []
    exp = TopExp_Explorer(hls.VCompound(), TopAbs_EDGE)
    while exp.More():
        e = exp.Current()
        try:
            ac = BRepAdaptor_Curve(e)
            t = ac.GetType()
            u0, u1 = ac.FirstParameter(), ac.LastParameter()
            p0, p1 = ac.Value(u0), ac.Value(u1)
            if t == 0 and math.hypot(p0.X() - p1.X(), p0.Y() - p1.Y()) > 0.5:
                lines.append([round(p0.X(), 3), round(p0.Y(), 3),
                              round(p1.X(), 3), round(p1.Y(), 3)])
            elif t == 1:
                circ = ac.Circle()
                c = circ.Location()
                if u1 - u0 > 2 * math.pi - 0.01:
                    circles.append([round(c.X(), 3), round(c.Y(), 3),
                                    round(circ.Radius(), 3)])
                else:
                    lines.append([round(p0.X(), 3), round(p0.Y(), 3),
                                  round(p1.X(), 3), round(p1.Y(), 3)])
        except Exception:
            pass
        exp.Next()
    return {'lines': lines, 'circles': circles}


def shift(sv, du, dv):
    return {'lines': [[round(a + du, 3), round(b + dv, 3),
                       round(c + du, 3), round(d + dv, 3)]
                      for a, b, c, d in sv['lines']],
            'circles': [[round(a + du, 3), round(b + dv, 3), r]
                        for a, b, r in sv['circles']]}


def part_bbox(p):
    bx = Bnd_Box()
    brepbndlib.Add(p, bx, False)
    x0, y0, z0, x1, y1, z1 = bx.Get()
    return [x0, y0, z0, x1, y1, z1]


def part_com(p):
    props = GProp_GProps()
    brepgprop.VolumeProperties(p, props)
    c = props.CentreOfMass()
    return [c.X(), c.Y(), c.Z(), props.Mass()]


def main():
    print('MAIN ENTER', flush=True)
    path = os.environ.get('TMP_IN')
    out = os.environ.get('TMP_OUT')
    try:
        explode_scale = float(os.environ.get('TMP_SCALE', '1.5'))
    except ValueError:
        explode_scale = 1.5
    if not path or not out or not os.path.exists(path):
        sys.stderr.write("TMP_IN=<装配STEP> TMP_OUT=<parts.json> [TMP_SCALE] freecadcmd 本脚本\n")
        return 1

    reader = STEPControl_Reader()
    if not reader.ReadFile(path):
        sys.stderr.write(f"ReadFile failed: {path}\n")
        return 2
    reader.TransferRoots()
    one = reader.OneShape()
    # 拆件: compound 直接子 (每个 = 一个零件); 空则 TopExp 遍历 SOLID
    parts = []
    try:
        kids = [k for k in one.Children() if not k.IsNull()]
        parts = kids if kids else []
    except Exception:
        pass
    if not parts:
        exp = TopExp_Explorer(one, TopAbs_SOLID)
        while exp.More():
            parts.append(exp.Current())
            exp.Next()
    print(f'顶层形状数: {len(parts)}', flush=True)
    if not parts:
        sys.stderr.write('未枚举到零件\n')
        return 3

    # 逐件 bbox / com / 体积 (纯OCC)
    bboxes, coms, vols = [], [], []
    for p in parts:
        bboxes.append(part_bbox(p))
        c = part_com(p)
        coms.append([c[0], c[1], c[2]])
        vols.append(c[3])
    # 主轴 = 各件包围盒"最薄"轴 (装配叠层轴, 不用质心离散度)
    thickness = [max(b[3]) - min(b[0]) for b in []] or \
                [max(bk[i] for bk in bboxes) - min(bk[i] for bk in bboxes)
                 for i in range(3)]
    mean_thick = [max(b[i + 3] - b[i] for b in bboxes) / len(parts) for i in range(3)]
    cand = [k for k in range(3) if mean_thick[k] > 1e-6]
    main_axis = min(cand, key=lambda k: mean_thick[k]) if cand else 2
    axis_name = ['X', 'Y', 'Z'][main_axis]

    # 子视图朝向: 沿主轴朝内 (主轴Z → 主视图Front; X/Y主轴 → 侧视)
    if main_axis == 2:
        vdir, xdir, off_axis = (0, 0, -1), (1, 0, 0), 'v'
    elif main_axis == 0:
        vdir, xdir, off_axis = (0, 0, -1), (0, 1, 0), 'u'
    else:
        vdir, xdir, off_axis = (0, 0, -1), (1, 0, 0), 'u'
    view_name = 'Front' if main_axis == 2 else 'Side'
    main_center = ((max(b[2] for b in bboxes) + min(b[2] for b in bboxes)) / 2 if main_axis == 2
                   else ((max(b[0] for b in bboxes) + min(b[0] for b in bboxes)) / 2 if main_axis == 0
                         else (max(b[1] for b in bboxes) + min(b[1] for b in bboxes)) / 2))
    assembly_bbox = {'xmin': round(min(b[0] for b in bboxes), 2),
                     'ymin': round(min(b[1] for b in bboxes), 2),
                     'zmin': round(min(b[2] for b in bboxes), 2),
                     'xmax': round(max(b[3] for b in bboxes), 2),
                     'ymax': round(max(b[4] for b in bboxes), 2),
                     'zmax': round(max(b[5] for b in bboxes), 2)}
    print(f'整体bbox: {assembly_bbox} 主轴: {axis_name}', flush=True)

    parts_out = []
    for i, p in enumerate(parts):
        axis_coord = coms[i][main_axis]
        off = (axis_coord - main_center) * explode_scale
        du = off if off_axis == 'u' else 0.0
        dv = off if off_axis == 'v' else 0.0
        print(f'  件{i + 1}: com=({coms[i][0]:.1f},{coms[i][1]:.1f},{coms[i][2]:.1f}) off={off:.1f}', flush=True)
        sw = subview(p, vdir, xdir)
        parts_out.append({
            'name': f'P{i + 1:02d}',
            'label': f'part_{i + 1:02d}',
            'center': [round(v, 2) for v in coms[i]],
            'bbox': [round(v, 2) for v in bboxes[i]],
            'volume_mm3': round(vols[i], 2),
            'step_file': f"P{i + 1:02d}.step",
            'normal': sw,
            'exploded': shift(sw, du, dv),
            'explode_offset': round(off, 2),
        })

    # 逐件单件STEP (OCC writer, 不经 Part.export)
    outdir = os.path.dirname(out) or '.'
    os.makedirs(outdir, exist_ok=True)
    for i, p in enumerate(parts, 1):
        out_step = os.path.join(outdir, f"P{i:02d}.step")
        try:
            w = STEPControl_Writer()
            w.Transfer(p, STEPControl_AsIs)
            ok = w.Write(out_step) == 1
            parts_out[i - 1]['step_ok'] = ok and os.path.exists(out_step)
        except Exception as ex:
            parts_out[i - 1]['step_ok'] = False
            print(f'P{i:02d} STEP 导出失败: {ex}', flush=True)
    with open(os.path.join(outdir, 'labels.txt'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(f"{p['name']}: {p['label']}" for p in parts_out))

    result = {'assembly_bbox': assembly_bbox, 'main_axis': axis_name,
              'view': view_name, 'explode_scale': explode_scale,
              'parts': parts_out}
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    ok_steps = sum(1 for p in parts_out if p.get('step_ok'))
    print(f'输出: {out} (主轴{axis_name}, 视图{view_name}, {len(parts_out)}件, 单件STEP {ok_steps}个)', flush=True)
    return 0


sys.exit(main())
