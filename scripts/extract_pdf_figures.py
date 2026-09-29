import argparse
import csv
import json
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image, ImageDraw, ImageOps


IMAGE_PLACEHOLDER_PATTERN = re.compile(r"<!--\s*image[^>]*-->", re.IGNORECASE)
PDF_IMAGE_ROW_PATTERN = re.compile(
    r"^\s*(\d+)\s+(\d+)\s+(image|smask)\s+(\d+)\s+(\d+)\s+"
    r"(\S+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(\d+)\s+(\d+)"
)
EXTRACTED_FILE_PATTERN = re.compile(r"-(\d+)\.[^.]+$")


@dataclass
class Figure:
    sequence: int
    page: int
    pdf_image_number: int
    pdf_object_number: int
    width: int
    height: int
    color_space: str
    encoding: str
    file: str
    tikz_review: str


def run_pdfimages(pdfimages: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [pdfimages, *arguments],
        check=False,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        text=True,
    )
    if completed.returncode:
        raise RuntimeError(
            f"pdfimages failed with exit code {completed.returncode}:\n{completed.stderr}"
        )
    return completed


def read_pdf_images(pdfimages: str, source: Path) -> list[Figure]:
    completed = run_pdfimages(pdfimages, ["-list", str(source)])
    figures: list[Figure] = []
    for line in completed.stdout.splitlines():
        match = PDF_IMAGE_ROW_PATTERN.match(line)
        if match is None or match.group(3) != "image":
            continue
        fields = line.split()
        figures.append(
            Figure(
                sequence=len(figures) + 1,
                page=int(match.group(1)),
                pdf_image_number=int(match.group(2)),
                pdf_object_number=int(match.group(11)),
                width=int(match.group(4)),
                height=int(match.group(5)),
                color_space=match.group(6),
                encoding=match.group(9),
                file="",
                tikz_review="unreviewed",
            )
        )
    if not figures:
        raise RuntimeError(f"No embedded PDF images were found in {source}")
    return figures


def extracted_files(directory: Path) -> dict[int, Path]:
    files: dict[int, Path] = {}
    for path in directory.iterdir():
        if not path.is_file():
            continue
        match = EXTRACTED_FILE_PATTERN.search(path.name)
        if match is None:
            continue
        image_number = int(match.group(1))
        if image_number in files:
            raise RuntimeError(f"Duplicate extracted image number {image_number} in {directory}")
        files[image_number] = path
    return files


def extract_figures(pdfimages: str, source: Path, figures: list[Figure], images_dir: Path) -> None:
    images_dir.mkdir(parents=True)
    with TemporaryDirectory(prefix="mineru-figure-") as temporary_directory:
        extracted_dir = Path(temporary_directory)
        prefix = extracted_dir / "source-image"
        # PNG output preserves one exported file per entry in `pdfimages -list`.
        # In contrast, `-all` may skip duplicate or unsupported source objects.
        run_pdfimages(pdfimages, ["-png", str(source), str(prefix)])
        files = extracted_files(extracted_dir)
        expected_numbers = {figure.pdf_image_number for figure in figures}
        missing_numbers = sorted(expected_numbers - files.keys())
        if missing_numbers:
            raise RuntimeError(
                f"pdfimages did not extract image numbers {missing_numbers} for {source}"
            )

        for figure in figures:
            raw_path = files[figure.pdf_image_number]
            target_name = (
                f"p{figure.page:03d}-i{figure.sequence:03d}-obj{figure.pdf_object_number:04d}"
                f"{raw_path.suffix.lower()}"
            )
            target = images_dir / target_name
            shutil.move(str(raw_path), target)
            figure.file = str(target.relative_to(images_dir.parent).as_posix())


def draw_label(draw: ImageDraw.ImageDraw, text: str, x: int, y: int) -> None:
    box = draw.textbbox((x, y), text)
    draw.rectangle((box[0] - 2, box[1] - 1, box[2] + 2, box[3] + 1), fill="white")
    draw.text((x, y), text, fill="black")


