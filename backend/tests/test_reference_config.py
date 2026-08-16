"""Reference intervals a deployment owns, and the ways it must not go wrong.

No database and no fixtures: this is file parsing and validation, and it should
stay fast enough to run on every save.

The module reads its file at import, which is the right time — boot should fail,
not the first result — but makes it awkward to test. Each case therefore writes
a file and calls the private loader directly rather than re-importing.
"""

import json
import re
from pathlib import Path

import pytest

from app.data import reference_config as rc


def write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "ref.json"
    p.write_text(json.dumps(payload))
    return p


def load(tmp_path, payload, monkeypatch):
    monkeypatch.setenv(rc.ENV_VAR, str(write(tmp_path, payload)))
    return rc._load()


PROV = {
    "approved_by": "Dr A. Example",
    "approved_on": "2026-03-01",
    "population": "Adults 18+",
}


def test_no_path_means_the_shipped_tables(monkeypatch):
    monkeypatch.delenv(rc.ENV_VAR, raising=False)
    assert rc._load() is None


def test_a_configured_interval_replaces_the_shipped_one(tmp_path, monkeypatch):
    cfg = load(tmp_path, {
        "provenance": PROV,
        "reference_intervals": {
            "2160-0": {"unit": "mg/dL", "rows": [
                {"sex": "M", "age_low": 18, "age_high": 120, "low": 0.8, "high": 1.2},
            ]},
        },
    }, monkeypatch)
    assert cfg["intervals"]["2160-0"] == [("M", 18, 120, 0.8, 1.2)]


def test_the_basis_names_who_approved_it(tmp_path, monkeypatch):
    """A flag carries its threshold *and* where the threshold came from. With a
    file in play that has to be the institution, not our illustrative note."""
    cfg = load(tmp_path, {"provenance": PROV, "critical_limits": {}}, monkeypatch)
    assert "Dr A. Example" in cfg["basis"]
    assert "2026-03-01" in cfg["basis"]
    assert "illustrative" not in cfg["basis"]


# --- the rule the module exists to enforce ----------------------------------

def test_a_limit_in_the_wrong_unit_refuses_to_boot(tmp_path, monkeypatch):
    """The failure worth all of this.

    Potassium is emitted in mmol/L. A limit written in mEq/L is never compared,
    so the analyte is not flagged, not errored — simply never assessed. Silence
    is the worst possible outcome for a critical limit, so it is a boot failure.
    """
    with pytest.raises(rc.ConfigError, match="never compared"):
        load(tmp_path, {
            "provenance": PROV,
            "critical_limits": {"2823-3": {"unit": "mEq/L", "low": 2.5, "high": 6.5}},
        }, monkeypatch)


def test_an_unknown_loinc_refuses_to_boot(tmp_path, monkeypatch):
    with pytest.raises(rc.ConfigError, match=re.escape("units.CANONICAL")):
        load(tmp_path, {
            "provenance": PROV,
            "critical_limits": {"9999-9": {"unit": "mg/dL", "low": 1.0, "high": 2.0}},
        }, monkeypatch)


def test_inverted_bounds_refuse_to_boot(tmp_path, monkeypatch):
    with pytest.raises(rc.ConfigError, match=re.escape(">=")):
        load(tmp_path, {
            "provenance": PROV,
            "critical_limits": {"2823-3": {"unit": "mmol/L", "low": 6.5, "high": 2.5}},
        }, monkeypatch)


def test_a_limit_with_no_bounds_at_all_refuses_to_boot(tmp_path, monkeypatch):
    with pytest.raises(rc.ConfigError, match="neither a low nor a high"):
        load(tmp_path, {
            "provenance": PROV,
            "critical_limits": {"2823-3": {"unit": "mmol/L"}},
        }, monkeypatch)


def test_a_one_sided_limit_is_allowed(tmp_path, monkeypatch):
    """"No limit published on that side" is a real statement, and different
    from "any value is acceptable"."""
    cfg = load(tmp_path, {
        "provenance": PROV,
        "critical_limits": {"2777-1": {"unit": "mg/dL", "low": 1.0}},
    }, monkeypatch)
    assert cfg["limits"]["2777-1"] == ("mg/dL", 1.0, None)


# --- provenance is not optional ---------------------------------------------

@pytest.mark.parametrize("missing", ["approved_by", "approved_on", "population"])
def test_provenance_is_required(tmp_path, monkeypatch, missing):
    """A limit nobody is recorded as having approved is not a limit anyone can
    rely on — and the UI shows this string next to a clinical flag."""
    prov = {k: v for k, v in PROV.items() if k != missing}
    with pytest.raises(rc.ConfigError, match=missing):
        load(tmp_path, {"provenance": prov, "critical_limits": {}}, monkeypatch)


def test_a_missing_file_refuses_to_boot(tmp_path, monkeypatch):
    """Silently falling back would leave a deployment believing its own limits
    are live while the illustrative ones actually run — the confidence without
    the substance."""
    monkeypatch.setenv(rc.ENV_VAR, str(tmp_path / "nope.json"))
    with pytest.raises(rc.ConfigError, match="not a file"):
        rc._load()


def test_malformed_json_refuses_to_boot(tmp_path, monkeypatch):
    p = tmp_path / "ref.json"
    p.write_text("{ not json")
    monkeypatch.setenv(rc.ENV_VAR, str(p))
    with pytest.raises(rc.ConfigError, match="not valid JSON"):
        rc._load()


# --- the shipped example must itself be valid -------------------------------

def test_the_example_config_loads(monkeypatch):
    """A sample that does not parse is worse than no sample: it is the first
    thing anyone copies."""
    example = Path(__file__).parent.parent / "reference-config.example.json"
    assert example.is_file()
    monkeypatch.setenv(rc.ENV_VAR, str(example))

    cfg = rc._load()
    assert cfg["intervals"] and cfg["limits"] and cfg["deltas"]
    assert "Dr A. Example" in cfg["basis"]
