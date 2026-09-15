# Fault-Detection Adequacy of LLM-Generated Test Suites
## Research Protocol & Preregistration Plan (v1.0)

**Principal investigator:** Seedy M. Jahateh
**Status:** Protocol draft — to be frozen before data collection
**Execution window:** Autumn 2026 – Summer 2027
**Target:** Registered report (MSR / ESEM / EMSE), arXiv preprint at protocol freeze

---

## 0. Executive summary

LLM-generated test suites are overwhelmingly evaluated on structural coverage and compilability. A large body of empirical work — beginning with Inozemtseva & Holmes (ICSE 2014) — establishes that coverage is a weak proxy for fault-detection effectiveness, and that suite size confounds most naive comparisons. This study measures the *gap* between coverage and real-fault detection for LLM-generated suites, under controls that the existing literature has not applied, and identifies which properties of the code under test predict a large gap.

The study is designed as a **confirmatory, preregistered** experiment with a frozen protocol and a commitment to publish null and negative results.

---

## 1. Positioning against prior work

### 1.1 The immediate competitor

**Vathana, Bhatt, Patel & Eisty, "LLM vs. Human Unit Tests: Fault Detection on Real Python Bugs," arXiv:2606.08588 (7 June 2026).**

Findings: LLM tests detected 20/29 BugsInPy faults (69.0%) vs 5/29 (17.2%) for human tests (Fisher's exact p<0.001, Cohen's h=1.10), with statistically indistinguishable line coverage (84.8% vs 88.5%, p=0.28) and branch coverage (75.2% vs 82.1%, p=0.17).

**Why this does not close the question — and what we do differently:**

| Their limitation (their words or data) | Our correction |
|---|---|
| "Information asymmetry… LLM tests receive bug patch diffs and descriptions via the RAG pipeline" | **Information-parity arms.** The primary arm gives the LLM *no* bug knowledge. Patch-informed generation is a separate, clearly labelled arm. |
| n=29 bugs; "limited statistical power for subgroup comparisons" | Target n ≥ 120 reproducible bugs; power analysis in §7.5 |
| Single model (Gemini 2.5 Flash), single run | ≥4 models spanning closed/open weights; k=5 generation runs per subject |
| LLM tests 31.0 LOC vs human 9.6 LOC, no size control | **Suite-size-matched comparison** as the primary analysis (§6.3) |
| No mutation analysis ("future work") | Mutation arm with per-mutant kill data (§5.4) |
| No contamination handling | Temporal split + post-cutoff holdout (§8.2) |
| Not preregistered | OSF preregistration, frozen protocol, negative results committed |
| No analysis of *what predicts* the gap | RQ4 — mixed-effects model over code features |

### 1.2 Other essential related work

- **Widyasari et al., ESEC/FSE 2020** — BugsInPy: 493 real bugs from 17 real-world Python projects, hand-curated for reproducibility and isolation, each with at least one test that fails on the buggy version and passes on the fixed version.
- **Inozemtseva & Holmes, ICSE 2014** — "Coverage Is Not Strongly Correlated with Test Suite Effectiveness." The suite-size confound is the methodological core of this study.
- **Just, Jalali & Ernst, ISSTA 2014** — Defects4J (854 bugs / 17 Java projects), the gold-standard comparator and the basis for a possible replication arm.
- **Just et al., FSE 2014** — "Are mutants a valid substitute for real faults?" Justifies and bounds the mutation arm.
- **Schäfer, Nadi, Eghbali & Tip, TSE 50(1):85–105, 2024** — TestPilot; competitive branch coverage but semantic gaps and test smells vs developer tests.
- **Yang et al., ASE 2024** — "On the Evaluation of LLMs in Unit Test Generation." Critical finding: *"87.13% of defects cannot be detected due to the compilation issue on average across all studied LLMs"*, and among remaining detectable defects only 47.28% were detected. **Validity rate, not detection rate, is the dominant term.** Our design measures this explicitly.
- **Fine-tuning study, arXiv:2412.16620** — On Defects4J, best model DeepSeek-Coder-6b found 8/163 bugs at 0.74% precision, with build errors accounting for 69.24%–92.94% of failing tests.
- **Ouédraogo et al., arXiv:2407.00225** — large-scale LLM test generation study; introduced CMD, a dataset excluding projects released before May 2023 to mitigate training-data leakage. Precedent for our contamination control.
- **Ouédraogo et al., arXiv:2410.10628** — test smells in LLM-generated unit tests.
- **Watson et al., ICSE 2020** — learning meaningful assert statements; the oracle problem in automated test generation.
- **Barr, Harman, McMinn, Shahbaz & Yoo, TSE 2015** — "The Oracle Problem in Software Testing: A Survey."
- **Arcuri & Briand, ICSE 2011** — "A Hitchhiker's Guide to Statistical Tests for Assessing Randomized Algorithms in SE." Governs our repetition count and effect-size reporting.