def make_contact_sheets(figures: list[Figure], images_dir: Path, contacts_dir: Path) -> list[str]:
    contacts_dir.mkdir(parents=True, exist_ok=True)
    columns = 4
    rows = 5
    thumb_width = 230
    thumb_height = 180
    label_height = 34
    page_capacity = columns * rows
    output_files = []

    for start in range(0, len(figures), page_capacity):
        subset = figures[start : start + page_capacity]
        canvas = Image.new(
            "RGB",
            (columns * thumb_width, rows * (thumb_height + label_height)),
            "white",
        )
        draw = ImageDraw.Draw(canvas)
        for offset, figure in enumerate(subset):
            column = offset % columns
            row = offset // columns
            x = column * thumb_width
            y = row * (thumb_height + label_height)
            image_path = images_dir.parent / figure.file
            with Image.open(image_path) as source:
                image = ImageOps.contain(source.convert("RGB"), (thumb_width - 10, thumb_height - 10))
            image_x = x + (thumb_width - image.width) // 2
            image_y = y + (thumb_height - image.height) // 2
            canvas.paste(image, (image_x, image_y))
            draw.rectangle(
                (x, y, x + thumb_width - 1, y + thumb_height + label_height - 1),
                outline="#aaaaaa",
            )
            draw_label(draw, f"p{figure.page}  i{figure.sequence}  obj{figure.pdf_object_number}", x + 4, y + thumb_height + 2)
            draw_label(draw, f"{figure.width}x{figure.height}", x + 4, y + thumb_height + 17)

        target = contacts_dir / f"contact-{start // page_capacity + 1:03d}.png"
        canvas.save(target, "PNG", optimize=True)
        output_files.append(str(target.relative_to(contacts_dir.parent).as_posix()))
    return output_files


def count_mineru_placeholders(results_dir: Path | None) -> int | None:
    if results_dir is None:
        return None
    if not results_dir.is_dir():
        raise FileNotFoundError(f"MinerU results directory does not exist: {results_dir}")
    return sum(
        len(IMAGE_PLACEHOLDER_PATTERN.findall(path.read_text(encoding="utf-8")))
        for path in results_dir.glob("*.md")
    )


