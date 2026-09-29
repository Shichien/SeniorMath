import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path


PAGE_RANGE_PATTERN = re.compile(r"_p(\d+)-(\d+)\.md$")
IMAGE_PATTERN = re.compile(r"<!--\s*image", re.IGNORECASE)
MALFORMED_RATING_PATTERN = re.compile(
    r"\$\(\s*[^$\n]*?(?:\\star|\\bigstar)[^$\n]*?\)\$"
)
MALFORMED_RATING_DISPLAY_PATTERN = re.compile(
    r"\$\$\s*\(\s*([^$\n]*?(?:\\star|\\bigstar)[^$\n]*?)\s*\)\s*\$\$",
    re.DOTALL,
)
UNICODE_STARS_PATTERN = re.compile(r"[★☆]+")
SPACED_INLINE_MATH_PATTERN = re.compile(r"\$[ \t]+([^$\n]*?)[ \t]*\$")
ESCAPED_DOLLAR_PATTERN = re.compile(r"\\\$")
RAW_INLINE_MATH_PATTERN = re.compile(r"\$([^$\n]+)\$")
DISPLAY_MATH_PATTERN = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
INLINE_MATH_PATTERN = re.compile(r"(?<!\$)\$([^$\n]+)\$(?!\$)")
MATH_SEGMENT_PATTERN = re.compile(
    r"(\$\$.*?\$\$|\$[^$\n]*\$|\\\\\[.*?\\\\\]|\\\\\([^\n]*?\\\\\))",
    re.DOTALL,
)
MISPLACED_ARRAY_CLOSER_PATTERN = re.compile(
    r"(\\begin\s*\{\s*array\s*\}\s*\{\s*[^{}]+\s*\}\s*&\s*\{.*?\\ddots.*?)(\\end\s*\{\s*array\s*\})\s*\}",
    re.DOTALL,
)
ARRAY_PREAMBLE_PATTERN = re.compile(
    r"(\\begin\s*\{\s*array\s*\}\s*\{)([^{}]*)(\})"
)
MALFORMED_LEFT_BRACE_PATTERN = re.compile(r"\\left\s*\{")
MALFORMED_RIGHT_BRACE_PATTERN = re.compile(r"\\right\s*\}")
MALFORMED_RIGHT_VERTICAL_BAR_PATTERN = re.compile(r"\}\s*\{\s*\\right\s*\|\s*\}")
MALFORMED_MULTIPLE_CHOICE_PLACEHOLDER_PATTERN = re.compile(
    r"\s*\\mathcal\s*\{\s*\\left(?:\\\{|\{)\s*\\pm\s*"
    r"\\begin\s*\{\s*array\s*\}\s*\{\s*l\s*l\s*\}\s*"
    r"\{\s*\\quad\s*\}\s*&\s*\{\s*\\quad\s*\}\s*"
    r"\\end\s*\{\s*array\s*\}\s*\\right\.\s*\}\s*\}"
)
PUNCTUATION_SUPERSCRIPT_PATTERN = re.compile(r"\s*\^\s*\{\s*[,;:]+\s*\}")
INCOMPLETE_SQRT_PATTERN = re.compile(r"\\sqrt\s*\$\s*([A-Za-z0-9])")
TEXTCIRCLED_PATTERN = re.compile(r"\\textcircled\s*\{\s*([^{}]+?)\s*\}")
LOWERCASE_MATHSCR_PATTERN = re.compile(r"\\mathscr\s*\{\s*([a-z])\s*\}")
SMALL_COMMAND_PATTERN = re.compile(r"\\small\b")
MALFORMED_DELIMITER_BLOCK_PATTERN = re.compile(
    r"\$\$.*?(?:\\Biggr\s*\|\s*\}\s*-\s*\{\s*){10,}.*?\$\$", re.DOTALL
)
SYMBOL_REPLACEMENTS = {
    "①": "（1）",
    "②": "（2）",
    "③": "（3）",
    "④": "（4）",
    "⑤": "（5）",
    "⑥": "（6）",
    "⑦": "（7）",
    "⑧": "（8）",
    "⑨": "（9）",
    "⑩": "（10）",
    "\uf047": r"$\Gamma$",
}
CONTEXTUAL_MATH_SYMBOLS = {
    "△": r"\triangle",
    "∠": r"\angle",
    "⊥": r"\perp",
    "∴": r"\therefore",
    "∵": r"\because",
    "∈": r"\in",
    "∉": r"\notin",
    "∩": r"\cap",
    "∪": r"\cup",
    "⊂": r"\subset",
    "⊆": r"\subseteq",
    "⊃": r"\supset",
    "⊇": r"\supseteq",
    "∅": r"\varnothing",
    "′": r"^\prime",
    "𝑛": "n",
    "∀": r"\forall",
    "∃": r"\exists",
    "⇒": r"\Rightarrow",
    "↔": r"\Leftrightarrow",
    "→": r"\to",
}


