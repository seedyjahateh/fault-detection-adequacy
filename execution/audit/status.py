"""Command verdicts, run verdicts and per-bug status (EXCLUSION_RULES.md §A)."""

from __future__ import annotations

from . import parse
from .parse import ASSERTION_ONLY, STRICT_CATEGORIES, PatchInfo, TestFailure

SIGNAL_RCS = {134: "SIGABRT", 135: "SIGBUS", 136: "SIGFPE", 139: "SIGSEGV"}
CRASH_IMPORT_TYPES = {"ImportError", "ModuleNotFoundError"}


def command_verdict(line: str, rc: int, seconds: float, timeout_s: int, output: str,
                    junit_xml: str | None, patch: PatchInfo, package: str | None) -> dict:
    """Verdict for one run_test.sh command: pass | fail | not_run | timeout."""
    res = {"command": line, "rc": rc, "seconds": round(seconds, 3), "verdict": None,
           "tests_total": None, "failures": [], "note": ""}
    if rc in (124, 137) and seconds >= timeout_s - 1:
        res["verdict"] = "timeout"
        res["failures"] = [TestFailure("<command>", "timeout", "Timeout", f"exceeded {timeout_s}s",
                                       "TIMEOUT", "timeout").as_dict()]
        return res

    failures: list[TestFailure] = []
    if parse.is_pytest_command(line):
        j = parse.parse_junit(junit_xml) if junit_xml else None
        if j is not None:
            res["tests_total"] = j["total"]
            failures = j["failures"]
        if rc == 0:
            if j is not None and j["total"] > 0 and j["passed"] == 0 and j["skipped"] == j["total"]:
                res["verdict"], res["note"] = "not_run", "all tests skipped"
            else:
                res["verdict"] = "pass"
        elif rc in (4, 5) and failures:
            # e.g. the test module failed to import during collection, so the node id is then "not found":
            # the junit collection error is the real outcome (luigi/2 smoke, 2026-09-15).
            res["verdict"], res["note"] = "fail", f"pytest rc={rc} after collection errors"
        elif rc in (4, 5):
            res["verdict"], res["note"] = "not_run", {4: "pytest usage error (test not found?)",
                                                      5: "no tests collected"}[rc]
        elif rc in (1, 2, 3) and junit_xml is None:
            # pytest writes junit at session end, even after collection errors. No junit means pytest
            # itself crashed during startup/config (e.g. a broken plugin): never a test outcome.
            res["verdict"] = "fail"
            failures = [TestFailure("<command>", "runner", None, _last_exc_line(output) or f"pytest rc={rc}",
                                    "RUNNER_ERROR", "pytest exited without writing junit (startup/config crash)")]
            res["note"] = f"pytest rc={rc} without junit report"
        elif rc in (1, 2, 3):
            res["verdict"] = "fail"
            if not failures:
                failures = [TestFailure("<command>", "runner", None, _last_exc_line(output) or f"pytest rc={rc}",
                                        "RUNNER_ERROR", "junit report contains no failures despite non-zero rc")]
                res["note"] = f"pytest rc={rc} with empty junit"
        elif rc in SIGNAL_RCS:
            res["verdict"] = "fail"
            failures = [TestFailure("<command>", "signal", None, SIGNAL_RCS[rc], "PROCESS_CRASH", SIGNAL_RCS[rc])]
        else:
            res["verdict"] = "fail"
            failures = [TestFailure("<command>", "error", None, _last_exc_line(output))]
            res["note"] = f"unexpected pytest rc={rc}"
    elif parse.is_unittest_command(line):
        u = parse.parse_unittest(output)
        res["tests_total"] = u["total"]
        failures = u["failures"]
        if rc == 0:
            res["verdict"] = "pass" if (u["total"] or 0) > 0 else "not_run"
            if res["verdict"] == "not_run":
                res["note"] = "unittest ran 0 tests"
        elif rc in SIGNAL_RCS:
            res["verdict"] = "fail"
            failures = [TestFailure("<command>", "signal", None, SIGNAL_RCS[rc], "PROCESS_CRASH", SIGNAL_RCS[rc])]
        else:
            res["verdict"] = "fail"
            if not failures:
                failures = [TestFailure("<command>", "runner", None, _last_exc_line(output) or f"unittest rc={rc}",
                                        "RUNNER_ERROR", "unittest exited non-zero without FAIL/ERROR blocks")]
                res["note"] = f"unittest rc={rc} without parsed FAIL/ERROR blocks"
    else:
        res["verdict"] = "pass" if rc == 0 else "fail"
        if rc != 0:
            failures = [TestFailure("<command>", "error", None, _last_exc_line(output))]
            res["note"] = "unsupported runner; exit code only"

    for f in failures:
        if f.category in ("UNKNOWN", "") and f.kind not in ("signal", "timeout"):
            kind = "unittest_fail" if f.kind == "unittest_fail" else ("error" if "error" in f.kind else "failure")
            f.category, f.basis = parse.categorize(f.exc_type, f.message, patch, package, kind)
    res["failures"] = [f.as_dict() for f in failures]
    return res