---

## 2. Research questions and hypotheses

**RQ1 (Descriptive).** What is the real-fault detection rate of LLM-generated unit test suites on reproducible historical Python defects, under information parity with human developers?

**RQ2 (The gap — primary).** How large is the divergence between structural coverage and real-fault detection for LLM-generated suites, and does it exceed the divergence for human-written suites at matched suite size?

- **H2₀:** At matched suite size, the coverage–fault-detection divergence for LLM suites does not differ from human suites.
- **H2₁:** LLM suites exhibit a strictly larger divergence: comparable or higher coverage, lower real-fault detection.

**RQ3 (Mutants vs real faults).** Does mutation score predict real-fault detection for LLM-generated suites, and is the mutant/real-fault relationship the same for LLM and human suites?

- **H3₀:** The mutation-score-to-real-fault-detection relationship is invariant across suite provenance.
- **H3₁:** Mutation score systematically *over*-predicts real-fault detection for LLM suites (i.e. LLM suites kill mutants they would not catch as real faults), because LLM assertions tend to encode observed rather than intended behaviour.

**RQ4 (Predictors).** Which measurable characteristics of the code under test predict a large coverage-versus-fault-detection gap?

Exploratory, with a preregistered candidate feature set (§6.5) and preregistered modelling approach. Reported as exploratory regardless of significance.

**RQ5 (Mechanism — qualitative).** Among LLM suites that cover the faulty lines but fail to detect the fault, what is the failure mode? Preregistered taxonomy: (a) no assertion on the affected value; (b) assertion encodes buggy behaviour (*oracle capture*); (c) fault requires unreached input partition despite line coverage; (d) exception swallowed; (e) other.

RQ5 is the intellectual heart. The PIE model (Propagation–Infection–Execution) gives the theory: coverage guarantees only Execution. The gap is Infection and Propagation. Oracle capture is Propagation failure, and it is the mechanism most specific to LLMs, because a model shown buggy code will happily assert that the buggy output is correct.

---

## 3. Variables

**Independent:**
- Suite provenance: `{human-developer, LLM-zero-context, LLM-docstring-context, LLM-patch-informed, Pynguin-SBST}`
- Model: ≥4 pinned model snapshots
- Generation run: k = 1..5 (seeds / repeated sampling)

**Dependent (primary):**
- `detects_fault` ∈ {0,1} per (bug × suite), per the strict definition in §5.2
- `line_coverage`, `branch_coverage` on the faulty module (buggy version)
- `mutation_score` on sampled mutants
- `gap` = coverage-normalised fault-detection shortfall (§5.3)

**Dependent (secondary):**
- `valid_suite_rate` — fraction of generated suites that execute at all
- `passes_on_fixed` — prerequisite for validity
- `flaky` — fails the stability screen
- suite size (tests, LOC, assertions)

**Controlled:** suite size (matched sub-sampling), project (random effect), prompt template (fixed), temperature, model version string.

---

## 4. Subjects and benchmark construction

### 4.1 Primary benchmark: BugsInPy

493 bugs / 17 projects. **Reproducibility is the binding constraint:** an independent evaluation (Aguilar et al.) found only ~67% of expected results reproduced three years after release, and unlike Defects4J, BugsInPy does not track per-bug reproducibility status. Budget real time for this.

