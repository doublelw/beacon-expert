# drawing_plan.json — Schema 契约（v1）

AI 绘图规划器(plan_ai.py)产出，annotator/render 消费，audit 核对。所有 agent 按此契约对接。

## 顶层结构
```json
{
  "title_block": {...},
  "views": [...],
  "annotations": [...],
  "roughness": [...],
  "gdt": [...],
  "tech_req": ["..."],
  "plan_meta": {"part_name":"后壳","process":"机加工","confidence":0.9}
}
```

## title_block (GB/T 10609.1)
```json
{"name":"后壳","drawing_no":"BEACON-{project}-{seq}","material":"铝6061",
 "scale":"1:2","sheet":"A2","designer":"","checker":"","reviewer":"",
 "date":"2026-07-03","company":"Beacon"}
```

## views[] — 视图集（AI 决定，判据=每个识别特征在≥1视图清晰可见）
```json
[
  {"view":"Top","type":"standard"},
  {"view":"Front","type":"standard"},
  {"view":"Left","type":"standard"},
  {"view":"Right","type":"standard"},
  {"view":"Section-A","type":"section","plane":{"axis":"Y","offset":0.0},"source":"Top","label":"A-A"},
  {"view":"Detail-B","type":"detail","source":"Top","zone":{"cx":0,"cy":0,"r":15},"scale":5,"label":"B(5:1)"}
]
```
- type: `standard`(6视图之一) / `section`(剖视,带 plane+label) / `detail`(局部放大,带 zone+scale) / `auxiliary`(向视).
- 标准6视图名固定: Front/Back/Top/Bottom/Left/Right。

## annotations[] — 每特征标什么（annotator据此选Dimension类型+文字）
```json
[
  {"feature_id":"F0016","view":"Top","type":"diameter","value":"%%C5"},
  {"feature_id":"F0030","view":"Top","type":"thread","value":"M5-6H","spec":{"d":5,"pitch":0.8,"class":"6H"}},
  {"feature_id":"F0012","view":"Top","type":"csink","value":"%%C10x90%%D","spec":{"big_d":10,"angle":90}},
  {"feature_id":"F0001","view":"Front","type":"chamfer","value":"C0.5"},
  {"feature_id":null,"view":"Front","type":"thickness","value":"t31.9"},
  {"feature_id":null,"view":"Top","type":"linear","value":"242"}
]
```
- type: `diameter`(φ/%%C) / `thread`(M规格) / `csink`(φ×°) / `chamfer`(C) / `thickness`(t=) / `linear` / `radius`(R).
- `%%C`=φ, `%%D`=° (AutoCAD DXF special chars).
- feature_id 对应 veritas.json 的 feature id（供 audit 逐特征核对）。

## roughness[] (GB/T 131) — 哪些面 Ra 多少
```json
[{"face":"配合孔/配合面","ra":"Ra1.6","placement":"孔周围"},
 {"face":"非配合面/外形","ra":"Ra6.3","placement":"外形附近"},
 {"face":"不去除材料","ra":"Ra25","placement":"右上"}]
```

## gdt[] (GB/T 1182) — 形位公差框格
```json
[{"char":"⊥","name":"垂直度","value":0.05,"face":"孔F0016","datum":"A"},
 {"char":"∥","name":"平行度","value":0.05,"face":"底面/顶面","datum":"A"}]
```
- char: GB/T 1182 符号(⊥∥∠◎=⊖⌒等); datum: 基准 A/B/C。

## tech_req[] — 技术要求文字行
```json
["未注尺寸公差按 GB/T 1804-m","未注形位公差按 GB/T 1184-K","锐边倒钝 R0.5","材料: 铝6061","表面去毛刺、清洁"]
```

## 关键约定（所有 agent 遵守）
- plan_ai 产此 JSON 落 `{work_dir}/drawing_plan.json`；annotator/render 经 CLI `--plan` 读。
- veritas feature id 是 audit 核对主键：每个 PIERCING/CHAMFOUT 特征须在 annotations 里有对应条目（thread→M, csink→φ×°, clear→φ）。
- render 新增视图(剖视/放大)必须画对应剖切面/圈出 callout。
- audit 输出 feature_coverage: {holes:{covered:34,total:34}, csink:{14,16}, thread:{2,2}}。
