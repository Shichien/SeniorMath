from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}


def latex_path(path: Path, output_dir: Path) -> str:
    return Path(os.path.relpath(path, output_dir)).as_posix()


def inventory(root: Path) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    chapter_root = root / "chapters"
    images = sorted(
        (
            path
            for path in chapter_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in IMAGE_SUFFIXES
            and "handwritten-tikz" not in path.parts
        ),
        key=lambda path: path.as_posix().casefold(),
    )
    paired: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []
    for image in images:
        tikz = image.parent / "handwritten-tikz" / f"{image.stem}.tikz"
        relative_image = image.relative_to(root).as_posix()
        parts = image.relative_to(chapter_root).parts
        category = parts[0] if parts else ""
        item = {"category": category, "image": relative_image}
        if tikz.is_file():
            item["tikz"] = tikz.relative_to(root).as_posix()
            paired.append(item)
        else:
            missing.append(item)
    return paired, missing


def build_tex(root: Path, output: Path, paired: list[dict[str, str]]) -> str:
    output_dir = output.parent
    pages: list[str] = []
    total = len(paired)
    for number, item in enumerate(paired, start=1):
        image = latex_path(root / item["image"], output_dir)
        tikz = latex_path(root / item["tikz"], output_dir)
        image_label = item["image"]
        tikz_label = item["tikz"]
        page = rf"""\noindent
\begin{{minipage}}[t]{{\textwidth}}
  \textbf{{第 {number} / {total} 组}}\hfill\textbf{{{item['category']}}}\\[-1pt]
  \footnotesize\ttfamily\detokenize{{{image_label}}}
\end{{minipage}}
\vspace{{3mm}}

\noindent
\begin{{minipage}}[t][0.82\textheight][t]{{0.485\textwidth}}
  \centering\large\bfseries 原图\par\vspace{{3mm}}
  \includegraphics[width=\linewidth,height=0.73\textheight,keepaspectratio]{{\detokenize{{{image}}}}}
\end{{minipage}}\hfill
\begin{{minipage}}[t][0.82\textheight][t]{{0.485\textwidth}}
  \centering\large\bfseries TikZ 渲染图\par\vspace{{3mm}}
  \begin{{adjustbox}}{{max width=\linewidth,max totalheight=0.73\textheight,center}}
    \input{{\detokenize{{{tikz}}}}}
  \end{{adjustbox}}
  \par\vfill
  \footnotesize\ttfamily\detokenize{{{tikz_label}}}
\end{{minipage}}
"""
        pages.append(page)

    body = "\n\n\\newpage\n\n".join(pages)
    return r"""\documentclass[11pt,landscape]{ctexart}
\usepackage[a4paper,margin=10mm]{geometry}
\usepackage{amsmath,amssymb}
\usepackage{graphicx}
\usepackage{adjustbox}
\usepackage{xcolor}
\usepackage{tikz}
\usetikzlibrary{arrows.meta,calc,patterns,patterns.meta,angles,quotes,intersections,positioning,shapes.geometric,tikzmark,decorations.pathmorphing,decorations.markings,matrix,fit,backgrounds}
\pagestyle{empty}
\setlength{\parindent}{0pt}
\begin{document}
""" + body + "\n\\end{document}\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--missing", type=Path, required=True)
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    paired, missing = inventory(root)
    output.write_text(build_tex(root, output, paired), encoding="utf-8")
    args.inventory.write_text(
        json.dumps({"paired": paired, "missing": missing}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    missing_lines = [
        f"原图总数：{len(paired) + len(missing)}",
        f"已有 TikZ：{len(paired)}",
        f"尚无 TikZ：{len(missing)}",
        "",
        *[item["image"] for item in missing],
    ]
    args.missing.write_text("\n".join(missing_lines) + "\n", encoding="utf-8")
    print(f"paired={len(paired)} missing={len(missing)} output={output}")


if __name__ == "__main__":
    main()