**Mitigations:**
1. Use the Docker-based reproduction fork (`reproducing-research-projects/BugsInPy`), which pins Python versions and dependencies via miniconda3 images. Full reproduction is documented as needing roughly 4 cores / 8 GB RAM / 100 GB disk.
2. Run a **reproduction audit** as Phase 1 and publish the per-bug status table as a standalone artifact. This is a genuine community contribution independent of the study's findings.
3. Freeze the surviving set as `BENCHMARK_FROZEN.json` with commit SHAs before any generation.

**Inclusion criteria (preregistered):**
- Buggy version fails the bug-triggering test; fixed version passes — verified locally, 3 consecutive runs
- Patch touches ≤3 functions in ≤2 files (keeps the unit-test framing honest)
- Faulty function is reachable from a public API without network/filesystem fixtures
- Deterministic across 5 consecutive runs of the developer suite

### 4.2 Robustness benchmark: PyBugHive

PyBugHive (Antal et al.) is a manually validated, reproducible Python bug database built from issue-tracker links rather than heuristics. Use as a **robustness replication** for the primary hypothesis. If BugsInPy reproduction yields <100 usable bugs, promote PyBugHive to co-primary.

### 4.3 Contamination holdout (critical)

Every BugsInPy project is a public GitHub repository, and the fixed versions with their developer tests are almost certainly memorised. Following the CMD precedent in Ouédraogo et al.:

- Build a **post-cutoff holdout**: 25–40 bugs mined from projects' commit history *after* the newest model's training cutoff, using the BugsInPy construction criteria.
- Preregister the holdout as the contamination check: if LLM performance drops materially on holdout vs main set, contamination is the explanation and headline numbers must be reported on the holdout.
- Additionally run a memorisation probe: prompt each model for the developer test of a sample of bugs *without* providing it; measure near-duplicate rate.

### 4.4 Java replication arm (stretch, only if time permits)

Defects4J with EvoSuite as the SBST baseline. Include only if the Python arms complete by Month 7. Do not let this sink the study.

---

## 5. Treatments and measurement

### 5.1 Generation arms

| Arm | Context given to generator | Purpose |
|---|---|---|
| A. Human baseline | — (developer tests from repo, bug-triggering test **excluded**) | Reference |
| B. LLM zero-context | Function source + signature only | **Primary** — information parity |
| C. LLM docstring | Source + docstring + module imports | Realistic dev use |
| D. LLM patch-informed | + patch diff + issue text | Replicates arXiv:2606.08588; upper bound |
| E. Pynguin (SBST) | Automated, coverage-driven | Non-LLM automated comparator |

**Excluding the bug-triggering test from Arm A is mandatory.** BugsInPy bugs ship with a test that by construction detects the fault; including it makes the human baseline trivially 100%.

### 5.2 Fault detection — strict operational definition

A suite `detects_fault` iff **all** of:
1. It executes without collection/import error on the **fixed** version, and **passes** there
2. It **fails** on the buggy version
3. The failure is an **assertion failure or the documented exception**, not an `ImportError`, `AttributeError` on a hallucinated API, `TypeError` from a wrong signature, or a timeout
4. The failure reproduces in **3 of 3** repeated runs

Condition 3 is what separates this from prior work. A test that crashes on a nonexistent method is not detecting a fault. Failures excluded by condition 3 are reported separately as `spurious_failure_rate` — a headline metric in its own right, given that build errors accounted for 69–93% of failing tests in the fine-tuning study.

### 5.3 The gap metric

Report three, preregistered:

1. **Raw gap** = `mean(coverage_of_faulty_lines) − fault_detection_rate`
2. **Conditional detection** = P(detect | faulty lines covered). This is the cleanest statement of the phenomenon: given the suite *executed* the bug, did it *catch* it?
3. **Normalised effectiveness** = fault detection at matched suite size (§6.3)

Metric 2 is the money metric. It isolates Infection+Propagation from Execution.

### 5.4 Mutation arm

