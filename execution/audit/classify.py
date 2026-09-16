"""Post-hoc eligibility classifier: benchmark/audit/EXCLUSION_RULES.md §B–§E (rules v1, frozen).

    uv run python -m execution.audit.classify run        # classify REPRODUCES bugs -> eligibility.jsonl
    uv run python -m execution.audit.classify pending    # list flags awaiting a manual decision
    uv run python -m execution.audit.classify decide --project pandas --bug 12 --criterion C.2 \
        --flag EXTERNAL_PATH --decision keep --reason "open() target is tmp_path / 'x.csv'"

Inputs are the audit records and the source snapshots the harness saved per bug, so no clone is
needed. Output is append-only: a bug gets a new eligibility record only when its inputs change
(audit attempt, classifier version, or its manual decisions). The latest record wins.
"""

from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import config, records
from .parse import PatchInfo, is_test_path, parse_patch

CLASSIFIER_VERSION = "1.0.0"

TEMP_FIXTURES = {"tmp_path", "tmpdir", "tmp_path_factory", "tmpdir_factory"}
TEMP_CALL = re.compile(r"(^|\.)(ensure_clean|ensure_clean_dir|TemporaryDirectory|NamedTemporaryFile|TemporaryFile|"
                       r"SpooledTemporaryFile|mkdtemp|mkstemp|gettempdir)$|^tempfile\.")
REPO_READ_HINTS = {"__file__", "datapath", "get_data_path"}
NETWORK_MODULES = ("requests", "urllib", "urllib2", "urllib3", "http.client", "httplib", "socket", "httpx",
                   "aiohttp", "websocket", "websockets", "ftplib", "smtplib")
NETWORK_MARKERS = re.compile(r"(^|\.)(network|online)$")
SETUP_NAMES = {"setUp", "setUpClass", "setup_method", "setup_class", "setUpModule", "setup_module"}
# Receiver-method writes are limited to unambiguous names. pathlib's rename/replace/chmod are omitted:
# `.replace` / `.rename` are overwhelmingly str/DataFrame methods (false positive on tqdm/2 smoke data).
# os.rename/os.replace/os.chmod are still caught via OS_WRITE.
WRITE_METHODS = {"write_text", "write_bytes", "unlink", "mkdir", "touch", "rmdir",
                 "to_csv", "to_excel", "to_json", "to_pickle", "to_parquet", "to_hdf", "to_feather", "to_stata",
                 "to_html", "to_latex", "to_sql", "savefig", "tofile"}
OS_WRITE = {"os.remove", "os.unlink", "os.rename", "os.replace", "os.makedirs", "os.mkdir", "os.rmdir",
            "os.removedirs", "os.chmod", "os.symlink", "os.link", "os.truncate"}
READ_METHODS = {"read_text", "read_bytes"}
NP_IO_WRITE = {"numpy.save", "numpy.savez", "numpy.savez_compressed", "numpy.savetxt"}
NP_IO_READ = {"numpy.load", "numpy.loadtxt", "numpy.genfromtxt", "numpy.fromfile"}
OPEN_FUNCS = {"open", "io.open", "builtins.open", "codecs.open", "gzip.open", "bz2.open", "lzma.open"}


# --------------------------------------------------------------------------- inputs

def read_gz(path: Path) -> str | None:
    try:
        return gzip.decompress(path.read_bytes()).decode("utf-8", "replace")
    except (FileNotFoundError, OSError):
        return None


def load_sources(log_dir: Path, kind: str) -> dict[str, str]:
    root = log_dir / "sources" / kind
    out = {}
    if root.exists():
        for p in root.rglob("*.gz"):
            rel = p.relative_to(root).as_posix()[:-3]
            text = read_gz(p)
            if text is not None:
                out[rel] = text
    return out


def safe_parse(src: str | None) -> ast.Module | None:
    if src is None:
        return None
    try:
        return ast.parse(src)
    except (SyntaxError, ValueError):
        return None


# --------------------------------------------------------------------------- §B patch size

