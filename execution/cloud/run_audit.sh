#!/usr/bin/env bash
# Start an audit stage in a detached tmux session. Every stage is resumable: re-running it skips
# bugs that already have records. Logs go to .audit-cache/<stage>-<timestamp>.log
#
#   bash execution/cloud/run_audit.sh smoke     # 1 bug from each not-yet-exercised project (scratch output)
#   bash execution/cloud/run_audit.sh pilot     # tqdm, luigi, keras, pandas probe + pilot part of the sample
#   bash execution/cloud/run_audit.sh full      # the 13 remaining projects + rest of the sample
#   bash execution/cloud/run_audit.sh parked    # revisit TIMEOUT_PROJECT projects (only after `full`)
#   bash execution/cloud/run_audit.sh finalize  # classify, report, freeze, commit
#
# PUSH=1 pushes to origin after each stage. Attach with: tmux attach -t fda-<stage>
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"

STAGE="${1:?usage: run_audit.sh smoke|pilot|full|parked|finalize}"
PILOT="tqdm luigi keras pandas"
REMAINING="PySnooper ansible black cookiecutter fastapi httpie matplotlib sanic scrapy spacy thefuck tornado youtube-dl"
SAMPLE="benchmark/audit/unmodified_sample/SAMPLE.json"
A="uv run python -m execution.audit.audit run"

case "$STAGE" in
  smoke)
    # Runners/builds not yet exercised: unittest (youtube-dl, thefuck), tox (cookiecutter), C builds (matplotlib),
    # ansible's layout. Output goes to a scratch directory, never to benchmark/audit.
    CMD="$A --projects youtube-dl thefuck cookiecutter matplotlib ansible --bugs 1 --out .audit-cache/smoke-vm --allow-dirty"
    ;;
  pilot)
    CMD="$A --projects $PILOT --commit && $A --procedure unmodified --sample-file $SAMPLE --projects luigi pandas --commit \
&& uv run python -m execution.audit.classify run && uv run python -m execution.audit.report --pilot \
&& git add benchmark/audit && (git commit -m 'audit: pilot eligibility classification and pilot report' || true)"
    ;;
  full)
    CMD="$A --projects $REMAINING --commit && $A --procedure unmodified --sample-file $SAMPLE --commit"
    ;;
  parked)
    CMD="$A --projects $PILOT $REMAINING --include-parked --commit"
    ;;
  finalize)
    CMD="uv run python -m execution.audit.classify run && uv run python -m execution.audit.report \
&& uv run python -m execution.audit.freeze && git add benchmark && (git commit -m 'audit: classification, final report, provisional frozen benchmark' || true)"
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 2 ;;
esac

if [ "${PUSH:-0}" = "1" ]; then
  CMD="$CMD && git push origin HEAD"
fi

mkdir -p .audit-cache
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG=".audit-cache/${STAGE}-${STAMP}.log"
RUNNER=".audit-cache/${STAGE}-${STAMP}.sh"
# Write the stage to a script file so commit messages and quoting survive tmux unchanged.
cat > "$RUNNER" <<EOF
#!/usr/bin/env bash
cd "$(pwd)"
export PATH="\$HOME/.local/bin:\$PATH"
( $CMD ) 2>&1 | tee "$LOG"
echo "[stage $STAGE exited with status \${PIPESTATUS[0]}]" | tee -a "$LOG"
EOF
tmux new-session -d -s "fda-${STAGE}" "bash $RUNNER; exec bash"
echo "started tmux session fda-${STAGE}; log: $LOG"
