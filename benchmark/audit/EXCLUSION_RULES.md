# Phase 1 Classification and Exclusion Rules

- **Version:** `exclusion-rules-v1` — **DRAFT v0, committed 2026-09-15, before any official audit data exists**
- **Governing:** `docs/PROTOCOL.md` §4.1 and §5.2, `docs/AMENDMENTS.md` 001–003
- **Implementation:** `execution/audit/parse.py` and `execution/audit/status.py` (§A); post-hoc classifier (§B–§D)

Items marked **[PROPOSED]** are operationalisations the PI has not yet approved. This file is
committed before the audit so that the timestamp precedes the data. After the PI rules on the
[PROPOSED] items it is re-committed as final. It is then **frozen**: any later change needs PI
approval and a new amendment. Every raw observation needed to recompute §A under an alternative
reading is kept in `audit_results.jsonl`, so a ruling on §A items can be applied without re-running.

---

## §A Reproduction status (harness, per bug)

### A.1 What is run

1. **Versions.** "Buggy" and "fixed" are defined by the BugsInPy checkout, exactly as the fork implements it:
   - **Buggy:** `buggy_commit` plus the test files from `fixed_commit`.
   - **Fixed:** `buggy_commit` plus every file changed by `fixed_commit`.

   Both commit SHAs are resolved to full 40-character SHAs and recorded.
2. **Commands.** Each line of the bug's `run_test.sh` is executed exactly as `bugsinpy-test` does
   (unquoted word splitting, from the checkout root, inside the conda env the fork names). Harness
   additions, none of which change which tests run:
   - an in-container `timeout` of 20 min per command;
   - for pytest commands only: `-p no:cacheprovider --junitxml=<tmp>`.
3. **Repetitions.** 3 consecutive runs per version (§4.1), all in the same environment after a single checkout and compile.
4. **Isolation** (stricter than the fork, which shares one environment across all bugs with the same requirements):
   - Every (bug, version) gets a fresh clone of a pristine base env: `python=<declared>` plus `pytest`, exactly as `bugsinpy-testall` creates it.
   - The project clone is `git clean -ffdx`'d before every checkout.
   - Editable VCS requirements (`-e git+...`) are cloned to `/work/pip-src` rather than inside the checkout.

### A.2 Command verdicts

| Runner | `pass` | `fail` | `not_run` | `timeout` |
|---|---|---|---|---|
| pytest | rc 0 (and not all tests skipped) | rc 1, 2, 3, or death by signal | rc 4 (usage error, e.g. test not found), rc 5 (no tests), all skipped | rc 124/137 at the timeout |
| unittest | rc 0 with ≥1 test run | rc ≠ 0 | rc 0 with 0 tests | rc 124/137 at the timeout |
| other (e.g. `tox`) | rc 0 | rc ≠ 0 | — | rc 124/137 at the timeout |

**Run verdict** = the first match in the order `timeout > not_run > fail > pass` over the run's commands.

### A.3 Failure categories

Each failing test is assigned an exception type and a category:
- **pytest:** from the JUnit `message` attribute, or the last `E   <Type>:` line of the traceback.
- **unittest:** from the last line of the `FAIL:`/`ERROR:` block.

| Category | Rule |
|---|---|
| `ASSERTION` | `AssertionError`; unittest `FAIL`; pytest `Failed` (incl. `DID NOT RAISE`) |
| `API_SURFACE_CRASH` | `ImportError` `cannot import name 'X'`, `NameError` on `X`, `AttributeError` `has no attribute 'X'`, or `TypeError` `unexpected keyword argument 'X'`, **where `X` appears as an identifier in the patch's added lines and not in its removed lines**. Also: a `TypeError` signature error naming `f()` whose `def f(` line the patch changes; `ModuleNotFoundError` for a project module the patch introduces |
| `IMPORT_ERROR` | `ImportError`/`ModuleNotFoundError` not attributable to the patch (environment) |
| `COLLECTION_ERROR` | `SyntaxError`/`IndentationError`, or a pytest collection failure without a parseable type |
| `UNRESOLVED_CRASH` | `AttributeError`/`NameError` without an extractable name, or a signature-style `TypeError` without an extractable function |
| `RUNTIME_EXCEPTION` | Every other exception, incl. `AttributeError`/`TypeError` on names the patch does **not** introduce, and non-signature `TypeError`s (e.g. `unsupported operand`) |
| `RUNNER_ERROR` | The test runner itself did not run tests. Either pytest exited rc 1–3 **without writing a JUnit report** (a startup/config/plugin crash; pytest writes JUnit even after collection errors), or it wrote a JUnit report with no failures despite a non-zero rc; or unittest exited non-zero without any `FAIL:`/`ERROR:` block. Never counted as a test outcome. |
| `PROCESS_CRASH` | Test process killed by SIGSEGV/SIGABRT/SIGBUS/SIGFPE |
| `TIMEOUT` | Command exceeded its timeout |
| `UNKNOWN` | No exception type could be parsed |

