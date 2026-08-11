#!/usr/bin/env python3
"""
Append discovered candidates into categories/*.md.

Default DRY_RUN=1. Set DRY_RUN=0 to modify files / open PR.

Reads:
  - candidates/by_topic/*.json (preferred)
  - or candidates/all_candidates.json

Env:
  - DRY_RUN
  - GITHUB_TOKEN / GITHUB_REPOSITORY (only needed when applying for real + PR)
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import subprocess
import traceback
from typing import Dict, List

DRY_RUN = os.getenv("DRY_RUN", "1").lower() not in ("0", "false", "no")
SEEN_FILE = ".data/seen.json"
CANDIDATES_DIR = pathlib.Path("candidates/by_topic")
MERGED_FILE = pathlib.Path("candidates/all_candidates.json")
CATEGORIES_DIR = pathlib.Path("categories")
BRANCH_PREFIX = "updates"

# topic_id / category field -> markdown file
CATEGORY_FILES = {
    "research-industrial": CATEGORIES_DIR / "research-industrial.md",
    "research-trading": CATEGORIES_DIR / "research-trading.md",
    "research-credit": CATEGORIES_DIR / "research-credit.md",
    "research-products": CATEGORIES_DIR / "research-products.md",
    "data-aggregator": CATEGORIES_DIR / "data-aggregator.md",
    "data-indicators": CATEGORIES_DIR / "data-indicators.md",
    "data-knowledge": CATEGORIES_DIR / "data-knowledge.md",
}


def load_seen() -> set:
    p = pathlib.Path(SEEN_FILE)
    if not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return set()


def save_seen(seen: set) -> None:
    p = pathlib.Path(SEEN_FILE)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(sorted(seen), ensure_ascii=False, indent=2), encoding="utf-8")


def load_candidates() -> List[dict]:
    items: List[dict] = []
    if CANDIDATES_DIR.exists():
        for path in sorted(CANDIDATES_DIR.glob("*.json")):
            try:
                arr = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(arr, list):
                    items.extend(arr)
            except Exception as e:
                print("skip", path, e)
    elif MERGED_FILE.exists():
        items = json.loads(MERGED_FILE.read_text(encoding="utf-8"))
    return items if isinstance(items, list) else []


def format_entry(item: dict) -> str:
    name = item.get("full_name")
    url = item.get("url")
    desc = (item.get("desc") or "").replace("\n", " ").strip()
    stars = item.get("stars") or 0
    updated = (item.get("updated_at") or "")[:10]
    score = item.get("score")
    topic = item.get("topic_name") or item.get("topic_id") or ""
    score_part = f" score:{score}" if score is not None else ""
    topic_part = f" 专题：{topic}。" if topic else ""
    return (
        f"- [{name}]({url}) — {desc} (★{stars}{score_part})。"
        f"{topic_part}来源：{url}。最后更新时间：{updated}"
    )


def append_to_file(path: pathlib.Path, lines: List[str]) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {path.stem}\n\n", encoding="utf-8")
    existing = path.read_text(encoding="utf-8")
    with path.open("a", encoding="utf-8") as f:
        for ln in lines:
            if ln.strip() and ln not in existing:
                f.write("\n" + ln)


def git(cmd_args: List[str], check: bool = True) -> None:
    print("git", *cmd_args)
    subprocess.run(["git"] + cmd_args, check=check)


def create_branch_and_commit(files: List[str], branch: str, message: str) -> None:
    git(["checkout", "-b", branch])
    git(["config", "user.email", "actions@github.com"])
    git(["config", "user.name", "github-actions"])
    git(["add"] + files)
    git(["commit", "-m", message])
    git(["push", "--set-upstream", "origin", branch])


def create_pr(branch: str, title: str, body: str) -> None:
    try:
        subprocess.run(
            ["gh", "pr", "create", "--title", title, "--body", body, "--base", "main", "--head", branch],
            check=True,
        )
    except Exception:
        print("gh PR create failed; create manually:")
        print(branch, title, body)


def main() -> None:
    seen = load_seen()
    items = load_candidates()
    if not items:
        print("No candidates, nothing to do.")
        return

    grouped: Dict[str, List[dict]] = {}
    for it in items:
        name = it.get("full_name")
        if not name or name in seen:
            continue
        cat = it.get("category") or "research-industrial"
        grouped.setdefault(cat, []).append(it)

    changed = []
    new_seen = set()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for cat, cat_items in grouped.items():
        path = CATEGORY_FILES.get(cat)
        if not path:
            print(f"No category file for {cat}, skip")
            continue
        # keep best scores first
        cat_items.sort(key=lambda x: (-(x.get("score") or 0), -(x.get("stars") or 0)))
        lines = [format_entry(it) for it in cat_items]
        print(f"{cat}: will append {len(lines)} entries -> {path}")
        for ln in lines[:3]:
            print("  ", ln[:120])
        if DRY_RUN:
            continue
        append_to_file(path, lines)
        changed.append(str(path))
        for it in cat_items:
            new_seen.add(it.get("full_name"))

    if DRY_RUN:
        print("[DRY RUN] no files modified. Set DRY_RUN=0 to apply.")
        return

    if not changed:
        print("No changes.")
        return

    branch = f"{BRANCH_PREFIX}/{now}"
    try:
        create_branch_and_commit(changed, branch, f"Auto-update categories {now}")
        create_pr(
            branch,
            f"[Auto-update] thematic financial skills {now}",
            "Automated update from topic-based discovery. Please review scores and descriptions.",
        )
    except Exception:
        traceback.print_exc()

    seen |= new_seen
    save_seen(seen)
    print("Done.")


if __name__ == "__main__":
    main()
