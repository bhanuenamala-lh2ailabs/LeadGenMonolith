"""The unified daily funnel report (docs/ARCHITECTURE.md section 4, docs/REPORTS.md).

* ``metrics``  - every number, computed from ``deal_stage_event`` / ``deal`` (never from labels or legacy snapshots); one function per
  entry of the metric dictionary in ``config/report.yaml``.
* ``build``    - assembles the report model for an IST day and persists the facts to ``rpt_*``.
* ``render``   - HTML / Markdown / CSV / JSON from the model (pure functions, no I/O).
* ``daily``    - the CLI: ``python -m leadgen.reports.daily [--date D] [--days N] [--send] [--to ADDR] [--no-persist]``.
* ``mailer``   - the Gmail API transport (dry-run unless ``--send``).
"""
REPORT_TOOL_VERSION = "1.0.0"
