"""Thin Docker helpers. All bash is sent over stdin with LF line endings, so nothing depends on
Windows quoting or CRLF conversion."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass

from . import config


@dataclass
class ExecResult:
    rc: int
    output: str
    seconds: float
    host_timeout: bool


def docker(*args: str, input_bytes: bytes | None = None, timeout: float | None = None,
           check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], input=input_bytes, capture_output=True,
                          timeout=timeout, check=check)


def container_name(project: str, worker: int = 0) -> str:
    return f"fda-audit-{project.lower()}-w{worker}"


def exec_script(name: str, script: str, timeout_s: float) -> ExecResult:
    """Run a bash script inside the container. The script must enforce its own in-container
    `timeout`; the host timeout is only a backstop (it cannot kill in-container processes)."""
    body = script.replace("\r\n", "\n").encode("utf-8")
    start = time.monotonic()
    try:
        p = subprocess.run(["docker", "exec", "-i", name, "bash", "-s"], input=body,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout_s + 120)
        return ExecResult(p.returncode, p.stdout.decode("utf-8", "replace"), time.monotonic() - start, False)
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else ""
        # Kill whatever is still running in the container for this exec.
        docker("exec", name, "bash", "-c", "pkill -KILL -f 'timeout -k' || true", timeout=60)
        return ExecResult(124, out, time.monotonic() - start, True)


def inspect(name: str) -> dict | None:
    p = docker("inspect", name)
    if p.returncode != 0:
        return None
    return json.loads(p.stdout)[0]


def ensure_container(project: str, image_tag: str, worker: int = 0) -> dict:
    name = container_name(project, worker)
    info = inspect(name)
    image_id = json.loads(docker("image", "inspect", image_tag, check=True).stdout)[0]["Id"]
    if info is not None:
        if info["Image"] != image_id:
            raise RuntimeError(
                f"Container {name} was created from a different image ({info['Image'][:19]}); "
                f"current image is {image_id[:19]}. Finish or clean up that project before switching images.")
        if not info["State"]["Running"]:
            docker("start", name, check=True)
        return {"name": name, "image_id": image_id, "created": False}
    # --init: PID 1 reaps orphaned children (sleep infinity does not, leaving zombies).
    args = ["run", "-d", "--init", "--name", name, "--label", "org.fda.audit=1",
            "--label", f"org.fda.project={project}", "--label", f"org.fda.worker={worker}"]
    for vol, mount in config.VOLUMES.items():
        args += ["-v", f"{vol.format(worker=worker)}:{mount}"]
    # Only environment variables that change *where* things are stored, never test behaviour.
    args += ["-e", "CCACHE_DIR=/root/.ccache", "-e", f"PIP_SRC={config.PIP_SRC}",
             image_tag, "sleep", "infinity"]
    docker(*args, check=True)
    return {"name": name, "image_id": image_id, "created": True}


def remove_containers(project: str) -> None:
    ids = docker("ps", "-aq", "--filter", "label=org.fda.audit=1", "--filter", f"label=org.fda.project={project}").stdout.split()
    if ids:
        docker("rm", "-f", *[i.decode() for i in ids], timeout=900)
