"""Draw the unmodified-procedure sample (Amendment 004). Writes SAMPLE.json once; never redraws.

    uv run python -m execution.audit.sample
"""

from __future__ import annotations

import json
import random
import re

from . import config, records
from .audit import bug_ids

SAMPLE_SIZE = 20


def affected_bugs() -> dict[str, list[int]]:
    """Bugs whose declared Python version gets the R1 workaround (3.8.x)."""
    out: dict[str, list[int]] = {}
    for project in sorted(config.PACKAGE_MAP):
        for b in bug_ids(project):
            info = (config.FORK_DIR / "projects" / project / "bugs" / str(b) / "bug.info").read_text(encoding="utf-8")
            m = re.search(r'python_version="(\d+\.\d+)', info)
            if m and m.group(1) in config.R1_BASE_EXTRA_SPECS:
                out.setdefault(project, []).append(b)
    return out


def allocate(sizes: dict[str, int], n: int) -> dict[str, int]:
    """At least 1 per stratum, remainder proportional by largest remainder (ties: larger stratum, then name)."""
    alloc = {p: 1 for p in sizes}
    rest = n - len(sizes)
    total = sum(sizes.values())
    quotas = {p: rest * s / total for p, s in sizes.items()}
    for p, q in quotas.items():
        alloc[p] += int(q)
    left = n - sum(alloc.values())
    for p in sorted(quotas, key=lambda p: (-(quotas[p] - int(quotas[p])), -sizes[p], p))[:left]:
        alloc[p] += 1
    return {p: min(alloc[p], sizes[p]) for p in alloc}


def main() -> int:
    path = config.UNMODIFIED_SAMPLE_DIR / "SAMPLE.json"
    if path.exists():
        print(f"{path} already exists; the sample is drawn once and never redrawn.")
        return 1
    strata = affected_bugs()
    alloc = allocate({p: len(v) for p, v in strata.items()}, SAMPLE_SIZE)
    rng = random.Random(config.UNMODIFIED_SAMPLE_SEED)
    bugs = {p: sorted(rng.sample(sorted(strata[p]), alloc[p])) for p in sorted(strata)}
    doc = {"amendment": "004", "seed": config.UNMODIFIED_SAMPLE_SEED, "size": SAMPLE_SIZE,
           "population": "bugs declaring Python 3.8.x", "strata_sizes": {p: len(v) for p, v in strata.items()},
           "allocation": alloc, "method": "min 1 per project, remainder proportional (largest remainder); "
           "random.Random(seed).sample over sorted bug ids per project, projects in sorted order",
           "drawn_at": records.utcnow(), "fork_sha": config.FORK_SHA, "bugs": bugs}
    records.write_once(path, json.dumps(doc, indent=2), compress=False)
    print(json.dumps(doc, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
