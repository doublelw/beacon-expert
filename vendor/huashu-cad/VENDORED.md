# 本目录来源说明 (Vendored)

本目录是 [huashu-cad](https://github.com/alchaincyf/huashu-cad) 的 vendored 副本，用于
Beacon「自然语言生成图纸」产品线（B 线：参数化生成；C 线：改图验证纪律）的生成侧引擎。

- 上游仓库: https://github.com/alchaincyf/huashu-cad （作者：花叔）
- 上游 commit: `093b9ddc7b88b7a86317a5f6353a705e4761616d`
- 引入日期: 2026-10-20
- 许可: MIT（见 LICENSE，版权所有者 huashu）
- 官网: https://www.huasheng.ai/cad/

## 收录内容

| 路径 | 用途 |
|---|---|
| `SKILL.md` | 方法论（先定交付→选路线→必做验证），后续翻译为 M2 planner prompt |
| `references/` | environment / geometry / mechanical / floorplans / experience 方法论文档 |
| `scripts/cad_io.py` | doctor 环境探测 / inspect 文件检查 / render 预览 / preserve 证据保留 |
| `scripts/mech/` | Assembly 登记 + 验证（mech.py）、爆炸图网页（viewer.py）、装配工程图（drawing.py）、出片（render_media.py） |
| `scripts/overlay_saved_dxf.py` | 改图证据链：修改后 DXF 与参考图叠合 |
| `examples/` | build123d 用法示例 |
| `assets/viewer/` | viewer.py 依赖的 three.js 离线 bundle（696K，网页爆炸图用） |

未收录：上游 `docs/`（作者演示图片，4.2M）、`.git/`。

## 修改原则

保持 vendored 副本只读，修改尽量通过外层适配层（`tools/install_cad_env.py`、
后续 `src/engine/cad_gen/`）进行；确需改上游文件时在本文件登记差异。
当前差异：无（仅新增本说明文件）。

## 环境约束

build123d 依赖的 OCP 只支持 Python ≤3.13，主环境 3.14 → 用
`tools/install_cad_env.py` 建 `.venv-cad`（uv + Python 3.12，版本 pin 沿用上游
`references/environment.md` 验证组合）。

**Windows 运行注意**：上游部分 `write_text` 未指定 `encoding='utf-8'`（macOS
环境无此问题），中文 Windows 默认 GBK 遇到报告中的 '↔' 等字符会
UnicodeEncodeError → 运行 vendor 脚本时统一加 `PYTHONUTF8=1` 环境变量
（实测 2026-10-20：planetary_reducer.py + viewer.py 全链路通过）。

**实测记录**（2026-10-20，Windows 11 / Python 3.12.13）：
- doctor 全绿：ezdxf 1.4.4 / matplotlib 3.11.2 / build123d 0.13.0 / py-gearworks 0.0.24 / bd_warehouse 0.4.0 / playwright 1.64.0（ffmpeg 未装，Phase 5 出视频时补）
- build123d 冒烟（install_cad_env.py）：板+双孔体积/包围盒精确命中
- examples/planetary_reducer.py 全链路：STEP 往返体积一致、啮合中心距理论=实测、总判定 ✅
- viewer.py 爆炸图网页正常产出