def page_range(path: Path) -> tuple[int, int]:
    match = PAGE_RANGE_PATTERN.search(path.name)
    if match is None:
        raise ValueError(f"Cannot determine page range from {path.name}")
    return int(match.group(1)), int(match.group(2))


def collect_markdown(results_dir: Path, expected_pages: int) -> list[Path]:
    files = sorted(results_dir.glob("*.md"), key=lambda item: page_range(item)[0])
    if not files:
        raise ValueError(f"No Markdown files found in {results_dir}")

    expected_start = 1
    for path in files:
        start, end = page_range(path)
        if start != expected_start or end < start:
            raise ValueError(
                f"Page coverage is not continuous at {path.name}: expected {expected_start}"
            )
        expected_start = end + 1

    if expected_start - 1 != expected_pages:
        raise ValueError(
            f"Page coverage ends at {expected_start - 1}, expected {expected_pages}"
        )
    return files


def pandoc_to_latex(pandoc: str, source: Path, destination: Path, prefix: str) -> None:
    command = [
        pandoc,
        "--from=markdown+tex_math_dollars+raw_tex",
        "--to=latex",
        "--wrap=none",
        f"--id-prefix={prefix}",
        "--output",
        str(destination),
        str(source),
    ]
    subprocess.run(command, check=True)


def load_corrections(path: Path | None) -> list[dict[str, str]]:
    if path is None:
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    corrections = data.get("replacements")
    if not isinstance(corrections, list):
        raise ValueError("Correction file must contain a replacements list")
    for correction in corrections:
        if not isinstance(correction, dict):
            raise ValueError("Each correction must be an object")
        keys = set(correction)
        range_keys = {"id", "start", "end", "replacement"}
        match_keys = {"id", "match", "replacement"}
        if keys not in (range_keys, match_keys):
            raise ValueError(
                "Each correction must be a range replacement or an exact replacement"
            )
        if not all(isinstance(value, str) for value in correction.values()):
            raise ValueError("Correction values must all be strings")
        required_values = ("id", "match") if keys == match_keys else ("id", "start", "end")
        if not all(correction[key] for key in required_values):
            raise ValueError("Correction markers must not be empty")
    return corrections


def apply_corrections(
    source: str, corrections: list[dict[str, str]]
) -> tuple[str, list[str]]:
    applied = []
    for correction in corrections:
        if "match" in correction:
            match = correction["match"]
            occurrences = source.count(match)
            if occurrences == 0:
                continue
            if occurrences != 1:
                raise ValueError(
                    f"Correction {correction['id']} match marker must occur exactly once"
                )
            source = source.replace(match, correction["replacement"])
            applied.append(correction["id"])
            continue
        start = correction["start"]
        end = correction["end"]
        occurrences = source.count(start)
        if occurrences == 0:
            continue
        if occurrences != 1:
            raise ValueError(
                f"Correction {correction['id']} start marker must occur exactly once"
            )
        start_index = source.index(start)
        end_index = source.find(end, start_index + len(start))
        if end_index == -1:
            raise ValueError(f"Correction {correction['id']} end marker was not found")
        end_index += len(end)
        source = source[:start_index] + correction["replacement"] + source[end_index:]
        applied.append(correction["id"])
    return source, applied


