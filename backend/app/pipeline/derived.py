"""Numbers a laboratory reports that were never measured.

An eGFR is not on the analyser. It is a creatinine, an age and a sex put
through a published equation, and on most reports it is the number the
clinician actually reads. The same is true of an anion gap, of a calcium
corrected for albumin, and of the LDL on the majority of lipid panels.

This module computes them, and it is only possible because the rest of the
pipeline already did the hard part. Deriving anything requires knowing that
two rows on two different reports are the same analyte, and that both are in a
unit you can do arithmetic with — which is precisely what the LOINC cascade and
the conversion table exist to establish. A tracker that stores "CREAT 1.03" as
text cannot do this at all.

---

**Deterministic or absent**, the same rule the conversion table follows, and it
bites harder here because every derivation has several inputs. A value is
computed only when *every* input is present, in the exact canonical unit the
formula expects, and drawn on the same day. A missing albumin does not mean
"assume 4.0"; it means there is no corrected calcium, and the interface says
so rather than showing a number.

**Same draw, not same patient.** An anion gap from a sodium in January and a
chloride in June is arithmetic, not a measurement. Inputs must share a
collection date.

**Nothing is stored.** These are computed on read, from rows that may later be
re-coded or corrected — a stored derivation would quietly outlive the
correction that invalidated it.

**Every result carries its formula and its inputs**, for the same reason every
mapped value carries its provenance: a number the reader cannot check is one
they have to take on trust, and this module is doing arithmetic on their
clinical data.
"""

from dataclasses import dataclass, field
from datetime import date, datetime

# --- the inputs, by LOINC and the unit each formula is written in -------------

CREATININE = ("2160-0", "mg/dL")
SODIUM = ("2951-2", "mmol/L")
CHLORIDE = ("2075-0", "mmol/L")
BICARBONATE = ("2028-9", "mmol/L")
CALCIUM = ("17861-6", "mg/dL")
ALBUMIN = ("1751-7", "g/dL")
CHOLESTEROL = ("2093-3", "mg/dL")
HDL = ("2085-9", "mg/dL")
IRON = ("2498-4", "ug/dL")
TIBC = ("2500-7", "ug/dL")


@dataclass(frozen=True)
class Input:
    """One measured value that went into a derivation."""

    display: str
    value: float
    unit: str


@dataclass(frozen=True)
class Derivation:
    """A computed value, with everything needed to check it."""

    code: str | None          # LOINC, where the calculated form has one
    display: str
    value: float
    unit: str
    collected_at: datetime
    formula: str
    inputs: list[Input] = field(default_factory=list)
    ref_low: float | None = None
    ref_high: float | None = None
    note: str | None = None

    @property
    def flag(self) -> str:
        """Low / normal / high against this derivation's own interval."""
        if self.ref_low is not None and self.value < self.ref_low:
            return "low"
        if self.ref_high is not None and self.value > self.ref_high:
            return "high"
        return "normal" if (self.ref_low, self.ref_high) != (None, None) else "unknown"


def _get(draw: dict, spec: tuple[str, str]) -> float | None:
    """Return a value from this draw, but only in the unit the formula expects.

    The unit check is not defensive programming, it is the whole safety
    argument: a creatinine in µmol/L fed to an equation expecting mg/dL returns
    an eGFR that is wrong by a factor of eighty-eight and looks entirely
    plausible on a chart.
    """
    code, unit = spec
    got = draw.get(code)
    return got[0] if got and got[1] == unit else None


def _years(dob: date | None, at: datetime) -> float | None:
    if dob is None:
        return None
    age = at.year - dob.year - ((at.month, at.day) < (dob.month, dob.day))
    return age if 0 < age < 130 else None


# --- the derivations ----------------------------------------------------------

def egfr(draw, dob, sex, at) -> Derivation | None:
    """Estimated glomerular filtration rate — CKD-EPI 2021.

    The 2021 equation, which removed the race coefficient the 2009 one carried.
    That coefficient raised the estimate for Black patients, which delayed
    referral and transplant listing; the National Kidney Foundation and ASN
    recommended dropping it. Using the older equation here would be a choice,
    and the wrong one.

    Needs age and sex, so it is absent for a record without demographics — and
    that absence is the concrete reason those two fields are worth filling in.
    """
    scr = _get(draw, CREATININE)
    age = _years(dob, at)
    if scr is None or scr <= 0 or age is None or sex not in ("M", "F"):
        return None

    female = sex == "F"
    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    ratio = scr / kappa
    value = (
        142
        * min(ratio, 1.0) ** alpha
        * max(ratio, 1.0) ** -1.200
        * 0.9938**age
        * (1.012 if female else 1.0)
    )
    return Derivation(
        code="98979-8", display="eGFR (CKD-EPI 2021)",
        value=round(value, 1), unit="mL/min/1.73m2", collected_at=at,
        formula="CKD-EPI 2021, from creatinine, age and sex",
        inputs=[Input("Creatinine", scr, "mg/dL"),
                Input("Age", float(age), "years"),
                Input("Sex at birth", 1.0 if female else 0.0, "F" if female else "M")],
        # Below 60 sustained is the CKD threshold; this is a screening
        # boundary, not a diagnosis, which needs 90 days of persistence.
        ref_low=60.0,
        note="A single value is not a diagnosis: chronic kidney disease is "
             "defined by a reduced rate sustained over three months.",
    )


