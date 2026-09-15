"""Bash step scripts executed inside the per-project container, and parsers for their output.

Each script prints lines prefixed with a random per-exec marker so harness metadata can be
separated from arbitrary tool output."""

from __future__ import annotations

import base64
import re
import secrets
import shlex

from . import config

PRELUDE = """set -u
export CCACHE_DIR=/root/.ccache
. /opt/conda/etc/profile.d/conda.sh
W={work}
P={project}
M={marker}
mkdir -p "$W" {pip_src}
"""


def _script(project: str, body: str) -> tuple[str, str]:
    marker = "@@" + secrets.token_hex(8) + "@@"
    pre = PRELUDE.format(work=config.WORK, project=shlex.quote(project), marker=marker,
                         pip_src=config.PIP_SRC)
    return pre + body, marker


def marked(output: str, marker: str) -> list[str]:
    return [ln[len(marker):].strip() for ln in output.splitlines() if ln.startswith(marker)]


def marked_value(output: str, marker: str, key: str) -> str | None:
    for v in marked(output, marker):
        if v == key or v.startswith(key + " "):
            return v[len(key):].strip()
    return None


def marked_block(output: str, marker: str, key: str) -> str | None:
    begin, end = f"{marker} {key}_BEGIN", f"{marker} {key}_END"
    lines = output.splitlines()
    try:
        i = next(k for k, ln in enumerate(lines) if ln.strip() == begin)
        j = next(k for k in range(i + 1, len(lines)) if lines[k].strip() == end)
    except StopIteration:
        return None
    return "\n".join(lines[i + 1:j])


# --------------------------------------------------------------------------- project level

def clone_project(project: str, timeout_s: int) -> tuple[str, str]:
    return _script(project, f"""
if [ ! -d "$W/$P/.git" ]; then
  url=$(grep '^github_url=' /home/user/BugsInPy/projects/$P/project.info | cut -d'"' -f2)
  echo "$M URL $url"
  timeout -k 30 {timeout_s} git clone "$url" "$W/$P" 2>&1 | tail -20
fi
cd "$W/$P" && echo "$M CLONE_HEAD $(git rev-parse HEAD)"
""")


def env_snapshot(project: str) -> tuple[str, str]:
    return _script(project, """
echo "$M DPKG_BEGIN"; cat /image-manifest/dpkg.txt; echo "$M DPKG_END"
echo "$M CONDA_BASE_BEGIN"; cat /image-manifest/conda-base-explicit.txt; echo "$M CONDA_BASE_END"
echo "$M FORK_SHA $(cat /image-manifest/bugsinpy_fork_sha.txt)"
echo "$M CONDA_VERSION $(conda --version)"
echo "$M CCACHE $(ccache --version | head -1)"
""")


# --------------------------------------------------------------------------- bug level

def resolve_commits(project: str, commits: list[str]) -> tuple[str, str]:
    body = 'cd "$W/$P"\n'
    for c in commits:
        q = shlex.quote(c)
        body += f"""
if ! git cat-file -e {q}^{{commit}} 2>/dev/null; then
  echo "$M FETCH_ATTEMPT {q}"
  timeout -k 30 600 git fetch -q origin {q} 2>&1 | tail -3
fi
r=$(git rev-parse --verify -q {q}^{{commit}} 2>/dev/null || echo MISSING)
echo "$M RESOLVE {q} $r"
"""
    return _script(project, body)


def checkout(project: str, bug_id: int, version: int, timeout_s: int) -> tuple[str, str]:
    return _script(project, f"""
cd "$W/$P" && git reset --hard -q && git clean -ffdxq
cd /
timeout -k 30 {timeout_s} bugsinpy-checkout -p "$P" -v {version} -i {bug_id} -w "$W"
echo "$M CHECKOUT_RC $?"
cd "$W/$P"
echo "$M HEAD $(git rev-parse HEAD)"
test -f bugsinpy_bug.info && echo "$M INFO_OK"
echo "$M STATUS_BEGIN"; git status --porcelain --untracked-files=all | grep -v ' bugsinpy_'; echo "$M STATUS_END"
""")


