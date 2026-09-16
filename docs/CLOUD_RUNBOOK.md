# Cloud Runbook: Phase 1 Audit on a Google Cloud VM

The laptop cannot run the audit with parallel workers: on 2026-09-15, 3 workers exhausted host memory
and C: fell from 17.7 GB to 2.3 GB free. This runbook moves the audit to a VM. The harness, rules and
records are unchanged. Each record stores the image and harness versions, so records from different
hosts can be compared.

## 1. Create the VM (you)

- **Account and budget.** Sign up for the Google Cloud Free Trial. Create a project and set a budget
  alert (Billing → Budgets & alerts), e.g. at $50.
- **Create the VM.** In Cloud Shell or with a local `gcloud`:

  ```bash
  gcloud compute instances create fda-audit \
    --zone=us-central1-a --machine-type=e2-standard-8 \
    --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
    --boot-disk-size=200GB --boot-disk-type=pd-balanced
  gcloud compute ssh fda-audit --zone=us-central1-a
  ```

  If the image family name has changed, find the current one with
  `gcloud compute images list --filter="family~ubuntu-2404"`.
- **Quota.** If creation fails on CPU quota (Free Trial accounts cannot request increases), use
  `e2-standard-4` and run with `--workers 2`.

## 2. Set up (on the VM)

```bash
curl -LsSf https://raw.githubusercontent.com/seedyjahateh/fault-detection-adequacy/main/execution/cloud/setup_vm.sh -o setup_vm.sh
bash setup_vm.sh
newgrp docker            # or log out and back in
cd ~/research/fault-detection-adequacy
git config user.name "<name>"; git config user.email "<email>"
gh auth login            # or a fine-grained token with Contents: read/write on this repo only
```

`setup_vm.sh` does the following:
- installs Docker Engine and uv 0.9.8;
- clones this repo and the BugsInPy fork, pinned at `316b95e`;
- installs Python 3.11.14 and runs the unit tests;
- builds the audit image;
- writes the VM's versions to `.audit-cache/vm-environment.txt`.

## 3. Run (in order; each stage resumes if interrupted)

| Stage | Command | Rough duration (8 vCPU) |
|---|---|---|
| Smoke test of unexercised runners | `bash execution/cloud/run_audit.sh smoke` | ~1–2 h |
| Pilot → **PI go-ahead** | `PUSH=1 bash execution/cloud/run_audit.sh pilot` | ~10–20 h |
| Remaining 13 projects + sample | `PUSH=1 bash execution/cloud/run_audit.sh full` | ~1–2 days |
| Parked projects | `PUSH=1 bash execution/cloud/run_audit.sh parked` | open-ended |
| Classify, report, freeze | `PUSH=1 bash execution/cloud/run_audit.sh finalize` | minutes |

**Monitoring:**
- Attach with `tmux attach -t fda-<stage>` and detach with `Ctrl-b d`.
- Check disk with `df -h`, and Docker usage with `docker system df`.

**Manual reviews between `pilot`/`full` and `finalize`:**

```bash
uv run python -m execution.audit.classify pending
uv run python -m execution.audit.classify decide --project P --bug N --criterion C.2 --flag FLAG \
    --decision keep|exclude --reason "one line"
```

## 4. Tear down

When the audit is finished and pushed, delete the VM **and its disk**; a stopped VM still bills for the disk:

```bash
gcloud compute instances delete fda-audit --zone=us-central1-a
```
