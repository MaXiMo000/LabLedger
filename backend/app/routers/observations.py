"""Time-series API: the endpoint the rest of the pipeline exists to serve."""

from datetime import date, datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import repo
from app.audit import record
from app.data.analyte_reference import reference_for
from app.data.panels import panel_for
from app.deps import current_user
from app.models.document import LabDocument
from app.models.loinc import LoincEntry
from app.models.observation import Observation
from app.models.user import User
from app.pipeline import flags
from app.pipeline.derived import derive
from app.pipeline.insights import analyse
from app.security import decrypt_str

router = APIRouter(prefix="/api/observations", tags=["observations"])


class CriticalOut(BaseModel):
    """A value at or beyond a published critical limit — and that limit.

    The threshold travels with the flag on purpose. A reader who can see the
    basis and reach the same conclusion themselves is being given background;
    a reader handed only a verdict is being given a recommendation, and those
    are different kinds of software.
    """

    side: str
    threshold: float
    unit: str
    basis: str


class DeltaOut(BaseModel):
    """A change from this patient's own previous result."""

    percent: float
    from_value: float
    from_at: datetime | None
    days: int
    limit_percent: float


class SeriesPoint(BaseModel):
    """One charted measurement, already in the canonical unit."""

    observation_id: str
    document_id: str
    collected_at: datetime | None
    value: float
    unit: str
    operator: str | None          # "<" / ">" when the lab censored the value
    ref_low: float | None
    ref_high: float | None
    ref_source: str
    flag: str
    stage: str
    confidence: float
    critical: CriticalOut | None = None
    delta: DeltaOut | None = None


class ExcludedPoint(BaseModel):
    """A measurement that exists but cannot join the series, and why."""

    observation_id: str
    raw_name: str
    raw_value: str
    raw_unit: str | None
    reason: str


class InsightOut(BaseModel):
    """One factual statement about the series."""

    kind: str
    text: str
    severity: str
    at: datetime | None = None


class Series(BaseModel):
    """A single analyte's trend, plus everything deliberately left out of it.

    `insights` are arithmetic on these points and nothing more. `reference` is
    general education about the analyte and is not about this person — the two
    are separate fields precisely so the interface cannot blur them.
    """

    loinc_code: str
    display: str | None
    unit: str | None
    points: list[SeriesPoint]
    excluded: list[ExcludedPoint]
    direction: str = "unknown"
    change_pct: float | None = None
    span_days: int | None = None
    insights: list[InsightOut] = []
    reference: dict | None = None


class PanelEntry(BaseModel):
    """One analyte the user has data for."""

    loinc_code: str
    display: str | None
    unit: str | None
    count: int
    latest_at: datetime | None
    latest_value: float | None
    latest_flag: str
    pending_review: int
    # The rail in the panel row needs the interval, not just the value: a
    # number without its reference range is exactly what this product exists
    # to stop showing people.
    ref_low: float | None = None
    ref_high: float | None = None
    latest_critical: CriticalOut | None = None
    # False when no critical limit is published for this analyte and unit.
    # "Not assessed" and "assessed and fine" must not look the same.
    critical_assessed: bool = False
    # Which heading a report prints this under. The label travels with the key
    # so the taxonomy stays in one place rather than being restated in JSX.
    panel: str = "other"
    panel_label: str = "Other results"


class Provenance(BaseModel):
    """Everything needed to explain one number, end to end.

    A resolved value the user cannot trace is not finished work: this is the
    payload behind that claim. It carries the row exactly as printed, every
    decision made about it, and what each decision was based on.
    """

    observation_id: str
    document_id: str
    document_filename: str
    lab_name: str | None
    page: int
    collected_at: datetime | None
    date_source: str
    extraction_method: str | None

    # as printed — never mutated
    raw_name: str
    raw_value: str
    raw_unit: str | None
    raw_ref_range: str | None
    raw_specimen: str | None
    raw_flag: str | None

    # resolved
    loinc_code: str | None
    loinc_display: str | None
    loinc_component: str | None
    loinc_system: str | None
    loinc_property: str | None

    stage: str
    confidence: float
    candidates_considered: int
    llm_model: str | None
    decided_at: datetime
    confirmed_by_user_at: datetime | None
    note: str | None
    review_status: str

    value_num: float | None
    value_text: str | None
    value_operator: str | None
    canonical_value: float | None
    canonical_unit: str | None
    unit_conversion_factor: float | None

    ref_low: float | None
    ref_high: float | None
    ref_source: str
    flag: str


