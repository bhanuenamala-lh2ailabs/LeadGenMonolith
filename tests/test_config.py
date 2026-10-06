"""leadgen.config: .env parser, account registry (no secret access), get_secret(), redact().

These tests never read the real .env: they point the loader at a temp file.
"""
import copy
import os

import pytest

from leadgen import config


@pytest.fixture(autouse=True)
def _clean():
    config.reload()
    yield
    config.reload()


def test_parse_env_text_variants():
    text = """
# comment
A=1
export B = two words
C="quoted # not a comment"
D='single $HOME'
E=value # inline comment
F=
G="esc\\"aped\\nnewline"
not a line
1BAD=x
H=a=b=c
"""
    env = config.parse_env_text(text)
    assert env == {"A": "1", "B": "two words", "C": "quoted # not a comment", "D": "single $HOME", "E": "value", "F": "",
                   "G": 'esc"aped\nnewline', "H": "a=b=c"}


def test_registry_matches_architecture_ground_truth():
    accts = config.get_accounts()
    assert sorted(accts) == ["companyops", "main", "rat"]
    assert {s: a.portal_id for s, a in accts.items()} == {"main": 246754894, "companyops": 246897735, "rat": 247485022}
    assert {s: a.env_key for s, a in accts.items()} == {"main": "HUBSPOT_KEY_MAIN", "companyops": "HUBSPOT_KEY_COMPANYOPS", "rat": "HUBSPOT_KEY_RAT"}
    assert {s: a.timezone for s, a in accts.items()} == {"main": "Asia/Calcutta", "companyops": "US/Eastern", "rat": "US/Eastern"}
    ids = {(a.slug, p.pipeline_id): p.funnel_slug for a in accts.values() for p in a.pipelines}
    assert ids == {("main", "default"): "coding", ("main", "2425754306"): "coops_global", ("companyops", "default"): "cluster1",
                   ("companyops", "2464812771"): "cluster2", ("rat", "2575252183"): "rat"}
    assert all(isinstance(p.pipeline_id, str) for a in accts.values() for p in a.all_pipelines)
    assert [p.pipeline_id for p in accts["rat"].ignored_pipelines] == ["default"] and not accts["rat"].ignored_pipelines[0].is_reporting_funnel
    c2 = accts["companyops"].pipeline("2464812771")
    assert (c2.cohort_start, c2.cohort_exclude_migration_on) == ("2026-09-15", "2026-09-15")
    assert accts["main"].pipeline("2425754306").previous_labels == ("Campaign",)
    assert [p.funnel_slug for p in config.reporting_pipelines()] == ["coding", "coops_global", "cluster1", "cluster2", "rat"]
    assert config.account_for_portal(247485022).slug == "rat"
    with pytest.raises(config.ConfigError):
        config.get_account("nope")
    assert config.report_settings()["utc_offset_minutes"] == 330


