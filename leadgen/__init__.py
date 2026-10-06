"""LeadGenMonolith: one repo, one SQLite database, one daily unified funnel report.

Modules
    leadgen.db      connect(), migrate(), status(), doctor(), seed_reference_data()
    leadgen.config  .env + config/*.yaml loader, account registry, get_secret(), redact()
    leadgen.norm    normalisers that produce exactly the shapes the database CHECKs demand (domains, LinkedIn, CIN, E.164, UTC ms timestamps, ...)
    leadgen.suppression  the one sanctioned "may I push this?" gate over the suppression table (fail-closed)

Python 3.9 compatible (no ``X | Y`` unions, no tomllib).  See docs/ARCHITECTURE.md.
"""

__version__ = "0.1.0"
