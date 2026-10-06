"""Configuration: .env loader, config/*.yaml loader, account registry, secret access and redaction.

Design rules
    * The ACCOUNT REGISTRY (slug, portal id, env-var NAME, timezone, pipelines) comes from config/accounts.yaml and
      never touches a secret value.  Loading the registry does not even read ``.env``.
    * A secret is only read when someone calls :func:`get_secret` (or ``AccountCfg.token()``).  The value is returned to
      the caller and remembered, in memory only, so :func:`redact` can mask it later.  Errors name the variable, never the value.
    * ``.env`` is parsed by a tiny parser (no python-dotenv).  Process environment variables win over ``.env``.
    * Nothing in this module prints, logs or writes a secret.

Python 3.9 compatible.
"""
import copy
import os
import re
import threading
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)
CONFIG_DIR = os.path.join(PROJECT_ROOT, "config")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")

REDACTED = "***REDACTED***"

_SLUG_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_ENV_LINE_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")


class ConfigError(Exception):
    """Malformed or inconsistent configuration."""


class MissingSecretError(ConfigError):
    """A required secret is not set.  The message names the variable only."""


# ------------------------------------------------------------------------------------------------------------- .env
def parse_env_text(text: str) -> Dict[str, str]:
    """Parse KEY=VALUE lines.  Supports ``export KEY=...``, ``#`` comments, blank lines, single/double quotes
    (double quotes honour \\n \\r \\t \\\\ \\"), inline `` # comment`` on unquoted values.  No variable expansion."""
    out = {}  # type: Dict[str, str]
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if not m:
            continue
        key, rest = m.group(1), m.group(2).strip()
        out[key] = _parse_env_value(rest)
    return out


def _parse_env_value(rest: str) -> str:
    if not rest:
        return ""
    q = rest[0]
    if q in ("'", '"'):
        i, buf = 1, []
        while i < len(rest):
            c = rest[i]
            if q == '"' and c == "\\" and i + 1 < len(rest):
                nxt = rest[i + 1]
                buf.append({"n": "\n", "r": "\r", "t": "\t"}.get(nxt, nxt))
                i += 2
                continue
            if c == q:
                return "".join(buf)
            buf.append(c)
            i += 1
        return "".join(buf)  # unterminated quote: take the rest literally
    m = re.search(r"\s#", rest)
    return (rest[: m.start()] if m else rest).strip()


_env_lock = threading.Lock()
_env_cache = {"path": None, "mtime": None, "values": None}  # type: Dict[str, Any]
_known_secret_values = set()  # type: set


def _env_values(path: Optional[str] = None) -> Dict[str, str]:
    """Parsed .env (cached by path + mtime).  PRIVATE: callers must go through get_secret / has_secret / redact."""
    path = os.path.abspath(path or ENV_PATH)
    with _env_lock:
        try:
            mtime = os.stat(path).st_mtime
        except OSError:
            _env_cache.update(path=path, mtime=None, values={})
            return {}
        if _env_cache["path"] == path and _env_cache["mtime"] == mtime and _env_cache["values"] is not None:
            return _env_cache["values"]
        with open(path, "r", encoding="utf-8") as fh:
            values = parse_env_text(fh.read())
        _env_cache.update(path=path, mtime=mtime, values=values)
        return values


def env_keys(path: Optional[str] = None) -> List[str]:
    """Variable NAMES defined in .env (values are discarded immediately)."""
    return sorted(_env_values(path).keys())


def _lookup(name: str, path: Optional[str] = None) -> Optional[str]:
    v = os.environ.get(name)
    if v:
        return v
    v = _env_values(path).get(name)
    return v if v else None


def has_secret(name: str, path: Optional[str] = None) -> bool:
    """True if ``name`` is set (non-empty) in the environment or .env.  Never returns the value."""
    return _lookup(name, path) is not None


def get_secret(name: str, default: Optional[str] = None, path: Optional[str] = None) -> str:
    """Return the secret value of ``name`` (process environment first, then .env).  Raises MissingSecretError (naming
    the variable only) if unset and no default is given.  The value is remembered in memory so redact() can mask it."""
    v = _lookup(name, path)
    if v is None:
        if default is not None:
            return default
        raise MissingSecretError("secret %s is not set in the environment or .env" % name)
    if len(v) >= 8:
        _known_secret_values.add(v)
    return v


