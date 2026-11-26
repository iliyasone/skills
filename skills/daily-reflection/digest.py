#!/usr/bin/env python3
"""Digest of T3 chats: every message the user typed, plus the end of the agent reply it answered.

Reads the local T3 database (read-only). Takes the top-level threads of the given projects
that have messages from the user, and prints for each:
  ## <title> — t3-thread://v1/<id>
  [time] AGENT (before): <start and end of the last agent reply>
  [time] USER: <the message>

Messages written by scheduled tasks, watchers and subagents are skipped; times are Moscow time.

Examples:
  digest.py --project <T3 project id> --since 2026-10-24T01:00 --out /tmp/digest.md
  digest.py --project <id> --since-file <state file> --chunk-chars 200000 --out /tmp/digest.md
    # splits into digest.01.md, ...; since comes from the file, the start time of this run
    # goes to <file>.next; after a successful pass, `mv <file>.next <file>`
"""
import argparse
import json
import sqlite3
from datetime import datetime, timedelta, timezone

DB = "/root/.t3/userdata/statev2.sqlite"
MSK = timezone(timedelta(hours=3))


def clip(text, limit):
    text = text.strip()
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return text[:head].rstrip() + " … " + text[-(limit - head):].lstrip()


def msk(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(MSK).strftime("%d.%m %H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", action="append", required=True, help="T3 project id; repeatable")
    ap.add_argument("--since", help="ISO time (UTC): user messages from this time on")
    ap.add_argument("--since-file", help="file with the ISO since time; the start of this run goes to <file>.next")
    ap.add_argument("--until", default=None, help="ISO time (UTC): up to this time")
    ap.add_argument("--user-chars", type=int, default=4000, help="clip user messages to this many chars")
    ap.add_argument("--ctx-chars", type=int, default=900, help="clip the agent reply before to this many chars")
    ap.add_argument("--skip-title", action="append", default=[], help="skip threads whose title contains this text")
    ap.add_argument("--chunk-chars", type=int, default=0, help="split the output into files of about this size")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if args.since_file and not args.since:
        with open(args.since_file) as f:
            args.since = f.read().strip()
    if not args.since:
        ap.error("--since or --since-file is required")

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    marks = ",".join("?" * len(args.project))
    projects = dict(db.execute(f"select project_id, title from projection_projects where project_id in ({marks})", args.project))
    if missing := set(args.project) - set(projects):
        ap.error(f"no such T3 projects: {', '.join(sorted(missing))}")
    threads = {}
    for tid, pid, title, created, payload in db.execute(
        f"select thread_id, project_id, title, created_at, payload_json from orchestration_v2_projection_threads "
        f"where project_id in ({marks}) and deleted_at is null",
        args.project,
    ):
        lineage = json.loads(payload).get("lineage") or {}
        if lineage.get("parentThreadId"):
            continue  # subagent threads: the user does not write there
        if any(s in title for s in args.skip_title):
            continue
        threads[tid] = {"project": projects[pid], "title": title, "created": created, "msgs": []}

    for tid, role, created, payload in db.execute(
        "select thread_id, role, created_at, payload_json from orchestration_v2_projection_messages order by created_at"
    ):
        if tid not in threads:
            continue
        p = json.loads(payload)
        text = p.get("text") or ""
        if not text.strip():
            continue
        threads[tid]["msgs"].append((role, p.get("createdBy"), created, text))

    blocks = []
    for tid, t in threads.items():
        lines, last_agent, has_user = [], None, False
        for role, by, created, text in t["msgs"]:
            if role == "assistant":
                last_agent = (created, text)
                continue
            if by != "user":
                last_agent = None  # an automatic prompt (watcher, webhook, subagent), not the user
                continue
            if created < args.since or (args.until and created > args.until):
                last_agent = None
                continue
            if last_agent:
                lines.append(f"[{msk(last_agent[0])}] AGENT (before): {clip(last_agent[1], args.ctx_chars)}")
            lines.append(f"[{msk(created)}] USER: {clip(text, args.user_chars)}")
            last_agent, has_user = None, True
        if has_user:
            first = min(m[2] for m in t["msgs"])
            head = f"## {t['title']} — t3-thread://v1/{tid} ({t['project']}, started {msk(first)})"
            blocks.append((first, head + "\n" + "\n".join(lines) + "\n"))
    blocks.sort()
    if args.since_file:
        with open(args.since_file + ".next", "w") as f:
            f.write(started + "\n")

    if not args.chunk_chars:
        with open(args.out, "w") as f:
            f.write("\n".join(b for _, b in blocks))
        print(f"{len(blocks)} threads → {args.out}")
        return

    chunks, cur = [], ""
    for _, b in blocks:
        if cur and len(cur) + len(b) > args.chunk_chars:
            chunks.append(cur)
            cur = ""
        cur += b + "\n"
    if cur:
        chunks.append(cur)
    stem = args.out[:-3] if args.out.endswith(".md") else args.out
    for i, ch in enumerate(chunks, 1):
        path = f"{stem}.{i:02d}.md"
        with open(path, "w") as f:
            f.write(ch)
        print(f"{path}: {len(ch)} chars")
    print(f"{len(blocks)} threads, {len(chunks)} files")


if __name__ == "__main__":
    main()