def write_manifest(destination: Path, figures: list[Figure]) -> None:
    destination.write_text(
        json.dumps([asdict(figure) for figure in figures], ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    with destination.with_suffix(".csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(asdict(figures[0])))
        writer.writeheader()
        writer.writerows(asdict(figure) for figure in figures)


def write_report(
    destination: Path,
    source: Path,
    figures: list[Figure],
    contacts: list[str],
    placeholder_count: int | None,
) -> None:
    pages = sorted({figure.page for figure in figures})
    small_figures = [
        figure
        for figure in figures
        if figure.width * figure.height < 1_500_000 and min(figure.width, figure.height) >= 50
    ]
    placeholder_line = "未提供 MinerU 结果目录。"
    if placeholder_count is not None:
        placeholder_line = f"MinerU Markdown 中有 {placeholder_count} 个图像占位符。"
    contact_lines = "\n".join(f"- [{Path(item).name}]({item})" for item in contacts)
    destination.write_text(
        "# PDF 图像提取报告\n\n"
        f"来源：`{source}`\n\n"
        f"共提取 {len(figures)} 个嵌入图像对象，分布在 {len(pages)} 个 PDF 页面。{placeholder_line}\n\n"
        f"其中 {len(small_figures)} 个对象满足基础几何图候选的尺寸条件；这只是供人工审阅的筛选条件，不代表可自动生成正确的 TikZ。\n\n"
        "MinerU 图像占位符没有坐标或资源名，因此不能据此建立与 PDF 图像对象的一一对应关系。"
        "图像对象既可能是题图，也可能是封面、装饰或透明蒙版。所有对象均已保留页码和 PDF 对象编号，"
        "需要在联系表中确认题图语义后，才能可靠地重建为 TikZ。\n\n"
        "## 联系表\n\n"
        f"{contact_lines}\n\n"
        "## 后续处理原则\n\n"
        "- 坐标图、平面几何图、集合图和简单立体图可在确认点线关系后手工重建为 TikZ。\n"
        "- 统计图、复杂示意图、含大量文字或照片的图应保留为原始位图。\n"
        "- 不能用描边或自动矢量化替代重建，因为它无法保留虚实线、交点、标注和数学关系。\n",
        encoding="utf-8",
    )


def resolve_path(value: str, config_path: Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (config_path.parent / path).resolve()


def load_sources(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Source catalog must contain a non-empty sources list")
    for item in sources:
        if not isinstance(item, dict) or set(item) != {"slug", "source", "mineru_results"}:
            raise ValueError("Each source must contain slug, source, and mineru_results")
        if not all(isinstance(item[key], str) and item[key] for key in item):
            raise ValueError("Source values must be non-empty strings")
    return sources


def process_source(
    pdfimages: str,
    output_root: Path,
    config_path: Path,
    item: dict[str, str],
) -> dict[str, int | str]:
    slug = item["slug"]
    source = resolve_path(item["source"], config_path)
    results_dir = resolve_path(item["mineru_results"], config_path)
    if not source.is_file():
        raise FileNotFoundError(f"Source PDF does not exist: {source}")
    destination = output_root / slug
    if destination.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing extraction directory: {destination}"
        )
    images_dir = destination / "images"
    contacts_dir = destination / "contacts"
    destination.mkdir(parents=True)

    figures = read_pdf_images(pdfimages, source)
    extract_figures(pdfimages, source, figures, images_dir)
    contacts = make_contact_sheets(figures, images_dir, contacts_dir)
    placeholders = count_mineru_placeholders(results_dir)
    write_manifest(destination / "manifest.json", figures)
    write_report(destination / "report.md", source, figures, contacts, placeholders)
    return {
        "slug": slug,
        "figures": len(figures),
        "placeholders": placeholders if placeholders is not None else "",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract embedded PDF figures with page-level manifests and contact sheets."
    )
    parser.add_argument("--sources", type=Path, required=True, help="JSON source catalog")
    parser.add_argument("--output-root", type=Path, default=Path("converted/figure-extraction"))
    parser.add_argument("--pdfimages", default=shutil.which("pdfimages") or "pdfimages")
    parser.add_argument("--jobs", type=int, default=1, help="Number of independent PDFs to extract at once")
    parser.add_argument("--slugs", nargs="+", help="Only extract the named source entries")
    args = parser.parse_args()

    catalog = args.sources.resolve()
    output_root = args.output_root.resolve()
    sources = load_sources(catalog)
    if args.slugs is not None:
        requested_slugs = set(args.slugs)
        sources = [item for item in sources if item["slug"] in requested_slugs]
        missing_slugs = requested_slugs - {item["slug"] for item in sources}
        if missing_slugs:
            raise ValueError(f"Unknown source entries: {sorted(missing_slugs)}")
    if output_root.exists() and any(output_root.iterdir()):
        raise FileExistsError(
            f"Refusing to write into a non-empty output root: {output_root}"
        )
    if args.jobs < 1:
        raise ValueError("--jobs must be at least 1")
    output_root.mkdir(parents=True, exist_ok=True)

    if args.jobs == 1:
        summary = []
        for item in sources:
            result = process_source(args.pdfimages, output_root, catalog, item)
            summary.append(result)
            print(f"{result['slug']}: extracted {result['figures']} image objects", flush=True)
    else:
        completed: dict[str, dict[str, int | str]] = {}
        with ThreadPoolExecutor(max_workers=args.jobs) as executor:
            futures = [
                executor.submit(process_source, args.pdfimages, output_root, catalog, item)
                for item in sources
            ]
            for future in as_completed(futures):
                result = future.result()
                completed[str(result["slug"])] = result
                print(f"{result['slug']}: extracted {result['figures']} image objects", flush=True)
        summary = [completed[item["slug"]] for item in sources]

    with (output_root / "summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    total_figures = sum(int(item["figures"]) for item in summary)
    total_placeholders = sum(int(item["placeholders"]) for item in summary)
    print(f"Complete: {total_figures} PDF image objects; {total_placeholders} MinerU placeholders")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
