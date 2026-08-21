#!/usr/bin/env python3
"""
Discover financial skills from GitHub by thematic topic.

Design goals:
  - Search per topic (产业链 / 生物医药 / 信用 / 量化 …)
  - Fetch more candidates than needed, then score & keep ~target_per_topic quality items
  - Prefer precision over recall

Env:
  - GITHUB_TOKEN (required)
  - MAX_RESULTS          override global.max_fetch_per_topic
  - TARGET_PER_TOPIC     override global.target_per_topic
  - MIN_STARS            override global.stars (minimum stars filter)
  - TOPIC_IDS            comma-separated topic ids to run (default: all)
  - SEARCH_PARAMS        path to config JSON (default: search_params.json)

Outputs:
  - candidates/all_candidates.json
  - candidates/by_topic/<topic_id>.json
  - candidates/rejected_sample.json  (for tuning filters)
"""

from __future__ import annotations

import json
import os
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

# Allow `python tools/fetch_candidates.py` from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent))
from scoring import score_text

try:
    import github
    Auth = getattr(github, "Auth", None)
    from github import Github
except Exception:
    from github import Github
    Auth = None

SEARCH_FILE = Path(os.getenv("SEARCH_PARAMS", "search_params.json"))
OUT_DIR = Path("candidates")
BY_TOPIC_DIR = OUT_DIR / "by_topic"
OUT_FILE = OUT_DIR / "all_candidates.json"
REJECTED_FILE = OUT_DIR / "rejected_sample.json"


def load_config() -> dict:
    if not SEARCH_FILE.exists():
        raise SystemExit(f"Missing config: {SEARCH_FILE}")
    cfg = json.loads(SEARCH_FILE.read_text(encoding="utf-8"))
    if "topics" not in cfg or "global" not in cfg:
        raise SystemExit("search_params.json must contain 'global' and 'topics'")
    return cfg


def get_github_client(token: str) -> Github:
    if not token:
        raise SystemExit("GITHUB_TOKEN is required in environment")
    try:
        if Auth is not None and hasattr(Auth, "Token"):
            return Github(auth=Auth.Token(token))
    except Exception:
        pass
    return Github(token)


def pushed_qualifier(gcfg: dict) -> str:
    df = gcfg.get("date_from")
    dt = gcfg.get("date_to")
    if df and dt:
        return f"pushed:{df}..{dt}"
    days = gcfg.get("pushed_within_days")
    if isinstance(days, int) and days > 0:
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        return f"pushed:>={start}"
    return ""


def _count_bool_ops(q: str) -> int:
    return q.count(" OR ") + q.count(" AND ") + q.count(" NOT ")


def build_topic_queries(topic: dict, gcfg: dict) -> list[str]:
    """Build GitHub search queries for one topic (≤5 boolean operators each)."""
    stars = gcfg.get("stars", 5)
    stars_part = f"stars:>={stars}" if isinstance(stars, int) and stars > 0 else ""
    pushed = pushed_qualifier(gcfg)

    queries = []
    for raw in topic.get("search_queries") or []:
        q = " ".join(p for p in [raw.strip(), stars_part, pushed] if p).strip()
        if len(q) > 256:
            q = q[:256].rsplit(" ", 1)[0]
        if not q:
            continue
        if _count_bool_ops(q) > 5:
            print(f"  skip query (too many boolean ops): {q}")
            continue
        queries.append(q)
    return queries


def safe_repo_info(repo) -> dict:
    """Serialize search hit without eager topic API calls."""
    updated = getattr(repo, "updated_at", None)
    return {
        "full_name": getattr(repo, "full_name", None),
        "url": getattr(repo, "html_url", None),
        "desc": getattr(repo, "description", None),
        "stars": getattr(repo, "stargazers_count", None),
        "updated_at": updated.isoformat() if updated else None,
        "language": getattr(repo, "language", None),
        "fork": getattr(repo, "fork", False),
        "archived": getattr(repo, "archived", False),
        "topics": [],
    }


def enrich_topics(repo, info: dict) -> dict:
    """Lazily fetch GitHub topics when name/desc alone is not enough."""
    if info.get("topics"):
        return info
    try:
        topics = list(repo.get_topics())
        info = {**info, "topics": topics}
    except Exception as e:
        print(f"  topics fetch failed for {info.get('full_name')}: {e}")
    return info


def evaluate_repo(info: dict, topic: dict, cfg: dict, gcfg: dict) -> dict | None:
    """Return enriched candidate dict if it passes filters, else None."""
    text = " ".join(
        [
            info.get("full_name") or "",
            info.get("desc") or "",
            " ".join(info.get("topics") or []),
        ]
    )
    result = score_text(
        text,
        finance_signals=cfg.get("finance_signals") or [],
        topic_signals=topic.get("topic_signals") or [],
        skill_signals=cfg.get("skill_signals") or [],
        blacklist=cfg.get("blacklist_keywords") or [],
    )

    if result["rejected"]:
        return {"_reject": True, **result, "full_name": info.get("full_name")}

    if gcfg.get("require_finance_hit", True) and not result["finance_hits"]:
        return {
            "_reject": True,
            "reject_reason": "no_finance_signal",
            "score": result["score"],
            "full_name": info.get("full_name"),
        }

    if not result["topic_hits"]:
        return {
            "_reject": True,
            "reject_reason": "no_topic_signal",
            "score": result["score"],
            "full_name": info.get("full_name"),
            "_retry_with_topics": True,
        }

    min_score = float(gcfg.get("min_score", 6.0))
    stars = info.get("stars") or 0
    star_bonus = min(2.0, (stars or 0) / 200.0)
    final_score = result["score"] + star_bonus

    if final_score < min_score:
        return {
            "_reject": True,
            "reject_reason": f"low_score:{final_score}",
            "score": final_score,
            "full_name": info.get("full_name"),
            "_retry_with_topics": True,
        }

    return {
        **info,
        "topic_id": topic["id"],
        "topic_name": topic.get("name"),
        "category": topic.get("category"),
        "score": round(final_score, 2),
        "finance_hits": result["finance_hits"],
        "topic_hits": result["topic_hits"],
        "skill_hits": result["skill_hits"],
    }


