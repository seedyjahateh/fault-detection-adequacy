"""Append-only JSONL storage. Every record is one line, flushed and fsynced before returning, so a
crash can lose at most the bug in progress."""

from __future__ import annotations

import datetime as dt
import gzip
import json
import os
import threading
from pathlib import Path

# Serialises appends from concurrent worker threads (one harness process per run).
LOCK = threading.RLock()


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
    with LOCK, open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # A torn final line from a hard crash; never rewrite the file, just skip it.
                if n != sum(1 for _ in open(path, encoding="utf-8")):
                    raise
    return out


def latest_by_bug(records: list[dict]) -> dict[tuple[str, int], dict]:
    """"Latest attempt wins" (CLAUDE.md)."""
    best: dict[tuple[str, int], dict] = {}
    for r in records:
        key = (r["project"], int(r["bug_id"]))
        cur = best.get(key)
        if cur is None or (r["attempt"], r["recorded_at"]) >= (cur["attempt"], cur["recorded_at"]):
            best[key] = r
    return best


def write_once(path: Path, data: bytes | str, compress: bool = True) -> str:
    """Write a raw artifact that must never be overwritten. Returns the repo-relative-ish path."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if compress:
        path = path.with_name(path.name + ".gz")
        data = gzip.compress(data, mtime=0)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "xb") as fh:  # "x": fail rather than overwrite
        fh.write(data)
    return str(path)