@dataclass
class Span:
    start: int
    end: int
    qualname: str
    node: ast.AST


def function_spans(tree: ast.Module) -> list[Span]:
    spans: list[Span] = []

    def visit(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                q = ".".join(stack + [child.name])
                spans.append(Span(start, child.end_lineno, q, child))
                visit(child, stack + [child.name])
            elif isinstance(child, ast.ClassDef):
                visit(child, stack + [child.name])
            else:
                visit(child, stack)

    visit(tree, [])
    return spans


def innermost(spans: list[Span], line: int) -> Span | None:
    containing = [s for s in spans if s.start <= line <= s.end]
    return min(containing, key=lambda s: s.end - s.start) if containing else None


def _docstring_and_import_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            lines.update(range(node.lineno, node.end_lineno + 1))
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) \
                    and isinstance(first.value.value, str):
                lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def patch_size(patch: PatchInfo, buggy_src: dict[str, str], fixed_src: dict[str, str]) -> dict:
    files = [f for f in patch.files if not is_test_path(f)]
    units: set[tuple[str, str]] = set()
    undetermined: list[str] = []
    for f in files:
        if not f.endswith(".py"):
            undetermined.append(f"non-Python patched file {f}")
            continue
        for side, linenos, texts, src in (("buggy", patch.removed_linenos.get(f, []), patch.removed.get(f, []), buggy_src.get(f)),
                                          ("fixed", patch.added_linenos.get(f, []), patch.added.get(f, []), fixed_src.get(f))):
            if not linenos:
                continue
            tree = safe_parse(src)
            if tree is None:
                undetermined.append(f"{side} source of {f} missing or unparseable")
                continue
            src_lines = src.splitlines()
            ignorable = _docstring_and_import_lines(tree)
            spans = function_spans(tree)
            for ln, text in zip(linenos, texts):
                if ln - 1 >= len(src_lines) or src_lines[ln - 1].rstrip() != text.rstrip():
                    undetermined.append(f"{side} snapshot of {f} does not match patch at line {ln}")
                    break
                sp = innermost(spans, ln)
                if sp:
                    units.add((f, sp.qualname))
                elif text.strip() and not text.strip().startswith("#") and ln not in ignorable:
                    units.add((f, "<module/class level>"))
    n_files = len(files)
    result = {"n_files": n_files, "files": files, "n_units": len(units),
              "units": sorted(f"{f}::{q}" for f, q in units), "undetermined": undetermined}
    result["passed"] = None if undetermined else (n_files <= 2 and len(units) <= 3)
    return result


# --------------------------------------------------------------------------- AST helpers

