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
