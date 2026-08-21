#!/usr/bin/env python3
"""
Download high-quality candidate repos as zipballs.

Default for CI: keep .zip only (do NOT extract). Extracted trees often contain
filenames with ':' etc. that GitHub Actions artifacts reject.

Env:
  - GITHUB_TOKEN
  - TARGET_PER_TOPIC    used as default per-topic download cap
  - PER_TOPIC_DOWNLOADS per-topic cap (default: TARGET_PER_TOPIC or 10)
  - MAX_DOWNLOADS       total cap (default: max(80, PER_TOPIC_DOWNLOADS*20))
  - MAX_SKILL_SIZE_MB   skip a repo if zip exceeds this many MB (0 = no limit)
  - CANDIDATES_JSON     default candidates/all_candidates.json
  - CANDIDATES_DIR      default candidates/by_topic
  - SKIP_EXISTING       default 1
  - TOPIC_IDS           optional comma filter
  - EXTRACT_ZIPS        set 1 to extract locally (default 0)
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import time
import traceback
import zipfile
from typing import Any

import requests

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN") or os.getenv("SECRET_TOKEN")
# Prefer explicit PER_TOPIC_DOWNLOADS; otherwise follow TARGET_PER_TOPIC so
# downloaded zips match the scored candidate list size.
_DEFAULT_PER_TOPIC = os.getenv("TARGET_PER_TOPIC") or "10"
PER_TOPIC_DOWNLOADS = int(os.getenv("PER_TOPIC_DOWNLOADS") or _DEFAULT_PER_TOPIC)
MAX_DOWNLOADS = int(os.getenv("MAX_DOWNLOADS") or str(max(80, PER_TOPIC_DOWNLOADS * 20)))
MAX_SKILL_SIZE_MB = float(os.getenv("MAX_SKILL_SIZE_MB") or "50")
CANDIDATES_JSON = os.getenv("CANDIDATES_JSON") or "candidates/all_candidates.json"
CANDIDATES_DIR = pathlib.Path(os.getenv("CANDIDATES_DIR") or "candidates/by_topic")
SKIP_EXISTING = os.getenv("SKIP_EXISTING", "1") not in ("0", "false", "no")
EXTRACT_ZIPS = os.getenv("EXTRACT_ZIPS", "0").lower() in ("1", "true", "yes")
OUT_DIR = pathlib.Path("artifacts/skills")
OUT_DIR.mkdir(parents=True, exist_ok=True)

HEADERS = {"Accept": "application/vnd.github+json"}
if GITHUB_TOKEN:
    HEADERS["Authorization"] = f"token {GITHUB_TOKEN}"

# Characters forbidden in GitHub Actions artifact paths
_INVALID = re.compile(r'[":<>|*?\r\n]')


def max_bytes() -> int | None:
    """Return byte limit, or None if unlimited (MAX_SKILL_SIZE_MB <= 0)."""
    if MAX_SKILL_SIZE_MB <= 0:
        return None
    return int(MAX_SKILL_SIZE_MB * 1024 * 1024)


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

    items.sort(
        key=lambda x: (x.get("topic_id") or "", -(x.get("score") or 0), -(x.get("stars") or 0))
    )
    return items


def safe_name(full_name: str) -> str:
    return _INVALID.sub("_", full_name.replace("/", "_"))


def repo_size_kb(full_name: str) -> int | None:
    """GitHub API reports approximate repository size in KB."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{full_name}",
            headers=HEADERS,
            timeout=30,
        )
        if r.status_code == 200:
            return int(r.json().get("size") or 0)
        print(f"  size lookup fail {full_name}: {r.status_code}")
    except Exception as e:
        print(f"  size lookup exception {full_name}: {e}")
    return None


