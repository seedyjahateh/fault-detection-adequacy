# Protocol Amendments

Changes to `docs/PROTOCOL.md` design decisions, logged in order. Entries dated
before the OSF preregistration timestamp are **pre-freeze design decisions**, not
deviations. They are logged anyway so the record of researcher decisions is
complete from the start.

Each entry gives: the protocol section affected, the original text, the
change, the rationale, and the date.

---

## Amendment 001 — Scope of the subject determinism check

- **Date:** 2026-09-15
- **Status:** Pre-freeze design decision
- **Affects:** §4.1, inclusion criterion 4
- **Original:** "Deterministic across 5 consecutive runs of the developer suite"
- **Amended:** Deterministic across 5 consecutive runs of (a) the test files
  touched by the fix commit plus (b) the bug-triggering test(s), on the fixed
  version.
- **Rationale:** Running the full developer suite 5 times is not feasible for
  the largest subjects (pandas especially) on a solo compute budget. It also
  would not materially change which subjects are admitted, because the tests
  relevant to the fault are the fix-touched and bug-triggering tests.
- **Consequences:**
  - `BENCHMARK_FROZEN.json` carries `"status": "provisional"` and a
    `stability_scope` field recording exactly what was checked. It becomes final
    at protocol freeze (Month 3).
  - This check is separate from the generated-suite flakiness screen (§5.5),
    which it does not replace.

---

## Amendment 002 — Eligibility requires a non-crash manifestation on the buggy version

- **Date:** 2026-09-15
- **Status:** Pre-freeze design decision
- **Affects:** §4.1, inclusion criterion 1 (coupling with §5.2 condition 3)
- **Original:** "Buggy version fails the bug-triggering test; fixed version
  passes — verified locally, 3 consecutive runs"
- **Amended:**
  - That condition, unchanged, defines **broad reproduction**, which is
    reported as the community-facing reproduction count.
  - **Eligibility** additionally requires the buggy-version failure to be an
    assertion failure or a documented exception, per §5.2 condition 3.
  - Bugs that reproduce only through crash-type failures (for example
    `ImportError` or `AttributeError` on a symbol introduced by the fix) get
    status `REPRODUCES_CRASH_ONLY`. They count toward broad reproduction but are
    not eligible.
  - The §6.6 power floor (n ≥ 120) is evaluated against the eligible count.
- **Rationale:** If a subject's only possible manifestation on the buggy version
  is a crash on missing or changed API surface, no test suite can separate a good
  oracle from a bad one on that subject. Oracle quality is the study's dependent
  variable, so such subjects carry no information for RQ2, RQ3 or RQ5. The size
  of this population is reported as a finding in its own right: it bears on why
  prior work reports build errors dominating failing tests.
- **Consequences:** The exception type is recorded for every run of every bug.

---

## Amendment 003 — Operational scope of the "network/filesystem fixtures" exclusion

- **Date:** 2026-09-15
- **Status:** Pre-freeze design decision
- **Affects:** §4.1, inclusion criterion 3
- **Original:** "Faulty function is reachable from a public API without
  network/filesystem fixtures"
- **Amended:**
  - pytest-managed temporary directories (`tmp_path`, `tmpdir`,
    `tmp_path_factory`, `tmpdir_factory`) are **allowed**.
  - Exclusion applies to:
    - network access (`requests`, `urllib`, `socket`, `httpx`, network markers)
    - reads or writes to paths outside pytest-managed temp directories
    - dependence on wall-clock time or timezone
    - unseeded randomness
    - subprocess calls to external binaries
  - The binding operational rules are in `benchmark/audit/EXCLUSION_RULES.md`,
    committed before the classifier runs.
- **Rationale:** The concern behind the criterion is nondeterminism and
  dependence on external state, not filesystem access as such. pytest-managed
  temp directories are hermetic and deterministic.
- **Consequences:**
  - Classification has two stages: automatic flagging, then manual review.
  - Every manual decision is logged with a one-line reason in
    `benchmark/audit/manual_decisions.jsonl`.
  - Rule changes after the classifier starts require PI approval and a new
    amendment.

---

## Amendment 004 — Environment workaround R1 (setuptools pin) and parallel execution