def create_env(project: str, timeout_s: int, procedure: str) -> tuple[str, str]:
    # Mirrors bugsinpy-testall's env naming exactly (python version + requirements hash), but builds
    # a pristine base once and gives every (bug, version) a fresh clone of it (isolation).
    # Procedure r1 (Amendment 004) adds an extra conda spec for some Python versions; base envs are
    # named per procedure so the two procedures never share a base.
    cases = "\n".join(f'  {mm}.*) EXTRA={shlex.quote(spec)} ;;' for mm, spec in config.R1_BASE_EXTRA_SPECS.items())
    extra = f"""case "$PYV" in
{cases}
  *) EXTRA="" ;;
esac""" if procedure == "r1" else 'EXTRA=""'
    return _script(project, f"""
cd "$W/$P"
sed -i -e '/^\\s*#.*$/d' -e '/^\\s*$/d' bugsinpy_requirements.txt
dos2unix bugsinpy_requirements.txt >/dev/null 2>&1
PYV=$(grep -o "3\\..\\.." bugsinpy_bug.info)
H=$(cat <(echo $PYV) bugsinpy_requirements.txt | md5sum | cut -d' ' -f 1)
{extra}
B="base_{procedure}_$H"
echo "$M PYV $PYV"
echo "$M ENV $H"
echo "$M BASE_EXTRA $EXTRA"
if ! conda env list | awk '{{print $1}}' | grep -qx "$B"; then
  echo "$M BASE_CREATE"
  ok=""
  for try in 1 2; do  # one retry for transient download/extraction errors; both attempts are logged
    echo "$M BASE_TRY $try"
    if timeout -k 30 {timeout_s} conda create -n "$B" -y python=$PYV pytest $EXTRA; then ok=1; break; fi
    conda env remove -n "$B" >/dev/null 2>&1
    conda clean -y --tarballs >/dev/null 2>&1
  done
  [ -n "$ok" ] || {{ echo "$M BASE_FAIL"; exit 3; }}
fi
conda env remove -n "$H" >/dev/null 2>&1
if ! timeout -k 30 {timeout_s} conda create -n "$H" --clone "$B" --offline -y >/dev/null; then
  echo "$M CLONE_FAIL"; exit 4
fi
echo "$M SETUPTOOLS $(conda list -n "$H" '^setuptools$' 2>/dev/null | awk '/^setuptools/ {{print $2}}')"
echo "$M ENV_OK"
""")


def compile_version(project: str, timeout_s: int) -> tuple[str, str]:
    cc = 'export CC="ccache gcc" CXX="ccache g++"\necho "$M CCACHE_ON"\n' if project in config.CCACHE_PROJECTS else ""
    return _script(project, f"""
cd "$W/$P"
{cc}timeout -k 30 {timeout_s} bugsinpy-compile
echo "$M COMPILE_RC $?"
test -f bugsinpy_compile_flag && echo "$M FLAG_OK"
{'ccache -s 2>/dev/null | sed "s/^/$M CCSTAT /"' if project in config.CCACHE_PROJECTS else ''}
""")


def probe(project: str, env: str, package: str | None, timeout_s: int) -> tuple[str, str]:
    imp = ""
    if package:
        imp = f"""
timeout -k 10 {timeout_s} python -c "import os, {package} as m; print('MODULE_FILE ' + os.path.realpath(m.__file__))" > /tmp/fda_probe.txt 2>&1
echo "$M IMPORT_RC $?"
grep '^MODULE_FILE ' /tmp/fda_probe.txt | sed "s/^MODULE_FILE /$M MODULE /"
tail -3 /tmp/fda_probe.txt | sed "s/^/$M IMPORT_TAIL /"
"""
    return _script(project, f"""
cd "$W/$P"
conda activate {shlex.quote(env)} || {{ echo "$M ACTIVATE_FAIL"; exit 5; }}
echo "$M PYTHON $(python --version 2>&1)"
echo "$M PYTEST $(python -m pytest --version 2>&1 | tr '\\n' ' ')"
echo "$M FREEZE_BEGIN"; pip freeze 2>&1; echo "$M FREEZE_END"
echo "$M CONDA_BEGIN"; conda list --explicit 2>&1; echo "$M CONDA_END"
{imp}""")