def download_zipball(
    full_name: str,
    dest_zip: pathlib.Path,
    limit_bytes: int | None,
    max_retries: int = 3,
) -> tuple[bool, str | None]:
    """
    Stream-download zipball. Returns (ok, skip_reason).
    skip_reason is set when skipped for size (not a hard failure).
    """
    url = f"https://api.github.com/repos/{full_name}/zipball"
    for attempt in range(1, max_retries + 1):
        try:
            with requests.get(url, headers=HEADERS, allow_redirects=True, timeout=120, stream=True) as r:
                if r.status_code != 200:
                    print(f"  download fail {full_name}: {r.status_code}")
                    time.sleep(2 * attempt)
                    continue

                # Content-Length is a soft pre-check when present
                cl = r.headers.get("Content-Length")
                if limit_bytes and cl and int(cl) > limit_bytes:
                    return False, f"content_length>{MAX_SKILL_SIZE_MB}MB"

                total = 0
                too_large = False
                with open(dest_zip, "wb") as f:
                    for chunk in r.iter_content(chunk_size=1024 * 256):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if limit_bytes and total > limit_bytes:
                            too_large = True
                            break
                        f.write(chunk)
                if too_large:
                    dest_zip.unlink(missing_ok=True)
                    mb = total / (1024 * 1024)
                    return False, f"zip>{MAX_SKILL_SIZE_MB}MB ({mb:.1f}MB)"
                return True, None
        except Exception as e:
            print(f"  exception {full_name}: {e}")
            traceback.print_exc()
            dest_zip.unlink(missing_ok=True)
            time.sleep(2 * attempt)
    return False, None


def safe_extract(zip_path: pathlib.Path, dest_dir: pathlib.Path) -> None:
    """Extract zip, renaming members that contain OS/artifact-illegal characters."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        for info in zf.infolist():
            raw = info.filename
            cleaned = _INVALID.sub("_", raw)
            target = dest_dir / cleaned
            if info.is_dir() or raw.endswith("/"):
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def select_downloads(items: list[dict]) -> list[dict]:
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


def write_meta(path: pathlib.Path, meta: dict[str, Any]) -> None:
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    items = load_candidates()
    if not items:
        print("No candidates found, exiting.")
        return

    limit = max_bytes()
    todo = select_downloads(items)
    print(
        f"Downloading {len(todo)} repos "
        f"(MAX_DOWNLOADS={MAX_DOWNLOADS}, PER_TOPIC={PER_TOPIC_DOWNLOADS}, "
        f"MAX_SKILL_SIZE_MB={MAX_SKILL_SIZE_MB}, EXTRACT_ZIPS={int(EXTRACT_ZIPS)})"
    )

    failed = []
    skipped_oversized = []
    for idx, entry in enumerate(todo, start=1):
        full = entry["full_name"]
        tid = entry.get("topic_id") or "unknown"
        topic_dir = OUT_DIR / tid
        topic_dir.mkdir(parents=True, exist_ok=True)
        base = safe_name(full)
        zip_path = topic_dir / f"{base}.zip"
        meta_path = topic_dir / f"{base}.meta.json"
        extract_dir = topic_dir / base

        if SKIP_EXISTING and zip_path.exists():
            # Still enforce size on already-downloaded zips
            if limit and zip_path.stat().st_size > limit:
                print(f"[{idx}/{len(todo)}] remove oversized existing {tid}/{full}")
                zip_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                skipped_oversized.append({**entry, "skip_reason": "existing_zip_too_large"})
                continue
            print(f"[{idx}/{len(todo)}] skip existing {tid}/{full}")
            continue

        print(f"[{idx}/{len(todo)}] {tid} :: {full}")

        # Pre-filter by GitHub reported repo size (KB). Zip is usually smaller,
        # so only skip when clearly over ~1.5x the limit.
        if limit:
            kb = repo_size_kb(full)
            if kb is not None:
                approx_bytes = kb * 1024
                if approx_bytes > limit * 1.5:
                    reason = f"repo_size~{kb/1024:.1f}MB>{MAX_SKILL_SIZE_MB}MB"
                    print(f"  skip oversized (API): {reason}")
                    skipped_oversized.append({**entry, "skip_reason": reason, "repo_size_kb": kb})
                    continue

        ok, skip_reason = download_zipball(full, zip_path, limit)
        if skip_reason:
            print(f"  skip oversized: {skip_reason}")
            skipped_oversized.append({**entry, "skip_reason": skip_reason})
            continue
        if not ok:
            failed.append(entry)
            continue

        size_mb = zip_path.stat().st_size / (1024 * 1024)
        entry_with_size = {**entry, "zip_size_mb": round(size_mb, 2)}
        write_meta(meta_path, entry_with_size)
        print(f"  saved {size_mb:.1f}MB")

        if EXTRACT_ZIPS:
            try:
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                safe_extract(zip_path, extract_dir)
                write_meta(extract_dir / "candidate_meta.json", entry_with_size)
            except Exception as e:
                print("  extract failed (zip kept):", e)

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

    if skipped_oversized:
        (OUT_DIR / "skipped_oversized.json").write_text(
            json.dumps(skipped_oversized, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Skipped oversized: {len(skipped_oversized)}")

    print("Done ->", OUT_DIR)


if __name__ == "__main__":
    main()