- **Tool:** `mutmut` primary; `cosmic-ray` cross-check on a 20-bug subsample. Verify per-mutant kill export before committing — the statistical plan needs mutant-level data, not just a score.
- **Scope:** mutants in the faulty function ± its module only. Whole-project mutation is computationally infeasible solo.
- **Sampling:** uniform random sample of ≥200 mutants per subject (or all, if fewer). Preregister the sampling seed.
- **Equivalent mutants:** not solvable in general. Preregistered handling: report mutation score with surviving mutants unclassified, plus a manually-classified 100-mutant subsample to estimate the equivalent-mutant rate, and report it as a bounded threat.
- **Timeouts:** 10× the baseline test runtime, counted as killed, reported separately.

### 5.5 Stability / flakiness screen

Every suite runs 5× on the fixed version. Any suite with inconsistent outcomes is marked `flaky` and excluded from primary analysis, reported as a rate. Do this *before* the buggy-version runs.

---

## 6. Analysis plan

### 6.1 Primary test (RQ2)

Paired comparison on the same bug across provenances → **McNemar's test** on paired binary detection outcomes, with odds ratio and exact 95% CI. This is the correct test and is not what the competitor used (they used Fisher's exact, which treats the samples as independent — defensible for their design, weaker for a paired one).

### 6.2 Secondary comparisons

- Continuous metrics (coverage, LOC, assertions): **Wilcoxon signed-rank** (paired), effect size **Cliff's delta** or **Vargha-Delaney Â₁₂**
- Across-model comparison: **Friedman** + post-hoc Nemenyi
- All p-values corrected with **Benjamini-Hochberg** within each RQ family; correction families preregistered

### 6.3 Suite-size control (non-negotiable)

Three complementary strategies, all preregistered:
1. **Matched sub-sampling** — repeatedly draw random sub-suites of size *n* from both LLM and human suites, where *n* = min(|LLM|, |human|); bootstrap 1000×; compare detection at matched size.
2. **Size as covariate** in the mixed model.
3. **Detection-per-assertion** and **detection-per-LOC** as normalised efficiency metrics.

Given the competitor found LLM tests 3.2× longer, this control may well reverse or erase their headline effect. That would be a publishable finding.

### 6.4 LLM nondeterminism

k=5 generation runs per (bug × model × arm). Per Arcuri & Briand, report the distribution rather than a single run. Primary analysis on the **per-run mean detection rate**; report the union ("any run detects") and intersection ("all runs detect") as bounds. Temperature fixed at a preregistered value; if temperature 0, still run k=5 to capture API nondeterminism.

### 6.5 RQ4 model

Mixed-effects logistic regression: `detects_fault ~ provenance + suite_size + code_features + (1|project)`.

Preregistered candidate features: cyclomatic complexity, function LOC, parameter count, number of branches, presence/quality of docstring, type-annotation coverage, external dependency count, patch size, whether the fault is in a boundary condition, input-domain type (numeric/string/collection/object), and exception-handling density.

Reported as **exploratory**. A gradient-boosted model with SHAP may be run as a secondary descriptive analysis, clearly labelled non-confirmatory.

### 6.6 Power

For McNemar's with an expected discordant-pair proportion of ~0.25 and a target detectable odds ratio of 2.0 at α=0.05, power 0.80 requires roughly 110–130 paired observations. **Minimum viable n = 120 bugs.** If the reproduction audit yields fewer, reduce the number of model arms rather than the number of bugs, and say so in the preregistration.

---

## 7. Threats to validity (pre-committed)

**Internal.** Prompt engineering asymmetry — fixed template, published verbatim, no per-subject tuning. Environment drift — everything in pinned Docker images. Flakiness — §5.5 screen. Researcher degrees of freedom — protocol frozen and timestamped before generation.

**External.** Python unit-level only; 17 projects; open-source only. Defects4J arm partially addresses language generality if completed.

**Construct.** Fault detection as fail-on-buggy/pass-on-fixed does not credit tests that correctly specify different behaviour. Mutation score is a proxy whose validity rests on the coupling hypothesis (Just et al., FSE 2014) — cite and bound, do not assume. Coverage measured on the faulty module, not whole project.

