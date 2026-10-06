"""Unified entry point:  .venv/bin/python -m leadgen <command> [args...]

Thin dispatcher: every command forwards the remaining argv to the module's own main(), so
`--help` on any command shows that module's real options.

    migrate | status | doctor | analyze | seed        database admin        (leadgen.db)
    sync hubspot [--account X|all] [--full]           read-only pull + canonicalize
    import-legacy tam|repo-dbs|flatfiles [...]        legacy SQLite / CSV / xlsx imports
    pull gsheets [...]                                Google Sheets (read-only)
    pull gmail [...]                                  Gmail inbox (read-only; needs tools/gmail_auth.py first)
    report daily [--date D] [--days N] [--send]       the unified daily funnel report (dry-run unless --send)
    dedup <leads.csv> [--no-live]                     check a lead file against ALL THREE portals before any push
"""
import importlib
import sys
from typing import List, Optional

DB_COMMANDS = ("migrate", "status", "doctor", "analyze", "seed")

HELP = __doc__


def _call(module: str, argv: List[str]) -> int:
    import inspect
    mod = importlib.import_module(module)
    if inspect.signature(mod.main).parameters:
        rc = mod.main(argv)
    else:                                   # modules whose main() reads sys.argv itself
        old = sys.argv
        sys.argv = [module] + list(argv)
        try:
            rc = mod.main()
        finally:
            sys.argv = old
    return int(rc or 0)


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(HELP)
        return 0
    cmd, rest = argv[0], argv[1:]

    if cmd in DB_COMMANDS:
        return _call("leadgen.db", [cmd] + rest)

    if cmd == "sync":
        if not rest or rest[0] != "hubspot":
            print("usage: python -m leadgen sync hubspot [--account main|companyops|rat|all] [--full] [--verify]")
            return 2
        args = rest[1:]
        rc = _call("leadgen.hubspot.sync", args if args else ["--account", "all"])
        if rc != 0:
            return rc
        canon = ["--all"]
        if "--account" in args:
            acct = args[args.index("--account") + 1]
            if acct != "all":
                canon = ["--account", acct]
        return _call("leadgen.hubspot.canonicalize", canon)

    if cmd == "import-legacy":
        targets = {"tam": "leadgen.legacy_import.tam",
                   "repo-dbs": "leadgen.legacy_import.hubspot_repo_dbs",
                   "flatfiles": "leadgen.legacy_import.flatfiles"}
        if not rest or rest[0] not in targets:
            print("usage: python -m leadgen import-legacy {tam|repo-dbs|flatfiles} [options]")
            return 2
        return _call(targets[rest[0]], rest[1:])

    if cmd == "pull":
        targets = {"gsheets": "leadgen.google.sheets", "gmail": "leadgen.google.gmail"}
        if not rest or rest[0] not in targets:
            print("usage: python -m leadgen pull {gsheets|gmail} [options]")
            return 2
        return _call(targets[rest[0]], rest[1:])

    if cmd == "dedup":
        return _call("leadgen.dedup", rest)

    if cmd == "report":
        if not rest or rest[0] != "daily":
            print("usage: python -m leadgen report daily [--date YYYY-MM-DD] [--days N] [--send] [--to ADDR]")
            return 2
        return _call("leadgen.reports.daily", rest[1:])

    print("unknown command: %s\n" % cmd)
    print(HELP)
    return 2


if __name__ == "__main__":
    sys.exit(main())
