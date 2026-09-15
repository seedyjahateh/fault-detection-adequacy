"""Pinned constants for the Phase 1 audit. Changing any value here is a harness change:
bump HARNESS_VERSION and commit before running."""

import os
from pathlib import Path

HARNESS_VERSION = "1.1.0"
SCHEMA_VERSION = 1
RULES_VERSION = "exclusion-rules-v1"

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = REPO_ROOT / "benchmark" / "audit"
CACHE_DIR = REPO_ROOT / ".audit-cache"
DOCKERFILE = REPO_ROOT / "execution" / "docker" / "bugsinpy" / "Dockerfile"

FORK_URL = "https://github.com/reproducing-research-projects/BugsInPy"
FORK_SHA = "316b95e2353ecda832bad9b42f86fa7c2fcec8ac"
FORK_DIR = Path(os.environ.get("BUGSINPY_FORK_DIR", REPO_ROOT.parent / "BugsInPy"))

BASE_IMAGE = (
    "continuumio/miniconda3:23.3.1-0"
    "@sha256:f3637fcc44fac7c20aebcc6fb8910cf76139ff2f4e6a7777d7643460bee50922"
)
IMAGE_REPO = "fda-bugsinpy-audit"

# Shared caches (rebuildable, not raw artifacts). Conda envs and project clones live in
# the per-project container and are removed with it.
VOLUMES = {
    # conda's package cache is not safe for concurrent writers ([Errno 17] File exists when two
    # workers extract the same package), so it is per worker: "{worker}" is substituted.
    "fda-audit-conda-pkgs-w{worker}": "/opt/conda/pkgs",
    # pip's cache and ccache use atomic writes and are safe to share between workers.
    "fda-audit-pip-cache": "/root/.cache/pip",
    "fda-audit-ccache": "/root/.ccache",
}
WORK = "/work/projects"          # the fork's temp/projects equivalent
PIP_SRC = "/work/pip-src"        # editable VCS requirements are cloned here, outside the checkout
CCACHE_PROJECTS = {"pandas"}     # approved 2026-09-15 for the pandas compile path only

RUNS_PER_VERSION = 3             # §4.1 criterion 1
DETERMINISM_RUNS = 5             # §4.1 criterion 4, scope per Amendment 001
COVERAGE_PIN = "coverage==5.5"   # last release supporting Python 3.5-3.9

TEST_TIMEOUT_S = 20 * 60
DETERMINISM_TIMEOUT_S = 30 * 60
CLONE_TIMEOUT_S = 45 * 60
CHECKOUT_TIMEOUT_S = 20 * 60
ENV_TIMEOUT_S = 45 * 60
COMPILE_TIMEOUT_S = 120 * 60
PROBE_TIMEOUT_S = 10 * 60

PROJECT_WALLCLOCK_GUARD_S = 6 * 3600   # TIMEOUT_PROJECT guard (elapsed wall clock, all sessions)
MIN_HOST_FREE_GB = 4.0

# Amendment 004. "r1" = primary procedure; "unmodified" = the fork's procedure exactly as written.
PROCEDURES = ("r1", "unmodified")
R1_BASE_EXTRA_SPECS = {"3.8": "setuptools==68.0.0"}   # python major.minor -> extra conda spec

# Amendment 004: concurrent workers per project (each has its own container and clone).
DEFAULT_WORKERS = 3
WORKERS = {"keras": 2, "pandas": 2}   # memory-heavy (TensorFlow tests / C compiles)
UNMODIFIED_SAMPLE_DIR = AUDIT_DIR / "unmodified_sample"
UNMODIFIED_SAMPLE_SEED = 20260915

# Top-level importable package per project, used to verify which copy of the code runs.
PACKAGE_MAP = {
    "PySnooper": "pysnooper", "ansible": "ansible", "black": "black",
    "cookiecutter": "cookiecutter", "fastapi": "fastapi", "httpie": "httpie",
    "keras": "keras", "luigi": "luigi", "matplotlib": "matplotlib", "pandas": "pandas",
    "sanic": "sanic", "scrapy": "scrapy", "spacy": "spacy", "thefuck": "thefuck",
    "tornado": "tornado", "tqdm": "tqdm", "youtube-dl": "youtube_dl",
}

PILOT_PROJECTS = ["tqdm", "luigi", "keras", "pandas"]