def secret_status(names: Iterable[str]) -> Dict[str, bool]:
    """{name: is_set} - safe to print."""
    return {n: has_secret(n) for n in names}


# ------------------------------------------------------------------------------------------------------- redaction
_SENSITIVE_KEY_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|apikey|authorization|private[_-]?key|credential|bearer|^key$|(?<!env)_key$|^pat$|_pat$)", re.I
)
_PATTERNS = [
    re.compile(r"pat-[a-z0-9]+-[A-Za-z0-9-]{8,}"),  # HubSpot private-app token, any spelling, so a leaked token is still redacted                      # HubSpot private-app token
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}"),                           # Anthropic
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                              # Google API key
    re.compile(r"ya29\.[0-9A-Za-z_\-]{10,}"),                            # Google OAuth access token
    re.compile(r"1//[0-9A-Za-z_\-]{20,}"),                               # Google OAuth refresh token
    re.compile(r"apify_api_[A-Za-z0-9]{10,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),                           # GitHub tokens
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=\-]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
]
_KV_PATTERNS = [
    # "api_key": "value"   /  'token': 'value'
    (re.compile(r"""(?i)(["']?(?:access_token|refresh_token|id_token|client_secret|private_key|api[_-]?key|apikey|token|secret|password|authorization)["']?\s*[:=]\s*)(["'])(.*?)\2"""),
     lambda m: m.group(1) + m.group(2) + REDACTED + m.group(2)),
    # ?api_key=value&...
    (re.compile(r"(?i)([?&](?:api[_-]?key|apikey|access_token|token|key|secret)=)[^&\s]+"), lambda m: m.group(1) + REDACTED),
]


def _env_secret_values() -> List[str]:
    """All values from .env whose variable name looks sensitive (length >= 8).  Loaded only for masking."""
    vals = []
    for k, v in _env_values().items():
        if len(v) >= 8 and _SENSITIVE_KEY_RE.search(k):
            vals.append(v)
    return vals


def redact(obj: Any, extra: Iterable[str] = ()) -> Any:
    """Return a copy of ``obj`` (str / dict / list / tuple / scalar) with secrets masked: values of sensitive-looking dict keys,
    every secret read through get_secret(), every sensitive value in .env, and well-known token shapes."""
    secrets = sorted({s for s in list(_known_secret_values) + _env_secret_values() + [e for e in extra if e and len(e) >= 4]},
                     key=len, reverse=True)
    return _redact(obj, secrets)


def _redact(obj: Any, secrets: List[str]) -> Any:
    if isinstance(obj, str):
        s = obj
        for sec in secrets:
            if sec in s:
                s = s.replace(sec, REDACTED)
        for pat in _PATTERNS:
            s = pat.sub(REDACTED, s)
        for pat, fn in _KV_PATTERNS:
            s = pat.sub(fn, s)
        return s
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEY_RE.search(k) and v not in (None, "", False):
                out[k] = REDACTED
            else:
                out[k] = _redact(v, secrets)
        return out
    if isinstance(obj, list):
        return [_redact(v, secrets) for v in obj]
    if isinstance(obj, tuple):
        return tuple(_redact(v, secrets) for v in obj)
    return obj


