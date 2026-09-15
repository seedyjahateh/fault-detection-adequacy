"""Phase 1 reproduction audit orchestrator.

    uv run python -m execution.audit.audit run --projects tqdm luigi keras pandas --commit
    uv run python -m execution.audit.audit run --projects tqdm --bugs 1 2 --out .audit-cache/smoke
    uv run python -m execution.audit.audit log-debug --project pandas --minutes 12 --note "..."

Resumable: a bug that already has a record in audit_results.jsonl is skipped. An interrupted bug
has no record and is re-audited with the next attempt number; its partial logs are kept.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import json
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config, container, parse, records, status, steps
from .image import ensure_image


# --------------------------------------------------------------------------- helpers

class Paths:
    def __init__(self, out_dir: Path):
        self.out = out_dir
        self.results = out_dir / "audit_results.jsonl"
        self.events = out_dir / "project_runs.jsonl"
        self.logs = out_dir / "logs"


def harness_git() -> dict:
    def git(*a):
        return subprocess.run(["git", "-C", str(config.REPO_ROOT), *a], capture_output=True, text=True).stdout.strip()
    dirty = git("status", "--porcelain", "--", "execution", "pyproject.toml", "uv.lock")
    return {"sha": git("rev-parse", "HEAD"), "dirty": bool(dirty)}


def read_bug(project: str, bug_id: int) -> dict:
    d = config.FORK_DIR / "projects" / project / "bugs" / str(bug_id)
    info = {}
    for ln in (d / "bug.info").read_text(encoding="utf-8").splitlines():
        m = re.match(r'^(\w+)="(.*)"\s*$', ln.strip())
        if m:
            info[m.group(1)] = m.group(2)
    run_test = [ln.strip() for ln in (d / "run_test.sh").read_text(encoding="utf-8").splitlines() if ln.strip()]
    patch_text = (d / "bug_patch.txt").read_text(encoding="utf-8", errors="replace") if (d / "bug_patch.txt").exists() else ""
    return {"dir": d, "info": info, "run_test": run_test, "patch_text": patch_text,
            "patch": parse.parse_patch(patch_text),
            "test_files": [t for t in info.get("test_file", "").split(";") if t]}


def bug_ids(project: str) -> list[int]:
    return sorted(int(p.name) for p in (config.FORK_DIR / "projects" / project / "bugs").iterdir() if p.name.isdigit())


def host_free_gb() -> float:
    return shutil.disk_usage(config.REPO_ROOT.anchor).free / 1e9


PIP_FAIL = [re.compile(r"ERROR: (?:Could not find a version that satisfies the requirement|No matching distribution found for) (\S+)"),
            re.compile(r"ERROR: Failed building wheel for (\S+)"),
            re.compile(r"ERROR: Command errored out with exit status \d+: .*?(\S+)$")]


def pip_failures(output: str) -> list[str]:
    found = []
    for ln in output.splitlines():
        for pat in PIP_FAIL:
            m = pat.search(ln)
            if m and m.group(0) not in found:
                found.append(m.group(0)[:200])
    return found


class Logger:
    def __init__(self, root: Path):
        self.root = root

    def write(self, rel: str, data, compress=True):
        return records.write_once(self.root / rel, data, compress)


# --------------------------------------------------------------------------- one version

def audit_version(ctx: dict, bug: dict, version: int, log: Logger) -> dict:
    project, bug_id, name = ctx["project"], ctx["bug_id"], ctx["container"]
    vname = "buggy" if version == 0 else "fixed"
    v = {"setup_ok": False, "setup_error": "", "steps": {}, "runs": []}
    t_setup = time.monotonic()

    def step(label, script_marker, timeout):
        script, marker = script_marker
        r = container.exec_script(name, script, timeout)
        log.write(f"{vname}/{label}.log", r.output)
        v["steps"][label] = {"rc": r.rc, "seconds": round(r.seconds, 1), "host_timeout": r.host_timeout}
        return r, marker

    # checkout
    r, m = step("checkout", steps.checkout(project, bug_id, version, config.CHECKOUT_TIMEOUT_S), config.CHECKOUT_TIMEOUT_S)
    head = steps.marked_value(r.output, m, "HEAD")
    v["head_sha"] = head
    v["worktree_changes"] = (steps.marked_block(r.output, m, "STATUS") or "").splitlines()
    if head != ctx["buggy_sha"] or steps.marked_value(r.output, m, "INFO_OK") is None:
        v["setup_error"] = f"checkout: HEAD={head} expected buggy {ctx['buggy_sha']}; info_ok={steps.marked_value(r.output, m, 'INFO_OK') is not None}"
        return v
    if version == 1 and not v["worktree_changes"]:
        v["setup_error"] = "checkout: fixed version has no changes relative to buggy commit"
        return v

    # environment
    r, m = step("env", steps.create_env(project, config.ENV_TIMEOUT_S, ctx["procedure"]), config.ENV_TIMEOUT_S)
    v["env_name"] = steps.marked_value(r.output, m, "ENV")
    v["python_declared"] = steps.marked_value(r.output, m, "PYV")
    v["base_env_created"] = steps.marked_value(r.output, m, "BASE_CREATE") is not None
    v["base_env_tries"] = sum(1 for x in steps.marked(r.output, m) if x.startswith("BASE_TRY "))
    v["base_env_extra_specs"] = steps.marked_value(r.output, m, "BASE_EXTRA") or ""
    v["setuptools_version"] = steps.marked_value(r.output, m, "SETUPTOOLS")
    if steps.marked_value(r.output, m, "ENV_OK") is None:
        tail = [ln for ln in r.output.splitlines() if ln.strip() and not ln.startswith(m)][-3:]
        v["setup_error"] = f"env: python={v['python_declared']} rc={r.rc} {' | '.join(tail)[:300]}"
        return v

    # compile
    r, m = step("compile", steps.compile_version(project, config.COMPILE_TIMEOUT_S), config.COMPILE_TIMEOUT_S)
    crc = steps.marked_value(r.output, m, "COMPILE_RC")
    v["compile_rc"] = int(crc) if crc and crc.lstrip("-").isdigit() else None
    v["pip_failures"] = pip_failures(r.output)
    v["ccache_stats"] = [x[len("CCSTAT "):] for x in steps.marked(r.output, m) if x.startswith("CCSTAT ")]
    if v["compile_rc"] in (124, 137) or r.host_timeout:
        v["setup_error"] = f"compile: timed out after {config.COMPILE_TIMEOUT_S}s"
        return v
    if steps.marked_value(r.output, m, "FLAG_OK") is None:
        v["setup_error"] = "compile: bugsinpy_compile_flag not written (env activation failed?)"
        return v

    # probe
    r, m = step("probe", steps.probe(project, v["env_name"], ctx["package"], config.PROBE_TIMEOUT_S), config.PROBE_TIMEOUT_S)
    v["python_actual"] = steps.marked_value(r.output, m, "PYTHON")
    v["pytest_version"] = steps.marked_value(r.output, m, "PYTEST")
    v["module_file"] = steps.marked_value(r.output, m, "MODULE")
    v["import_rc"] = steps.marked_value(r.output, m, "IMPORT_RC")
    mf = v["module_file"] or ""
    v["module_origin"] = ("checkout" if mf.startswith(f"{config.WORK}/{project}/") else
                          "pip_src" if mf.startswith(config.PIP_SRC) else
                          "site_packages" if "site-packages" in mf else ("unknown" if mf else "import_failed"))
    freeze = steps.marked_block(r.output, m, "FREEZE") or ""
    conda_explicit = steps.marked_block(r.output, m, "CONDA") or ""
    log.write(f"{vname}/pip_freeze.txt", freeze)
    log.write(f"{vname}/conda_explicit.txt", conda_explicit)
    import hashlib
    v["env_manifest_sha256"] = hashlib.sha256((freeze + "\n" + conda_explicit).encode()).hexdigest()
    v["setup_seconds"] = round(time.monotonic() - t_setup, 1)
    v["setup_ok"] = True

    # source snapshots for post-hoc classifiers (clone is deleted after the project)
    if version == 0:
        s, mk = steps.dump_files(project, ctx["patch_files"], ctx["buggy_sha"])
        for pth, data in steps.file_payloads(container.exec_script(name, s, 300).output, mk).items():
            log.write(f"sources/buggy/{pth}", data)
    else:
        s, mk = steps.dump_files(project, ctx["patch_files"], ctx["fixed_sha"])
        for pth, data in steps.file_payloads(container.exec_script(name, s, 300).output, mk).items():
            log.write(f"sources/fixed/{pth}", data)
        s, mk = steps.changed_files(project, ctx["fixed_sha"])
        ctx["fix_changed_files"] = [x[len("CHANGED "):] for x in steps.marked(container.exec_script(name, s, 120).output, mk)
                                    if x.startswith("CHANGED ") and x[len("CHANGED "):].strip()]
        tests_to_dump = sorted(set(ctx["fix_changed_files"]) | set(bug["test_files"]))
        tests_to_dump = [t for t in tests_to_dump if t.endswith(".py") and parse.is_test_path(t)]
        tests_to_dump += [c for c in steps.conftest_chain(project, tests_to_dump) if c not in tests_to_dump]
        s, mk = steps.dump_files(project, tests_to_dump, None)
        for pth, data in steps.file_payloads(container.exec_script(name, s, 300).output, mk).items():
            log.write(f"sources/fixed_tests/{pth}", data)

    # official runs
    for run in range(1, config.RUNS_PER_VERSION + 1):
        script, m = steps.run_tests(project, v["env_name"], config.TEST_TIMEOUT_S)
        r = container.exec_script(name, script, config.TEST_TIMEOUT_S * max(1, len(bug["run_test"])))
        cmds = steps.parse_test_output(r.output, m)
        verdicts = []
        for c in cmds:
            log.write(f"{vname}/run{run}_cmd{c['index']}.out", c["output"])
            if c["junit"]:
                log.write(f"{vname}/run{run}_cmd{c['index']}.junit.xml", c["junit"])
            verdicts.append(status.command_verdict(c["line"], c["rc"] if c["rc"] is not None else -1, c["seconds"],
                                                   config.TEST_TIMEOUT_S, c["output"], c["junit"],
                                                   bug["patch"], ctx["package"]))
        if not cmds or steps.marked_value(r.output, m, "ALL_DONE") is None:
            log.write(f"{vname}/run{run}_raw.log", r.output)
            verdicts.append({"command": "<harness>", "rc": r.rc, "seconds": round(r.seconds, 1),
                             "verdict": "timeout" if r.host_timeout else "not_run", "tests_total": None,
                             "failures": [], "note": "run script did not complete"})
        v["runs"].append({"run": run, "verdict": status.run_verdict(verdicts),
                          "seconds": round(sum(c["seconds"] for c in verdicts), 3), "commands": verdicts})

    # auxiliary coverage (buggy only): which patched lines, and which copy of the code, the tests execute
    if version == 0:
        inc = [f"{config.WORK}/{project}/{p}" for p in ctx["patch_files"]]
        inc += [f"*/site-packages/{p.split('/', 1)[-1]}" for p in ctx["patch_files"]]
        inc += [f"{config.PIP_SRC}/*/{p}" for p in ctx["patch_files"]]
        script, m = steps.coverage_run(project, v["env_name"], inc, config.TEST_TIMEOUT_S)
        r = container.exec_script(name, script, config.TEST_TIMEOUT_S * max(1, len(bug["run_test"])))
        log.write("buggy/coverage.log", "\n".join(ln for ln in r.output.splitlines() if "COV_JSON_B64" not in ln))
        cov = {"coverage_version": steps.marked_value(r.output, m, "COVERAGE_VERSION"),
               "install_failed": steps.marked_value(r.output, m, "COV_INSTALL_FAIL") is not None,
               "executed_patched_lines": {}, "files_executed": []}
        b64 = steps.marked_value(r.output, m, "COV_JSON_B64")
        if b64:
            data = base64.b64decode(b64)
            log.write("buggy/coverage.json", data)
            j = json.loads(data)
            for fpath, fdata in j.get("files", {}).items():
                cov["files_executed"].append(fpath)
                for pp in ctx["patch_files"]:
                    if fpath.endswith("/" + pp) or fpath.endswith("/" + pp.split("/", 1)[-1]):
                        want = set(bug["patch"].removed_linenos.get(pp, [])) | set(bug["patch"].insertion_anchor_linenos.get(pp, []))
                        cov["executed_patched_lines"].setdefault(pp, {})[fpath] = sorted(want & set(fdata.get("executed_lines", [])))
        # coverage.py reports files under the working directory (the checkout) as relative paths.
        cov["origin"] = sorted({("checkout" if f.startswith(f"{config.WORK}/{project}/") or not f.startswith("/") else
                                 "pip_src" if f.startswith(config.PIP_SRC) else
                                 "site_packages" if "site-packages" in f else "other") for f in cov["files_executed"]})
        v["coverage"] = cov
    return v


def determinism(ctx: dict, bug: dict, log: Logger) -> dict:
    project, name, env = ctx["project"], ctx["container"], ctx["fixed_env"]
    launchers = {steps.launcher_of(l) for l in bug["run_test"]}
    if len(launchers) != 1 or None in launchers:
        return {"ran": False, "reason": f"unsupported or mixed runners: {sorted(map(str, launchers))}"}
    runner, launcher = launchers.pop()
    files = sorted({f for f in ctx.get("fix_changed_files", []) if f.endswith(".py") and parse.is_test_path(f)}
                   | set(bug["test_files"]))
    targets = files if runner == "pytest" else [steps.test_module_name(f) for f in files]
    scope = {"runs": config.DETERMINISM_RUNS, "runner": runner, "launcher": launcher, "targets": targets,
             "scope": "fix-touched test files + bug-triggering test files (Amendment 001)"}
    script, m = steps.determinism_runs(project, env, launcher, runner, targets, config.DETERMINISM_RUNS,
                                       config.DETERMINISM_TIMEOUT_S)
    r = container.exec_script(name, script, config.DETERMINISM_TIMEOUT_S * config.DETERMINISM_RUNS)
    maps, per_run = [], []
    for val in steps.marked(r.output, m):
        key, _, rest = val.partition(" ")
        if key == "DET_END":
            k, rc, s, e = rest.split()
            per_run.append({"run": int(k), "rc": int(rc), "seconds": round(float(e) - float(s), 1),
                            "timed_out": int(rc) in (124, 137) and float(e) - float(s) >= config.DETERMINISM_TIMEOUT_S - 1})
        elif key in ("DET_JUNIT_B64", "DET_OUT_B64"):
            k, _, b64 = rest.partition(" ")
            raw = gzip.decompress(base64.b64decode(b64)) if b64 else b""
            ext = "junit.xml" if key == "DET_JUNIT_B64" else "out"
            log.write(f"determinism/r{k}.{ext}", raw)
            text = raw.decode("utf-8", "replace")
            if runner == "pytest" and key == "DET_JUNIT_B64":
                maps.append((int(k), parse.parse_junit(text)["outcomes"]))
            elif runner == "unittest" and key == "DET_OUT_B64":
                maps.append((int(k), parse.parse_unittest(text)["outcomes"]))
    maps.sort()
    outcome_maps = [mp for _, mp in maps]
    varying = sorted({t for mp in outcome_maps for t in mp if len({x.get(t) for x in outcome_maps}) > 1})
    complete = len(per_run) == config.DETERMINISM_RUNS and len(outcome_maps) == config.DETERMINISM_RUNS
    any_timeout = any(p["timed_out"] for p in per_run) or steps.marked_value(r.output, m, "DET_STOPPED_AFTER_TIMEOUT") is not None
    scope["stopped_after_timeout_run"] = steps.marked_value(r.output, m, "DET_STOPPED_AFTER_TIMEOUT")
    counts = [dict(Counter(mp.values())) for mp in outcome_maps]
    deterministic = complete and not any_timeout and bool(outcome_maps and outcome_maps[0]) and not varying
    return {"ran": True, **scope, "per_run": per_run, "outcome_counts": counts,
            "varying_tests": varying[:50], "n_varying": len(varying), "complete": complete,
            "any_timeout": any_timeout, "deterministic": deterministic}


# --------------------------------------------------------------------------- one bug

def audit_bug(project: str, bug_id: int, cinfo: dict, img: dict, paths: Paths, git: dict,
              procedure: str, workers: int, reaudit_reason: str | None = None) -> dict:
    t0 = time.monotonic()
    bug = read_bug(project, bug_id)
    bug_log_root = paths.logs / project / str(bug_id)
    with records.LOCK:
        attempt = 1 + sum(1 for p in bug_log_root.glob("attempt*") if p.is_dir()) if bug_log_root.exists() else 1
        (bug_log_root / f"attempt{attempt}").mkdir(parents=True, exist_ok=False)
    log = Logger(bug_log_root / f"attempt{attempt}")
    info = bug["info"]
    patch_files = [f for f in bug["patch"].files if not parse.is_test_path(f)] or bug["patch"].files
    ctx = {"project": project, "bug_id": bug_id, "container": cinfo["name"], "procedure": procedure,
           "package": config.PACKAGE_MAP.get(project), "patch_files": patch_files}

    rec = {"schema_version": config.SCHEMA_VERSION, "harness_version": config.HARNESS_VERSION,
           "harness_git_sha": git["sha"], "harness_git_dirty": git["dirty"], "rules_version": config.RULES_VERSION,
           "procedure": procedure, "parallel_workers": workers, "worker_container": cinfo["name"],
           "reaudit_reason": reaudit_reason,
           "attempt": attempt, "project": project, "bug_id": bug_id,
           "python_version_declared": info.get("python_version"),
           "buggy_commit_declared": info.get("buggy_commit_id"), "fixed_commit_declared": info.get("fixed_commit_id"),
           "test_commands": bug["run_test"], "test_files": bug["test_files"], "patch_files": bug["patch"].files,
           "fork_sha": config.FORK_SHA, "image_tag": img["tag"], "image_id": img["image_id"],
           "base_image": config.BASE_IMAGE, "log_dir": str((bug_log_root / f"attempt{attempt}").relative_to(config.REPO_ROOT)).replace("\\", "/")
           if str(bug_log_root).startswith(str(config.REPO_ROOT)) else str(bug_log_root)}

    s, m = steps.resolve_commits(project, [info.get("buggy_commit_id", ""), info.get("fixed_commit_id", "")])
    r = container.exec_script(cinfo["name"], s, 900)
    log.write("resolve.log", r.output)
    resolved = {}
    for val in steps.marked(r.output, m):
        if val.startswith("RESOLVE "):
            _, decl, full = val.split(" ", 2)
            resolved[decl] = None if full == "MISSING" else full
    ctx["buggy_sha"] = resolved.get(info.get("buggy_commit_id"))
    ctx["fixed_sha"] = resolved.get(info.get("fixed_commit_id"))
    rec["buggy_commit_sha"], rec["fixed_commit_sha"] = ctx["buggy_sha"], ctx["fixed_sha"]
    rec["fixed_version_semantics"] = "buggy_commit_sha + files changed by fixed_commit_sha (BugsInPy checkout)"

    versions = {}
    if not ctx["buggy_sha"] or not ctx["fixed_sha"]:
        missing = [k for k, val in (("buggy", ctx["buggy_sha"]), ("fixed", ctx["fixed_sha"])) if not val]
        err = f"commit not available upstream: {', '.join(missing)}"
        versions = {"buggy": {"setup_ok": False, "setup_error": err, "runs": []},
                    "fixed": {"setup_ok": False, "setup_error": err, "runs": []}}
    else:
        versions["buggy"] = audit_version(ctx, bug, 0, log)
        if versions["buggy"]["setup_ok"]:
            versions["fixed"] = audit_version(ctx, bug, 1, log)
            ctx["fixed_env"] = versions["fixed"].get("env_name")
        else:
            versions["fixed"] = {"setup_ok": False, "setup_error": "skipped: buggy setup failed", "runs": []}

    st = status.determine_status(versions["buggy"], versions["fixed"])
    rec.update({"status": st["status"], "status_reason": st["reason"], "error": st["error"], "strict": st["strict"]})
    rec["python_version_actual"] = versions["buggy"].get("python_actual") or versions["fixed"].get("python_actual")
    rec["determinism"] = determinism(ctx, bug, log) if st["status"] == "REPRODUCES" else None
    rec["versions"] = versions
    rec["wall_clock_seconds"] = round(time.monotonic() - t0, 1)
    rec["recorded_at"] = records.utcnow()
    records.append_jsonl(paths.results, rec)
    return rec


# --------------------------------------------------------------------------- project loop

def project_elapsed(paths: Paths, project: str) -> float:
    """Elapsed wall clock across sessions: per session, the largest `session_elapsed` checkpoint.
    Sessions without checkpoints (harness v1.0.0) contribute the sum of their bug_attempt seconds."""
    per_session: dict[str, float] = {}
    legacy = 0.0
    for e in records.read_jsonl(paths.events):
        if e.get("project") != project:
            continue
        if "session_id" in e and "session_elapsed" in e:
            per_session[e["session_id"]] = max(per_session.get(e["session_id"], 0.0), e["session_elapsed"])
        elif e.get("event") in ("bug_attempt", "project_prep"):
            legacy += e.get("seconds", 0)
    return sum(per_session.values()) + legacy


def project_seconds(paths: Paths, project: str) -> float:
    """Compute seconds (sum over bug attempts and preparation, all workers)."""
    return sum(e.get("seconds", 0) for e in records.read_jsonl(paths.events)
               if e.get("project") == project and e.get("event") in ("bug_attempt", "project_prep"))


def is_parked(paths: Paths, project: str) -> bool:
    ev = [e for e in records.read_jsonl(paths.events) if e.get("project") == project and e.get("event") in ("project_parked", "project_unparked")]
    return bool(ev) and ev[-1]["event"] == "project_parked"


def commit_project(project: str, paths: Paths, note: str) -> None:
    latest = [r for (p, _), r in records.latest_by_bug(records.read_jsonl(paths.results)).items() if p == project]
    c = Counter(r["status"] for r in latest)
    total = len(bug_ids(project))
    counts = ", ".join(f"{k}={c[k]}" for k in sorted(c))
    msg = f"audit({project}): {len(latest)}/{total} bugs audited{note}\n\n{counts}\n"
    subprocess.run(["git", "-C", str(config.REPO_ROOT), "add", str(paths.out)], check=True)
    subprocess.run(["git", "-C", str(config.REPO_ROOT), "commit", "-m", msg], check=False)


def selection(args) -> dict[str, list[int]]:
    if args.sample_file:
        sample = json.loads(Path(args.sample_file).read_text(encoding="utf-8"))
        sel = {p: sorted(ids) for p, ids in sample["bugs"].items()}
        return {p: sel[p] for p in (args.projects or sorted(sel)) if p in sel}
    if not args.projects:
        raise SystemExit("--projects is required unless --sample-file is given")
    return {p: (args.bugs or bug_ids(p)) for p in args.projects}


def run(args) -> int:
    procedure = args.procedure
    default_out = config.AUDIT_DIR if procedure == "r1" else config.UNMODIFIED_SAMPLE_DIR
    out = Path(args.out).resolve() if args.out else default_out
    official = out.resolve() in (config.AUDIT_DIR.resolve(), config.UNMODIFIED_SAMPLE_DIR.resolve())
    git = harness_git()
    if official and git["dirty"] and not args.allow_dirty:
        print("Refusing to write official audit records with uncommitted harness changes. Commit first.")
        return 2
    paths = Paths(out)
    img = ensure_image()
    manifest_dir = paths.logs / "_image" / img["tag"].split(":")[1]
    sel = selection(args)
    full_projects = not args.bugs and not args.sample_file

    for project, ids in sel.items():
        if is_parked(paths, project) and not args.include_parked:
            print(f"[{project}] parked (TIMEOUT_PROJECT); skipping until all other projects are complete.")
            continue
        done = records.latest_by_bug(records.read_jsonl(paths.results))
        todo = [b for b in ids if (project, b) not in done]
        print(f"[{project}] {len(ids) - len(todo)} already audited, {len(todo)} to go", flush=True)
        if not todo:
            continue
        n_workers = max(1, min(args.workers or config.WORKERS.get(project, config.DEFAULT_WORKERS), len(todo)))
        session_id = uuid.uuid4().hex
        t_session = time.monotonic()
        prior_elapsed = project_elapsed(paths, project)

        def event(ev: dict) -> None:
            records.append_jsonl(paths.events, {**ev, "project": project, "at": records.utcnow(), "session_id": session_id,
                                                "session_elapsed": round(time.monotonic() - t_session, 1)})

        cinfos = [container.ensure_container(project, img["tag"], k) for k in range(n_workers)]
        event({"event": "project_session_start", "procedure": procedure, "harness_version": config.HARNESS_VERSION,
               "harness_git_sha": git["sha"], "image_tag": img["tag"], "workers": n_workers,
               "prior_elapsed_seconds": round(prior_elapsed, 1)})
        with records.LOCK:
            if not manifest_dir.exists():
                s, m = steps.env_snapshot(project)
                r = container.exec_script(cinfos[0]["name"], s, 120)
                manifest_dir.mkdir(parents=True, exist_ok=True)
                for key in ("DPKG", "CONDA_BASE"):
                    records.write_once(manifest_dir / f"{key.lower()}.txt", steps.marked_block(r.output, m, key) or "")
                records.write_once(manifest_dir / "image.json", json.dumps(
                    {**img, "conda": steps.marked_value(r.output, m, "CONDA_VERSION"),
                     "ccache": steps.marked_value(r.output, m, "CCACHE")}, indent=2), compress=False)

        def prep(ci: dict) -> str | None:
            t = time.monotonic()
            s, m = steps.clone_project(project, config.CLONE_TIMEOUT_S)
            r = container.exec_script(ci["name"], s, config.CLONE_TIMEOUT_S)
            head = steps.marked_value(r.output, m, "CLONE_HEAD")
            event({"event": "project_prep", "worker_container": ci["name"], "seconds": round(time.monotonic() - t, 1),
                   "clone_head": head})
            if head is None:
                print(f"[{project}] clone failed in {ci['name']}:\n{r.output[-2000:]}", flush=True)
            return head

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            if not all(ex.map(prep, cinfos)):
                return 3

        queue = deque(todo)
        state = {"parked": False, "disk_stop": False}
        qlock = threading.Lock()

        def one(ci: dict, b: int, workers: int, reaudit: str | None) -> None:
            t = time.monotonic()
            try:
                rec = audit_bug(project, b, ci, img, paths, git, procedure, workers, reaudit)
                recorded = True
                print(f"[{project} #{b} {ci['name'][-2:]}] {rec['status']} ({rec['status_reason']}) "
                      f"{rec['wall_clock_seconds']:.0f}s {rec['error'][:110]}", flush=True)
            except Exception as e:  # harness failure: no record; the bug is retried with the next attempt
                recorded = False
                print(f"[{project} #{b}] HARNESS ERROR (not recorded, will retry): {e!r}", flush=True)
                if args.stop_on_error:
                    raise
            event({"event": "bug_attempt", "bug_id": b, "worker_container": ci["name"], "workers": workers,
                   "reaudit_reason": reaudit, "seconds": round(time.monotonic() - t, 1), "recorded": recorded})

        def worker(ci: dict) -> None:
            while True:
                with qlock:
                    if state["parked"] or state["disk_stop"] or not queue:
                        return
                    elapsed = prior_elapsed + time.monotonic() - t_session
                    if elapsed >= config.PROJECT_WALLCLOCK_GUARD_S and not args.include_parked:
                        state["parked"] = True
                        event({"event": "project_parked", "status": "TIMEOUT_PROJECT",
                               "elapsed_seconds": round(elapsed), "guard_seconds": config.PROJECT_WALLCLOCK_GUARD_S,
                               "not_started": len(queue)})
                        print(f"[{project}] TIMEOUT_PROJECT after {elapsed/3600:.2f} h elapsed; "
                              f"finishing in-flight bugs, {len(queue)} not started.", flush=True)
                        return
                    free = host_free_gb()
                    if free < config.MIN_HOST_FREE_GB:
                        state["disk_stop"] = True
                        print(f"[{project}] host free disk {free:.1f} GB < {config.MIN_HOST_FREE_GB} GB; stopping safely.",
                              flush=True)
                        return
                    b = queue.popleft()
                one(ci, b, n_workers, None)

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            list(ex.map(worker, cinfos))
        if state["disk_stop"]:
            return 4

        # Amendment 004: serial confirmation of FLAKY/TIMEOUT outcomes observed under parallel load.
        latest = records.latest_by_bug(records.read_jsonl(paths.results))
        confirm = [b for b in ids if (project, b) in latest and latest[(project, b)]["status"] in ("FLAKY", "TIMEOUT")
                   and latest[(project, b)].get("parallel_workers", 1) > 1]
        for b in confirm:
            print(f"[{project} #{b}] serial confirmation of {latest[(project, b)]['status']}", flush=True)
            one(cinfos[0], b, 1, "serial_confirmation")

        latest = records.latest_by_bug(records.read_jsonl(paths.results))
        complete = all((project, x) in latest for x in ids)
        if complete or state["parked"]:
            if full_projects:
                event({"event": "project_end", "complete": complete, "parked": state["parked"],
                       "elapsed_seconds": round(project_elapsed(paths, project), 1),
                       "compute_seconds": round(project_seconds(paths, project), 1)})
            if not args.keep_container:
                container.remove_containers(project)
            if args.commit and official:
                commit_project(project, paths, (" (TIMEOUT_PROJECT, parked)" if state["parked"] else "")
                               + ("" if procedure == "r1" else " [unmodified-procedure sample]"))
    return 0


def log_debug(args) -> int:
    out = Path(args.out).resolve() if args.out else config.AUDIT_DIR
    records.append_jsonl(Paths(out).events, {"event": "debug", "project": args.project, "at": records.utcnow(),
                                             "debug_minutes": args.minutes, "note": args.note})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="audit")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--projects", nargs="+")
    r.add_argument("--bugs", nargs="+", type=int)
    r.add_argument("--sample-file", help="JSON with {'bugs': {project: [ids]}} (e.g. unmodified_sample/SAMPLE.json)")
    r.add_argument("--procedure", choices=config.PROCEDURES, default="r1")
    r.add_argument("--workers", type=int, help="override per-project worker count")
    r.add_argument("--out", help="results directory (default: benchmark/audit). Use .audit-cache/smoke for smoke tests.")
    r.add_argument("--commit", action="store_true", help="git commit benchmark/audit after each completed project")
    r.add_argument("--include-parked", action="store_true")
    r.add_argument("--keep-container", action="store_true")
    r.add_argument("--allow-dirty", action="store_true")
    r.add_argument("--stop-on-error", action="store_true")
    d = sub.add_parser("log-debug")
    d.add_argument("--project", required=True)
    d.add_argument("--minutes", type=float, required=True)
    d.add_argument("--note", required=True)
    d.add_argument("--out")
    args = ap.parse_args(argv)
    return {"run": run, "log-debug": log_debug}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
