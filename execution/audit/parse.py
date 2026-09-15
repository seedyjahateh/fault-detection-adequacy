"""Parsing of test outputs and patches, and failure categorisation.

Failure categories (benchmark/audit/EXCLUSION_RULES.md §A):
  ASSERTION          AssertionError, unittest FAIL, pytest Failed (e.g. DID NOT RAISE)
  RUNTIME_EXCEPTION  any other exception raised by the code under test that is not API surface
  API_SURFACE_CRASH  ImportError/NameError/AttributeError/TypeError tied to symbols the patch changes
  IMPORT_ERROR       ImportError/ModuleNotFoundError not attributable to the patch (environment)
  COLLECTION_ERROR   error collecting the test (SyntaxError etc.)
  UNRESOLVED_CRASH   AttributeError/TypeError(signature) where the symbol could not be extracted
  PROCESS_CRASH      the test process died on a signal
  TIMEOUT            the run exceeded its timeout
  UNKNOWN            outcome could not be parsed

STRICT_CATEGORIES are the categories that count as "assertion failure or documented exception"
under §5.2 condition 3 (Amendment 002). ASSERTION_ONLY is the stricter sensitivity reading.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

STRICT_CATEGORIES = frozenset({"ASSERTION", "RUNTIME_EXCEPTION"})
ASSERTION_ONLY = frozenset({"ASSERTION"})

_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_EXC_NAME = re.compile(r"^((?:[A-Za-z_][\w]*\.)*[A-Za-z_]\w*)(?::\s|:$|$)")
_E_LINE = re.compile(r"^E\s+((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Failed|Exit|Interrupt|Warning|Iteration))(?::|\s*$)")
_E_ASSERT = re.compile(r"^E\s+assert\b")
# Dotted, capitalised exception names without a conventional suffix, e.g. pandas' OutOfBoundsDatetime.
_E_GENERIC = re.compile(r"^E\s+((?:[a-z_]\w*\.)+[A-Z]\w*): ")


# --------------------------------------------------------------------------- patches

@dataclass
class PatchInfo:
    files: list[str] = field(default_factory=list)
    added: dict[str, list[str]] = field(default_factory=dict)
    removed: dict[str, list[str]] = field(default_factory=dict)
    # Hunk line numbers: removed lines in the old (buggy) file, added lines in the new (fixed) file.
    removed_linenos: dict[str, list[int]] = field(default_factory=dict)
    added_linenos: dict[str, list[int]] = field(default_factory=dict)
    # Old-file line just before each pure insertion, so a pure-addition fix still maps to a location.
    insertion_anchor_linenos: dict[str, list[int]] = field(default_factory=dict)

    def added_identifiers(self) -> set[str]:
        return {t for lines in self.added.values() for ln in lines for t in re.findall(_IDENT, ln)}

    def removed_identifiers(self) -> set[str]:
        return {t for lines in self.removed.values() for ln in lines for t in re.findall(_IDENT, ln)}

    def changed_def_names(self) -> set[str]:
        names = set()
        for lines in list(self.added.values()) + list(self.removed.values()):
            for ln in lines:
                m = re.match(rf"\s*(?:async\s+)?def\s+({_IDENT})\s*\(", ln)
                if m:
                    names.add(m.group(1))
        return names


def parse_patch(text: str) -> PatchInfo:
    info = PatchInfo()
    cur = None
    old_no = new_no = 0
    pending_insert = False
    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            m = re.match(r"diff --git a/(\S+) b/(\S+)", raw)
            cur = m.group(2) if m else None
            if cur and cur not in info.files:
                info.files.append(cur)
                for d in (info.added, info.removed, info.removed_linenos, info.added_linenos,
                          info.insertion_anchor_linenos):
                    d.setdefault(cur, [])
            continue
        if cur is None or raw.startswith(("index ", "--- ", "+++ ", "new file", "deleted file",
                                          "similarity", "rename ", "old mode", "new mode")):
            continue
        if raw.startswith("@@"):
            m = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if m:
                old_no, new_no = int(m.group(1)), int(m.group(2))
            pending_insert = False
            continue
        if raw.startswith("+"):
            info.added[cur].append(raw[1:])
            info.added_linenos[cur].append(new_no)
            if not pending_insert:
                info.insertion_anchor_linenos[cur].append(max(old_no - 1, 1))
                pending_insert = True
            new_no += 1
        elif raw.startswith("-"):
            info.removed[cur].append(raw[1:])
            info.removed_linenos[cur].append(old_no)
            pending_insert = True  # a replacement, not a pure insertion
            old_no += 1
        else:
            pending_insert = False
            old_no += 1
            new_no += 1
    return info


TEST_DIR_NAMES = {"test", "tests", "testing"}


def is_test_path(path: str) -> bool:
    parts = path.replace("\\", "/").split("/")
    base = parts[-1]
    if any(p in TEST_DIR_NAMES for p in parts[:-1]):
        return True
    return bool(re.match(r"^(test_.*|.*_test|.*_tests|tests|conftest)\.py$", base))


# --------------------------------------------------------------------------- categorisation

_ATTR_PATTERNS = [
    re.compile(r"has no attribute '(" + _IDENT + r")'"),
]
_TYPE_KWARG = re.compile(r"unexpected keyword argument '(" + _IDENT + r")'")
_TYPE_SIG_FUNC = re.compile(
    r"(" + _IDENT + r")\(\) (?:takes|missing \d+ required|got multiple values|got an unexpected)"
)
_TYPE_SIG_ANY = re.compile(
    r"(positional argument|keyword argument|required positional|takes \d+|takes from \d+|"
    r"takes no arguments|got multiple values)"
)
_IMPORT_NAME = re.compile(r"cannot import name '?(" + _IDENT + r")'?")
_MODULE_NAME = re.compile(r"No module named '([\w.]+)'")
_NAME_ERROR = re.compile(r"name '(" + _IDENT + r")' is not defined")


def short_type(exc_type: str | None) -> str | None:
    if not exc_type:
        return None
    return exc_type.rsplit(".", 1)[-1]


def _new_in_patch(name: str, patch: PatchInfo) -> bool:
    return name in patch.added_identifiers() and name not in patch.removed_identifiers()


def categorize(exc_type: str | None, message: str, patch: PatchInfo, package: str | None,
               kind: str = "failure") -> tuple[str, str]:
    """Return (category, basis). `kind` is "failure", "error" (pytest setup/collection error)
    or "unittest_fail"."""
    t = short_type(exc_type)
    msg = message or ""
    if kind == "unittest_fail":
        return "ASSERTION", "unittest FAIL block"
    if t is None:
        return "UNKNOWN", "no exception type parsed"
    if t in {"AssertionError"}:
        return "ASSERTION", "AssertionError"
    if t == "Failed":
        basis = "pytest.raises DID NOT RAISE" if "DID NOT RAISE" in msg else "pytest Failed"
        return "ASSERTION", basis
    if t in {"ImportError", "ModuleNotFoundError"}:
        m = _IMPORT_NAME.search(msg)
        if m and _new_in_patch(m.group(1), patch):
            return "API_SURFACE_CRASH", f"cannot import '{m.group(1)}', which the patch introduces"
        m = _MODULE_NAME.search(msg)
        if m and package and m.group(1).split(".")[0] == package:
            leaf = m.group(1).split(".")[-1]
            if any(f.endswith(leaf + ".py") or f"/{leaf}/" in f for f in patch.files):
                return "API_SURFACE_CRASH", f"module '{m.group(1)}' introduced by the patch"
        return "IMPORT_ERROR", f"{t} not attributable to patch: {msg[:120]}"
    if t == "NameError":
        m = _NAME_ERROR.search(msg)
        if m and _new_in_patch(m.group(1), patch):
            return "API_SURFACE_CRASH", f"name '{m.group(1)}' introduced by the patch"
        if m:
            return "RUNTIME_EXCEPTION", f"NameError on '{m.group(1)}', not introduced by patch"
        return "UNRESOLVED_CRASH", "NameError without extractable name"
    if t == "AttributeError":
        for pat in _ATTR_PATTERNS:
            m = pat.search(msg)
            if m:
                if _new_in_patch(m.group(1), patch):
                    return "API_SURFACE_CRASH", f"attribute '{m.group(1)}' introduced by the patch"
                return "RUNTIME_EXCEPTION", f"attribute '{m.group(1)}' not introduced by patch"
        return "UNRESOLVED_CRASH", "AttributeError without extractable name"
    if t == "TypeError":
        m = _TYPE_KWARG.search(msg)
        if m:
            if _new_in_patch(m.group(1), patch):
                return "API_SURFACE_CRASH", f"keyword '{m.group(1)}' introduced by the patch"
            return "RUNTIME_EXCEPTION", f"keyword '{m.group(1)}' not introduced by patch"
        m = _TYPE_SIG_FUNC.search(msg)
        if m:
            if m.group(1) in patch.changed_def_names() or m.group(1) == "__init__" and patch.changed_def_names() & {"__init__"}:
                return "API_SURFACE_CRASH", f"signature of '{m.group(1)}' changed by the patch"
            return "RUNTIME_EXCEPTION", f"signature error on '{m.group(1)}', not changed by patch"
        if _TYPE_SIG_ANY.search(msg):
            return "UNRESOLVED_CRASH", "signature-style TypeError without extractable function"
        return "RUNTIME_EXCEPTION", "non-signature TypeError"
    if t in {"SyntaxError", "IndentationError"}:
        return "COLLECTION_ERROR", t
    if kind == "error" and msg.strip().lower() == "collection failure":
        return "COLLECTION_ERROR", "pytest collection failure"
    return "RUNTIME_EXCEPTION", t


# --------------------------------------------------------------------------- pytest junit

@dataclass
class TestFailure:
    test: str
    kind: str            # failure | error | unittest_fail | unittest_error
    exc_type: str | None
    message: str
    category: str = "UNKNOWN"
    basis: str = ""

    def as_dict(self) -> dict:
        return {"test": self.test, "kind": self.kind, "exc_type": self.exc_type,
                "message": self.message[:300], "category": self.category, "basis": self.basis}


def _exc_from_text(text: str) -> str | None:
    """Last exception named on an `E   ` line (the final raise in a chain)."""
    last = None
    generic = None
    for ln in (text or "").splitlines():
        if _E_ASSERT.match(ln):
            last = last or "AssertionError"
        m = _E_LINE.match(ln)
        if m:
            last = m.group(1)
            continue
        g = _E_GENERIC.match(ln)
        if g:
            generic = g.group(1)
    return last or generic


def _exc_from_message(message: str) -> str | None:
    msg = (message or "").strip()
    if msg.startswith("assert ") or msg == "assert":
        return "AssertionError"
    m = _EXC_NAME.match(msg)
    if m:
        name = m.group(1)
        base = short_type(name)
        if base and (base[0].isupper() or "." in name):
            return name
    return None


def parse_junit(xml_text: str) -> dict:
    """Returns {"total", "passed", "skipped", "outcomes": {test: outcome}, "failures": [TestFailure]}."""
    out = {"total": 0, "passed": 0, "skipped": 0, "outcomes": {}, "failures": []}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        out["parse_error"] = True
        return out
    for tc in root.iter("testcase"):
        name = f"{tc.get('classname', '')}::{tc.get('name', '')}".strip(":")
        out["total"] += 1
        failure = tc.find("failure")
        error = tc.find("error")
        skipped = tc.find("skipped")
        node = failure if failure is not None else error
        if node is not None:
            kind = "failure" if failure is not None else "error"
            message = node.get("message", "") or ""
            text = node.text or ""
            low = message.strip().lower()
            generic = (low == "" or low.startswith(("collection failure", "test setup failure",
                                                    "test teardown failure", "failed on setup",
                                                    "failed on teardown")))
            if generic:
                exc = _exc_from_text(text) or _exc_from_message(message)
            else:
                exc = _exc_from_message(message) or _exc_from_text(text)
            detail = message
            st = short_type(exc)
            if st and (generic or not message.lstrip().startswith((exc, st))):
                for ln in text.splitlines():
                    if ln.startswith("E") and st in ln:
                        detail = ln[1:].strip()
            out["failures"].append(TestFailure(name, kind, exc, detail))
            out["outcomes"][name] = kind
        elif skipped is not None:
            out["skipped"] += 1
            out["outcomes"][name] = "skipped"
        else:
            out["passed"] += 1
            out["outcomes"][name] = "passed"
    return out


# --------------------------------------------------------------------------- unittest text

_UT_HEADER = re.compile(r"^(FAIL|ERROR): (\S+) \(([^)]*)\)")
_UT_SEP_EQ = "=" * 70
_UT_SEP_DASH = "-" * 70
_UT_VERBOSE = re.compile(r"^(\S+) \(([^)]*)\) \.\.\. (ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$")


def parse_unittest(output: str) -> dict:
    lines = (output or "").splitlines()
    failures: list[TestFailure] = []
    outcomes: dict[str, str] = {}
    i = 0
    while i < len(lines):
        m = _UT_HEADER.match(lines[i])
        if not m:
            vm = _UT_VERBOSE.match(lines[i])
            if vm:
                outcomes[f"{vm.group(2)}.{vm.group(1)}"] = vm.group(3).split()[0].lower()
            i += 1
            continue
        kind, test, where = m.group(1), m.group(2), m.group(3)
        j = i + 1
        if j < len(lines) and lines[j].startswith(_UT_SEP_DASH):
            j += 1
        body = []
        while j < len(lines) and not lines[j].startswith(_UT_SEP_EQ) and not (
                lines[j].startswith(_UT_SEP_DASH) and j + 1 < len(lines) and lines[j + 1].startswith("Ran ")):
            body.append(lines[j])
            j += 1
        exc, msg = None, ""
        for ln in reversed([b for b in body if b.strip()]):
            if ln.startswith((" ", "\t", "Traceback", "During handling", "The above exception")):
                continue
            em = _EXC_NAME.match(ln)
            if em:
                exc, msg = em.group(1), ln
                break
        name = f"{where}.{test}"
        failures.append(TestFailure(name, "unittest_fail" if kind == "FAIL" else "unittest_error", exc, msg))
        outcomes[name] = "failure" if kind == "FAIL" else "error"
        i = j
    ran = re.search(r"^Ran (\d+) tests?", output or "", re.M)
    return {"total": int(ran.group(1)) if ran else None, "failures": failures, "outcomes": outcomes}


def is_pytest_command(line: str) -> bool:
    return "pytest" in line or "py.test" in line


def is_unittest_command(line: str) -> bool:
    return "unittest" in line