def matching_group_end(source: str, start: int) -> int | None:
    depth = 0
    index = start
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def repair_fraction_braces(source: str) -> tuple[str, int]:
    repairs = 0
    index = 0
    while True:
        fraction = source.find(r"\frac", index)
        if fraction == -1:
            break
        numerator_start = fraction + len(r"\frac")
        while numerator_start < len(source) and source[numerator_start].isspace():
            numerator_start += 1
        if numerator_start >= len(source) or source[numerator_start] != "{":
            index = numerator_start
            continue
        numerator_end = matching_group_end(source, numerator_start)
        if numerator_end is None:
            index = numerator_start + 1
            continue
        extra_closer = numerator_end + 1
        while extra_closer < len(source) and source[extra_closer].isspace():
            extra_closer += 1
        denominator_start = extra_closer + 1
        while denominator_start < len(source) and source[denominator_start].isspace():
            denominator_start += 1
        if (
            extra_closer < len(source)
            and source[extra_closer] == "}"
            and denominator_start < len(source)
            and source[denominator_start] == "{"
        ):
            source = source[:extra_closer] + source[extra_closer + 1 :]
            repairs += 1
            index = denominator_start - 1
        else:
            denominator_start = numerator_end + 1
            while denominator_start < len(source) and source[denominator_start].isspace():
                denominator_start += 1
            incomplete = source.startswith(r"\end", denominator_start) or source.startswith(
                r"\\", denominator_start
            )
            incomplete = incomplete or (
                denominator_start < len(source)
                and source[denominator_start] in "}$"
            )
            if incomplete:
                source = source[:fraction] + source[fraction + len(r"\frac") :]
                repairs += 1
                index = numerator_end - len(r"\frac")
            else:
                index = numerator_end + 1
    return source, repairs


def repair_array_preambles(source: str) -> tuple[str, int]:
    repairs = 0

    def replace_preamble(match: re.Match[str]) -> str:
        nonlocal repairs
        preamble = match.group(2)
        repaired = "".join(
            character if character in "lrcpmb" or not character.isalpha() else "c"
            for character in preamble
        )
        repairs += sum(left != right for left, right in zip(preamble, repaired))
        return match.group(1) + repaired + match.group(3)

    return ARRAY_PREAMBLE_PATTERN.sub(replace_preamble, source), repairs


def brace_balance(source: str) -> int:
    balance = 0
    index = 0
    while index < len(source):
        if source[index] == "\\":
            index += 2
            continue
        if source[index] == "{":
            balance += 1
        elif source[index] == "}":
            balance -= 1
        index += 1
    return balance


def close_unclosed_math_groups(source: str) -> tuple[str, int]:
    repairs = 0

    def close_groups(match: re.Match[str]) -> str:
        nonlocal repairs
        missing = max(brace_balance(match.group(1)), 0)
        repairs += missing
        return match.group(0)[:-2] + "}" * missing + "$$"

    source = DISPLAY_MATH_PATTERN.sub(close_groups, source)

    def close_inline_groups(match: re.Match[str]) -> str:
        nonlocal repairs
        missing = max(brace_balance(match.group(1)), 0)
        repairs += missing
        return "$" + match.group(1) + "}" * missing + "$"

    return INLINE_MATH_PATTERN.sub(close_inline_groups, source), repairs


def replace_contextual_math_symbols(source: str) -> tuple[str, int]:
    changes = 0
    parts = MATH_SEGMENT_PATTERN.split(source)
    for index, part in enumerate(parts):
        if not part:
            continue
        in_math = index % 2 == 1
        for symbol, latex in CONTEXTUAL_MATH_SYMBOLS.items():
            occurrences = part.count(symbol)
            if occurrences:
                replacement = latex if in_math else " $" + latex + "$ "
                parts[index] = parts[index].replace(symbol, replacement)
                changes += occurrences
    return "".join(parts), changes


