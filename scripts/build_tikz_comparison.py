import argparse
import json
from pathlib import Path


def latex_path(path: Path, output_dir: Path) -> str:
    return Path(__import__("os").path.relpath(path, output_dir)).as_posix()


def source_image(source_root: Path, item: dict[str, object]) -> Path:
    pattern = f"p{int(item['page']):03d}-i*-obj{int(item['object']):04d}.*"
    matches = sorted((source_root / str(item["source"]) / "images").glob(pattern))
    if len(matches) != 1:
        raise ValueError(f"Expected one source image for {item}, found {matches}")
    return matches[0]


def build_document(items: list[dict[str, object]], manifest: Path, source_root: Path) -> str:
    output_dir = manifest.parent
    cards = []
    for index, item in enumerate(items, start=1):
        original = latex_path(source_image(source_root, item), output_dir)
        fragment = latex_path(output_dir / str(item["file"]), output_dir)
        label = f"{item['source']} p.{item['page']} obj {item['object']}"
        cards.append(
            "\\begin{minipage}[t][0.45\\textheight][t]{0.48\\textwidth}\n"
            "\\centering\\scriptsize " + label + "\\par\\vspace{2mm}\n"
            "\\begin{minipage}[t]{0.48\\linewidth}\\centering Original\\par\\vspace{1mm}\n"
            "\\includegraphics[width=\\linewidth,height=0.31\\textheight,keepaspectratio]{"
            + original + "}\n\\end{minipage}\\hfill\n"
            "\\begin{minipage}[t]{0.48\\linewidth}\\centering TikZ\\par\\vspace{1mm}\n"
            "\\resizebox{\\linewidth}{!}{\\input{" + fragment + "}}\n\\end{minipage}\n"
            "\\end{minipage}"
        )

    pages = []
    for start in range(0, len(cards), 4):
        page_cards = cards[start : start + 4]
        rows = []
        for row_start in range(0, len(page_cards), 2):
            rows.append("\\noindent" + "\\hfill\n".join(page_cards[row_start : row_start + 2]))
        pages.append("\\vspace*{3mm}\n\\vfill\n".join(rows))

    return """\\documentclass[9pt]{article}
\\usepackage[a4paper,margin=9mm]{geometry}
\\usepackage{amsmath}
\\usepackage{graphicx}
\\usepackage{tikz}
\\usetikzlibrary{arrows.meta,calc,patterns,patterns.meta}
\\pagestyle{empty}
\\begin{document}
""" + "\n\\newpage\n".join(pages) + "\n\\end{document}\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build four-up original-versus-TikZ comparison PDF source.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    manifest = args.manifest.resolve()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    items = [item for item in data if item["status"] == "tikz"]
    if not items:
        raise ValueError("The manifest contains no TikZ entries")
    args.output.write_text(build_document(items, manifest, args.source_root.resolve()), encoding="utf-8")
    print(f"Wrote {len(items)} comparisons to {args.output}")


if __name__ == "__main__":
    main()
