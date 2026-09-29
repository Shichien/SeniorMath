import json
from pathlib import Path


ROOT = Path("converted/tikz-work")
REASON = "旧版 TikZ 与原图不一致，已撤销，待逐图重画后重新核验"


def main() -> None:
    for manifest in ROOT.rglob("manifest.json"):
        items = json.loads(manifest.read_text(encoding="utf-8"))
        changed = invalidate(items)
        if changed:
            manifest.write_text(
                json.dumps(items, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            print(f"{manifest}: invalidated {changed} entries")


def invalidate(value: object) -> int:
    if isinstance(value, list):
        return sum(invalidate(item) for item in value)
    if not isinstance(value, dict):
        return 0
    changed = 0
    if value.get("status") == "tikz":
        value["status"] = "review"
        value["reason"] = REASON
        changed += 1
    return changed + sum(invalidate(item) for item in value.values())


if __name__ == "__main__":
    main()