def strip_pandoc_labels(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    path.write_text(re.sub(r"\\label\{[^{}]*\}", "", source), encoding="utf-8")


def normalise_markdown(source: str) -> tuple[str, int]:
    changes = 0

    def replace_malformed_rating(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        count = len(re.findall(r"\\(?:big)?star", match.group(0)))
        return "（难度：$" + " ".join(r"\star" for _ in range(count)) + "$）"

    source = MALFORMED_RATING_PATTERN.sub(replace_malformed_rating, source)

    def replace_malformed_display_rating(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        count = len(re.findall(r"\\(?:big)?star", match.group(1)))
        return "（难度：$" + " ".join(r"\star" for _ in range(count)) + "$）"

    source = MALFORMED_RATING_DISPLAY_PATTERN.sub(
        replace_malformed_display_rating, source
    )
    source, fraction_repairs = repair_fraction_braces(source)
    changes += fraction_repairs
    source, punctuation_superscripts = PUNCTUATION_SUPERSCRIPT_PATTERN.subn("", source)
    changes += punctuation_superscripts
    source, incomplete_sqrts = INCOMPLETE_SQRT_PATTERN.subn(r"\\sqrt{\1}$", source)
    changes += incomplete_sqrts
    for malformed, corrected in ((r"\llangle", r"\langle"), (r"\rrangle", r"\rangle")):
        occurrences = source.count(malformed)
        if occurrences:
            source = source.replace(malformed, corrected)
            changes += occurrences
    mathbfit_commands = source.count(r"\mathbfit")
    if mathbfit_commands:
        source = source.replace(r"\mathbfit", r"\mathbf")
        changes += mathbfit_commands
    source, array_preamble_repairs = repair_array_preambles(source)
    changes += array_preamble_repairs
    source, malformed_left_braces = MALFORMED_LEFT_BRACE_PATTERN.subn(r"\\left\\{", source)
    source, malformed_right_braces = MALFORMED_RIGHT_BRACE_PATTERN.subn(
        r"\\right\\}", source
    )
    source, malformed_right_vertical_bars = MALFORMED_RIGHT_VERTICAL_BAR_PATTERN.subn(
        r"\\right|", source
    )
    changes += (
        malformed_left_braces + malformed_right_braces + malformed_right_vertical_bars
    )
    source, malformed_choice_placeholders = MALFORMED_MULTIPLE_CHOICE_PLACEHOLDER_PATTERN.subn(
        "（ ）", source
    )
    changes += malformed_choice_placeholders
    escaped_dollars = len(ESCAPED_DOLLAR_PATTERN.findall(source))
    if escaped_dollars:
        source = ESCAPED_DOLLAR_PATTERN.sub("$", source)
        changes += escaped_dollars

    def replace_spaced_inline_math(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        return "$" + match.group(1).strip() + "$"

    source = SPACED_INLINE_MATH_PATTERN.sub(replace_spaced_inline_math, source)

    def separate_inline_math(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        return " $" + match.group(1).strip() + "$ "

    source = RAW_INLINE_MATH_PATTERN.sub(separate_inline_math, source)

    def move_array_closer(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        return match.group(1) + "} " + match.group(2)

    source = MISPLACED_ARRAY_CLOSER_PATTERN.sub(move_array_closer, source)

    def replace_unicode_stars(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        return "$" + " ".join(r"\star" for _ in match.group(0)) + "$"

    source = UNICODE_STARS_PATTERN.sub(replace_unicode_stars, source)

    def replace_circled_number(match: re.Match[str]) -> str:
        nonlocal changes
        changes += 1
        content = match.group(1).strip()
        if content.startswith("\\"):
            content = r"\ensuremath{" + content + "}"
        return r"\text{\textcircled{" + content + "}}"

    source = TEXTCIRCLED_PATTERN.sub(replace_circled_number, source)
    lowercase_mathscr = len(LOWERCASE_MATHSCR_PATTERN.findall(source))
    if lowercase_mathscr:
        source = LOWERCASE_MATHSCR_PATTERN.sub(r"\1", source)
        changes += lowercase_mathscr
    small_commands = len(SMALL_COMMAND_PATTERN.findall(source))
    if small_commands:
        source = SMALL_COMMAND_PATTERN.sub("", source)
        changes += small_commands
    for symbol, replacement in SYMBOL_REPLACEMENTS.items():
        occurrences = source.count(symbol)
        if occurrences:
            source = source.replace(symbol, replacement)
            changes += occurrences
    source, contextual_symbol_changes = replace_contextual_math_symbols(source)
    changes += contextual_symbol_changes
    compatibility_normalised = unicodedata.normalize("NFKC", source)
    changes += sum(left != right for left, right in zip(source, compatibility_normalised))
    source = compatibility_normalised
    source, group_repairs = close_unclosed_math_groups(source)
    changes += group_repairs
    return source, changes


def write_document(path: Path, title: str, content_path: Path) -> None:
    content_reference = content_path.as_posix()
    source = f"""\\special{{dvipdfmx:config z 3}}
\\documentclass[lang=cn,10pt,color=cyan,titlestyle=hang,device=normal]{{elegantbook}}
\\input{{additional.tex}}

\\providecommand{{\\tightlist}}{{%
  \\setlength{{\\itemsep}}{{0pt}}\\setlength{{\\parskip}}{{0pt}}}}

\\setcounter{{tocdepth}}{{3}}
\\title{{{title}}}
\\author{{Shichien}}
\\institute{{数学讲义转写}}
\\date{{2026}}
\\version{{1.0}}
\\extrainfo{{由原始讲义整理而成}}

\\begin{{document}}
\\maketitle
\\frontmatter
\\tableofcontents
\\mainmatter
\\chapter{{{title}}}
\\input{{{content_reference}}}
\\end{{document}}
"""
    path.write_text(source, encoding="utf-8")


def build_report(files: list[Path], expected_pages: int, title: str) -> dict:
    chunks = []
    for path in files:
        source = path.read_text(encoding="utf-8")
        start, end = page_range(path)
        chunks.append(
            {
                "file": path.name,
                "pages": [start, end],
                "characters": len(source),
                "image_placeholders": len(IMAGE_PATTERN.findall(source)),
            }
        )
    return {
        "title": title,
        "expected_pages": expected_pages,
        "parsed_pages": sum(end - start + 1 for start, end in (page_range(p) for p in files)),
        "chunks": chunks,
        "image_placeholders": sum(chunk["image_placeholders"] for chunk in chunks),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert complete MinerU Markdown chunks into project-template LaTeX."
    )
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument("--pages", type=int, required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--slug", required=True)
    parser.add_argument("--output-root", type=Path, default=Path("converted"))
    parser.add_argument("--pandoc", default="pandoc")
    parser.add_argument(
        "--corrections",
        type=Path,
        help="JSON file containing verified source-text corrections",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.pages < 1:
        raise SystemExit("--pages must be positive")

    results_dir = args.results_dir.resolve()
    files = collect_markdown(results_dir, args.pages)
    output_dir = args.output_root / args.slug
    content_path = output_dir / "content.tex"
    document_path = args.output_root / f"{args.slug}.tex"
    report_path = output_dir / "conversion-report.json"
    corrections = load_corrections(args.corrections)

    existing = [path for path in (content_path, document_path, report_path) if path.exists()]
    if existing and not args.overwrite:
        names = ", ".join(str(path) for path in existing)
        raise SystemExit(f"Output already exists: {names}. Use --overwrite to replace it.")

    output_dir.mkdir(parents=True, exist_ok=True)
    converted_chunks = []
    normalisation_changes = 0
    applied_corrections = []
    suspicious_math_blocks = 0
    cleaned_dir = output_dir / "cleaned-markdown"
    cleaned_dir.mkdir(exist_ok=True)
    for source_path in files:
        start, _ = page_range(source_path)
        cleaned_path = cleaned_dir / source_path.name
        converted_path = output_dir / f"chunk-{start:03d}.tex"
        source, applied = apply_corrections(
            source_path.read_text(encoding="utf-8"), corrections
        )
        applied_corrections.extend(applied)
        normalised, changes = normalise_markdown(source)
        suspicious_math_blocks += len(MALFORMED_DELIMITER_BLOCK_PATTERN.findall(normalised))
        cleaned_path.write_text(normalised, encoding="utf-8")
        normalisation_changes += changes
        pandoc_to_latex(args.pandoc, cleaned_path, converted_path, f"{args.slug}-{start:03d}-")
        strip_pandoc_labels(converted_path)
        converted_chunks.append(converted_path)

    expected_corrections = [correction["id"] for correction in corrections]
    if sorted(applied_corrections) != sorted(expected_corrections):
        raise ValueError("Each configured correction must apply exactly once")

    content = "\n\n".join(
        f"% Source pages {page_range(source_path)[0]}-{page_range(source_path)[1]}\n"
        f"\\input{{{converted_path.as_posix()}}}"
        for source_path, converted_path in zip(files, converted_chunks)
    )
    content_path.write_text(content + "\n", encoding="utf-8")
    write_document(document_path, args.title, content_path)
    report = build_report(files, args.pages, args.title)
    report["normalisation_changes"] = normalisation_changes
    report["applied_corrections"] = applied_corrections
    report["suspicious_math_blocks"] = suspicious_math_blocks
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )

    print(document_path)
    print(content_path)
    print(report_path)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