def test_loading_the_registry_never_touches_secrets(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("registry must not read .env")
    monkeypatch.setattr(config, "_env_values", boom)
    accts = config.get_accounts()
    assert accts["main"].env_key == "HUBSPOT_KEY_MAIN"
    assert "token" not in repr(accts["main"]).lower().replace("hubspot_key", "")     # repr shows names only
    assert config.load_canonical_stages()["stages"]
    assert config.load_config()["accounts"]["accounts"]


def test_get_secret_lazy_and_error_never_leaks(tmp_path, monkeypatch):
    envf = tmp_path / ".env"
    envf.write_text("HUBSPOT_KEY_MAIN=pat-na2-FAKE-TEST-TOKEN-not-real\nEMPTY=\n")
    monkeypatch.delenv("HUBSPOT_KEY_MAIN", raising=False)
    monkeypatch.delenv("NOT_SET_ANYWHERE", raising=False)
    assert config.has_secret("HUBSPOT_KEY_MAIN", str(envf)) is True
    assert config.has_secret("EMPTY", str(envf)) is False
    assert config.env_keys(str(envf)) == ["EMPTY", "HUBSPOT_KEY_MAIN"]
    assert config.get_secret("HUBSPOT_KEY_MAIN", path=str(envf)).startswith("pat-na2-")
    assert config.get_secret("NOT_SET_ANYWHERE", default="fallback", path=str(envf)) == "fallback"
    with pytest.raises(config.MissingSecretError) as ei:
        config.get_secret("NOT_SET_ANYWHERE", path=str(envf))
    assert "NOT_SET_ANYWHERE" in str(ei.value)
    # process environment wins over .env
    monkeypatch.setenv("HUBSPOT_KEY_MAIN", "from-process-environment")
    assert config.get_secret("HUBSPOT_KEY_MAIN", path=str(envf)) == "from-process-environment"
    assert config.secret_status(["HUBSPOT_KEY_MAIN", "NOT_SET_ANYWHERE"]) == {"HUBSPOT_KEY_MAIN": True, "NOT_SET_ANYWHERE": False}


def test_env_cache_follows_file_changes(tmp_path):
    envf = tmp_path / ".env"
    envf.write_text("K1=aaaaaaaaaa\n")
    assert config.get_secret("K1", path=str(envf)) == "aaaaaaaaaa"
    envf.write_text("K1=bbbbbbbbbb\n")
    os.utime(str(envf), (1_900_000_000, 1_900_000_000))
    assert config.get_secret("K1", path=str(envf)) == "bbbbbbbbbb"


def test_redact_masks_known_values_token_shapes_and_sensitive_keys(tmp_path, monkeypatch):
    envf = tmp_path / ".env"
    envf.write_text("APOLLO_API_KEY=apollo-secret-value-123456\nPLAIN_NAME=just-a-label-value\n")
    monkeypatch.setattr(config, "ENV_PATH", str(envf))          # never read the real .env in tests
    monkeypatch.delenv("APOLLO_API_KEY", raising=False)
    secret = config.get_secret("APOLLO_API_KEY", path=str(envf))
    out = config.redact("calling with %s now" % secret)
    assert secret not in out and config.REDACTED in out
    s = ("hubspot pat-na2-FAKE-TEST-TOKEN-not-real google AIza-FAKE-TEST-KEY-not-real "
         "Authorization: Bearer abcdefghijklmnop1234 url https://x.test/api?api_key=SUPERSECRET&q=1 "
         '{"access_token": "ya29.abcdefghijklmnop", "ok": "fine"} sk-ant-api03-abcdefghijklmnop')
    red = config.redact(s)
    for leaked in ("0123abcd-4567", "AIzaSyA1234567890", "abcdefghijklmnop1234", "SUPERSECRET", "ya29.abcdefghijklmnop", "sk-ant-api03"):
        assert leaked not in red, leaked
    assert '"ok": "fine"' in red and "q=1" in red
    data = {"env_key": "HUBSPOT_KEY_MAIN", "api_key": "abc", "nested": {"client_secret": "x", "name": "n"}, "list": [{"token": "t"}, "plain"], "n": 3, "none": None}
    r = config.redact(data)
    assert r["env_key"] == "HUBSPOT_KEY_MAIN"            # a variable NAME is not a secret
    assert r["api_key"] == config.REDACTED and r["nested"] == {"client_secret": config.REDACTED, "name": "n"}
    assert r["list"] == [{"token": config.REDACTED}, "plain"] and r["n"] == 3 and r["none"] is None
    assert data["api_key"] == "abc"                       # input untouched
    assert config.redact(("a", secret)) == ("a", config.REDACTED)
    assert config.redact("x", extra=["x-extra-secret"]) == "x"
    assert config.redact("my x-extra-secret", extra=["x-extra-secret"]) == "my " + config.REDACTED


def _good():
    return copy.deepcopy(config.load_yaml("accounts"))


def test_account_validation_rejects_bad_config():
    d = _good()
    d["accounts"][0]["env_key"] = "pat-na2-0123abcd-4567-89ef"          # a token value where a NAME belongs
    with pytest.raises(config.ConfigError):
        config.parse_accounts(d)
    d = _good()
    d["accounts"][0]["pipelines"][1]["id"] = 2425754306                   # unquoted YAML int
    with pytest.raises(config.ConfigError):
        config.parse_accounts(d)
    d = _good()
    d["accounts"][1]["slug"] = "main"
    with pytest.raises(config.ConfigError):
        config.parse_accounts(d)
    d = _good()
    d["accounts"][1]["portal_id"] = d["accounts"][0]["portal_id"]
    with pytest.raises(config.ConfigError):
        config.parse_accounts(d)
    d = _good()
    d["accounts"][1]["pipelines"][1]["cohort_start"] = "15/09/2026"
    with pytest.raises(config.ConfigError):
        config.parse_accounts(d)
    d = _good()
    d["accounts"][2]["pipelines"][0]["slug"] = "coding"                   # funnel slugs are global
    with pytest.raises(config.ConfigError):
        config.parse_accounts(d)
    with pytest.raises(config.ConfigError):
        config.parse_accounts({})


def test_canonical_stage_loader_validates(tmp_path):
    good = config.load_canonical_stages()
    assert [s["code"] for s in good["stages"]][0] == "SOURCED" and len(good["dead_reasons"]) == 10
    import yaml
    bad = copy.deepcopy(good)
    bad["stages"][3]["depth"] = 0.0
    (tmp_path / "canonical_stages.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(config.ConfigError):
        config.load_canonical_stages(str(tmp_path))
    bad = copy.deepcopy(good)
    bad["stages"][3]["is_dead"] = True
    (tmp_path / "canonical_stages.yaml").write_text(yaml.safe_dump(bad))
    with pytest.raises(config.ConfigError):
        config.load_canonical_stages(str(tmp_path))
