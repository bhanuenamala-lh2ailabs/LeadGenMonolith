# LeadGenMonolith — read this first

This repo is the consolidated lead-gen tooling for LH2: a SQLite database, a HubSpot sync, the Apollo
credit ledger, 5-vertical funnel automation (Coding, Company Ops Cluster 1, Cluster 2, Global, and
RAT/Specialty Datasets), and the daily funnel Performance Report. It holds code only — no business data,
no secrets. Those are gitignored and live per-machine; a fresh clone needs them copied in by hand (see
README.md section 2-3) before anything here will run.

**Before doing anything in this repo, read, in order:**
1. `README.md` — setup, what's in git vs not, how to run every tool, and a ready-to-paste Claude Code
   bootstrap prompt (section 7) for setting this up on a new machine from scratch.
2. `CONTEXT.md` — the standing hard rules (mail recipient default, no automated mailers/schedulers for
   mail, HubSpot write path, Apollo pause state, +91/mobile/LinkedIn push gates) and known-stale items
   not to trust blindly.
3. `docs/playbooks/icp_sourcing_playbook.md` — the repeatable ICP-sourcing workflow and its standing
   rules (India-only, SignalHire-before-Apollo, per-vertical headcount bands, the hospital-200+ floor).

**Hard rules that apply on every clone, not just this machine:**
- Every Apollo call goes through `leadgen/apollo_ledger.py`'s `call()` — never call `api.apollo.io`
  directly from anywhere. Apollo does not discount or cache a repeat reveal; the ledger is the only
  thing preventing a double-charge for a person/org already revealed. A fresh clone has no ledger until
  `python -m leadgen migrate` has been run there.
- Apollo stays paused (`APOLLO_PAUSED=1` in `.env`) until the owner explicitly says go, per-job.
- HubSpot is written to only through `tools/`, never raw/ad-hoc, and only +91 Indian mobile numbers with
  a LinkedIn URL and POC email may be pushed.
- Mail goes to `bhanu.enamala@lh2.ai` unless another recipient is explicitly named. No automated mailers
  or schedulers send mail on their own.
- Never print, log, or commit a secret. `.env` and `secrets/` are gitignored for a reason — if a task
  seems to need a secret value to proceed, ask the user for it rather than reading it from chat history
  or a file outside this repo.

The `legacy/` folder (the three original repos this was consolidated from) is intentionally gitignored —
it's ~3GB, holds real business data mixed into code, and is not meant to be pushed. Don't add it to git
even if a task seems to need something inside it; ask the user first.
