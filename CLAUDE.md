# CLAUDE.md — fault-detection-adequacy

## Study goal

Measure the gap between structural coverage and real-fault detection for
LLM-generated Python unit test suites, compared with human-written suites on
reproducible BugsInPy bugs under information parity, suite-size control, and
contamination control, and identify which properties of the code under test
predict a large gap.

## Governing specification

`docs/PROTOCOL.md` (v1.0) is the specification. Do not deviate from its design
decisions. If something in it looks infeasible, **flag it to the PI**; do not
quietly swap in an alternative. After the OSF preregistration timestamp, every
change is a logged amendment with a rationale.

**Current phase: Phase 1, the BugsInPy reproduction audit (§10, Month 1).**
LLM generation, Pynguin, mutation analysis, the contamination holdout, and
statistical analysis are out of scope until the PI explicitly opens them.

## Strict fault-detection definition (§5.2)

A suite `detects_fault` iff **all** of:

1. It executes without collection/import error on the **fixed** version, and **passes** there
2. It **fails** on the buggy version
3. The failure is an **assertion failure or the documented exception**, not an
   `ImportError`, `AttributeError` on a hallucinated API, `TypeError` from a
   wrong signature, or a timeout
4. The failure reproduces in **3 of 3** repeated runs

Failures excluded by condition 3 are reported separately as
`spurious_failure_rate`. They are never silently dropped.

## Inclusion criteria (§4.1, preregistered)

- Buggy version fails the bug-triggering test; fixed version passes. Verified locally, 3 consecutive runs
- Patch touches ≤3 functions in ≤2 files
- Faulty function is reachable from a public API without network/filesystem fixtures
- Deterministic across 5 consecutive runs of the developer suite

"Reproduces" and "eligible" are **separate counts, reported independently**.
Never conflate them. Pre-freeze refinements to these criteria are logged in
`docs/AMENDMENTS.md`. Read it together with this section.

- **Broad reproduction** (community-facing number): the bug-triggering test fails
  on buggy and passes on fixed, 3/3 runs, for any failure type.
- **ELIGIBLE** (the headline number) adds all of the following:
  - The buggy-version failure is an assertion failure or a documented exception,
    per §5.2 condition 3. A bug that shows up only as `ImportError`,
    `AttributeError` or `TypeError` on the changed API surface, or as a timeout,
    cannot tell a good oracle from a bad one (Amendment 002).
  - The patch passes the size rule.
  - The fault passes the `benchmark/audit/EXCLUSION_RULES.md` rules (Amendment 003).
  - The determinism check passes. Its scope is the fix-touched test files plus
    the bug-triggering test, 5 runs (Amendment 001).

Power floor (§6.6): **n ≥ 120, evaluated against ELIGIBLE.** If n < 120, reduce
model arms, not bugs. If fewer than 100 bugs are usable, PyBugHive becomes
co-primary (§4.2).

## Phase 1 audit conventions

- Per-bug status (exactly one):
  - `REPRODUCES`
  - `REPRODUCES_CRASH_ONLY`
  - `FAILS_SETUP`
  - `FAILS_EXPECTED_BEHAVIOR`
  - `FLAKY`
  - `TIMEOUT`

  Broad reproduction = `REPRODUCES` + `REPRODUCES_CRASH_ONLY`. `FLAKY`
  (inconsistent across the 3 runs) is distinct from a failure.
- Per-project status `TIMEOUT_PROJECT`: a project whose audit exceeds **6 hours
  wall clock** is parked and revisited only after every other project is done.
- Each bug record holds:
  - Per-run outcomes and the exception type for every run.
  - The subject Python version.
  - Buggy and fixed commit SHAs.
  - Wall-clock time.
  - `attempt` and `harness_version`.

  If the same bug has several records, the latest attempt wins.
- Record debug-minutes and compute wall-clock per project.
- Pilot: tqdm (small), luigi (mid), keras (hard), plus pandas as a probe
  time-boxed by the 6 h guard. Get PI go-ahead before the remaining 13 projects.
- **Amendment 004** (read it before touching environments or runs):
  - **R1** (primary procedure): base conda envs for Python 3.8.x add
    `setuptools==68.0.0`. Unpinned setuptools 75.1.0 vendors typeguard, which
    crashes pytest before any test runs; this affects 320/501 bugs.
  - **Unmodified-procedure sample:** a stratified 20-bug sample runs the fork's
    procedure unchanged, recorded in `benchmark/audit/unmodified_sample/`.
  - **Parallel workers:** each worker has its own container, clone and conda
    package cache. FLAKY/TIMEOUT outcomes seen under parallel load are
    re-audited once serially.
- **Amendment 005:** the approved operationalisations in `EXCLUSION_RULES.md` v1
  (frozen). Notably: runtime exceptions raised by the faulty code count as
  strict; `tempfile`/`ensure_clean` temp dirs and reads of repo-tracked files are
  allowed; localhost sockets are excluded (with a sensitivity figure).
- `project_runs.jsonl` (session events, debug minutes), `eligibility.jsonl`
  (classifier output) and `unmodified_sample/audit_results.jsonl` are data files:
  append-only.

## Harness (execution/audit)