@router.get("/{patient_id}/series", response_model=Series)
async def series(
    patient_id: str,
    loinc: str = Query(min_length=3, max_length=12),
    user: User = Depends(current_user),
):
    """Return one analyte's trend.

    A point joins the series only if it has a resolved LOINC code AND a
    successful canonical unit conversion AND is not awaiting review. Anything
    else is reported in `excluded` with a reason rather than dropped silently:
    plotting a umol/L point against mg/dL points as if they were the same
    series is exactly the failure this whole architecture exists to prevent.
    """
    patient, q = await repo.patient_observations(user, patient_id)
    rows = await q.find(Observation.loinc_code == loinc).sort("+collected_at").to_list()

    points: list[SeriesPoint] = []
    excluded: list[ExcludedPoint] = []
    unit = display = None
    previous_at: datetime | None = None

    for o in rows:
        display = display or o.loinc_display
        if o.review_status == "pending":
            reason = "awaiting review: mapping not confirmed"
        elif o.canonical_value is None or o.canonical_unit is None:
            reason = (f"unit {o.raw_unit!r} has no audited conversion for this analyte"
                      if o.value_num is not None else
                      f"non-numeric result {o.raw_value!r}")
        elif unit is not None and o.canonical_unit != unit:
            reason = f"unit {o.canonical_unit} differs from series unit {unit}"
        else:
            unit = unit or o.canonical_unit
            crit = flags.critical_for(loinc, o.canonical_value, o.canonical_unit)
            # Against the previous *charted* point, not the previous row: the
            # excluded ones were dropped precisely because they are not
            # comparable, and comparing against one anyway would manufacture a
            # change out of a unit mismatch.
            change = None
            if points and o.collected_at and previous_at:
                change = flags.delta_for(loinc, o.canonical_value, o.collected_at,
                                         points[-1].value, previous_at)
            points.append(SeriesPoint(
                observation_id=str(o.id), document_id=str(o.document_id),
                collected_at=o.collected_at, value=o.canonical_value,
                unit=o.canonical_unit, operator=o.value_operator,
                ref_low=o.ref_low, ref_high=o.ref_high, ref_source=o.ref_source,
                flag=o.flag, stage=o.mapping.stage, confidence=o.mapping.confidence,
                critical=CriticalOut(**vars(crit)) if crit else None,
                delta=DeltaOut(**vars(change)) if change else None,
            ))
            previous_at = o.collected_at
            continue
        excluded.append(ExcludedPoint(
            observation_id=str(o.id), raw_name=o.raw_name,
            raw_value=o.raw_value, raw_unit=o.raw_unit, reason=reason,
        ))

    await record("read", "series", loinc, patient_id=patient.id)
    ins = analyse([p.model_dump() for p in points], loinc)
    return Series(
        loinc_code=loinc, display=display, unit=unit,
        points=points, excluded=excluded,
        direction=ins.direction, change_pct=ins.change_pct,
        span_days=ins.span_days,
        insights=[InsightOut(kind=i.kind, text=i.text, severity=i.severity, at=i.at)
                  for i in ins.items],
        reference=reference_for(loinc),
    )


class DerivedInput(BaseModel):
    """One measured value that went into a calculation."""

    display: str
    value: float
    unit: str


class DerivedPoint(BaseModel):
    """One calculated value, with everything needed to check it."""

    collected_at: datetime
    value: float
    flag: str
    inputs: list[DerivedInput]


class DerivedSeries(BaseModel):
    """A value the laboratory never measured, over time."""

    code: str | None
    display: str
    unit: str
    formula: str
    ref_low: float | None
    ref_high: float | None
    note: str | None
    points: list[DerivedPoint]


class DerivedOut(BaseModel):
    """Calculated values, and what stopped the rest from being calculable."""

    series: list[DerivedSeries]
    # Why something expected is missing. An absent eGFR because the record has
    # no date of birth is a fixable gap; silence would make it look like the
    # system simply does not do that.
    unavailable: list[str]


@router.get("/{patient_id}/derived", response_model=DerivedOut)
async def derived_values(patient_id: str, user: User = Depends(current_user)):
    """Values computed from the measured ones — eGFR, anion gap, and the rest.

    Only possible because the cascade and the conversion table already
    established that two rows on two reports are the same analyte in a unit
    arithmetic can be done with. Every value states its formula and its inputs.
    """
    patient, q = await repo.patient_observations(user, patient_id)
    rows = await q.to_list()

    dob_raw = decrypt_str(patient.dob_enc)
    dob = date.fromisoformat(dob_raw) if dob_raw else None
    sex = decrypt_str(patient.sex_at_birth_enc)

    grouped: dict[tuple, DerivedSeries] = {}
    for d in derive(rows, dob, sex):
        key = (d.code, d.display)
        series = grouped.get(key)
        if series is None:
            series = DerivedSeries(
                code=d.code, display=d.display, unit=d.unit, formula=d.formula,
                ref_low=d.ref_low, ref_high=d.ref_high, note=d.note, points=[],
            )
            grouped[key] = series
        series.points.append(DerivedPoint(
            collected_at=d.collected_at, value=d.value, flag=d.flag,
            inputs=[DerivedInput(**vars(i)) for i in d.inputs],
        ))

    # Named, not counted: "no eGFR because this record has no date of birth" is
    # a gap somebody can close, and reporting it as absence hides that.
    unavailable = []
    if (not dob or sex not in ("M", "F")) and not any(
        k[1].startswith("eGFR") for k in grouped
    ):
        missing = " and ".join(
            x for x in ("date of birth" if not dob else "",
                        "sex at birth" if sex not in ("M", "F") else "") if x
        )
        unavailable.append(f"eGFR needs the record's {missing}.")

    await record("list", "series", patient_id=patient.id)
    return DerivedOut(series=list(grouped.values()), unavailable=unavailable)