When the automated rule cannot decide, the bug falls into a crash category (`UNRESOLVED_CRASH`,
`UNKNOWN`). Leaning toward crash never inflates the eligible count. Such bugs may be manually
reviewed, with each decision logged in `manual_decisions.jsonl`.

### A.4 Strict set **[PROPOSED]**

`STRICT = {ASSERTION, RUNTIME_EXCEPTION}`, read as §5.2 condition 3's "assertion failure or the
documented exception".
- *Rationale:* §5.2 excludes named API-misuse crashes and timeouts. An exception raised *inside the
  faulty code* on the fault-triggering input is a genuine manifestation of the fault.
- *Alternative (sensitivity):* `STRICT = {ASSERTION}` only. It is recorded per bug as
  `strict.strict_assertion_only` and always reported alongside.

### A.5 Status (first matching rule wins)

1. **`FAILS_SETUP`**: any of:
   - a commit is not resolvable upstream;
   - the checkout does not land on the buggy SHA;
   - the fixed checkout shows no changes;
   - conda env creation fails;
   - compile times out (120 min) or does not complete;
   - every run of either version contains a `RUNNER_ERROR` (reason `<version>_runner_error`);
   - **or** the fixed version fails consistently with only `IMPORT_ERROR`/`COLLECTION_ERROR`/`RUNNER_ERROR` failures.

   If buggy setup fails, the fixed version is not attempted.
2. **`FLAKY`**: run verdicts are not identical across the 3 buggy runs, or across the 3 fixed runs.
3. **`TIMEOUT`**: consistent `timeout` on either version.
4. **`REPRODUCES`**: buggy runs are all `fail`, fixed runs are all `pass`, **and every buggy run contains ≥1 failure in STRICT**.
   - **[PROPOSED]** "≥1 per run": for bugs with several triggering tests, one strict failure is enough, because that test alone can separate oracles.
   - Whether failure signatures match across runs is recorded (`failure_signature_consistent`), but does not affect the status.
5. **`REPRODUCES_CRASH_ONLY`**: buggy all `fail` and fixed all `pass`, but some buggy run has no STRICT failure.
6. **`FAILS_EXPECTED_BEHAVIOR`**: everything else. The reason is one of `buggy_passes`, `fixed_fails`, `inverted`, or `buggy_<v>_fixed_<v>`.

**Broad reproduction** = `REPRODUCES` + `REPRODUCES_CRASH_ONLY`.

**Project-level:** `TIMEOUT_PROJECT` when a project's cumulative audit wall clock reaches 6 h. The
project is parked and its un-audited bugs are reported as *not audited*.

---

## §B Patch size (§4.1 criterion 2): ≤3 functions in ≤2 files

Computed from `bug_patch.txt`, the snapshots of the buggy and fixed sources, and the line numbers of the patch hunks.

1. **Files counted:** patched files that are not test files. A test file is a path with a directory
   component `test`, `tests` or `testing`, or a basename matching `test_*.py`, `*_test.py`,
   `*_tests.py`, `tests.py` or `conftest.py`. BugsInPy patches in this fork touch `.py` files only.
2. **Functions counted:** the set of innermost enclosing `def`/`async def` qualified names
   (`Class.method`, `outer.inner`) of:
   - every removed line, located in the buggy file's AST;
   - every added line, located in the fixed file's AST.

   A newly added function counts as touched.
3. **Changes outside any function [PROPOSED]:**
   - Import lines, blank lines, comments and docstring-only changes are ignored.
   - Any other module- or class-level change counts as **one** unit per file.
4. **Rule:** `n_files ≤ 2 AND n_function_units ≤ 3`.

## §C Reachability and hermeticity (§4.1 criterion 3, Amendment 003)

### C.1 Reachable from a public API **[PROPOSED]**

`REACHABLE` iff both hold:
- **(a)** the coverage run of the triggering test(s) on the buggy version executed ≥1 patched line of the buggy file. For pure insertions, this means the line immediately before the insertion. The run imports the checked-out code, as verified by the coverage file origin.
- **(b)** the triggering test body does not call the faulty function by a private (`_`-prefixed) name.

If (a) shows 0 executed lines, or the coverage run failed, the bug goes to manual review.

### C.2 Hermeticity: automated flags, then manual review

