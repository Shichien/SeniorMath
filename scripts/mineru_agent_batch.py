import argparse
import json
import os
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import requests
from pypdf import PdfReader, PdfWriter


AGENT_CREATE_URL = "https://mineru.net/api/v1/agent/parse/file"
AGENT_TASK_URL = "https://mineru.net/api/v1/agent/parse/{task_id}"
MAX_PAGES = 20
MAX_BYTES = 10 * 1024 * 1024
RETRYABLE_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ProxyError,
)


def safe_stem(path):
    return "".join(ch if ch.isalnum() else "_" for ch in path.stem).strip("_")


def request_with_retry(method, url, *, attempts, **kwargs):
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            data = kwargs.get("data")
            if hasattr(data, "seek"):
                data.seek(0)
            return requests.request(method, url, **kwargs)
        except RETRYABLE_EXCEPTIONS as error:
            last_error = error
            if attempt == attempts:
                break
            time.sleep(min(2 ** attempt, 10))
    raise last_error


def upload_proxies(file_url):
    host = urlparse(file_url).hostname or ""
    if host.endswith("aliyuncs.com"):
        return {"http": "", "https": ""}
    return None


def write_chunk(reader, source_path, chunks_dir, start, end):
    writer = PdfWriter()
    for page_index in range(start, end):
        writer.add_page(reader.pages[page_index])

    chunk_path = chunks_dir / f"{safe_stem(source_path)}_p{start + 1:03d}-{end:03d}.pdf"
    with chunk_path.open("wb") as handle:
        writer.write(handle)

    return {
        "path": chunk_path,
        "start": start + 1,
        "end": end,
        "pages": end - start,
        "size": chunk_path.stat().st_size,
    }


def split_interval(reader, source_path, chunks_dir, start, end, chunks):
    item = write_chunk(reader, source_path, chunks_dir, start, end)
    if item["pages"] <= MAX_PAGES and item["size"] <= MAX_BYTES:
        chunks.append(item)
        return

    if item["pages"] == 1:
        chunks.append(item)
        return

    item["path"].unlink(missing_ok=True)
    mid = start + (end - start) // 2
    split_interval(reader, source_path, chunks_dir, start, mid, chunks)
    split_interval(reader, source_path, chunks_dir, mid, end, chunks)


def split_pdf(path, chunks_dir, pages_per_chunk):
    reader = PdfReader(str(path))
    chunks_dir.mkdir(parents=True, exist_ok=True)
    chunks = []

    total_pages = len(reader.pages)
    for start in range(0, total_pages, pages_per_chunk):
        end = min(start + pages_per_chunk, total_pages)
        split_interval(reader, path, chunks_dir, start, end, chunks)

    return chunks