def search_topic(g: Github, topic: dict, cfg: dict, gcfg: dict, max_fetch: int) -> tuple[list, list]:
    queries = build_topic_queries(topic, gcfg)
    print(f"\n=== Topic: {topic.get('name')} ({topic['id']}) | {len(queries)} queries ===")

    accepted = []
    rejected = []
    seen = set()

    for q in queries:
        if len(accepted) >= max_fetch * 2:
            # enough raw hits to rank later
            break
        print("  query:", q)
        try:
            results = g.search_repositories(q, sort="stars", order="desc")
        except Exception as e:
            print("  search failed:", e)
            traceback.print_exc()
            continue

        scanned = 0
        try:
            for repo in results:
                scanned += 1
                if scanned > max_fetch:
                    break
                if getattr(repo, "fork", False) or getattr(repo, "archived", False):
                    continue
                info = safe_repo_info(repo)
                key = info.get("full_name")
                if not key or key in seen:
                    continue
                seen.add(key)

                judged = evaluate_repo(info, topic, cfg, gcfg)
                if not judged:
                    continue
                # Name/desc missed topic signals: retry once with GitHub topics
                if (
                    judged.get("_reject")
                    and judged.get("_retry_with_topics")
                    and not info.get("topics")
                ):
                    info = enrich_topics(repo, info)
                    judged = evaluate_repo(info, topic, cfg, gcfg)
                    if not judged:
                        continue
                if judged.get("_reject"):
                    judged.pop("_retry_with_topics", None)
                    if len(rejected) < 40:
                        rejected.append(judged)
                    continue
                accepted.append(judged)
        except Exception as e:
            # Incomplete pagination / rate limit mid-iteration
            print("  iteration error:", e)

        time.sleep(1.2)

    # Rank and trim to target
    accepted.sort(key=lambda x: (x.get("score", 0), x.get("stars") or 0), reverse=True)
    target = int(gcfg.get("target_per_topic", 10))
    kept = accepted[:target]
    print(f"  kept {len(kept)}/{len(accepted)} accepted (target={target})")
    return kept, rejected


def select_topics(cfg: dict) -> list[dict]:
    topics = cfg.get("topics") or []
    raw = os.getenv("TOPIC_IDS", "").strip()
    if not raw:
        return topics
    wanted = {x.strip() for x in raw.split(",") if x.strip()}
    selected = [t for t in topics if t.get("id") in wanted]
    if not selected:
        raise SystemExit(f"No topics matched TOPIC_IDS={raw}")
    return selected


def main() -> None:
    cfg = load_config()
    gcfg = dict(cfg.get("global") or {})

    # Env overrides
    if os.getenv("MAX_RESULTS"):
        gcfg["max_fetch_per_topic"] = int(os.getenv("MAX_RESULTS"))
    if os.getenv("TARGET_PER_TOPIC"):
        gcfg["target_per_topic"] = int(os.getenv("TARGET_PER_TOPIC"))
    if os.getenv("MIN_STARS"):
        gcfg["stars"] = int(os.getenv("MIN_STARS"))

    print(f"Config: stars>={gcfg.get('stars')}, target_per_topic={gcfg.get('target_per_topic')}")

    max_fetch = int(gcfg.get("max_fetch_per_topic", 80))
    topics = select_topics(cfg)

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("WARNING: GITHUB_TOKEN not set")
    g = get_github_client(token)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BY_TOPIC_DIR.mkdir(parents=True, exist_ok=True)

    all_items = []
    all_rejected = []
    global_seen = set()

    for topic in topics:
        kept, rejected = search_topic(g, topic, cfg, gcfg, max_fetch)
        all_rejected.extend(rejected)

        topic_items = []
        for it in kept:
            key = it.get("full_name")
            if not key or key in global_seen:
                continue
            global_seen.add(key)
            topic_items.append(it)
            all_items.append(it)

        out_path = BY_TOPIC_DIR / f"{topic['id']}.json"
        out_path.write_text(json.dumps(topic_items, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  wrote {len(topic_items)} -> {out_path}")

    # Global rank for merged file (keep topic grouping via fields)
    all_items.sort(key=lambda x: (x.get("topic_id") or "", -(x.get("score") or 0)))

    OUT_FILE.write_text(json.dumps(all_items, ensure_ascii=False, indent=2), encoding="utf-8")
    REJECTED_FILE.write_text(
        json.dumps(all_rejected[:200], ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Summary
    by_topic = {}
    for it in all_items:
        by_topic.setdefault(it.get("topic_id"), 0)
        by_topic[it.get("topic_id")] += 1
    print("\n==== SUMMARY ====")
    print(f"Total quality candidates: {len(all_items)}")
    for tid, n in by_topic.items():
        print(f"  {tid}: {n}")
    print(f"Wrote {OUT_FILE}")
    print(f"Rejected sample: {REJECTED_FILE}")


if __name__ == "__main__":
    main()
