# TikZ 整理版

本目录只保留原进度清单选定的当前 TikZ 源文件及由这些源文件重新编译的同名 PDF。

- core：21 张。
- analytic：79 张。
- geometry：169 张（其中 geometry/solid 下 79 张于 2026-09-29 按《一轮闯关训练-立体几何》原图重画，见下）。
- lectures：50 张，其中包含 14 个重建后的 .tikz 文件，未采用其旧版替代文件。
- 共 319 个源文件和 319 个同名 PDF。
- index.json 记录来源讲义、原页码、对象编号与对应文件。
- unconverted-review.json 现为空：原清单中 79 个待重画的立体几何条目已全部重画。重画时逐张与原图并排对照（虚实线、顶点标注、辅助线一一对应），中点、交点、棱上点均用 calc 按题设构造。这 79 张的 PDF 由沙盒以 SimSun 字体编译，运行 `python build.py` 可按本目录统一字体重新生成。

## 重新编译

运行 `python build.py`。需要系统已安装 XeLaTeX、中文排版组件、TikZ，以及 Times New Roman 和 XITS Math 字体。不依赖已清理的 .venv-mineru 环境。

编译临时文件写入系统临时目录，不写入 OneDrive。原有 240 份源文件均已重新编译成功，并核对生成一页且包含可见内容；新增的 79 份在沙盒中编译通过（各一页）。五个源文件中的机器绝对引用已改为相对本目录的引用，图形数据未改动。

原 Math 主讲义、chapters 内的手写 TikZ 和 main.pdf 不属于本轮中间产物，保留在原处。
