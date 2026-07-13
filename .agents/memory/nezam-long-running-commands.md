---
name: Nezam long-running run_batch commands
description: How to run a multi-stage/multi-law run_batch.py invocation that takes longer than a single ShellExec timeout without losing progress.
---

`run_batch.py` enrichment stages call Gemini in batches and can easily run 5-15+ minutes for a
single law (e.g. OCR of a 100+ page PDF, or Stage-3 enrichment of 200+ articles across multiple
sub-batches on JSON-parse retries). Plain ShellExec backgrounding does not survive this:
- A blocking ShellExec call is capped at 300s and gets killed on timeout, taking the child
  process with it.
- Even `nohup ... & disown` / `setsid nohup ... & disown -a` was observed being killed shortly
  after — the sandbox appears to tear down detached child processes on container-level restarts
  that happen independently of any single tool call.

**Why:** discovered while onboarding EG_IP/EG_EVIDENCE/EG_COMMERCIAL — three consecutive
approaches (plain blocking call, nohup+disown, setsid+nohup+disown) all lost the process
mid-run with no output persisted.

**How to apply:** for any `run_batch.py` invocation expected to exceed ~4 minutes, temporarily
`configureWorkflow({ name: "Nezam Pipeline", command: "cd nezam-legal-corpus && ... python run_batch.py ...", outputType: "console" })`
then `WorkflowsRestart`, then poll progress with `RefreshAllLogs` (or read the workflow's log
file directly) using repeated `sleep N` ShellExec calls under 300s each. Workflows are managed
by the platform and survive across the polling calls, unlike bare background shell processes.
