from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps, JpegImagePlugin
from scipy.ndimage import distance_transform_edt, label
from skimage.feature import canny
from skimage.metrics import structural_similarity


ID_PATTERN = re.compile(r"p(?P<page>\d+)-i(?P<index>\d+)-obj(?P<object>\d+)")


@dataclass(frozen=True)
class Figure:
    identifier: str
    source: Path
    fragment: Path


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(
        command,
        cwd=cwd,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )


def latex_path(path: Path) -> str:
    return path.resolve().as_posix().replace("#", r"\#").replace("%", r"\%")


def find_source(item: dict[str, object], fragment: Path, roots: list[Path]) -> Path:
    match = ID_PATTERN.search(fragment.stem)
    if match:
        stem = match.group(0)
        patterns = [f"{stem}.*"]
    else:
        patterns = [f"p{int(item['page']):03d}-i*-obj{int(item['object']):04d}.*"]

    matches: list[Path] = []
    for root in roots:
        image_dir = root / str(item["source"]) / "images"
        for pattern in patterns:
            matches.extend(path for path in image_dir.glob(pattern) if path.is_file())
    matches = sorted(set(path.resolve() for path in matches))
    if len(matches) != 1:
        raise ValueError(f"Expected one source image for {item}, found {matches}")
    return matches[0]


def load_figures(
    manifest: Path, source_roots: list[Path], sources: set[str] | None = None
) -> list[Figure]:
    entries = json.loads(manifest.read_text(encoding="utf-8"))
    figures: list[Figure] = []
    for item in entries:
        if item["status"] != "tikz":
            continue
        if sources is not None and str(item["source"]) not in sources:
            continue
        fragment = (manifest.parent / str(item["file"])).resolve()
        source = find_source(item, fragment, source_roots)
        match = ID_PATTERN.search(fragment.stem) or ID_PATTERN.search(source.stem)
        identifier = match.group(0) if match else source.stem
        figures.append(Figure(identifier, source, fragment))
    return figures


def compile_fragment(
    figure: Figure, build_dir: Path, dpi: int, reuse_builds: bool
) -> Path:
    figure_dir = build_dir / figure.identifier
    figure_dir.mkdir(parents=True, exist_ok=True)
    tex = figure_dir / "figure.tex"
    tex_content = (
        """\\documentclass[border=0pt]{standalone}
\\usepackage[UTF8]{ctex}
\\usepackage{amsmath}
\\usepackage{unicode-math}
\\setmainfont{Times New Roman}
\\setmathfont{XITS Math}
\\usepackage{tikz}
\\usetikzlibrary{arrows.meta,calc,patterns,patterns.meta}
\\pagestyle{empty}
\\begin{document}
\\input{"""
        + latex_path(figure.fragment)
        + "}\n\\end{document}\n"
    )
    output = figure_dir / "render.png"
    if (
        reuse_builds
        and output.exists()
        and tex.exists()
        and tex.read_text(encoding="utf-8") == tex_content
        and output.stat().st_mtime_ns >= figure.fragment.stat().st_mtime_ns
    ):
        return output

    tex.write_text(tex_content, encoding="utf-8")
    run(
        [
            "xelatex",
            "-interaction=nonstopmode",
            "-halt-on-error",
            "-file-line-error",
            tex.name,
        ],
        figure_dir,
    )
    run(
        [
            "pdftocairo",
            "-png",
            "-singlefile",
            "-r",
            str(dpi),
            "figure.pdf",
            output.stem,
        ],
        figure_dir,
    )
    return output


