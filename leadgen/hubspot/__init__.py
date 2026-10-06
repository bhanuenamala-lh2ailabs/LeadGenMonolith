"""Read-only HubSpot sync: client (guarded REST), sync (API -> hsraw_*), canonicalize (hsraw_* -> canonical tables).

CLI:  python -m leadgen.hubspot.sync --account main|companyops|rat [--full] [--object deals]
      python -m leadgen.hubspot.canonicalize [--account X]
"""