def run_tests(project: str, env: str, timeout_s: int) -> tuple[str, str]:
    """One run of every command in bugsinpy_run_test.sh, executed the way bugsinpy-test does
    (unquoted word splitting), with a timeout and, for pytest, junit XML + no cache plugin."""
    return _script(project, f"""
cd "$W/$P"
conda activate {shlex.quote(env)} || {{ echo "$M ACTIVATE_FAIL"; exit 5; }}
rm -rf .pytest_cache /tmp/fda_junit; mkdir -p /tmp/fda_junit
i=0
while IFS= read -r line || [ -n "$line" ]; do
  line=$(printf '%s' "$line" | sed -e 's/\\r//g')
  [ -z "$(printf '%s' "$line" | tr -d '[:space:]')" ] && continue
  i=$((i+1))
  x=/tmp/fda_junit/c$i.xml
  case "$line" in
    *pytest*|*py.test*) cmd="$line -p no:cacheprovider --junitxml=$x" ;;
    *) cmd="$line" ;;
  esac
  echo "$M CMD_BEGIN $i"
  echo "$M CMDLINE_B64 $(printf '%s' "$line" | base64 -w0)"
  s=$(date +%s.%N)
  timeout -k 30 {timeout_s} $cmd < /dev/null 2>&1
  rc=$?
  e=$(date +%s.%N)
  echo ""
  echo "$M CMD_END $i $rc $s $e"
  if [ -f "$x" ]; then echo "$M JUNIT_B64 $i $(base64 -w0 "$x")"; fi
done < bugsinpy_run_test.sh
echo "$M ALL_DONE $i"
""")


def parse_test_output(output: str, marker: str) -> list[dict]:
    """Split run_tests output into per-command dicts: line, rc, seconds, output, junit."""
    cmds: dict[int, dict] = {}
    cur = None
    buf: list[str] = []
    for ln in output.splitlines():
        if ln.startswith(marker):
            payload = ln[len(marker):].strip()
            key, _, rest = payload.partition(" ")
            if key == "CMD_BEGIN":
                cur = int(rest)
                cmds[cur] = {"index": cur, "line": "", "rc": None, "seconds": 0.0, "output": "", "junit": None}
                buf = []
            elif key == "CMDLINE_B64" and cur is not None:
                cmds[cur]["line"] = base64.b64decode(rest).decode("utf-8", "replace") if rest else ""
            elif key == "CMD_END":
                idx, rc, s, e = rest.split()
                c = cmds[int(idx)]
                c["rc"] = int(rc)
                c["seconds"] = float(e) - float(s)
                c["output"] = "\n".join(buf)
                cur = None
            elif key == "JUNIT_B64":
                idx, _, b64 = rest.partition(" ")
                cmds[int(idx)]["junit"] = base64.b64decode(b64).decode("utf-8", "replace") if b64 else None
        elif cur is not None:
            buf.append(ln)
    return [cmds[k] for k in sorted(cmds)]


def coverage_run(project: str, env: str, include_globs: list[str], timeout_s: int) -> tuple[str, str]:
    """Auxiliary, after the official runs: which patched source lines the triggering tests execute,
    and from which copy of the code (checkout vs pip src vs site-packages)."""
    # No quoting: the command is expanded unquoted (as bugsinpy-test does), so quotes would be literal.
    inc = ",".join(g for g in include_globs if not re.search(r"\s", g))
    return _script(project, f"""
cd "$W/$P"
conda activate {shlex.quote(env)} || {{ echo "$M ACTIVATE_FAIL"; exit 5; }}
if ! python -m coverage --version >/dev/null 2>&1; then
  pip install -q {shlex.quote(config.COVERAGE_PIN)} > /tmp/fda_cov_install.txt 2>&1 || {{ echo "$M COV_INSTALL_FAIL"; tail -3 /tmp/fda_cov_install.txt; exit 6; }}
fi
echo "$M COVERAGE_VERSION $(python -m coverage --version 2>&1 | head -1)"
rm -rf .coverage .coverage.* /tmp/fda_cov.json
# coverage.py does not follow pytest-xdist workers (e.g. keras addopts -n); serialise this auxiliary run only.
nox=""; python -c "import xdist" >/dev/null 2>&1 && nox="-n 0" && echo "$M COV_XDIST_DISABLED"
while IFS= read -r line || [ -n "$line" ]; do
  line=$(printf '%s' "$line" | sed -e 's/\\r//g')
  [ -z "$(printf '%s' "$line" | tr -d '[:space:]')" ] && continue
  set -- $line
  # Rewrite the launcher only; keep the arguments exactly as run_test.sh gives them.
  if [ "$1" = "pytest" ] || [ "$1" = "py.test" ]; then
    shift; launcher="$(command -v pytest || command -v py.test)"; cmd="python -m coverage run -p --include={inc} $launcher $* -p no:cacheprovider $nox"
  elif [ "${{2:-}}" = "-m" ]; then
    py="$1"; mod="$3"; shift 3; cmd="$py -m coverage run -p --include={inc} -m $mod $*"
    case "$mod" in pytest) cmd="$cmd -p no:cacheprovider $nox" ;; esac
  else
    echo "$M COV_UNSUPPORTED $line"; continue
  fi
  echo "$M COV_CMD $cmd"
  timeout -k 30 {timeout_s} $cmd < /dev/null > /tmp/fda_cov_cmd.txt 2>&1
  echo "$M COV_RC $?"
done < bugsinpy_run_test.sh
python -m coverage combine >/dev/null 2>&1
python -m coverage json -o /tmp/fda_cov.json >/dev/null 2>&1 && echo "$M COV_JSON_B64 $(base64 -w0 /tmp/fda_cov.json)"
rm -rf .coverage .coverage.*
""")