def dotted(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        base = dotted(expr.value)
        return f"{base}.{expr.attr}" if base else None
    return None


def import_aliases(*nodes: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for n in nodes:
        for node in ast.walk(n):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.asname:
                        aliases[a.asname] = a.name
                    else:
                        aliases[a.name.split(".")[0]] = a.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                mod = ("." * node.level) + (node.module or "")
                for a in node.names:
                    aliases[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name
    # Conventional aliases when the import is not visible in the scanned module.
    aliases.setdefault("np", "numpy")
    aliases.setdefault("pd", "pandas")
    return aliases


def resolve(name: str | None, aliases: dict[str, str]) -> str:
    if not name:
        return ""
    root, _, rest = name.partition(".")
    base = aliases.get(root, root)
    return f"{base}.{rest}" if rest else base


def names_in(expr: ast.AST) -> set[str]:
    out = set()
    for n in ast.walk(expr):
        d = dotted(n) if isinstance(n, (ast.Name, ast.Attribute)) else None
        if d:
            out.add(d)
    return out


def snippet(src: str, node: ast.AST) -> str:
    seg = ast.get_source_segment(src, node) or ""
    return " ".join(seg.split())[:160]


# --------------------------------------------------------------------------- §C.2 hermeticity scan

@dataclass
class Target:
    label: str
    path: str
    node: ast.AST
    module: ast.Module
    src: str
    decorators: list = field(default_factory=list)   # extra decorator nodes (e.g. the test's class)
    temp_seed: set = field(default_factory=set)


def _temp_names(node: ast.AST, aliases: dict[str, str], seed: set[str]) -> set[str]:
    temp = set(seed)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        temp |= {a.arg for a in node.args.args + node.args.kwonlyargs if a.arg in TEMP_FIXTURES}

    def is_temp_expr(e: ast.AST) -> bool:
        for sub in ast.walk(e):
            if isinstance(sub, ast.Call) and TEMP_CALL.search(resolve(dotted(sub.func), aliases)):
                return True
        return bool(names_in(e) & temp) or any(any(n == t or n.startswith(t + ".") for t in temp) for n in names_in(e))

    for _ in range(4):  # propagate through assignments to a fixpoint
        before = len(temp)
        for sub in ast.walk(node):
            if isinstance(sub, (ast.With, ast.AsyncWith)):
                for item in sub.items:
                    if item.optional_vars is not None and is_temp_expr(item.context_expr):
                        temp |= names_in(item.optional_vars)
            elif isinstance(sub, ast.Assign) and is_temp_expr(sub.value):
                for t in sub.targets:
                    temp |= names_in(t)
            elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)) and sub.value is not None and is_temp_expr(sub.value):
                temp |= names_in(sub.target)
        if len(temp) == before:
            break
    return temp


def _refs_temp(expr_list: list[ast.AST], temp: set[str], aliases: dict[str, str]) -> bool:
    for e in expr_list:
        for sub in ast.walk(e):
            if isinstance(sub, ast.Call) and TEMP_CALL.search(resolve(dotted(sub.func), aliases)):
                return True
        for n in names_in(e):
            if n in temp or any(n.startswith(t + ".") for t in temp):
                return True
    return False


def _refs_repo_file(expr_list: list[ast.AST]) -> bool:
    return any(n.split(".")[0] in REPO_READ_HINTS or n.endswith(".datapath") for e in expr_list for n in names_in(e))


def _is_in_memory(arg: ast.AST, aliases) -> bool:
    return isinstance(arg, ast.Call) and resolve(dotted(arg.func), aliases).split(".")[-1] in {"StringIO", "BytesIO"}


def scan_target(t: Target) -> list[dict]:
    aliases = import_aliases(t.module, t.node)
    temp = _temp_names(t.node, aliases, t.temp_seed)
    ev: list[dict] = []

    def add(flag, node, detail):
        ev.append({"flag": flag, "target": t.label, "file": t.path, "line": getattr(node, "lineno", None),
                   "detail": detail, "code": snippet(t.src, node)})

    decos = list(getattr(t.node, "decorator_list", [])) + list(t.decorators)
    for d in decos:
        name = resolve(dotted(d.func if isinstance(d, ast.Call) else d), aliases)
        if NETWORK_MARKERS.search(name):
            add("NETWORK", d, f"network marker {name}")

    seeded = False
    random_calls = []
    body_nodes = [t.node] if not isinstance(t.node, (ast.FunctionDef, ast.AsyncFunctionDef)) else t.node.body
    for top in body_nodes:
        for sub in ast.walk(top):
            if isinstance(sub, (ast.Import, ast.ImportFrom)):
                mods = [a.name for a in sub.names] if isinstance(sub, ast.Import) else [sub.module or ""]
                for mname in mods:
                    if any(mname == m or mname.startswith(m + ".") for m in NETWORK_MODULES):
                        add("NETWORK", sub, f"import {mname}")
                    if mname.split(".")[0] in {"subprocess", "pexpect", "sh"}:
                        add("SUBPROCESS", sub, f"import {mname}")
            if isinstance(sub, (ast.Name, ast.Attribute)) and not isinstance(getattr(sub, "ctx", None), ast.Store):
                r = resolve(dotted(sub), aliases)
                if r and any(r == m or r.startswith(m + ".") for m in NETWORK_MODULES):
                    add("NETWORK", sub, r)
            if isinstance(sub, ast.Subscript) and resolve(dotted(sub.value), aliases) == "os.environ" \
                    and isinstance(sub.slice, ast.Constant) and sub.slice.value == "TZ":
                add("WALL_CLOCK_TZ", sub, "os.environ['TZ']")
            if not isinstance(sub, ast.Call):
                continue
            fn = resolve(dotted(sub.func), aliases)
            short = fn.split(".")[-1] if fn else (sub.func.attr if isinstance(sub.func, ast.Attribute) else "")
            args = list(sub.args) + [k.value for k in sub.keywords]

            # SUBPROCESS
            if fn.startswith(("subprocess.", "pexpect.", "sh.")) or fn in {"os.system", "os.popen"} \
                    or fn.startswith(("os.exec", "os.spawn")):
                add("SUBPROCESS", sub, fn)

            # WALL_CLOCK_TZ
            if short in {"now", "utcnow", "today"} and re.search(r"(datetime|date|Timestamp)", fn):
                add("WALL_CLOCK_TZ", sub, fn)
            if fn in {"time.time", "time.localtime", "time.gmtime", "time.ctime", "time.tzset", "time.mktime"} and \
                    not (fn in {"time.localtime", "time.gmtime", "time.ctime"} and sub.args):
                add("WALL_CLOCK_TZ", sub, fn)
            if fn == "time.strftime" and len(sub.args) < 2:
                add("WALL_CLOCK_TZ", sub, fn)
            if "tzlocal" in fn:
                add("WALL_CLOCK_TZ", sub, fn)
            if short in {"Timestamp", "to_datetime", "datetime64"} and any(
                    isinstance(a, ast.Constant) and a.value in ("now", "today") for a in sub.args):
                add("WALL_CLOCK_TZ", sub, f"{fn}('now'/'today')")
            if fn == "os.environ.get" and sub.args and isinstance(sub.args[0], ast.Constant) and sub.args[0].value == "TZ":
                add("WALL_CLOCK_TZ", sub, "os.environ.get('TZ')")

            # UNSEEDED_RANDOM
            if fn in {"random.seed", "numpy.random.seed"} or (short in {"RandomState", "default_rng", "Random"} and args):
                seeded = True
            elif fn.startswith(("random.", "numpy.random.")) or (short in {"RandomState", "default_rng"} and not args):
                random_calls.append((sub, fn))
            elif re.search(r"(^|\.)(tm|testing|_testing|util\.testing)\.make\w+$", fn):
                random_calls.append((sub, fn))

            # EXTERNAL_PATH
            if short == "open" and fn in OPEN_FUNCS:
                mode = sub.args[1] if len(sub.args) > 1 else next((k.value for k in sub.keywords if k.arg == "mode"), None)
                writing = isinstance(mode, ast.Constant) and isinstance(mode.value, str) and any(c in mode.value for c in "wax+")
                if sub.args and _is_in_memory(sub.args[0], aliases):
                    continue
                _path_flag(add, sub, fn, args, writing, temp, aliases)
            elif fn in OS_WRITE or fn.startswith("shutil."):
                _path_flag(add, sub, fn, args, True, temp, aliases)
            elif fn in NP_IO_WRITE:
                _path_flag(add, sub, fn, args, True, temp, aliases)
            elif fn in NP_IO_READ or (fn.startswith("pandas.read_") and args and not _is_in_memory(args[0], aliases)
                                      and not (isinstance(args[0], ast.Constant) and isinstance(args[0].value, str)
                                               and "\n" in args[0].value)):
                _path_flag(add, sub, fn, args, False, temp, aliases)
            elif isinstance(sub.func, ast.Attribute) and sub.func.attr in WRITE_METHODS and \
                    (args or sub.func.attr in {"unlink", "mkdir", "touch", "rmdir"}):
                recv = [sub.func.value]
                if sub.func.attr.startswith("to_") or sub.func.attr in {"savefig", "tofile"}:
                    if args and _is_in_memory(args[0], aliases):
                        continue
                    _path_flag(add, sub, f".{sub.func.attr}", args, True, temp, aliases)
                else:
                    _path_flag(add, sub, f".{sub.func.attr}", recv + args, True, temp, aliases)
            elif isinstance(sub.func, ast.Attribute) and sub.func.attr in READ_METHODS:
                _path_flag(add, sub, f".{sub.func.attr}", [sub.func.value], False, temp, aliases)

    if random_calls and not seeded:
        for node, fn in random_calls[:5]:
            add("UNSEEDED_RANDOM", node, fn)
    return ev


def _path_flag(add, node, fn, args, writing, temp, aliases) -> None:
    if _refs_temp(args, temp, aliases):
        return
    if not writing and _refs_repo_file(args):
        return  # read of a repository file (path derived from __file__ / datapath): allowed (Amendment 005 5b)
    add("EXTERNAL_PATH", node, f"{'write' if writing else 'read'} via {fn} outside allowed temp locations")


# --------------------------------------------------------------------------- targets and tests

def parse_test_ids(run_test: list[str]) -> list[dict]:
    ids = []
    for line in run_test:
        words = line.split()
        if any(w in ("pytest", "py.test") for w in words):
            for w in words:
                if "::" in w:
                    parts = re.sub(r"\[.*\]$", "", w).split("::")
                    ids.append({"raw": w, "file": parts[0], "class": parts[1] if len(parts) > 2 else None,
                                "func": parts[-1], "runner": "pytest"})
        elif "unittest" in words:
            for w in words[words.index("unittest") + 1:]:
                if not w.startswith("-") and "." in w:
                    ids.append({"raw": w, "dotted": w, "runner": "unittest"})
    return ids


def resolve_unittest_id(tid: dict, test_sources: dict[str, str]) -> dict:
    parts = tid["dotted"].split(".")
    for k in range(len(parts) - 1, 0, -1):
        path = "/".join(parts[:k]) + ".py"
        if path in test_sources:
            rest = parts[k:]
            return {**tid, "file": path, "class": rest[0] if len(rest) >= 2 else None, "func": rest[-1]}
    return {**tid, "file": None, "class": None, "func": parts[-1]}


def find_def(tree: ast.Module, cls: str | None, func: str):
    scope = tree
    cls_node = None
    if cls:
        cls_node = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls), None)
        if cls_node is None:
            return None, None
        scope = cls_node
    fn = next((n for n in scope.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == func), None)
    return fn, cls_node


