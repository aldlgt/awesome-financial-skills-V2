#!/usr/bin/env python3
"""
Download high-quality candidate repos as zipballs.

Prefer topic-grouped candidates when present:
  candidates/by_topic/*.json  -> artifacts/skills/<topic_id>/<owner_repo>/
Also writes a flat copy under artifacts/skills/_all/ for convenience.

Env:
  - GITHUB_TOKEN
  - MAX_DOWNLOADS       total cap across all topics (default 80)
  - PER_TOPIC_DOWNLOADS per-topic cap (default 10)
  - CANDIDATES_JSON     fallback merged file (default candidates/all_candidates.json)
  - CANDIDATES_DIR      by-topic dir (default candidates/by_topic)
  - SKIP_EXISTING       default 1
  - TOPIC_IDS           optional comma filter
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import time
import traceback
import zipfile
from typing import Any

import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("SECRET_TOKEN")
MAX_DOWNLOADS = int(os.getenv("MAX_DOWNLOADS") or "80")
PER_TOPIC_DOWNLOADS = int(os.getenv("PER_TOPIC_DOWNLOADS") or "10")
CANDIDATES_JSON = os.getenv("CANDIDATES_JSON") or "candidates/all_candidates.json"
CANDIDATES_DIR = pathlib.Path(os.getenv("CANDIDATES_DIR") or "candidates/by_topic")
SKIP_EXISTING = os.getenv("SKIP_EXISTING", "1") not in ("0", "false", "no")
OUT_DIR = pathlib.Path("artifacts/skills")
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"Accept": "application/vnd.github+json"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"


def read_json_list(path: pathlib.Path | str) -> list[dict]:
    p = pathlib.Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception as e:
        print("Failed to read", p, e)
        return []


def load_candidates() -> list[dict]:
    items: list[dict] = []
    topic_filter = {
        x.strip() for x in os.getenv("TOPIC_IDS", "").split(",") if x.strip()
    }

    if CANDIDATES_DIR.exists():
        for path in sorted(CANDIDATES_DIR.glob("*.json")):
            tid = path.stem
            if topic_filter and tid not in topic_filter:
                continue
            for it in read_json_list(path):
                it = dict(it)
                it.setdefault("topic_id", tid)
                items.append(it)

    if not items:
        items = read_json_list(CANDIDATES_JSON)
        if topic_filter:
            items = [it for it in items if it.get("topic_id") in topic_filter]

    # Prefer higher score first
    items.sort(key=lambda x: (x.get("topic_id") or "", -(x.get("score") or 0), -(x.get("stars") or 0)))
    return items


def safe_name(full_name: str) -> str:
    return full_name.replace("/", "_")


def download_zipball(full_name: str, dest_zip: pathlib.Path, max_retries: int = 3) -> bool:
    url = f"https://api.github.com/repos/{full_name}/zipball"
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, allow_redirects=True, timeout=90)
            if r.status_code == 200:
                dest_zip.write_bytes(r.content)
                return True
            print(f"  download fail {full_name}: {r.status_code}")
            if r.status_code == 403:
                time.sleep(5 * attempt)
            else:
                time.sleep(2 * attempt)
        except Exception as e:
            print(f"  exception {full_name}: {e}")
            traceback.print_exc()
            time.sleep(2 * attempt)
    return False


def extract_zip(zip_path: pathlib.Path, dest_dir: pathlib.Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(dest_dir)


def select_downloads(items: list[dict]) -> list[dict]:
    """Cap per topic and globally; dedupe by full_name."""
    seen = set()
    per_topic: dict[str, int] = {}
    todo = []
    for it in items:
        name = it.get("full_name")
        if not name or name in seen:
            continue
        tid = it.get("topic_id") or "unknown"
        if per_topic.get(tid, 0) >= PER_TOPIC_DOWNLOADS:
            continue
        seen.add(name)
        per_topic[tid] = per_topic.get(tid, 0) + 1
        todo.append(it)
        if len(todo) >= MAX_DOWNLOADS:
            break
    return todo


def write_meta(dest_dir: pathlib.Path, meta: dict[str, Any]) -> None:
    (dest_dir / "candidate_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> None:
    items = load_candidates()
    if not items:
        print("No candidates found, exiting.")
        return

    todo = select_downloads(items)
    print(
        f"Downloading {len(todo)} repos "
        f"(MAX_DOWNLOADS={MAX_DOWNLOADS}, PER_TOPIC={PER_TOPIC_DOWNLOADS})"
    )

    failed = []
    for idx, entry in enumerate(todo, start=1):
        full = entry["full_name"]
        tid = entry.get("topic_id") or "unknown"
        topic_dir = OUT_DIR / tid
        topic_dir.mkdir(parents=True, exist_ok=True)
        zip_path = topic_dir / f"{safe_name(full)}.zip"
        extract_dir = topic_dir / safe_name(full)

        if SKIP_EXISTING and (zip_path.exists() or extract_dir.exists()):
            print(f"[{idx}/{len(todo)}] skip existing {tid}/{full}")
            continue

        print(f"[{idx}/{len(todo)}] {tid} :: {full}")
        ok = download_zipball(full, zip_path)
        if not ok:
            failed.append(entry)
            continue

        try:
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_zip(zip_path, extract_dir)
            write_meta(extract_dir, entry)
            # keep disk lean: remove zip after extract
            zip_path.unlink(missing_ok=True)
        except Exception as e:
            print("  extract failed:", e)
            failed.append(entry)

    # Persist run outputs for artifact upload
    merged = pathlib.Path(CANDIDATES_JSON)
    if merged.exists():
        shutil.copy(merged, OUT_DIR / "all_candidates.json")

    if CANDIDATES_DIR.exists():
        dest_topics = OUT_DIR / "by_topic"
        if dest_topics.exists():
            shutil.rmtree(dest_topics)
        shutil.copytree(CANDIDATES_DIR, dest_topics)

    if failed:
        (OUT_DIR / "failed_candidates.json").write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Failed: {len(failed)}")

    print("Done ->", OUT_DIR)


if __name__ == "__main__":
    main()
