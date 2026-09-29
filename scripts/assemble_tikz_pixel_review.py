from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from scripts.validate_tikz_pixels import comparison_pdf, contact_sheet


def diagnostic_pdf(
    rows: list[dict[str, object]],
    output_dir: Path,
    image_name: str,
    title: str,
    filename: str,
) -> None:
    page_width = 1600
    page_height = 2200
    columns = 2
    rows_per_page = 3
    gutter = 20
    cell_width = (page_width - gutter) // columns
    cell_height = page_height // rows_per_page
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 22)
    pages: list[Image.Image] = []
    page = Image.new("RGB", (page_width, page_height), "white")

    for index, row in enumerate(rows):
        slot = index % (columns * rows_per_page)
        column = slot % columns
        row_index = slot // columns
        left = column * (cell_width + gutter)
        top = row_index * cell_height
        figure_dir = output_dir / "figures" / str(row["id"])
        with Image.open(figure_dir / image_name) as opened:
            image = opened.convert("RGB")
            thumb = ImageOps.contain(image, (cell_width - 30, cell_height - 96))
        draw = ImageDraw.Draw(page)
        draw.text(
            (left + 10, top + 8),
            f"{row['id']}  {title}",
            fill="black",
            font=font,
        )
        draw.text(
            (left + 10, top + 40),
            f"median={row['median_edge_distance_px']:.2f}px  "
            f"p95={row['p95_edge_distance_px']:.2f}px",
            fill="black",
            font=font,
        )
        x = left + (cell_width - thumb.width) // 2
        y = top + 82 + (cell_height - 82 - thumb.height) // 2
        page.paste(thumb, (x, y))

        if slot == columns * rows_per_page - 1:
            pages.append(page)
            page = Image.new("RGB", (page_width, page_height), "white")

    if len(rows) % (columns * rows_per_page) or not pages:
        pages.append(page)
    pages[0].save(
        output_dir / filename,
        save_all=True,
        append_images=pages[1:],
        resolution=150,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble validated figure reports into one comparison PDF."
    )
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--id", action="append", help="Optional output order and filter.")
    parser.add_argument(
        "--passed-only",
        action="store_true",
        help="Include only figures that passed strict pixel validation.",
    )
    args = parser.parse_args()

    rows_by_id: dict[str, dict[str, object]] = {}
    sources_by_id: dict[str, Path] = {}
    discovery_order: list[str] = []
    for input_dir in args.input:
        rows = json.loads((input_dir / "metrics.json").read_text(encoding="utf-8"))
        for row in rows:
            identifier = str(row["id"])
            if identifier not in rows_by_id:
                discovery_order.append(identifier)
            rows_by_id[identifier] = row
            sources_by_id[identifier] = input_dir / "figures" / identifier

    identifiers = args.id or discovery_order
    if args.passed_only:
        identifiers = [
            identifier for identifier in identifiers if bool(rows_by_id[identifier]["passed"])
        ]
    missing = [identifier for identifier in identifiers if identifier not in rows_by_id]
    if missing:
        raise ValueError(f"Missing requested figures: {missing}")

    output_dir = args.output_dir.resolve()
    figures_dir = output_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for identifier in identifiers:
        row = rows_by_id[identifier]
        if not bool(row["passed"]):
            raise ValueError(f"Refusing to assemble failed figure: {identifier}")
        destination = figures_dir / identifier
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(sources_by_id[identifier], destination)
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
    diagnostic_pdf(
        rows,
        output_dir,
        "edge-overlay.png",
        "红色为原图，蓝色为 TikZ",
        "edge-overlay.pdf",
    )
    diagnostic_pdf(
        rows,
        output_dir,
        "absolute-difference.png",
        "绝对差分",
        "absolute-difference.pdf",
    )
    print(f"Assembled {len(rows)} strictly passed figures in {output_dir}")


if __name__ == "__main__":
    main()
