"""Export observations as a FHIR R4 Bundle.

So a record can leave in a form another system reads.

Three rules shape what goes out:

* **A value travels with the range it was judged against.** A printed range is
  in the laboratory's units and a built-in one is in the canonical unit, so
  the exported quantity follows whichever the range came from. Exporting the
  canonical value beside a printed range would pair a number with an interval
  in a different unit.
* **An unconfirmed code is not asserted.** A row still awaiting review goes
  out as `preliminary` with the printed name only. A LOINC code in an export
  reads as a fact to whatever imports it.
* **Units are the printed or canonical strings, without a UCUM `system`.**
  Laboratories print things like `x10E3/uL` that are not valid UCUM, and
  labelling them as UCUM would be a false claim about every one of them.
"""

import uuid
from datetime import date

from app.models.observation import Observation

LOINC = "http://loinc.org"
_CATEGORY = "http://terminology.hl7.org/CodeSystem/observation-category"
_INTERPRETATION = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
_FLAG = {"low": ("L", "Low"), "high": ("H", "High"),
         "abnormal": ("A", "Abnormal"), "normal": ("N", "Normal")}
_GENDER = {"M": "male", "F": "female", "X": "other"}


def _urn(object_id) -> str:
    """Return a stable urn:uuid for a database id.

    Bundle entries need an absolute fullUrl, and there is no FHIR server here
    to give them a real one.
    """
    return f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_OID, str(object_id))}"


def _quantity(value: float, unit: str | None) -> dict:
    return {"value": value, **({"unit": unit} if unit else {})}


def observation(o: Observation) -> dict:
    """Render one row as a FHIR Observation resource."""
    asserted = bool(o.loinc_code) and o.review_status != "pending"
    code: dict = {"text": o.raw_name}
    if asserted:
        coding = {"system": LOINC, "code": o.loinc_code}
        if o.loinc_display:
            coding["display"] = o.loinc_display
        code["coding"] = [coding]

    out: dict = {
        "resourceType": "Observation",
        "id": str(o.id),
        "status": "preliminary" if o.review_status == "pending" else "final",
        "category": [{"coding": [{"system": _CATEGORY, "code": "laboratory"}]}],
        "code": code,
        "subject": {"reference": _urn(o.patient_id)},
    }
    if o.collected_at:
        out["effectiveDateTime"] = o.collected_at.isoformat()

    builtin = o.ref_source == "builtin" and o.canonical_value is not None
    value = o.canonical_value if builtin else o.value_num
    unit = o.canonical_unit if builtin else o.raw_unit
    if value is not None:
        out["valueQuantity"] = _quantity(value, unit)
        if o.value_operator:
            out["valueQuantity"]["comparator"] = o.value_operator
    elif o.value_text or o.raw_value:
        out["valueString"] = o.value_text or o.raw_value

    if o.ref_low is not None or o.ref_high is not None or o.raw_ref_range:
        rng: dict = {}
        if o.ref_low is not None:
            rng["low"] = _quantity(o.ref_low, unit)
        if o.ref_high is not None:
            rng["high"] = _quantity(o.ref_high, unit)
        if o.ref_source == "pdf" and o.raw_ref_range:
            rng["text"] = o.raw_ref_range
        elif o.ref_source == "builtin":
            rng["text"] = "LabLedger built-in interval, not printed on the report"
        if rng:
            out["referenceRange"] = [rng]

    if o.flag in _FLAG:
        flag_code, display = _FLAG[o.flag]
        out["interpretation"] = [{"coding": [
            {"system": _INTERPRETATION, "code": flag_code, "display": display}]}]
    if o.raw_specimen:
        out["specimen"] = {"display": o.raw_specimen}
    return out


def bundle(patient_id: str, name: str, dob: date | None, sex: str | None,
           rows: list[Observation]) -> dict:
    """Build a `collection` Bundle: the patient, then every observation."""
    patient: dict = {"resourceType": "Patient", "id": patient_id, "name": [{"text": name}]}
    if dob:
        patient["birthDate"] = dob.isoformat()
    if sex in _GENDER:
        patient["gender"] = _GENDER[sex]
    resources = [patient] + [observation(o) for o in rows]
    return {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [{"fullUrl": _urn(r["id"]), "resource": r} for r in resources],
    }
