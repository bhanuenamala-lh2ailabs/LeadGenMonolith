# Daily Supply Funnel Report: n8n handoff

The report is one script. n8n's only job is to call it on a schedule.

## 1. Sync first

```
cd LeadGenMonolith
git pull origin main        # at least commit fe84eb3 (7 Oct 2026) — earlier commits don't have the report code
```

Then follow `README.md` sections 1–4 on the machine n8n will run this from: `.venv`, `.env` filled from `.env.example`, and the token files in `secrets/`. The one n8n needs most is `companyops_gmail_send_token.json` — that's what the mail sends through.

## 2. The command

```
.venv/bin/python tools/daily_report_run.py --send
```

One script: syncs HubSpot (all three portals), rebuilds the owner x stage snapshot, builds the five-vertical report (Coding, Company Ops Global, Cluster 1, Cluster 2, RAT), and mails it. Default recipient is `bhanu.enamala@lh2.ai`; pass `--to` for anyone else.

Add `--assume-complete` if the run fires before the IST day is actually over (see 3 below) — without it, a same-day run shows the daily numbers but hides their percentage-change chips.

Exit code is 0 on success and non-zero on any failed step, so n8n's node should alert on a non-zero exit.

## 3. n8n workflow

- **Schedule Trigger** node, cron. Two options:
  - **00:05 IST (18:35 UTC)**: full, final numbers for the day just ended. No `--assume-complete` needed.
  - **A fixed earlier time, e.g. 21:00 IST (15:30 UTC)**: the day isn't over yet, so add `--assume-complete`. The report will be missing the last few hours.
- **Execute Command** node (or an SSH node if the repo lives on a different machine than n8n):
  ```
  cd /full/path/to/LeadGenMonolith && .venv/bin/python tools/daily_report_run.py --send
  ```
- Optional: a node after it that checks the exit code and posts to Slack/email on failure.

## 4. One open point

CONTEXT.md's standing rule has been "no automated mailers or schedulers" — this n8n flow is the first exception, made for this report specifically. Bhanu should update that line in CONTEXT.md once this goes live, so it doesn't read as contradicting itself.

## 5. Link your n8n workflow back

Once it's built, send Bhanu the n8n workflow's share link so it's on record. I don't have your n8n instance's URL, so I can't link it from here.