def _fixture_defs(tree: ast.Module) -> dict[str, ast.AST]:
    out = {}
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.decorator_list:
                nm = dotted(d.func if isinstance(d, ast.Call) else d) or ""
                if nm.endswith("fixture"):
                    out[n.name] = n
    return out


def conftest_chain(test_file: str) -> list[str]:
    parts = test_file.split("/")[:-1]
    return ["/".join(parts[:k] + ["conftest.py"]) for k in range(len(parts), -1, -1)]


def reachability_and_targets(project: str, rec: dict, test_sources: dict[str, str], patch_units: list[str],
                             buggy_src: dict[str, str], fixed_src: dict[str, str]):
    """Returns (reachability dict, list[Target], unresolved list)."""
    targets: list[Target] = []
    unresolved: list[str] = []
    private_calls: list[str] = []
    faulty_short = {u.split("::", 1)[1].split(".")[-1] for u in patch_units if not u.endswith("<module/class level>")}

    for tid in parse_test_ids(rec.get("test_commands", [])):
        if tid["runner"] == "unittest":
            tid = resolve_unittest_id(tid, test_sources)
        src = test_sources.get(tid.get("file") or "")
        tree = safe_parse(src)
        if tree is None:
            unresolved.append(f"{tid['raw']}: test file source unavailable")
            continue
        fn, cls_node = find_def(tree, tid.get("class"), tid["func"])
        if fn is None:
            unresolved.append(f"{tid['raw']}: test definition not found (inherited or generated?)")
            continue
        label = f"test {tid['raw']}"
        seed: set[str] = set()
        cls_decos = list(cls_node.decorator_list) if cls_node is not None else []
        if cls_node is not None:
            for m in cls_node.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in SETUP_NAMES:
                    al = import_aliases(tree, m)
                    seed |= {n for n in _temp_names(m, al, set()) if n.startswith(("self.", "cls."))}
                    targets.append(Target(f"setup {cls_node.name}.{m.name}", tid["file"], m, tree, src))
        for m in tree.body:
            if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)) and m.name in SETUP_NAMES:
                targets.append(Target(f"module setup {m.name}", tid["file"], m, tree, src))
        targets.append(Target(label, tid["file"], fn, tree, src, decorators=cls_decos, temp_seed=seed))

        # (b) the test must not call the faulty function by a private name
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                nm = sub.func.id if isinstance(sub.func, ast.Name) else (sub.func.attr if isinstance(sub.func, ast.Attribute) else "")
                if nm.startswith("_") and not (nm.startswith("__") and nm.endswith("__")) and nm in faulty_short:
                    private_calls.append(f"{tid['raw']} calls {nm}() (line {sub.lineno})")

        # fixtures requested by the test (and by those fixtures), resolved in the file then its conftest chain
        wanted = [a.arg for a in fn.args.args if a.arg not in ("self", "cls") and a.arg not in TEMP_FIXTURES]
        for d in list(fn.decorator_list) + cls_decos:
            if isinstance(d, ast.Call) and (dotted(d.func) or "").endswith("usefixtures"):
                wanted += [a.value for a in d.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        seen: set[str] = set()
        sources_chain = [(tid["file"], tree, src)] + [(c, safe_parse(test_sources.get(c)), test_sources.get(c))
                                                      for c in conftest_chain(tid["file"])]
        while wanted:
            name = wanted.pop()
            if name in seen:
                continue
            seen.add(name)
            for path, t, s in sources_chain:
                if t is None:
                    continue
                fx = _fixture_defs(t).get(name)
                if fx is not None:
                    targets.append(Target(f"fixture {name} ({path})", path, fx, t, s))
                    wanted += [a.arg for a in fx.args.args if a.arg not in ("self", "request") and a.arg not in TEMP_FIXTURES]
                    break

    # faulty functions (buggy version; fixed version for functions the fix adds)
    for u in patch_units:
        path, qual = u.split("::", 1)
        if qual == "<module/class level>":
            continue
        for kind, srcs in (("buggy", buggy_src), ("fixed", fixed_src)):
            tree = safe_parse(srcs.get(path))
            sp = next((s for s in function_spans(tree) if s.qualname == qual), None) if tree else None
            if sp:
                targets.append(Target(f"faulty {path}::{qual} ({kind})", path, sp.node, tree, srcs[path]))
                break

    cov = (rec.get("versions", {}).get("buggy") or {}).get("coverage") or {}
    executed = sorted({ln for pp, d in (cov.get("executed_patched_lines") or {}).items() for cp, lines in d.items()
                       if (not cp.startswith("/") or cp.startswith(f"{config.WORK}/{project}/")) for ln in lines})
    reach = {"coverage_available": bool(cov) and not cov.get("install_failed"),
             "executed_patched_lines_in_checkout": executed, "private_calls": private_calls,
             "unresolved_tests": unresolved}
    if private_calls:
        reach["passed"], reach["basis"] = False, "test calls the faulty function by a private name (§C.1b)"
    elif executed:
        reach["passed"], reach["basis"] = True, "triggering test executes a patched line of the checkout (§C.1a)"
    else:
        reach["passed"], reach["basis"] = None, "no coverage evidence of a patched line executed: manual review (§C.1)"
    return reach, targets, unresolved


# --------------------------------------------------------------------------- per-bug classification

def latest_decisions(audit_dir: Path) -> dict[tuple, dict]:
    out = {}
    for d in records.read_jsonl(audit_dir / "manual_decisions.jsonl"):
        out[(d["project"], int(d["bug_id"]), d["criterion"], d["flag"])] = d
    return out


def classify_bug(rec: dict, decisions: dict[tuple, dict]) -> dict:
    project, bug = rec["project"], int(rec["bug_id"])
    log_dir = config.REPO_ROOT / rec["log_dir"]
    patch_text = (config.FORK_DIR / "projects" / project / "bugs" / str(bug) / "bug_patch.txt").read_text(
        encoding="utf-8", errors="replace")
    patch = parse_patch(patch_text)
    buggy_src, fixed_src = load_sources(log_dir, "buggy"), load_sources(log_dir, "fixed")
    test_src = load_sources(log_dir, "fixed_tests")

    ps = patch_size(patch, buggy_src, fixed_src)
    reach, targets, unresolved = reachability_and_targets(project, rec, test_src, ps["units"], buggy_src, fixed_src)

    evidence, seen = [], set()
    for t in targets:
        for e in scan_target(t):
            key = (e["flag"], e["target"], e["line"])  # a Name and its enclosing Attribute report the same site
            if key not in seen:
                seen.add(key)
                evidence.append(e)
    flags: dict[str, list] = {}
    for e in evidence:
        flags.setdefault(e["flag"], []).append(e)
    if unresolved:
        flags.setdefault("SCAN_INCOMPLETE", []).extend({"flag": "SCAN_INCOMPLETE", "detail": u} for u in unresolved)

    pending: list[dict] = []
    applied: dict[str, dict] = {}

    def decide(criterion: str, flag: str):
        d = decisions.get((project, bug, criterion, flag))
        if d is None:
            pending.append({"criterion": criterion, "flag": flag})
            return None
        applied[f"{criterion}:{flag}"] = {"decision": d["decision"], "reason": d["reason"], "at": d["at"],
                                          "localhost_only": d.get("localhost_only", False)}
        return d["decision"] == "keep"

    patch_ok = ps["passed"] if ps["passed"] is not None else decide("B", "PATCH_SIZE_UNDETERMINED")
    reach_ok = reach["passed"] if reach["passed"] is not None else decide("C.1", "REACHABILITY")
    herm_results = [decide("C.2", f) for f in sorted(flags)]
    hermetic = False if False in herm_results else (None if None in herm_results else True)
    det = rec.get("determinism") or {}
    deterministic = bool(det.get("ran") and det.get("deterministic"))

    criteria = {"reproduces": rec["status"] == "REPRODUCES", "patch_size": patch_ok, "reachable": reach_ok,
                "hermetic": hermetic, "deterministic": deterministic}
    vals = list(criteria.values())
    eligible = False if False in vals else (None if None in vals else True)

    # Sensitivity (Amendment 005 5c): excluded only because of localhost-only NETWORK usage.
    net = applied.get("C.2:NETWORK")
    other_herm_ok = all(v["decision"] == "keep" for k, v in applied.items() if k.startswith("C.2:") and k != "C.2:NETWORK")
    localhost_rescue = bool(net and net["decision"] == "exclude" and net["localhost_only"] and other_herm_ok
                            and all(v is True for k, v in criteria.items() if k != "hermetic"))

    return {"classifier_version": CLASSIFIER_VERSION, "rules_version": config.RULES_VERSION,
            "project": project, "bug_id": bug, "audit_attempt": rec["attempt"],
            "audit_harness_version": rec["harness_version"], "classified_at": records.utcnow(),
            "decisions_digest": decisions_digest(decisions, project, bug),
            "criteria": criteria, "eligible": eligible,
            "eligible_assertion_only": (eligible and bool(rec.get("strict", {}).get("strict_assertion_only")))
            if eligible is not None else None,
            "localhost_sensitivity_would_be_eligible": localhost_rescue,
            "patch_size": ps, "reachability": reach,
            "hermeticity": {"flags": {f: ev[:10] for f, ev in flags.items()}, "n_targets_scanned": len(targets),
                            "targets": sorted({t.label for t in targets})},
            "determinism": {"deterministic": deterministic, "ran": det.get("ran"), "reason": det.get("reason"),
                            "n_varying": det.get("n_varying"), "any_timeout": det.get("any_timeout")},
            "manual_decisions_applied": applied, "pending_manual": pending}


def decisions_digest(decisions: dict[tuple, dict], project: str, bug: int) -> str:
    mine = sorted((k[2], k[3], v["decision"], v["at"]) for k, v in decisions.items() if k[0] == project and k[1] == bug)
    return hashlib.sha256(json.dumps(mine).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- CLI

def cmd_run(args) -> int:
    audit_dir = Path(args.audit_dir)
    latest = records.latest_by_bug(records.read_jsonl(audit_dir / "audit_results.jsonl"))
    decisions = latest_decisions(audit_dir)
    existing = {(e["project"], int(e["bug_id"])): e for e in records.read_jsonl(audit_dir / "eligibility.jsonl")}
    n_new = n_skip = 0
    for (project, bug), rec in sorted(latest.items()):
        if rec["status"] != "REPRODUCES":
            continue
        prev = existing.get((project, bug))
        if prev and prev["audit_attempt"] == rec["attempt"] and prev["classifier_version"] == CLASSIFIER_VERSION \
                and prev["decisions_digest"] == decisions_digest(decisions, project, bug):
            n_skip += 1
            continue
        out = classify_bug(rec, decisions)
        records.append_jsonl(audit_dir / "eligibility.jsonl", out)
        n_new += 1
        print(f"{project}/{bug}: eligible={out['eligible']} criteria={out['criteria']} "
              f"pending={[p['criterion'] + ':' + p['flag'] for p in out['pending_manual']]}")
    print(f"classified {n_new}, unchanged {n_skip}")
    return 0


def cmd_pending(args) -> int:
    audit_dir = Path(args.audit_dir)
    latest = {(e["project"], int(e["bug_id"])): e for e in records.read_jsonl(audit_dir / "eligibility.jsonl")}
    n = 0
    for (project, bug), e in sorted(latest.items()):
        if e["eligible"] is False:
            continue  # already ineligible on automatic criteria; its manual items cannot change the outcome
        for p in e["pending_manual"]:
            n += 1
            print(f"\n== {project}/{bug}  criterion {p['criterion']}  flag {p['flag']}")
            if p["criterion"] == "C.2":
                for ev in e["hermeticity"]["flags"].get(p["flag"], []):
                    print(f"   {ev.get('target', '')} {ev.get('file', '')}:{ev.get('line', '')}  {ev['detail']}\n      {ev.get('code', '')}")
            elif p["criterion"] == "C.1":
                print(f"   {e['reachability']}")
            else:
                print(f"   {e['patch_size']['undetermined']}")
    print(f"\n{n} pending manual decisions")
    return 0


def cmd_decide(args) -> int:
    audit_dir = Path(args.audit_dir)
    if args.decision not in ("keep", "exclude"):
        raise SystemExit("--decision must be keep or exclude")
    d = {"project": args.project, "bug_id": args.bug, "criterion": args.criterion, "flag": args.flag,
         "decision": args.decision, "reason": args.reason, "rules_version": config.RULES_VERSION,
         "decided_by": args.decided_by, "at": records.utcnow()}
    if args.localhost_only:
        d["localhost_only"] = True
    records.append_jsonl(audit_dir / "manual_decisions.jsonl", d)
    print(json.dumps(d))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="classify")
    ap.add_argument("--audit-dir", default=str(config.AUDIT_DIR))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sub.add_parser("pending")
    d = sub.add_parser("decide")
    d.add_argument("--project", required=True)
    d.add_argument("--bug", type=int, required=True)
    d.add_argument("--criterion", required=True, choices=["B", "C.1", "C.2"])
    d.add_argument("--flag", required=True)
    d.add_argument("--decision", required=True, choices=["keep", "exclude"])
    d.add_argument("--reason", required=True)
    d.add_argument("--localhost-only", action="store_true", help="NETWORK usage is localhost-only (sensitivity)")
    d.add_argument("--decided-by", default="claude (PI review pending)")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "pending": cmd_pending, "decide": cmd_decide}[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
