"""Generate benchmark/BENCHMARK_FROZEN.json: the ELIGIBLE set with pinned SHAs (derived; regenerate,
never hand-edit). Status stays "provisional" until protocol freeze (Month 3, Amendment 001).

    uv run python -m execution.audit.freeze
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import config, records
from .audit import bug_ids


def build(audit_dir: Path) -> dict:
    latest = records.latest_by_bug(records.read_jsonl(audit_dir / "audit_results.jsonl"))
    elig = {(e["project"], int(e["bug_id"])): e for e in records.read_jsonl(audit_dir / "eligibility.jsonl")}
    events = records.read_jsonl(audit_dir / "project_runs.jsonl")
    parked = sorted({e["project"] for e in events if e.get("event") == "project_parked"})

    n_total = sum(len(bug_ids(p)) for p in config.PACKAGE_MAP)
    reproduces = [r for r in latest.values() if r["status"] == "REPRODUCES"]
    stale, pending, eligible = [], [], []
    for r in reproduces:
        e = elig.get((r["project"], int(r["bug_id"])))
        if e is None or e["audit_attempt"] != r["attempt"]:
            stale.append(f"{r['project']}/{r['bug_id']}")
        elif e["eligible"] is None:
            pending.append(f"{r['project']}/{r['bug_id']}")
        elif e["eligible"]:
            eligible.append((r, e))

    bugs = []
    for r, e in sorted(eligible, key=lambda x: (x[0]["project"], int(x[0]["bug_id"]))):
        v = r["versions"]
        det = r.get("determinism") or {}
        bugs.append({
            "project": r["project"], "bug_id": int(r["bug_id"]),
            "buggy_commit_sha": r["buggy_commit_sha"], "fixed_commit_sha": r["fixed_commit_sha"],
            "fixed_version_semantics": r.get("fixed_version_semantics"),
            "python_version_declared": r["python_version_declared"], "python_version_actual": r.get("python_version_actual"),
            "procedure": r.get("procedure", "r1"),
            "base_env_extra_specs": v["buggy"].get("base_env_extra_specs", ""),
            "test_commands": r["test_commands"], "test_files": r["test_files"], "patch_files": r["patch_files"],
            "patch_units": e["patch_size"]["units"],
            "strict_assertion_only": bool(r.get("strict", {}).get("strict_assertion_only")),
            "determinism": {"runs": det.get("runs"), "runner": det.get("runner"), "launcher": det.get("launcher"),
                            "targets": det.get("targets")},
            "env_manifest_sha256": {"buggy": v["buggy"].get("env_manifest_sha256"), "fixed": v["fixed"].get("env_manifest_sha256")},
            "image_tag": r["image_tag"], "image_id": r["image_id"], "base_image": r["base_image"],
            "audit": {"attempt": r["attempt"], "harness_version": r["harness_version"], "harness_git_sha": r["harness_git_sha"],
                      "log_dir": r["log_dir"], "recorded_at": r["recorded_at"]},
            "eligibility": {"classifier_version": e["classifier_version"], "classified_at": e["classified_at"],
                            "manual_decisions_applied": e["manual_decisions_applied"]},
        })

    determinable = len(latest) == n_total and not parked and not pending and not stale
    n = len(bugs)
    return {
        "status": "provisional",
        "becomes_final_at": "protocol freeze (docs/PROTOCOL.md §10, Month 3)",
        "generated_at": records.utcnow(), "generator": "execution/audit/freeze.py",
        "rules_version": config.RULES_VERSION,
        "stability_scope": {"amendment": "001", "runs": config.DETERMINISM_RUNS, "version": "fixed",
                            "targets": "test files changed by fixed_commit + the bug's test_file entries",
                            "runner": "same launcher as the bug's run_test.sh",
                            "note": "not the full developer suite (§4.1 criterion 4 as amended)"},
        "fork": {"url": config.FORK_URL, "sha": config.FORK_SHA},
        "counts": {"bugs_in_fork": n_total, "audited": len(latest),
                   "not_audited": n_total - len(latest), "parked_projects": parked,
                   "reproduces": len(reproduces), "eligible": n,
                   "eligible_assertion_only": sum(1 for b in bugs if b["strict_assertion_only"]),
                   "eligibility_pending_manual": pending, "eligibility_not_classified": stale},
        "power_floor": {"n_required": 120, "n_eligible": n, "determinable": determinable,
                        "met": (n >= 120) if determinable else None},
        "bugs": bugs,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit-dir", default=str(config.AUDIT_DIR))
    ap.add_argument("--out", default=str(config.REPO_ROOT / "benchmark" / "BENCHMARK_FROZEN.json"))
    args = ap.parse_args(argv)
    doc = build(Path(args.audit_dir))
    Path(args.out).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    c = doc["counts"]
    print(f"wrote {args.out}: eligible={c['eligible']} reproduces={c['reproduces']} audited={c['audited']}/{c['bugs_in_fork']} "
          f"pending={len(c['eligibility_pending_manual'])} unclassified={len(c['eligibility_not_classified'])} "
          f"power_floor_met={doc['power_floor']['met']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