def anion_gap(draw, dob, sex, at) -> Derivation | None:  # noqa: ARG001
    """Sodium minus chloride and bicarbonate — the acid-base workhorse."""
    na, cl, hco3 = _get(draw, SODIUM), _get(draw, CHLORIDE), _get(draw, BICARBONATE)
    if None in (na, cl, hco3):
        return None
    return Derivation(
        code="33037-3", display="Anion gap",
        value=round(na - cl - hco3, 1), unit="mmol/L", collected_at=at,
        formula="sodium - chloride - bicarbonate",
        inputs=[Input("Sodium", na, "mmol/L"), Input("Chloride", cl, "mmol/L"),
                Input("Bicarbonate", hco3, "mmol/L")],
        ref_low=8.0, ref_high=16.0,
        note="Intervals vary with the analyser's electrode set; this is the "
             "common range for a gap that excludes potassium.",
    )


def corrected_calcium(draw, dob, sex, at) -> Derivation | None:  # noqa: ARG001
    """Calcium adjusted for albumin.

    Roughly half of serum calcium is albumin-bound, so a low albumin lowers the
    measured total while the physiologically active ionised fraction is
    untouched. Reading the raw total on a hypoalbuminaemic patient understates
    their calcium, which is why the correction exists.
    """
    ca, alb = _get(draw, CALCIUM), _get(draw, ALBUMIN)
    if ca is None or alb is None:
        return None
    return Derivation(
        code=None, display="Calcium corrected for albumin",
        value=round(ca + 0.8 * (4.0 - alb), 2), unit="mg/dL", collected_at=at,
        formula="calcium + 0.8 * (4.0 - albumin)",
        inputs=[Input("Calcium", ca, "mg/dL"), Input("Albumin", alb, "g/dL")],
        ref_low=8.6, ref_high=10.2,
        note="An approximation. Where it matters, ionised calcium is measured "
             "directly rather than inferred.",
    )


def non_hdl(draw, dob, sex, at) -> Derivation | None:  # noqa: ARG001
    """Total cholesterol minus HDL — every atherogenic particle at once.

    Unlike LDL it needs no fasting sample and no assumptions, which is why
    guidelines increasingly prefer it.
    """
    total, hdl = _get(draw, CHOLESTEROL), _get(draw, HDL)
    if total is None or hdl is None:
        return None
    return Derivation(
        code="43396-1", display="Non-HDL cholesterol",
        value=round(total - hdl, 1), unit="mg/dL", collected_at=at,
        formula="total cholesterol - HDL",
        inputs=[Input("Total cholesterol", total, "mg/dL"), Input("HDL", hdl, "mg/dL")],
        ref_high=130.0,
    )


def transferrin_saturation(draw, dob, sex, at) -> Derivation | None:  # noqa: ARG001
    """Iron over total iron-binding capacity.

    Reads iron status better than either input alone: a ferritin rises with
    inflammation and can look reassuring in someone who is genuinely deficient.
    """
    iron, tibc = _get(draw, IRON), _get(draw, TIBC)
    if iron is None or not tibc:
        return None
    return Derivation(
        code="2502-3", display="Transferrin saturation",
        value=round(iron / tibc * 100, 1), unit="%", collected_at=at,
        formula="iron / total iron-binding capacity * 100",
        inputs=[Input("Iron", iron, "ug/dL"), Input("TIBC", tibc, "ug/dL")],
        ref_low=20.0, ref_high=50.0,
    )


DERIVATIONS = (egfr, anion_gap, corrected_calcium, non_hdl, transferrin_saturation)


def derive(
    observations, dob: date | None, sex: str | None
) -> list[Derivation]:
    """Every value derivable from these results, newest draw first.

    Groups by collection date first, because a derivation is a statement about
    one draw. Rows without a date cannot participate: there is no way to know
    what they belong with, and pairing them by proximity would be a guess
    dressed as a measurement.
    """
    draws: dict[datetime, dict[str, tuple[float, str]]] = {}
    for o in observations:
        if (o.collected_at is None or o.loinc_code is None
                or o.canonical_value is None or o.canonical_unit is None
                or o.review_status == "pending"):
            continue
        draws.setdefault(o.collected_at, {})[o.loinc_code] = (
            o.canonical_value, o.canonical_unit,
        )

    out: list[Derivation] = []
    for at, draw in draws.items():
        for fn in DERIVATIONS:
            got = fn(draw, dob, sex, at)
            if got is not None:
                out.append(got)
    out.sort(key=lambda d: (d.display, d.collected_at))
    return out
