"""Build the audit image from `git archive` of the pinned fork SHA (never a working tree)."""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import subprocess
import tarfile

from . import config
from .container import docker


def verify_fork() -> None:
    head = subprocess.run(["git", "-C", str(config.FORK_DIR), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    if head != config.FORK_SHA:
        raise RuntimeError(f"Fork at {config.FORK_DIR} is at {head}, expected pinned {config.FORK_SHA}")


def image_tag() -> str:
    h = hashlib.sha256(config.DOCKERFILE.read_bytes().replace(b"\r\n", b"\n") + config.FORK_SHA.encode())
    return f"{config.IMAGE_REPO}:{h.hexdigest()[:12]}"


def ensure_image() -> dict:
    verify_fork()
    tag = image_tag()
    p = docker("image", "inspect", tag)
    if p.returncode != 0:
        archive = subprocess.run(
            ["git", "-C", str(config.FORK_DIR), "-c", "core.autocrlf=false", "archive", "--format=tar",
             config.FORK_SHA, "framework", "projects"], capture_output=True, check=True).stdout
        # Extract the archive (exact blob bytes, LF preserved) into a fresh context directory.
        # Piping a tar context over stdin is unreliable with Docker Desktop's BuildKit.
        ctx_dir = config.CACHE_DIR / "build-context" / tag.split(":")[1]
        if ctx_dir.exists():
            shutil.rmtree(ctx_dir)
        ctx_dir.mkdir(parents=True)
        with tarfile.open(fileobj=io.BytesIO(archive)) as src:
            src.extractall(ctx_dir, filter="data")
        (ctx_dir / "Dockerfile").write_bytes(config.DOCKERFILE.read_bytes().replace(b"\r\n", b"\n"))
        print(f"[image] building {tag} ...", flush=True)
        b = subprocess.run(["docker", "build", "--build-arg", f"BUGSINPY_FORK_SHA={config.FORK_SHA}",
                            "-t", tag, str(ctx_dir)], capture_output=True)
        log = (b.stdout + b.stderr).decode("utf-8", "replace")
        (config.CACHE_DIR / f"image-build-{tag.split(':')[1]}.log").write_text(log, encoding="utf-8")
        if b.returncode != 0:
            raise RuntimeError(f"docker build failed:\n{log[-3000:]}")
    info = json.loads(docker("image", "inspect", tag, check=True).stdout)[0]
    return {"tag": tag, "image_id": info["Id"], "base_image": config.BASE_IMAGE,
            "fork_sha": config.FORK_SHA, "created": info["Created"]}
