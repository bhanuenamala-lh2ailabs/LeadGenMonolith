"""Suppression gate: the ONE sanctioned way to ask "may I push / contact / enrich this?".

The ``suppression`` table matches by an exact compare against ``match_value_norm``.  A probe that is not normalised exactly like the
stored value never matches, which is a silent bypass of the 'never push' (whale) rule.  Therefore:

* every probe value goes through :mod:`leadgen.norm` (the same normalisers that produce the stored values);
* an unnormalisable value does NOT count as "not suppressed": :func:`check` raises :class:`UnnormalisableProbe` so the caller must
  fix or skip the record (fail closed);
* every outbound path calls :func:`assert_pushable` before any write to HubSpot / any send.  Reads of ``v_suppression_active`` that
  skip this module are a bug.

Python 3.9 compatible.
"""
import sqlite3
from typing import Any, Dict, List, Optional

from leadgen import norm

#: probe keyword -> (suppression.match_type, normaliser)
PROBES = {
    "domain": ("domain", norm.norm_domain),
    "email": ("email", norm.norm_email),
    "phone": ("phone", norm.norm_phone_e164),
    "linkedin_company": ("linkedin_company", norm.norm_linkedin_company),
    "linkedin_person": ("linkedin_person", norm.norm_linkedin_person),
    "cin": ("cin", norm.norm_cin),
    "company_name": ("company_name", norm.norm_company_name),
}


class SuppressedError(RuntimeError):
    """The record is on an active suppression list; ``matches`` holds the rows."""

    def __init__(self, message: str, matches: List[Dict[str, Any]]):
        super().__init__(message)
        self.matches = matches


class UnnormalisableProbe(ValueError):
    """A probe value could not be normalised, so it cannot be checked - the caller must not treat it as clean."""


def check(con: sqlite3.Connection, account_id: Optional[str] = None, company_id: Optional[int] = None, **probes: Any) -> List[Dict[str, Any]]:
    """Active suppression rows matching ANY of the given raw probes (``domain=``, ``email=``, ``phone=``, ``linkedin_company=``,
    ``linkedin_person=``, ``cin=``, ``company_name=``) or ``company_id``.  Global rows (account_id NULL) always apply; account-specific
    rows only for ``account_id``.  Probes whose value is None / '' are skipped; a non-empty value that cannot be normalised raises."""
    unknown = set(probes) - set(PROBES)
    if unknown:
        raise TypeError("unknown probe(s): %s" % sorted(unknown))
    out = []  # type: List[Dict[str, Any]]
    seen = set()
    for key, raw in probes.items():
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            continue
        match_type, fn = PROBES[key]
        value = fn(raw)
        if value is None:
            raise UnnormalisableProbe("cannot normalise %s probe %r: not checked" % (key, raw))
        rows = con.execute(
            "SELECT * FROM v_suppression_active WHERE match_type = ? AND match_value_norm = ? AND (account_id IS NULL OR account_id IS ?)",
            (match_type, value, account_id)).fetchall()
        for r in rows:
            if r["suppression_id"] not in seen:
                seen.add(r["suppression_id"])
                out.append(dict(r))
    if company_id is not None:
        for r in con.execute("SELECT * FROM v_suppression_active WHERE (company_id = ? OR (match_type = 'company_id' AND match_value_norm = ?)) "
                             "AND (account_id IS NULL OR account_id IS ?)", (company_id, str(company_id), account_id)).fetchall():
            if r["suppression_id"] not in seen:
                seen.add(r["suppression_id"])
                out.append(dict(r))
    return out


def assert_pushable(con: sqlite3.Connection, account_id: Optional[str] = None, company_id: Optional[int] = None, **probes: Any) -> None:
    """Raise :class:`SuppressedError` if the record matches any active suppression (every kind blocks a push: never_push, dnc, competitor,
    unsubscribed, duplicate, off_icp, too_big, delivered, sheet_worked, bad_contact, other)."""
    hits = check(con, account_id=account_id, company_id=company_id, **probes)
    if hits:
        kinds = sorted({h["kind"] for h in hits})
        raise SuppressedError("suppressed (%s): %s" % (", ".join(kinds), [(h["match_type"], h["match_value_norm"]) for h in hits][:5]), hits)