Python 3.11.14 via uv (`uv sync`); stdlib-only at runtime. Tests: `uv run pytest -q`.

```
uv run python -m execution.audit.audit run --projects tqdm luigi keras pandas --commit   # resumable
uv run python -m execution.audit.audit run --procedure unmodified --sample-file benchmark/audit/unmodified_sample/SAMPLE.json
uv run python -m execution.audit.audit log-debug --project P --minutes N --note "..."
uv run python -m execution.audit.classify run | pending | decide ...   # §B–§E eligibility
uv run python -m execution.audit.report [--pilot]                      # derived report
uv run python -m execution.audit.freeze                                # benchmark/BENCHMARK_FROZEN.json
```

- Smoke tests must use `--out .audit-cache/<name>` (scratch, gitignored),
  never `benchmark/audit`.
- Official runs refuse to start with uncommitted harness changes.
- The fork must be checked out at `316b95e` in `../BugsInPy` (or set `BUGSINPY_FORK_DIR`).
- **Where to run:** the Windows laptop cannot run parallel workers (memory
  pressure grew the page file; C: fell to 2.3 GB free on 2026-09-15). The audit
  runs on a Google Cloud VM: see `docs/CLOUD_RUNBOOK.md` and `execution/cloud/`.

## Repository layout (§9)

```
fault-detection-adequacy/
├── PROTOCOL.md                  # frozen + git-tagged (currently at docs/PROTOCOL.md)
├── PREREGISTRATION.md           # OSF-submitted version
├── benchmark/
│   ├── audit/                   # Phase 1 reproduction audit results
│   ├── BENCHMARK_FROZEN.json    # bug IDs + commit SHAs
│   └── holdout/                 # post-cutoff contamination set
├── generation/
│   ├── prompts/                 # versioned, immutable templates
│   ├── models.yaml              # pinned model version strings
│   └── raw/                     # every raw completion, archived
├── execution/
│   ├── docker/                  # per-project pinned images
│   ├── runner.py                # fail-on-buggy/pass-on-fixed harness
│   └── stability.py             # flakiness screen
├── mutation/
├── analysis/
│   ├── analysis.R               # or Python; preregistered scripts
│   └── figures/
├── results/
└── paper/
```

Stack: Python 3.11 for harness code, pytest, coverage.py, mutmut, Pynguin,
Docker Compose, DuckDB, R or statsmodels/pingouin. Python 3.11 is the harness and
analysis environment. Subject bugs run their own pinned interpreters (mostly
3.6–3.8) inside Docker. Record the subject Python version per bug.

## Standing rules

- **Raw artifacts are never deleted, overwritten, or regenerated in place.**
  This covers audit records, run logs, raw LLM completions, and execution
  results. A re-run writes new records (appended, with an attempt/harness
  version) or a new versioned file. It never replaces the old ones.
- **Data files are append-only:** `benchmark/audit/audit_results.jsonl`,
  `benchmark/audit/manual_decisions.jsonl`, and every other raw record file.
- **Derived reports** (`AUDIT_REPORT_pilot.md`, `AUDIT_REPORT.md`,
  `BENCHMARK_FROZEN.json`) are generated by script from the JSONL data and may be
  overwritten, because git holds their history. Never hand-edit them. Change the
  generator and regenerate.
- **Exclusion rules are fixed once committed.** If a new rule seems needed
  mid-audit, stop and ask the PI. Log every manual classification decision with a
  one-line reason in `manual_decisions.jsonl`.
- Log every design change in `docs/AMENDMENTS.md` (numbered, dated, with rationale).
- Write results **incrementally**: one flushed JSONL record per unit of work,
  never buffered in memory until the end.
- Harnesses are **resumable**: detect units that are already recorded and skip them on restart.
- **Pin everything and record it**: Python versions, dependency versions,
  Docker image tags/digests, tool versions, upstream repo SHAs, and model
  version strings.
- **Report honestly.** Never relax a criterion to inflate n. Negative and null
  results are valid results and get reported.
- Commit after each project completes in the audit, with the audit counts in the commit message.
- Time box: at most **30 minutes of debugging effort** on any single project's
  environment. If a project is unsalvageable, document why and move on. Compute
  time is not limited by this box. It has its own guard (6 h per project,
  `TIMEOUT_PROJECT`).

## Environment notes

- Host: Windows 11, PowerShell 5.1. Subject execution happens in Linux Docker
  containers. BugsInPy tooling is bash, so run it inside containers/WSL, not natively.
- PowerShell 5.1 pitfalls that have caused real errors here:
  - A single matching object has an empty/1 `.Count`, which gives wrong counts and non-waiting wait loops.
    Use `@(...)` or `Select-String -Quiet`.
  - Piping strings to `docker exec -i` adds a BOM, and `Set-Content` adds a BOM or changes encoding.
    Use `docker cp` or `[IO.File]::WriteAllText` with UTF-8 without BOM.
  - Inline bash in PowerShell double quotes gets mangled: write a script file instead.
- Git on this host has `core.autocrlf=true` system-wide. `.gitattributes` forces LF, and the fork must be
  cloned with `-c core.autocrlf=false`.
