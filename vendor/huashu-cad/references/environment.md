# 环境准备

在用户项目中创建隔离Python环境，避免改动全局包。先运行doctor；已有可用环境就复用。

本轮验证组合：macOS arm64、Python3.12、ezdxf1.4.4、matplotlib3.11.2；三维另用build123d0.13.0。其它版本或操作系统应重新做文件读回与预览检查。

```sh
uv venv .venv --python 3.12
uv pip install --python .venv/bin/python ezdxf==1.4.4 matplotlib==3.11.2
```

参数零件任务再安装：

```sh
uv pip install --python .venv/bin/python build123d==0.13.0
```

机械装配（`references/mechanical.md`）再装：

```sh
uv pip install --python .venv/bin/python build123d==0.13.0 py-gearworks==0.0.24 bd_warehouse==0.4.0 playwright
.venv/bin/python -m playwright install chromium   # 出图出片用；还需要系统里有 ffmpeg
```

户型空间计算用Shapely，读取PDF用PyMuPDF；本轮测试版本为shapely2.2.0、pymupdf1.28.2。只在需要相应任务时安装，不为简单线稿装整套三维环境。

Windows的项目解释器路径通常为`.venv/Scripts/python.exe`。本轮没有验证Windows原生CAD连接；Python包可安装不代表CAD应用接口可用。

字体按当前机器检测；中文字体须包含所需字形。本轮用了系统Arial Unicode.ttf。交付DXF使用目标机器能解析的字体名称或文件名，不能假定作者机器的绝对字体路径在别人电脑上存在。不要未经许可打包分发系统字体。

DWG、原生尺寸关联、CTB打印和企业专用对象需用实际目标CAD验证；离线DXF库或FreeCAD导入不能代替这一步。