def _critical_out(loinc: str, o: Observation) -> CriticalOut | None:
    """Project the critical check for one observation, if there is one to make."""
    crit = flags.critical_for(loinc, o.canonical_value, o.canonical_unit)
    return CriticalOut(**vars(crit)) if crit else None


@router.get("/{patient_id}/panels", response_model=list[PanelEntry])
async def panels(
    patient_id: str,
    min_points: int = Query(default=1, ge=1, le=100),
    user: User = Depends(current_user),
):
    """List every analyte the user has data for — what is chartable, and what needs attention."""
    patient, q = await repo.patient_observations(user, patient_id)
    rows = await q.find(
        Observation.loinc_code != None  # noqa: E711 - Beanie builds a Mongo query, not a bool
    ).sort("+collected_at").to_list()

    grouped: dict[str, list[Observation]] = {}
    for o in rows:
        grouped.setdefault(o.loinc_code, []).append(o)

    out = [
        PanelEntry(
            panel=panel_for(code)[0],
            panel_label=panel_for(code)[1],
            loinc_code=code,
            display=next((o.loinc_display for o in obs if o.loinc_display), None),
            unit=next((o.canonical_unit for o in obs if o.canonical_unit), None),
            count=len(obs),
            latest_at=obs[-1].collected_at,
            latest_value=obs[-1].canonical_value,
            latest_flag=obs[-1].flag,
            pending_review=sum(o.review_status == "pending" for o in obs),
            ref_low=obs[-1].ref_low,
            ref_high=obs[-1].ref_high,
            latest_critical=_critical_out(code, obs[-1]),
            critical_assessed=flags.is_assessed(code, obs[-1].canonical_unit),
        )
        for code, obs in grouped.items()
    ]
    await record("list", "observation", patient_id=patient.id)
    out = [p for p in out if p.count >= min_points]
    # Out-of-range analytes first: those are what the user opened the app for.
    out.sort(key=lambda p: (p.latest_flag in ("normal", "unknown"), p.display or ""))
    return out


# Declared last on purpose: this path parameter would otherwise capture the
# literal sibling routes above it, and "/panels" would arrive here as an id.
@router.get("/item/{observation_id}", response_model=Provenance)
async def provenance(observation_id: str, user: User = Depends(current_user)):
    """Explain one result: what was printed, what was decided, and on what basis."""
    obs = await repo.get_observation(user, observation_id)
    doc = await LabDocument.get(obs.document_id)
    entry = (
        await LoincEntry.find_one(LoincEntry.loinc_num == obs.loinc_code)
        if obs.loinc_code
        else None
    )

    return Provenance(
        observation_id=str(obs.id),
        document_id=str(obs.document_id),
        document_filename=doc.filename if doc else "unknown",
        lab_name=doc.lab_name if doc else None,
        page=obs.page,
        collected_at=obs.collected_at,
        date_source=doc.date_source if doc else "none",
        extraction_method=doc.extraction_method if doc else None,
        raw_name=obs.raw_name, raw_value=obs.raw_value, raw_unit=obs.raw_unit,
        raw_ref_range=obs.raw_ref_range, raw_specimen=obs.raw_specimen,
        raw_flag=obs.raw_flag,
        loinc_code=obs.loinc_code, loinc_display=obs.loinc_display,
        loinc_component=entry.component if entry else None,
        loinc_system=entry.system if entry else None,
        loinc_property=entry.property if entry else None,
        stage=obs.mapping.stage, confidence=obs.mapping.confidence,
        candidates_considered=obs.mapping.candidates_considered,
        llm_model=obs.mapping.llm_model, decided_at=obs.mapping.decided_at,
        confirmed_by_user_at=obs.mapping.confirmed_by_user_at,
        note=obs.mapping.note, review_status=obs.review_status,
        value_num=obs.value_num, value_text=obs.value_text,
        value_operator=obs.value_operator,
        canonical_value=obs.canonical_value, canonical_unit=obs.canonical_unit,
        unit_conversion_factor=obs.unit_conversion_factor,
        ref_low=obs.ref_low, ref_high=obs.ref_high, ref_source=obs.ref_source,
        flag=obs.flag,
    )