- **Date:** 2026-09-15
- **Status:** Pre-freeze design decision, approved by the PI after smoke-test evidence
- **Affects:** §4.1 criterion 1 (how reproduction is operationalised); §4.1 mitigation 1 (use the fork's environments)
- **Evidence:**
  - The fork creates every base environment with an unpinned `conda create python=X pytest`.
  - Resolved on 2026-09-15, Python 3.8.1 and 3.8.3 get `setuptools=75.1.0` (and `pytest=7.4.4`).
    Python 3.5–3.7 get setuptools 40.2–65.6.3.
  - setuptools ≥71 bundles `typeguard`, whose metadata registers a `pytest11` plugin. Together
    with the bugs' 2020-era pins, pytest crashes before collecting any test:
    - `ImportError: cannot import name 'is_typeddict'` when an older `typing_extensions` is pinned (luigi/1);
    - `AssertionError` in `Parser.addini` under pytest 5.4.x (pandas/1).
  - This exposes 320 of 501 bugs: all of pandas, scrapy, luigi, matplotlib, black, fastapi, sanic and
    PySnooper, plus 1 spacy bug. (An earlier count of 319 in 8 projects, reported to the PI, missed the spacy bug.)
- **Amended:**
  - **R1 (primary procedure).** For bugs declaring Python 3.8.x, the base environment is created
    as `conda create python=X pytest setuptools==68.0.0`. setuptools 68.0.0 (June 2023) predates
    the fork's reproduction run (last commit 2023-09-04). Nothing else about the fork's procedure
    changes. Other Python versions are unaffected and unchanged.
  - **Unmodified-procedure sample.** A stratified random sample of 20 Python 3.8.x bugs is also
    audited with the fork's procedure exactly as written.
    - Allocation: at least 1 per affected project, the remainder proportional (largest remainder);
      seed `20260915`.
    - Results go to `benchmark/audit/unmodified_sample/`, separately from the primary records.
    - The report states the unmodified outcome from this sample; it is not extrapolated from two bugs.
  - **Parallel execution.** Bugs within a project are audited by 2–3 concurrent workers, each in
    its own container with its own clone. Isolation per (bug, version) is unchanged.
    - Every record carries `parallel_workers`.
    - Any bug classified `FLAKY` or `TIMEOUT` while running with more than one worker is re-audited
      once serially (1 worker). The serial record is the latest attempt and wins.
    - Both records are kept, and the report gives the count before and after this confirmation.
  - The 6 h `TIMEOUT_PROJECT` guard is measured as elapsed wall clock across sessions, unchanged in value.
- **Rationale:**
  - The audit gates the study on *usable subjects*. A single upstream packaging change that stops
    the test runner from starting says nothing about whether a bug reproduces.
  - Applying a minimal, era-appropriate, documented workaround follows the fork authors' own
    unmodified/rescued reporting.
  - The unmodified sample keeps the "BugsInPy as shipped" answer evidence-based.
  - Parallelism is needed to finish in a reasonable time (a serial audit is about 150+ compute
    hours). The serial confirmation stops CPU contention from manufacturing FLAKY/TIMEOUT outcomes.

---

## Amendment 005 — Approved operationalisations in EXCLUSION_RULES.md

- **Date:** 2026-09-15
- **Status:** Pre-freeze design decision, approved by the PI; applied before any official audit data
- **Affects:** §4.1 criteria 1–3, §5.2 condition 3, Amendment 003
- **Amended** (full text in `benchmark/audit/EXCLUSION_RULES.md` v1):
  1. **§A.4.** "Assertion failure or the documented exception" = `{ASSERTION, RUNTIME_EXCEPTION}`,
     where a runtime exception is one raised by the code under test that does not name a symbol or
     signature introduced by the patch. The assertion-only reading is always reported as a
     sensitivity figure.
  2. **§A.5.** For bugs with several triggering tests, one strict failure per buggy run is sufficient.
  3. **§B.3.** Import-, blank-, comment- and docstring-only changes are ignored. Any other module- or
     class-level change counts as one function unit per file.
  4. **§C.1.** "Reachable from a public API" = the triggering test executes a patched line of the
     buggy checkout (coverage evidence), and the test does not call the faulty function by a private
     name. Otherwise, manual review.
  5. **§C.2 scope of Amendment 003:**
     - (a) stdlib `tempfile` directories and `tm.ensure_clean()` are allowed, like `tmp_path`;
     - (b) **reads** of files tracked in the repository at the buggy commit are allowed (writes are
       still flagged);
     - (c) localhost-only sockets remain **excluded** under the `socket` rule as written, and the
       number of bugs a localhost exception would add is reported as a sensitivity figure.
- **Rationale:** Each item resolves an ambiguity in the protocol text before data collection, so that
  no rule is chosen after seeing results. (a) and (b) follow Amendment 003's stated concern
  (nondeterminism and external state, not filesystem access as such).