# --------------------------------------------------------------------------------------------------------- YAML
def load_yaml(name: str, config_dir: Optional[str] = None) -> Any:
    """Load config/<name>.yaml (name without extension).  Raises ConfigError if missing or not valid YAML."""
    path = os.path.join(config_dir or CONFIG_DIR, name + ".yaml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise ConfigError("cannot read %s: %s" % (path, exc))
    except yaml.YAMLError as exc:
        raise ConfigError("invalid YAML in %s: %s" % (path, exc))
    if data is None:
        data = {}
    return data


def load_config(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """Every config/*.yaml, keyed by file stem."""
    d = config_dir or CONFIG_DIR
    out = {}
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".yaml"):
            out[fn[:-5]] = load_yaml(fn[:-5], d)
    return out


# ----------------------------------------------------------------------------------------------- account registry
@dataclass(frozen=True)
class PipelineCfg:
    account_slug: str
    pipeline_id: str                   # 'default' or numeric text - ALWAYS a string
    label: str
    funnel_slug: Optional[str]
    display_order: Optional[int]
    is_reporting_funnel: bool
    previous_labels: Tuple[str, ...] = ()
    cohort_start: Optional[str] = None
    cohort_exclude_migration_on: Optional[str] = None


@dataclass(frozen=True)
class AccountCfg:
    slug: str
    portal_id: int
    name: str
    env_key: str                       # NAME of the env var holding the token
    timezone: str                      # HubSpot portal tz (informational; reports use IST)
    ui_domain: Optional[str]
    currency: str
    pipelines: Tuple[PipelineCfg, ...]         # reporting funnels
    ignored_pipelines: Tuple[PipelineCfg, ...]  # exist in the portal, ignored by every report

    @property
    def all_pipelines(self) -> Tuple[PipelineCfg, ...]:
        return self.pipelines + self.ignored_pipelines

    def pipeline(self, pipeline_id: str) -> PipelineCfg:
        for p in self.all_pipelines:
            if p.pipeline_id == pipeline_id:
                return p
        raise KeyError("account %s has no pipeline %r" % (self.slug, pipeline_id))

    def has_token(self) -> bool:
        """Whether the token variable is set; never reads or returns the value."""
        return has_secret(self.env_key)

    def token(self) -> str:
        """The HubSpot private-app token.  This is the ONLY place a portal secret is read."""
        return get_secret(self.env_key)


def _parse_pipeline(slug: str, raw: Dict[str, Any], reporting: bool) -> PipelineCfg:
    if not isinstance(raw, dict) or "id" not in raw or "label" not in raw:
        raise ConfigError("account %s: pipeline entries need `id` and `label`: %r" % (slug, raw))
    if not isinstance(raw["id"], str) or not raw["id"].strip():
        raise ConfigError("account %s: pipeline id %r must be a quoted string" % (slug, raw["id"]))
    fs = raw.get("slug")
    if reporting and not (isinstance(fs, str) and _SLUG_RE.match(fs)):
        raise ConfigError("account %s pipeline %s: invalid funnel slug %r" % (slug, raw["id"], fs))
    for k in ("cohort_start", "cohort_exclude_migration_on"):
        v = raw.get(k)
        if v is not None and not (isinstance(v, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", v)):
            raise ConfigError("account %s pipeline %s: %s must be a quoted YYYY-MM-DD string" % (slug, raw["id"], k))
    return PipelineCfg(
        account_slug=slug,
        pipeline_id=raw["id"],
        label=str(raw["label"]),
        funnel_slug=fs if reporting else None,
        display_order=raw.get("order"),
        is_reporting_funnel=reporting,
        previous_labels=tuple(str(x) for x in raw.get("previous_labels", []) or []),
        cohort_start=raw.get("cohort_start"),
        cohort_exclude_migration_on=raw.get("cohort_exclude_migration_on"),
    )


def parse_accounts(data: Dict[str, Any]) -> Dict[str, AccountCfg]:
    """Validate and build the registry from the parsed accounts.yaml (does not touch any secret)."""
    if not isinstance(data, dict) or not data.get("accounts"):
        raise ConfigError("accounts.yaml: missing `accounts` list")
    out = {}  # type: Dict[str, AccountCfg]
    portals, env_keys_seen, funnel_slugs = set(), set(), set()
    for raw in data["accounts"]:
        slug = raw.get("slug")
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            raise ConfigError("invalid account slug %r" % (slug,))
        if slug in out:
            raise ConfigError("duplicate account slug %r" % slug)
        pid = raw.get("portal_id")
        if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
            raise ConfigError("account %s: portal_id must be a positive integer" % slug)
        if pid in portals:
            raise ConfigError("duplicate portal_id %s" % pid)
        env_key = raw.get("env_key")
        if not isinstance(env_key, str) or not _ENV_NAME_RE.match(env_key):
            raise ConfigError("account %s: env_key must be an UPPER_CASE variable NAME (never a token value)" % slug)
        if env_key in env_keys_seen:
            raise ConfigError("account %s: env_key %s already used" % (slug, env_key))
        portals.add(pid)
        env_keys_seen.add(env_key)
        pipes = tuple(_parse_pipeline(slug, p, True) for p in raw.get("pipelines", []) or [])
        ign = tuple(_parse_pipeline(slug, p, False) for p in raw.get("ignored_pipelines", []) or [])
        ids = [p.pipeline_id for p in pipes + ign]
        if len(ids) != len(set(ids)):
            raise ConfigError("account %s: duplicate pipeline ids %s" % (slug, ids))
        for p in pipes:
            if p.funnel_slug in funnel_slugs:
                raise ConfigError("duplicate funnel slug %r" % p.funnel_slug)
            funnel_slugs.add(p.funnel_slug)
        out[slug] = AccountCfg(
            slug=slug, portal_id=pid, name=str(raw.get("name", slug)), env_key=env_key,
            timezone=str(raw.get("timezone", "UTC")), ui_domain=raw.get("ui_domain"),
            currency=str(raw.get("currency", "USD")), pipelines=pipes, ignored_pipelines=ign,
        )
    return out


_registry_cache = {}  # type: Dict[str, Dict[str, AccountCfg]]


def get_accounts(config_dir: Optional[str] = None) -> Dict[str, AccountCfg]:
    """The account registry {slug: AccountCfg}.  Cached; reads config/accounts.yaml only (no secrets)."""
    key = os.path.abspath(config_dir or CONFIG_DIR)
    if key not in _registry_cache:
        _registry_cache[key] = parse_accounts(load_yaml("accounts", key))
    return _registry_cache[key]


def get_account(slug: str, config_dir: Optional[str] = None) -> AccountCfg:
    try:
        return get_accounts(config_dir)[slug]
    except KeyError:
        raise ConfigError("unknown account %r (known: %s)" % (slug, ", ".join(sorted(get_accounts(config_dir)))))


def account_for_portal(portal_id: int, config_dir: Optional[str] = None) -> AccountCfg:
    for a in get_accounts(config_dir).values():
        if a.portal_id == portal_id:
            return a
    raise ConfigError("no account for portal id %s" % portal_id)


def reporting_pipelines(config_dir: Optional[str] = None) -> List[PipelineCfg]:
    """The five reporting funnels, in account then display order."""
    out = []
    for a in get_accounts(config_dir).values():
        out.extend(sorted(a.pipelines, key=lambda p: (p.display_order is None, p.display_order)))
    return out


def report_settings(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """The `report:` block of accounts.yaml (IST timezone, offset, day-close time)."""
    return copy.deepcopy(load_yaml("accounts", config_dir).get("report", {}))


def load_canonical_stages(config_dir: Optional[str] = None) -> Dict[str, Any]:
    """config/canonical_stages.yaml: {'stages': [...], 'dead_reasons': [...]}, validated."""
    data = load_yaml("canonical_stages", config_dir)
    stages, reasons = data.get("stages"), data.get("dead_reasons")
    if not stages or not reasons:
        raise ConfigError("canonical_stages.yaml needs `stages` and `dead_reasons`")
    codes = [s["code"] for s in stages]
    if len(codes) != len(set(codes)):
        raise ConfigError("duplicate canonical stage codes")
    depths = [s["depth"] for s in stages]
    if depths != sorted(depths) or len(set(depths)) != len(depths):
        raise ConfigError("canonical stage depths must be strictly increasing in file order")
    for s in stages:
        if s["is_live"] + s["is_dead"] + s["is_won"] != 1:
            raise ConfigError("stage %s must be exactly one of live / dead / won" % s["code"])
        if not isinstance(s["rank"], str):
            raise ConfigError("stage %s: rank must be a quoted string" % s["code"])
    return data


def reload() -> None:
    """Drop every cache (registry, parsed .env, remembered secrets).  Mainly for tests."""
    _registry_cache.clear()
    with _env_lock:
        _env_cache.update(path=None, mtime=None, values=None)
    _known_secret_values.clear()
