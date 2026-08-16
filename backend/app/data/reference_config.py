"""Reference intervals and critical limits a deployment can own.

The tables shipped in `pipeline/ranges.py` and `pipeline/flags.py` are
illustrative adult values and say so. They are a demonstration of the
mechanism, not clinical guidance: critical limits vary between hospitals in the
same city and vary by age in ways an adult-only table cannot express. Until a
deployment could replace them **without editing Python**, the honest ceiling on
this project was fixed where it was.

This loads them from a JSON file named by `REFERENCE_CONFIG_PATH`.

**A file, not a database table.** A lab director signs a document, and a file
in version control is that document: it carries who changed what, when, and
what the previous value was, for free and in a form an assessor can read.
Runtime-editable thresholds would need their own audit, their own permissions
and their own screen, and would still be a worse record than `git log`.

**Invalid configuration is fatal, never ignored.** A deployment that believes
its own limits are live while the shipped illustrative ones are actually
running is worse off than one with no configuration at all — it has the
confidence without the substance. Every failure here refuses to boot and names
the entry that caused it.

**The unit must match the canonical unit exactly.** This is the rule the whole
file exists to enforce. A limit written in a unit the pipeline never emits is
never compared, so the analyte is *silently never assessed* — not flagged, not
errored, simply absent from the check. Two entries in the shipped table were
wrong that way when they were written. Validating at load turns a silent gap
into a boot failure.
"""

import json
import os
from pathlib import Path
from typing import Any

from app.pipeline.units import CANONICAL

ENV_VAR = "REFERENCE_CONFIG_PATH"

# What the shipped tables are, when no file replaces them. Travels with every
# critical flag so a reader can see the basis rather than trust the number.
SHIPPED_BASIS = "illustrative adult critical limits — replace per institution"


class ConfigError(RuntimeError):
    """Bad reference configuration. Always fatal — see the module docstring."""


def _require(cond: bool, message: str) -> None:
    if not cond:
        raise ConfigError(message)


def _check_unit(code: str, unit: str, where: str) -> None:
    """Reject a unit the pipeline never emits. See the module docstring."""
    known = CANONICAL.get(code)
    _require(known is not None,
             f"{where}: LOINC {code} is not in units.CANONICAL. Either the code "
             f"is a typo, or the analyte needs a canonical unit defined first.")
    _require(unit == known[0],
             f"{where}: LOINC {code} is configured in {unit!r} but the pipeline "
             f"emits {known[0]!r}. A limit in a unit that is never produced is "
             f"never compared, and the analyte would be silently unassessed.")


def _check_bounds(code: str, low: Any, high: Any, where: str) -> None:
    _require(low is not None or high is not None,
             f"{where}: LOINC {code} has neither a low nor a high bound.")
    if low is not None and high is not None:
        _require(low < high,
                 f"{where}: LOINC {code} has low {low} >= high {high}.")


def _parse_intervals(raw: dict) -> dict[str, list[tuple]]:
    out: dict[str, list[tuple]] = {}
    for code, spec in raw.items():
        where = "reference_intervals"
        _require(isinstance(spec, dict) and "unit" in spec and "rows" in spec,
                 f"{where}: LOINC {code} needs a 'unit' and a 'rows' list.")
        _check_unit(code, spec["unit"], where)
        rows = []
        for row in spec["rows"]:
            sex = row.get("sex")
            _require(sex in (None, "M", "F"),
                     f"{where}: LOINC {code} has sex {sex!r}; use \"M\", \"F\" or null.")
            lo_age, hi_age = row.get("age_low", 0), row.get("age_high", 120)
            _require(lo_age <= hi_age,
                     f"{where}: LOINC {code} has age_low {lo_age} > age_high {hi_age}.")
            _check_bounds(code, row.get("low"), row.get("high"), where)
            rows.append((sex, lo_age, hi_age, row.get("low"), row.get("high")))
        _require(bool(rows), f"{where}: LOINC {code} has no rows.")
        out[code] = rows
    return out


def _parse_limits(raw: dict) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for code, spec in raw.items():
        where = "critical_limits"
        _require(isinstance(spec, dict) and "unit" in spec,
                 f"{where}: LOINC {code} needs a 'unit'.")
        _check_unit(code, spec["unit"], where)
        _check_bounds(code, spec.get("low"), spec.get("high"), where)
        out[code] = (spec["unit"], spec.get("low"), spec.get("high"))
    return out


def _parse_deltas(raw: dict) -> dict[str, tuple]:
    out: dict[str, tuple] = {}
    for code, spec in raw.items():
        where = "delta_checks"
        _require(code in CANONICAL, f"{where}: LOINC {code} is not in units.CANONICAL.")
        pct, days = spec.get("percent"), spec.get("within_days")
        _require(isinstance(pct, int | float) and pct > 0,
                 f"{where}: LOINC {code} needs a positive 'percent'.")
        _require(isinstance(days, int) and days > 0,
                 f"{where}: LOINC {code} needs a positive 'within_days'.")
        out[code] = (float(pct), days)
    return out


def _load() -> dict[str, Any] | None:
    path = os.environ.get(ENV_VAR)
    if not path:
        return None

    p = Path(path)
    _require(p.is_file(), f"{ENV_VAR} points at {path!r}, which is not a file.")
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from None

    prov = raw.get("provenance")
    _require(isinstance(prov, dict), f"{path}: a 'provenance' object is required.")
    for field in ("approved_by", "approved_on", "population"):
        _require(bool(prov.get(field)),
                 f"{path}: provenance.{field} is required. A limit nobody is "
                 f"recorded as having approved is not a limit anyone can rely on.")

    return {
        "provenance": prov,
        "intervals": _parse_intervals(raw.get("reference_intervals", {})),
        "limits": _parse_limits(raw.get("critical_limits", {})),
        "deltas": _parse_deltas(raw.get("delta_checks", {})),
        "basis": (
            f"{prov['population']} — approved by {prov['approved_by']} "
            f"on {prov['approved_on']}"
        ),
    }


# Loaded once, at import. Boot fails here rather than at the first result.
CONFIG = _load()

IS_CONFIGURED = CONFIG is not None
BASIS: str = CONFIG["basis"] if CONFIG else SHIPPED_BASIS
PROVENANCE: dict | None = CONFIG["provenance"] if CONFIG else None


def intervals(shipped: dict) -> dict:
    """Return configured reference intervals, or the shipped illustrative ones.

    Wholesale replacement rather than a merge. A half-replaced table is the
    worst of both: a deployment reading its own file would have no way to tell
    which analytes it actually governs and which quietly fell through to values
    nobody there approved.
    """
    return CONFIG["intervals"] if CONFIG and CONFIG["intervals"] else shipped


def limits(shipped: dict) -> dict:
    """Return configured critical limits, or the shipped illustrative ones."""
    return CONFIG["limits"] if CONFIG and CONFIG["limits"] else shipped


def deltas(shipped: dict) -> dict:
    """Return configured delta checks, or the shipped illustrative ones."""
    return CONFIG["deltas"] if CONFIG and CONFIG["deltas"] else shipped