def create_task(chunk_path, timeout, attempts):
    data = {
        "file_name": chunk_path.name,
        "language": "ch",
        "page_range": f"1-{len(PdfReader(str(chunk_path)).pages)}",
        "enable_table": True,
        "is_ocr": True,
        "enable_formula": True,
    }
    response = request_with_retry(
        "POST",
        AGENT_CREATE_URL,
        json=data,
        headers={"User-Agent": "mineru-agent-batch/1.0"},
        timeout=timeout,
        attempts=attempts,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") not in (0, 200, "0", "200", None):
        raise RuntimeError(f"task rejected: {payload}")

    data_payload = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    task_id = payload.get("task_id") or data_payload.get("task_id")
    file_url = payload.get("file_url") or data_payload.get("file_url")
    if not task_id or not file_url:
        raise RuntimeError(f"task id or upload url missing: {payload}")
    return task_id, file_url, payload


def upload_chunk(file_url, chunk_path, timeout, attempts):
    with chunk_path.open("rb") as handle:
        response = request_with_retry(
            "PUT",
            file_url,
            data=handle,
            timeout=timeout,
            attempts=attempts,
            proxies=upload_proxies(file_url),
        )
    if response.status_code >= 400:
        raise requests.exceptions.HTTPError(
            f"{response.status_code} upload failed: {response.text[:500]}",
            response=response,
        )
    return {"status_code": response.status_code, "headers": dict(response.headers)}


def get_download_url(payload):
    if not isinstance(payload, dict):
        return None

    candidates = [
        payload.get("full_zip_url"),
        payload.get("zip_url"),
        payload.get("markdown_url"),
        payload.get("md_link"),
        payload.get("md_url"),
        payload.get("download_url"),
        payload.get("result_url"),
    ]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend(
            [
                data.get("full_zip_url"),
                data.get("zip_url"),
                data.get("markdown_url"),
                data.get("md_link"),
                data.get("md_url"),
                data.get("download_url"),
                data.get("result_url"),
            ]
        )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.startswith("http"):
            return candidate
    return None


def poll_task(task_id, timeout, interval, request_timeout, attempts):
    deadline = time.time() + timeout
    last_payload = None
    while time.time() < deadline:
        response = request_with_retry(
            "GET",
            AGENT_TASK_URL.format(task_id=task_id),
            headers={"User-Agent": "mineru-agent-batch/1.0"},
            timeout=request_timeout,
            attempts=attempts,
        )
        response.raise_for_status()
        payload = response.json()
        last_payload = payload

        state = (
            payload.get("state")
            or payload.get("status")
            or payload.get("data", {}).get("state")
            or payload.get("data", {}).get("status")
        )
        download_url = get_download_url(payload)
        if download_url:
            return payload
        if str(state).lower() in {"failed", "fail", "error", "-1"}:
            raise RuntimeError(f"task failed: {payload}")

        time.sleep(interval)

    raise TimeoutError(f"task timeout: {last_payload}")


def download_result(url, output_dir, stem, timeout, attempts):
    output_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(urlparse(url).path).suffix or ".zip"
    target = output_dir / f"{stem}{suffix}"
    response = request_with_retry(
        "GET",
        url,
        stream=True,
        timeout=timeout,
        attempts=attempts,
        proxies=upload_proxies(url),
    )
    with response:
        response.raise_for_status()
        with target.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    handle.write(chunk)
    return target


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def saved_result(results_dir, stem):
    return next(results_dir.glob(f"{stem}.*"), None)


def process_pdf(path, output_dir, pages_per_chunk, poll_timeout, poll_interval, request_timeout, attempts, max_chunks, dry_run):
    path = Path(path).expanduser().resolve()
    base_dir = output_dir / safe_stem(path)
    chunks_dir = base_dir / "chunks"
    results_dir = base_dir / "results"
    logs_dir = base_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    chunks = split_pdf(path, chunks_dir, pages_per_chunk)
    too_large = [item for item in chunks if item["size"] > MAX_BYTES]
    summary = {
        "source": str(path),
        "chunks": [
            {
                "file": str(item["path"]),
                "pages": [item["start"], item["end"]],
                "size": item["size"],
                "too_large_for_agent": item["size"] > MAX_BYTES,
            }
            for item in chunks
        ],
    }
    (logs_dir / "split_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{path.name}: split into {len(chunks)} chunks")
    if too_large:
        print("These chunks are larger than 10 MB and will not be uploaded:")
        for item in too_large:
            print(f"  {item['path'].name}: {item['size'] / 1024 / 1024:.2f} MB")

    if dry_run:
        return

    uploadable_chunks = [item for item in chunks if item["size"] <= MAX_BYTES]
    if max_chunks is not None:
        uploadable_chunks = uploadable_chunks[:max_chunks]

    for index, item in enumerate(uploadable_chunks, 1):
        if item["size"] > MAX_BYTES:
            continue
        chunk_path = item["path"]
        stem = chunk_path.stem
        if saved_result(results_dir, stem):
            print(f"[{index}/{len(uploadable_chunks)}] already saved {chunk_path.name}", flush=True)
            continue

        create_path = logs_dir / f"{stem}.create.json"
        if create_path.exists():
            create_payload = load_json(create_path)
            task_id = create_payload.get("task_id") or create_payload.get("data", {}).get("task_id")
            file_url = create_payload.get("file_url") or create_payload.get("data", {}).get("file_url")
            if not task_id or not file_url:
                raise RuntimeError(f"invalid saved task payload: {create_path}")
            print(f"[{index}/{len(uploadable_chunks)}] resume task {chunk_path.name}", flush=True)
        else:
            print(f"[{index}/{len(uploadable_chunks)}] create task {chunk_path.name}", flush=True)
            task_id, file_url, create_payload = create_task(chunk_path, request_timeout, attempts)
            create_path.write_text(
                json.dumps(create_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        upload_path = logs_dir / f"{stem}.upload.json"
        if not upload_path.exists():
            print(f"[{index}/{len(uploadable_chunks)}] upload {chunk_path.name}", flush=True)
            upload_payload = upload_chunk(file_url, chunk_path, request_timeout, attempts)
            upload_path.write_text(
                json.dumps(upload_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        result_path = logs_dir / f"{stem}.result.json"
        if result_path.exists():
            result_payload = load_json(result_path)
            print(f"[{index}/{len(uploadable_chunks)}] download completed task {task_id}", flush=True)
        else:
            print(f"[{index}/{len(uploadable_chunks)}] poll task {task_id}", flush=True)
            result_payload = poll_task(task_id, poll_timeout, poll_interval, request_timeout, attempts)
            result_path.write_text(
                json.dumps(result_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        download_url = get_download_url(result_payload)
        target = download_result(download_url, results_dir, stem, request_timeout, attempts)
        print(f"[{index}/{len(uploadable_chunks)}] saved {target}", flush=True)


def default_desktop_pdfs():
    desktop = Path.home() / "Desktop"
    names = [
        "【2021寒】快数学高二讲义_冲顶班_全国通用版.pdf",
        "【一轮闯关训练】-三角.pdf",
    ]
    return [desktop / name for name in names if (desktop / name).exists()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdfs", nargs="*", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/mineru-agent"))
    parser.add_argument("--pages", type=int, default=MAX_PAGES)
    parser.add_argument("--poll-timeout", type=int, default=30 * 60)
    parser.add_argument("--poll-interval", type=int, default=10)
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--max-chunks", type=int)
    parser.add_argument("--run-id", help="Resume or write a specific output run directory")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.pages < 1 or args.pages > MAX_PAGES:
        raise SystemExit("Agent API allows at most 20 pages per file.")

    pdfs = args.pdfs or default_desktop_pdfs()
    if not pdfs:
        raise SystemExit("No PDF files provided and default desktop files were not found.")

    run_id = args.run_id or uuid.uuid4().hex[:8]
    output_dir = args.output / run_id
    for pdf in pdfs:
        process_pdf(
            pdf,
            output_dir,
            args.pages,
            args.poll_timeout,
            args.poll_interval,
            args.request_timeout,
            args.attempts,
            args.max_chunks,
            args.dry_run,
        )

    print(f"output: {output_dir.resolve()}")


if __name__ == "__main__":
    main()
