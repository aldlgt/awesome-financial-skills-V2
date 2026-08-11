#!/usr/bin/env python3
"""
Create candidate proposal PRs from topic-grouped discovery results.

Default DRY_RUN=1.

Env:
  - DRY_RUN
  - GITHUB_TOKEN
  - GITHUB_REPOSITORY
"""

from __future__ import annotations

import datetime
import json
import os
import pathlib
import time
import traceback

from github import Github

DRY_RUN = os.getenv("DRY_RUN", "1").lower() not in ("0", "false", "no")
SEEN_FILE = ".data/seen.json"
CANDIDATES_DIR = pathlib.Path("candidates/by_topic")
MERGED_FILE = pathlib.Path("candidates/all_candidates.json")
PROPOSALS_DIR = "proposals"

TOK = os.getenv("GITHUB_TOKEN")
REPO_ENV = os.getenv("GITHUB_REPOSITORY")
if not TOK:
    raise SystemExit("请在环境变量 GITHUB_TOKEN 中设置 token")
if not REPO_ENV:
    raise SystemExit("GITHUB_REPOSITORY 环境变量未设置")

try:
    import github

    Auth = getattr(github, "Auth", None)
    if Auth is not None and hasattr(Auth, "Token"):
        g = Github(auth=Auth.Token(TOK))
    else:
        g = Github(TOK)
except Exception:
    g = Github(TOK)

owner, repo_name = REPO_ENV.split("/")
repo = g.get_repo(f"{owner}/{repo_name}")


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


def load_by_topic() -> dict[str, list]:
    grouped: dict[str, list] = {}
    if CANDIDATES_DIR.exists():
        for path in sorted(CANDIDATES_DIR.glob("*.json")):
            try:
                arr = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(arr, list) and arr:
                    grouped[path.stem] = arr
            except Exception as e:
                print("skip", path, e)
        return grouped

    if MERGED_FILE.exists():
        for it in json.loads(MERGED_FILE.read_text(encoding="utf-8")):
            tid = it.get("topic_id") or "misc"
            grouped.setdefault(tid, []).append(it)
    return grouped


def make_proposal_md(topic_id: str, items: list, now: str) -> str:
    title = items[0].get("topic_name") if items else topic_id
    lines = [
        f"# Candidate proposal — {title} (`{topic_id}`)\n",
        f"Generated: {now} UTC\n",
        f"Count: {len(items)} (score-ranked thematic batch)\n",
    ]
    for it in items:
        lines.append(
            f"- [{it.get('full_name')}]({it.get('url')}) — {it.get('desc') or ''} "
            f"(★{it.get('stars')}, score:{it.get('score')}; "
            f"topic_hits={it.get('topic_hits')})\n"
        )
    return "\n".join(lines)


def main() -> None:
    seen = load_seen()
    grouped = load_by_topic()
    if not grouped:
        print("No candidates directory/content, exit")
        return

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    for topic_id, items in grouped.items():
        new_items = [it for it in items if it.get("full_name") not in seen]
        if not new_items:
            print(f"{topic_id}: no new candidates, skip")
            continue

        branch = f"candidates/{topic_id}-{now}"
        proposal_path = f"{PROPOSALS_DIR}/{topic_id}-{now}.md"
        content = make_proposal_md(topic_id, new_items, now)

        if DRY_RUN:
            print(
                f"[DRY RUN] Would create branch {branch} with {proposal_path} "
                f"({len(new_items)} items)"
            )
            continue

        base_branch = repo.default_branch
        try:
            sb = repo.get_branch(base_branch)
            repo.create_git_ref(ref=f"refs/heads/{branch}", sha=sb.commit.sha)
        except Exception as e:
            print(f"Could not create branch {branch}: {e}")
            traceback.print_exc()
            continue

        try:
            repo.create_file(
                proposal_path,
                f"Add proposal {topic_id} {now}",
                content,
                branch=branch,
            )
        except Exception as e:
            print(f"Could not create file {proposal_path}: {e}")
            traceback.print_exc()
            continue

        try:
            pr = repo.create_pull(
                title=f"[Candidate] {topic_id} - {now}",
                body=f"Thematic candidate batch for `{topic_id}`. Please review scores.",
                head=branch,
                base=base_branch,
            )
            print(f"Created PR: {pr.html_url}")
            try:
                pr.add_to_labels("candidate")
            except Exception:
                try:
                    repo.create_label("candidate", "f9d0c4", "Candidate discovered by automation")
                    pr.add_to_labels("candidate")
                except Exception as e:
                    print("Label create/add failed:", e)
            for it in new_items:
                seen.add(it.get("full_name"))
            save_seen(seen)
            time.sleep(1)
        except Exception as e:
            print(f"Could not create PR for {branch}: {e}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