def white_rgb(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        rgba = opened.convert("RGBA")
    background = Image.new("RGBA", rgba.size, "white")
    background.alpha_composite(rgba)
    return background.convert("RGB")


def remove_render_page_seam(image: Image.Image) -> Image.Image:
    """Remove the one-pixel Cairo page-edge seam from rendered figures."""
    cleaned = image.copy()
    color = border_color(cleaned)
    pixels = np.asarray(cleaned, dtype=np.uint8).copy()
    pixels[0, :] = color
    pixels[-1, :] = color
    pixels[:, 0] = color
    pixels[:, -1] = color
    return Image.fromarray(pixels, mode="RGB")


def ink_strength(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    gray = rgb.mean(axis=2)
    inset = min(2, (gray.shape[0] - 1) // 2, (gray.shape[1] - 1) // 2)
    border = np.concatenate(
        (gray[inset], gray[-1 - inset], gray[:, inset], gray[:, -1 - inset])
    )
    background = float(np.median(border))
    lighter = np.maximum(gray - background, 0.0)
    darker = np.maximum(background - gray, 0.0)
    lighter_scale = float(np.percentile(lighter, 99.5))
    darker_scale = float(np.percentile(darker, 99.5))
    delta = lighter if lighter_scale >= darker_scale else darker
    scale = max(lighter_scale, darker_scale, 1.0 / 255.0)
    return np.clip(delta / scale, 0.0, 1.0)


def border_color(image: Image.Image) -> tuple[int, int, int]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    inset = min(2, (rgb.shape[0] - 1) // 2, (rgb.shape[1] - 1) // 2)
    border = np.concatenate(
        (rgb[inset], rgb[-1 - inset], rgb[:, inset], rgb[:, -1 - inset]), axis=0
    )
    color = np.rint(np.median(border, axis=0)).astype(np.uint8)
    return int(color[0]), int(color[1]), int(color[2])


def content_box(ink: np.ndarray) -> tuple[int, int, int, int]:
    mask = ink > 0.12
    components, count = label(mask, structure=np.ones((3, 3), dtype=np.uint8))
    if count:
        sizes = np.bincount(components.ravel())
        mask &= sizes[components] >= 4
    ys, xs = np.nonzero(mask)
    if len(xs) == 0:
        raise ValueError("Image has no measurable ink")
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def edge_mask(ink: np.ndarray) -> np.ndarray:
    # A one-pixel scale preserves vector geometry while suppressing isolated
    # chromatic/JPEG fringes from scanned source pages.
    edges = canny(ink, sigma=1.0, low_threshold=0.08, high_threshold=0.22)
    if int(edges.sum()) < 8:
        raise ValueError("Image has too few measurable edges")
    return edges


def paste_scaled(
    image: Image.Image,
    canvas_size: tuple[int, int],
    scale: float,
    offset: tuple[int, int],
) -> Image.Image:
    width = max(1, round(image.width * scale))
    height = max(1, round(image.height * scale))
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", canvas_size, "white")
    canvas.paste(resized, offset)
    return canvas


def alignment_score(
    source_distance: np.ndarray,
    source_edge_y: np.ndarray,
    source_edge_x: np.ndarray,
    source_box: tuple[int, int, int, int],
    edge_y: np.ndarray,
    edge_x: np.ndarray,
    candidate_distance: np.ndarray,
    candidate_padding: int,
    candidate_box: tuple[int, int, int, int],
    offset_x: int,
    offset_y: int,
    search_factor: float,
) -> tuple[bool, float]:
    if len(edge_x) == 0:
        return False, math.inf
    target_x = edge_x + offset_x
    target_y = edge_y + offset_y
    inside = (
        (target_x >= 0)
        & (target_x < source_distance.shape[1])
        & (target_y >= 0)
        & (target_y < source_distance.shape[0])
    )
    if not inside.any():
        return False, math.inf
    candidate_to_source = source_distance[target_y[inside], target_x[inside]]
    local_x = source_edge_x - offset_x + candidate_padding
    local_y = source_edge_y - offset_y + candidate_padding
    reverse_inside = (
        (local_x >= 0)
        & (local_x < candidate_distance.shape[1])
        & (local_y >= 0)
        & (local_y < candidate_distance.shape[0])
    )
    if not reverse_inside.any():
        return False, math.inf
    source_to_candidate = candidate_distance[
        local_y[reverse_inside], local_x[reverse_inside]
    ]
    directed = np.concatenate((candidate_to_source, source_to_candidate))
    outside_fraction = 1.0 - 0.5 * (
        float(np.mean(inside)) + float(np.mean(reverse_inside))
    )
    outside_penalty = 10.0 * outside_fraction
    score = float(
        np.quantile(directed, 0.80) + 0.35 * np.mean(directed) + outside_penalty
    )
    cx0, cy0, cx1, cy1 = candidate_box
    aligned_box = (
        max(0, cx0 + offset_x),
        max(0, cy0 + offset_y),
        min(source_distance.shape[1] - 1, cx1 + offset_x),
        min(source_distance.shape[0] - 1, cy1 + offset_y),
    )
    if aligned_box[0] > aligned_box[2] or aligned_box[1] > aligned_box[3]:
        return False, math.inf
    bbox_error = [
        abs(source_value - aligned_value) / search_factor
        for source_value, aligned_value in zip(source_box, aligned_box)
    ]
    candidate_passes = bool(
        float(np.median(directed)) / search_factor <= 1.0
        and float(np.quantile(directed, 0.95)) / search_factor <= 2.0
        and max(bbox_error) <= 2.0
    )
    return candidate_passes, score


def align(source: Image.Image, rendered: Image.Image) -> tuple[Image.Image, dict[str, object]]:
    source_ink = ink_strength(source)
    rendered_ink = ink_strength(rendered)
    sx0, sy0, sx1, sy1 = content_box(source_ink)
    rx0, ry0, rx1, ry1 = content_box(rendered_ink)
    frame_width_scale = source.width / rendered.width
    frame_height_scale = source.height / rendered.height
    base_scale = math.sqrt(frame_width_scale * frame_height_scale)
    frame_aspect_ratio_error = abs(
        math.log(frame_width_scale / frame_height_scale)
    )
    source_edges = edge_mask(source_ink)
    source_distance = distance_transform_edt(~source_edges)
    source_edge_y, source_edge_x = np.nonzero(source_edges)
    source_center = ((sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0)

    best: tuple[int, float, float, int, int] | None = None
    for multiplier in np.linspace(0.98, 1.02, 17):
        scale = base_scale * float(multiplier)
        scaled_width = max(1, round(rendered.width * scale))
        scaled_height = max(1, round(rendered.height * scale))
        scaled = rendered.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
        scaled_ink = ink_strength(scaled)
        scaled_box = content_box(scaled_ink)
        scaled_edges = edge_mask(scaled_ink)
        edge_y, edge_x = np.nonzero(scaled_edges)
        candidate_padding = 20
        candidate_distance = distance_transform_edt(
            ~np.pad(scaled_edges, candidate_padding)
        )
        cx0, cy0, cx1, cy1 = scaled_box
        render_center = ((cx0 + cx1) / 2.0, (cy0 + cy1) / 2.0)
        center_x = round(source_center[0] - render_center[0])
        center_y = round(source_center[1] - render_center[1])
        for dy in range(-5, 6):
            for dx in range(-5, 6):
                offset = (center_x + dx, center_y + dy)
                candidate_passes, score = alignment_score(
                    source_distance,
                    source_edge_y,
                    source_edge_x,
                    (sx0, sy0, sx1, sy1),
                    edge_y,
                    edge_x,
                    candidate_distance,
                    candidate_padding,
                    scaled_box,
                    offset[0],
                    offset[1],
                    1.0,
                )
                candidate = (
                    0 if candidate_passes else 1,
                    score,
                    scale,
                    offset[0],
                    offset[1],
                )
                if best is None or candidate < best:
                    best = candidate

    if best is None:
        raise RuntimeError("Alignment search produced no candidate")
    _, _, scale, offset_x, offset_y = best
    scaled = rendered.resize(
        (max(1, round(rendered.width * scale)), max(1, round(rendered.height * scale))),
        Image.Resampling.LANCZOS,
    )
    aligned = Image.new("RGB", source.size, border_color(source))
    aligned.paste(scaled, (offset_x, offset_y))
    return aligned, {
        "scale": scale,
        "offset_x": offset_x,
        "offset_y": offset_y,
        "frame_width_scale": frame_width_scale,
        "frame_height_scale": frame_height_scale,
        "frame_aspect_ratio_error": frame_aspect_ratio_error,
        "source_content_box": [sx0, sy0, sx1, sy1],
        "render_content_box": [rx0, ry0, rx1, ry1],
    }


def metrics(source: Image.Image, aligned: Image.Image) -> tuple[dict[str, object], np.ndarray, np.ndarray]:
    source_ink = ink_strength(source)
    aligned_ink = ink_strength(aligned)
    source_edges = edge_mask(source_ink)
    aligned_edges = edge_mask(aligned_ink)
    source_distance = distance_transform_edt(~source_edges)
    aligned_distance = distance_transform_edt(~aligned_edges)
    distances = np.concatenate(
        (source_distance[aligned_edges], aligned_distance[source_edges])
    )
    source_box = content_box(source_ink)
    aligned_box = content_box(aligned_ink)
    bbox_error = [abs(a - b) for a, b in zip(source_box, aligned_box)]
    data: dict[str, object] = {
        "median_edge_distance_px": float(np.median(distances)),
        "p95_edge_distance_px": float(np.quantile(distances, 0.95)),
        "edge_fraction_within_1px": float(np.mean(distances <= 1.0)),
        "edge_fraction_within_2px": float(np.mean(distances <= 2.0)),
        "bbox_error_px": bbox_error,
        "structural_similarity": float(
            structural_similarity(source_ink, aligned_ink, data_range=1.0)
        ),
    }
    data["passed"] = bool(
        data["median_edge_distance_px"] <= 1.0
        and data["p95_edge_distance_px"] <= 2.0
        and max(bbox_error) <= 2
    )
    return data, source_edges, aligned_edges


def save_diagnostics(
    output_dir: Path,
    figure: Figure,
    source: Image.Image,
    aligned: Image.Image,
    source_edges: np.ndarray,
    aligned_edges: np.ndarray,
    data: dict[str, object],
) -> None:
    figure_dir = output_dir / "figures" / figure.identifier
    figure_dir.mkdir(parents=True, exist_ok=True)
    source.save(figure_dir / "source.png")
    aligned.save(figure_dir / "tikz-aligned.png")

    overlay = np.full((*source_edges.shape, 3), 255, dtype=np.uint8)
    source_only = source_edges & ~aligned_edges
    aligned_only = aligned_edges & ~source_edges
    overlap = source_edges & aligned_edges
    overlay[source_only] = (225, 35, 35)
    overlay[aligned_only] = (20, 105, 225)
    overlay[overlap] = (35, 35, 35)
    Image.fromarray(overlay).save(figure_dir / "edge-overlay.png")

    source_gray = np.asarray(source.convert("L"), dtype=np.int16)
    aligned_gray = np.asarray(aligned.convert("L"), dtype=np.int16)
    difference = np.uint8(np.abs(source_gray - aligned_gray))
    ImageOps.autocontrast(Image.fromarray(difference)).save(
        figure_dir / "absolute-difference.png"
    )
    (figure_dir / "metrics.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def contact_sheet(rows: list[dict[str, object]], output_dir: Path) -> None:
    panel_width = 1600
    header_height = 84
    gutter = 18
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 20)
    panels: list[Image.Image] = []
    for row in rows:
        identifier = str(row["id"])
        figure_dir = output_dir / "figures" / identifier
        images = [
            Image.open(figure_dir / name).convert("RGB")
            for name in (
                "source.png",
                "tikz-aligned.png",
                "edge-overlay.png",
                "absolute-difference.png",
            )
        ]
        cell_width = (panel_width - 3 * gutter) // 4
        target_height = 310
        thumbs = [ImageOps.contain(image, (cell_width, target_height)) for image in images]
        panel_height = header_height + max(image.height for image in thumbs) + gutter
        panel = Image.new("RGB", (panel_width, panel_height), "white")
        draw = ImageDraw.Draw(panel)
        state = "通过" if row["passed"] else "未通过"
        label = (
            f"{identifier}  {state}  median={row['median_edge_distance_px']:.2f}px  "
            f"p95={row['p95_edge_distance_px']:.2f}px"
        )
        draw.text((8, 8), label, fill="black", font=font)
        names = ("原图", "对齐后的 TikZ", "红蓝边缘叠加", "绝对差分")
        for index, name in enumerate(names):
            x = index * (cell_width + gutter) + cell_width // 2
            draw.text((x, 40), name, anchor="ma", fill="black", font=font)
        for index, thumb in enumerate(thumbs):
            x = index * (cell_width + gutter) + (cell_width - thumb.width) // 2
            y = header_height + (target_height - thumb.height) // 2
            panel.paste(thumb, (x, y))
        panels.append(panel)
        for image in images:
            image.close()

    page_height = 2100
    pages: list[Image.Image] = []
    page = Image.new("RGB", (panel_width, page_height), "white")
    y = 0
    for panel in panels:
        if y and y + panel.height > page_height:
            pages.append(page)
            page = Image.new("RGB", (panel_width, page_height), "white")
            y = 0
        page.paste(panel, (0, y))
        y += panel.height
    pages.append(page)
    pages[0].save(
        output_dir / "pixel-review.pdf",
        save_all=True,
        append_images=pages[1:],
        resolution=150,
    )


def comparison_pdf(rows: list[dict[str, object]], output_dir: Path) -> None:
    page_width = 1600
    page_height = 2200
    panel_height = 540
    gutter = 20
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
    pages: list[Image.Image] = []
    page = Image.new("RGB", (page_width, page_height), "white")
    row_index = 0
    for row in rows:
        identifier = str(row["id"])
        figure_dir = output_dir / "figures" / identifier
        source = Image.open(figure_dir / "source.png").convert("RGB")
        aligned = Image.open(figure_dir / "tikz-aligned.png").convert("RGB")
        cell_width = (page_width - gutter) // 2
        target_size = (cell_width - 24, panel_height - 88)
        source_thumb = ImageOps.contain(source, target_size)
        aligned_thumb = ImageOps.contain(aligned, target_size)
        top = row_index * panel_height
        draw = ImageDraw.Draw(page)
        state = "通过" if row["passed"] else "未通过"
        draw.text(
            (8, top + 6),
            f"{identifier}  {state}  median={row['median_edge_distance_px']:.2f}px  "
            f"p95={row['p95_edge_distance_px']:.2f}px",
            fill="black",
            font=font,
        )
        draw.text((cell_width // 2, top + 40), "原图", anchor="ma", fill="black", font=font)
        draw.text(
            (cell_width + gutter + cell_width // 2, top + 40),
            "TikZ 编译图",
            anchor="ma",
            fill="black",
            font=font,
        )
        source_x = (cell_width - source_thumb.width) // 2
        aligned_x = cell_width + gutter + (cell_width - aligned_thumb.width) // 2
        image_top = top + 76
        page.paste(source_thumb, (source_x, image_top))
        page.paste(aligned_thumb, (aligned_x, image_top))
        source.close()
        aligned.close()
        row_index += 1
        if row_index == 4:
            pages.append(page)
            page = Image.new("RGB", (page_width, page_height), "white")
            row_index = 0
    if row_index or not pages:
        pages.append(page)
    pages[0].save(
        output_dir / "comparison.pdf",
        save_all=True,
        append_images=pages[1:],
        resolution=150,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compile TikZ fragments and measure isotropically aligned pixel differences."
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--source",
        action="append",
        help="Only validate entries whose manifest source matches this value.",
    )
    parser.add_argument(
        "--id",
        action="append",
        help="Only validate the named pNNN-iNNN-objNNNN figure.",
    )
    parser.add_argument("--dpi", type=int, default=600)
    parser.add_argument(
        "--reuse-builds",
        action="store_true",
        help="Reuse a render only when its wrapper is unchanged and it is newer than the fragment.",
    )
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    manifest = args.manifest.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    figures = load_figures(
        manifest,
        [path.resolve() for path in args.source_root],
        set(args.source) if args.source else None,
    )
    if args.id:
        identifiers = set(args.id)
        figures = [figure for figure in figures if figure.identifier in identifiers]
    if args.limit is not None:
        figures = figures[: args.limit]
    rows: list[dict[str, object]] = []
    for index, figure in enumerate(figures, start=1):
        print(f"[{index}/{len(figures)}] {figure.identifier}", flush=True)
        rendered_path = compile_fragment(
            figure, output_dir / "build", args.dpi, args.reuse_builds
        )
        source = white_rgb(figure.source)
        rendered = remove_render_page_seam(white_rgb(rendered_path))
        aligned, transform = align(source, rendered)
        data, source_edges, aligned_edges = metrics(source, aligned)
        row = {
            "id": figure.identifier,
            "source": os.path.relpath(figure.source, manifest.parent),
            "fragment": os.path.relpath(figure.fragment, manifest.parent),
            **transform,
            **data,
        }
        row["passed"] = bool(
            row["passed"] and row["frame_aspect_ratio_error"] <= 0.02
        )
        save_diagnostics(
            output_dir,
            figure,
            source,
            aligned,
            source_edges,
            aligned_edges,
            row,
        )
        rows.append(row)

    columns = list(rows[0]) if rows else []
    with (output_dir / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    (output_dir / "metrics.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    contact_sheet(rows, output_dir)
    comparison_pdf(rows, output_dir)
    passed = sum(bool(row["passed"]) for row in rows)
    print(f"Passed {passed}/{len(rows)} figures")


if __name__ == "__main__":
    main()