**Scanned code:**
- each triggering test function (or `unittest` method) body;
- the fixtures it requests, resolved by name in the test file and its `conftest.py` chain;
- `setUp`/`setUpClass`/`setup_method` in its class;
- each faulty function from §B.

Transitive callees are **not** scanned. This is a stated limitation.

A bug is **excluded** if manual review confirms any of:

| Flag | Automated trigger (static, on the scanned code) |
|---|---|
| `NETWORK` | Imports or calls of `requests`, `urllib`, `urllib2`, `urllib3`, `http.client`, `httplib`, `socket`, `httpx`, `aiohttp`, `websocket(s)`, `ftplib`, `smtplib`; network markers (`@pytest.mark.network`, `@tm.network`, `@network`, `@online`) |
| `EXTERNAL_PATH` | File I/O (`open`, `io.open`, `os.*`/`shutil.*` file operations, `pathlib` read/write, `np.load`/`np.save`, `pd.read_*`/`to_*` with a path) whose path argument does not derive from `tmp_path`, `tmpdir`, `tmp_path_factory` or `tmpdir_factory` |
| `WALL_CLOCK_TZ` | `datetime.now/utcnow/today`, `date.today`, `time.time/localtime/gmtime/strftime` without an explicit time, `Timestamp.now/today`, reads of `TZ`/`tzlocal` |
| `UNSEEDED_RANDOM` | `random.*`, `np.random.*`, `default_rng()`, `RandomState()`, `tm.makeDataFrame`-style random generators, with no seeding call (`seed(`, `RandomState(<int>)`, `default_rng(<int>)`) in the same scanned scope |
| `SUBPROCESS` | `subprocess.*`, `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `pexpect`, `sh.` |

Every automatically flagged bug gets a manual decision: `exclude` or `keep` (false positive), with a
one-line reason, appended to `manual_decisions.jsonl`. Unflagged bugs pass without review.

**Open questions for the PI [PROPOSED; must be resolved before the classifier runs]:**
1. **stdlib `tempfile` directories and `tm.ensure_clean()`** (pandas, tempfile-based and cleaned up) are
   hermetic, but not pytest-managed. A literal reading of Amendment 003 excludes them.
   *Proposal:* treat them like `tmp_path` (allowed).
2. **Read-only fixture files checked into the repository** (e.g. `tests/data/*.csv`) are "paths outside
   pytest-managed temp dirs", but deterministic and version-controlled.
   *Proposal:* allow **reads** of files tracked in the repository at the buggy commit; flag writes.
3. **Localhost-only sockets** (tornado/sanic/scrapy test servers bound to 127.0.0.1) fall under `socket`
   in Amendment 003 as written, which would exclude most of tornado (16 bugs) and sanic (5).
   *Proposal:* apply the rule as written (exclude), and report how many bugs a localhost exception would add, as a sensitivity figure.

## §D Determinism (§4.1 criterion 4, Amendment 001)

- **Scope:** on the fixed version, 5 consecutive runs of the union of:
  - the test files changed by `fixed_commit`;
  - the bug's `test_file` entries.
- **Launcher:** the same one the bug's `run_test.sh` uses (pytest: JUnit XML; unittest: `-v` output).
- **When:** run only for `REPRODUCES` bugs.
- **`DETERMINISTIC`** iff:
  - all 5 runs complete without timeout (30 min each);
  - they yield a non-empty per-test outcome map;
  - that map is identical across the 5 runs.

A test that fails *consistently* on the fixed version does not make a bug nondeterministic.
Bugs with mixed or unsupported runners (e.g. `tox`) have determinism `not_run` and are **not eligible**.

## §E Eligibility

`ELIGIBLE` = all of:
- `status == REPRODUCES` (§A);
- `PATCH_SIZE_OK` (§B);
- `REACHABLE` and no confirmed hermeticity exclusion (§C);
- `DETERMINISTIC` (§D).

The n ≥ 120 power floor (§6.6) is evaluated against ELIGIBLE. The report also gives ELIGIBLE under
the assertion-only sensitivity reading of §A.4.

## §F Manual decisions log

`benchmark/audit/manual_decisions.jsonl` is append-only, one JSON object per decision:

```json
{"project": "pandas", "bug_id": 12, "criterion": "C.2", "flag": "EXTERNAL_PATH",
 "decision": "keep", "reason": "open() target is tmp_path / 'x.csv'",
 "rules_version": "exclusion-rules-v1", "decided_by": "claude (PI review pending)", "at": "2026-..."}
```

If the same (project, bug_id, criterion, flag) has several decisions, the latest wins, and the
earlier ones are kept.