**Conclusion.** Multiple comparisons corrected. Subgroup analyses exploratory. k=5 may understate high-temperature variance.

**Contamination.** First-order threat; §4.3 is the mitigation and the holdout result is reported alongside the main result, not buried.

---

## 8. Preregistration and open science

1. **Freeze protocol** → OSF preregistration with timestamp, before any generation run.
2. **arXiv the protocol** as a standalone preprint at freeze. This establishes priority and is itself a citable artifact — valuable for PhD applications.
3. **Registered report** submission. Realistic venues for an unaffiliated solo researcher: **MSR** (registered reports track), **ESEM**, **EMSE** (journal-first registered reports), **ICSME**. None require institutional affiliation. ICSE/FSE/ASE full tracks are viable for the completed study but are high-risk for a first paper.
4. **Artifact** targeting ACM SIGSOFT badging (Available → Functional → Reusable), Zenodo DOI.
5. **Negative results committed in writing** in the preregistration: if H2₁ is not supported, the paper reports that.

---

## 9. Repository structure

```
fault-detection-adequacy/
├── PROTOCOL.md                  # this document, frozen + git-tagged
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

**Stack:** Python 3.11, pytest, coverage.py, mutmut, Pynguin, Docker Compose, DuckDB for results, R or statsmodels/pingouin for statistics, GitHub Actions for the harness smoke tests.

**Archive every raw generation.** Closed models get deprecated and silently updated; the raw completions are the only reproducible record.

---

## 10. Execution timeline (10–15 hrs/week, solo)

| Month | Phase | Deliverable |
|---|---|---|
| 1 (Oct 2026) | Reproduction audit | Per-bug status table; usable-n decision |
| 2 | Harness + stability screen | `runner.py` validated on 10 bugs end-to-end |
| 3 | Protocol freeze | OSF preregistration + arXiv protocol preprint |
| 4 | Pilot (20 bugs, 2 models) | Pipeline validated; cost model confirmed |
| 5–6 | Full generation | All arms × models × k=5 archived |
| 7 | Execution + coverage | Primary dataset complete |
| 8 | Mutation arm | Per-mutant kill data |
| 9 | Contamination holdout | Holdout results |
| 10 | Analysis | Preregistered analyses run |
| 11 | RQ5 qualitative coding | Failure-mode taxonomy, second coder if possible |
| 12 | Writing + artifact | Submission + Zenodo DOI |

**Gate at Month 4.** If the pilot shows the harness is unreliable or n<80, cut to 2 models and 2 arms (B and A only) and ship a smaller, clean study. A tight n=100 single-question paper beats a sprawling unfinished one.

---

## 11. Budget

| Item | Estimate |
|---|---|
| API generation: ~120 bugs × 3 LLM arms × 4 models × 5 runs ≈ 7,200 calls | $150–400 |
| Pilot + retries + holdout | $100 |
| Compute (local + occasional cloud VM for mutation runs) | $50–150 |
| **Total** | **$300–650** |

Use cheap/fast models for pilot iteration; spend on frontier models only for the frozen final run. Open-weight models can run locally or on a rented GPU hour if budget binds.

---

## 12. Immediate next actions

1. Clone the Docker reproduction fork and run the audit on three projects this week — this single number (how many bugs actually reproduce in late 2026) determines whether the study is feasible as designed.
2. Read arXiv:2606.08588 in full and draft the one-paragraph positioning statement.
3. Read Inozemtseva & Holmes (ICSE 2014) and Just et al. (FSE 2014) closely; they are the two citations the design rests on.
4. Email Nasir Eisty. He is a faculty author on the competing paper and works in exactly this space. A short, specific note describing your controlled design is a legitimate route to a collaborator, a letter of recommendation, or both — and PhD applications are materially stronger with a faculty co-author.

---

*Protocol v1.0 — not yet frozen. All design decisions above are subject to revision until the OSF preregistration timestamp, after which changes are logged as amendments with rationale.*