def determinism_runs(project: str, env: str, launcher: str, runner: str, targets: list[str],
                     runs: int, timeout_s: int) -> tuple[str, str]:
    """Amendment 001: `runs` consecutive runs of the fix-touched test files + triggering tests."""
    tq = " ".join(shlex.quote(t) for t in targets)
    if runner == "pytest":
        cmd = f'{launcher} {tq} -p no:cacheprovider --junitxml=/tmp/fda_det/r$r.xml'
    else:
        cmd = f'{launcher} -v {tq}'
    return _script(project, f"""
cd "$W/$P"
conda activate {shlex.quote(env)} || {{ echo "$M ACTIVATE_FAIL"; exit 5; }}
rm -rf /tmp/fda_det; mkdir -p /tmp/fda_det
for r in $(seq 1 {runs}); do
  rm -rf .pytest_cache
  s=$(date +%s.%N)
  timeout -k 30 {timeout_s} {cmd} < /dev/null > /tmp/fda_det/r$r.txt 2>&1
  rc=$?
  e=$(date +%s.%N)
  echo "$M DET_END $r $rc $s $e"
  if [ -f /tmp/fda_det/r$r.xml ]; then echo "$M DET_JUNIT_B64 $r $(gzip -c /tmp/fda_det/r$r.xml | base64 -w0)"; fi
  echo "$M DET_OUT_B64 $r $(gzip -c /tmp/fda_det/r$r.txt | base64 -w0)"
  # A timeout already makes the bug non-deterministic (EXCLUSION_RULES §D); further runs cannot change that.
  if [ "$rc" = 124 ] || [ "$rc" = 137 ]; then echo "$M DET_STOPPED_AFTER_TIMEOUT $r"; break; fi
done
""")


def dump_files(project: str, paths: list[str], ref: str | None) -> tuple[str, str]:
    """Emit files (from the work tree, or from a git ref) as base64 so post-hoc classifiers can run
    without the clone."""
    body = 'cd "$W/$P"\n'
    for pth in paths:
        q = shlex.quote(pth)
        if ref:
            spec = shlex.quote(f"{ref}:{pth}")
            body += f'git cat-file -e {spec} 2>/dev/null && echo "$M FILE_B64 {q} $(git show {spec} | gzip -c | base64 -w0)"\n'
        else:
            body += f'[ -f {q} ] && echo "$M FILE_B64 {q} $(gzip -c {q} | base64 -w0)"\n'
    return _script(project, body)


def changed_files(project: str, fixed_sha: str) -> tuple[str, str]:
    return _script(project, f"""
cd "$W/$P"
git show --name-only --format= {shlex.quote(fixed_sha)} | sed "s/^/$M CHANGED /"
""")


def conftest_chain(project: str, test_files: list[str]) -> list[str]:
    """conftest.py candidates on the directory path of each test file (resolved inside the dump)."""
    out: list[str] = []
    for tf in test_files:
        parts = tf.split("/")[:-1]
        for k in range(len(parts) + 1):
            c = "/".join(parts[:k] + ["conftest.py"])
            if c not in out:
                out.append(c)
    return out


def file_payloads(output: str, marker: str) -> dict[str, bytes]:
    import gzip
    res = {}
    for v in marked(output, marker):
        if v.startswith("FILE_B64 "):
            _, path, b64 = v.split(" ", 2)
            res[path] = gzip.decompress(base64.b64decode(b64))
    return res


def launcher_of(line: str) -> tuple[str, str] | None:
    """('pytest', 'python3 -m pytest') / ('unittest', 'python -m unittest') from a run_test.sh line."""
    words = line.split()
    for k, w in enumerate(words):
        if w in ("pytest", "py.test"):
            return "pytest", " ".join(words[:k + 1])
        if w == "unittest" and k >= 2 and words[k - 1] == "-m":
            return "unittest", " ".join(words[:k + 1])
    return None


def test_module_name(path: str) -> str:
    return re.sub(r"\.py$", "", path).replace("/", ".")
