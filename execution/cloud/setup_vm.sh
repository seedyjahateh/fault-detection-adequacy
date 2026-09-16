#!/usr/bin/env bash
# One-time setup of an Ubuntu 24.04 VM for the Phase 1 audit. See docs/CLOUD_RUNBOOK.md.
#
#   curl -LsSf https://raw.githubusercontent.com/seedyjahateh/fault-detection-adequacy/main/execution/cloud/setup_vm.sh -o setup_vm.sh
#   bash setup_vm.sh
#
# Idempotent: safe to re-run. Does not configure git identity or push credentials (do that yourself).
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/seedyjahateh/fault-detection-adequacy.git}"
FORK_URL="https://github.com/reproducing-research-projects/BugsInPy"
FORK_SHA="316b95e2353ecda832bad9b42f86fa7c2fcec8ac"   # must match execution/audit/config.py
UV_VERSION="0.9.8"
PYTHON_VERSION="3.11.14"
BASE="${BASE:-$HOME/research}"

echo "== system packages"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git tmux

if ! command -v docker >/dev/null 2>&1; then
  echo "== Docker Engine (docker.com apt repository)"
  sudo install -m 0755 -d /etc/apt/keyrings
  sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  sudo chmod a+r /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin
fi
sudo usermod -aG docker "$USER"

if ! command -v uv >/dev/null 2>&1 || [ "$(uv --version | awk '{print $2}')" != "$UV_VERSION" ]; then
  echo "== uv $UV_VERSION"
  curl -LsSf "https://astral.sh/uv/$UV_VERSION/install.sh" | sh
fi
export PATH="$HOME/.local/bin:$PATH"

mkdir -p "$BASE"
cd "$BASE"
[ -d fault-detection-adequacy/.git ] || git clone "$REPO_URL" fault-detection-adequacy
if [ ! -d BugsInPy/.git ]; then
  git -c core.autocrlf=false clone "$FORK_URL" BugsInPy
fi
git -C BugsInPy checkout --quiet --detach "$FORK_SHA"
test "$(git -C BugsInPy rev-parse HEAD)" = "$FORK_SHA"

cd fault-detection-adequacy
uv python install "$PYTHON_VERSION"
uv sync --frozen
uv run pytest -q

echo "== building the audit image (as a docker-group member)"
sg docker -c "uv run python -c 'import json; from execution.audit.image import ensure_image; print(json.dumps(ensure_image(), indent=1))'"

echo "== recording the VM environment"
mkdir -p .audit-cache
{
  echo "recorded_at=$(date -u +%FT%TZ)"
  echo "hostname=$(hostname)"
  uname -a
  . /etc/os-release && echo "os=$PRETTY_NAME"
  echo "nproc=$(nproc)"
  free -g | sed -n 2p
  df -h "$BASE" | tail -1
  sg docker -c "docker version --format 'docker_client={{.Client.Version}} docker_server={{.Server.Version}}'"
  echo "uv=$(uv --version)"
  echo "python=$(uv run python --version)"
  echo "repo_sha=$(git rev-parse HEAD)"
  echo "fork_sha=$(git -C "$BASE/BugsInPy" rev-parse HEAD)"
} | tee .audit-cache/vm-environment.txt

cat <<'EOF'

Setup complete. Before the first audit run:
  1. Log out and back in (or run `newgrp docker`) so docker works without sudo.
  2. Set your git identity in this repo:
       git config user.name  "<name>"
       git config user.email "<email>"
  3. Give the VM push access to the repo (e.g. `gh auth login`, or a fine-grained
     personal access token limited to this repository with Contents: read/write).
  4. Start the pilot:  bash execution/cloud/run_audit.sh pilot
EOF