def _last_exc_line(output: str) -> str:
    for ln in reversed((output or "").splitlines()):
        s = ln.strip()
        if s and ("Error" in s or "Exception" in s):
            return s[:300]
    return ""


_PRIORITY = ["timeout", "not_run", "fail", "pass"]


def run_verdict(commands: list[dict]) -> str:
    verdicts = {c["verdict"] for c in commands}
    if not commands:
        return "not_run"
    for v in _PRIORITY:
        if v in verdicts:
            return v
    return "not_run"


def failure_signature(run: dict) -> list[list[str]]:
    return sorted([[f["test"], parse.short_type(f["exc_type"]) or "", f["category"]]
                   for c in run["commands"] for f in c["failures"]])


def run_has_category(run: dict, allowed: frozenset[str]) -> bool:
    return any(f["category"] in allowed for c in run["commands"] for f in c["failures"])


def determine_status(buggy: dict, fixed: dict) -> dict:
    """buggy/fixed: {"setup_ok": bool, "setup_error": str, "runs": [run...]}.
    Returns {"status", "reason", "error", "strict": {...}}."""
    strict = {"rule_set": "STRICT = ASSERTION | RUNTIME_EXCEPTION",
              "buggy_runs_with_strict_failure": None, "buggy_runs_with_assertion_failure": None,
              "failure_signature_consistent": None, "strict_assertion_only": None}

    for name, v in (("buggy", buggy), ("fixed", fixed)):
        if not v.get("setup_ok"):
            return {"status": "FAILS_SETUP", "reason": f"{name}_setup", "error": v.get("setup_error", ""),
                    "strict": strict}

    bv = [r["verdict"] for r in buggy["runs"]]
    fv = [r["verdict"] for r in fixed["runs"]]

    # The test runner itself could not start on either version: an environment failure, not a test result.
    for name, v in (("buggy", buggy), ("fixed", fixed)):
        cats_by_run = [{f["category"] for c in r["commands"] for f in c["failures"]} for r in v["runs"]]
        if v["runs"] and all(cats and "RUNNER_ERROR" in cats for cats in cats_by_run):
            first = next(f for c in v["runs"][0]["commands"] for f in c["failures"] if f["category"] == "RUNNER_ERROR")
            return {"status": "FAILS_SETUP", "reason": f"{name}_runner_error",
                    "error": f"{name}: test runner crashed: {first['message'][:200]}", "strict": strict}

    # Fixed version cannot import its own test module / dependencies: an environment failure.
    for r in fixed["runs"]:
        if r["verdict"] == "fail":
            cats = {f["category"] for c in r["commands"] for f in c["failures"]}
            if cats and cats <= {"IMPORT_ERROR", "COLLECTION_ERROR", "RUNNER_ERROR"}:
                first = next(f for c in r["commands"] for f in c["failures"])
                if len(set(fv)) == 1:
                    return {"status": "FAILS_SETUP", "reason": "fixed_import_or_collection_error",
                            "error": f"fixed: {first['exc_type']}: {first['message'][:200]}", "strict": strict}

    if len(set(bv)) > 1 or len(set(fv)) > 1:
        return {"status": "FLAKY", "reason": "inconsistent_verdicts",
                "error": f"buggy={bv} fixed={fv}", "strict": strict}

    b, f = bv[0], fv[0]
    if b == "timeout" or f == "timeout":
        return {"status": "TIMEOUT", "reason": f"{'buggy' if b == 'timeout' else 'fixed'}_timeout",
                "error": f"buggy={b} fixed={f}", "strict": strict}

    if b == "fail" and f == "pass":
        n_strict = sum(run_has_category(r, STRICT_CATEGORIES) for r in buggy["runs"])
        n_assert = sum(run_has_category(r, ASSERTION_ONLY) for r in buggy["runs"])
        sigs = [failure_signature(r) for r in buggy["runs"]]
        strict.update({"buggy_runs_with_strict_failure": n_strict,
                       "buggy_runs_with_assertion_failure": n_assert,
                       "failure_signature_consistent": all(s == sigs[0] for s in sigs),
                       "strict_assertion_only": n_assert == len(buggy["runs"])})
        if n_strict == len(buggy["runs"]):
            return {"status": "REPRODUCES", "reason": "buggy_fails_fixed_passes", "error": "", "strict": strict}
        cats = sorted({x[2] for s in sigs for x in s})
        return {"status": "REPRODUCES_CRASH_ONLY", "reason": "buggy_failure_not_strict",
                "error": f"buggy failure categories: {cats}", "strict": strict}

    reason = {("pass", "pass"): "buggy_passes", ("fail", "fail"): "fixed_fails",
              ("pass", "fail"): "inverted"}.get((b, f), f"buggy_{b}_fixed_{f}")
    detail = ""
    src = fixed if f != "pass" else buggy
    for r in src["runs"][:1]:
        for c in r["commands"]:
            for fl in c["failures"][:1]:
                detail = f"{'fixed' if src is fixed else 'buggy'}: {fl['exc_type']}: {fl['message'][:200]}"
            if c.get("note") and not detail:
                detail = c["note"]
    return {"status": "FAILS_EXPECTED_BEHAVIOR", "reason": reason, "error": detail, "strict": strict}
