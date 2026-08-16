"""Resolve a printed lab test name to a LOINC code.

Deterministic first; the LLM only ever sees the residue.

    stage 0  alias           user-confirmed lookup           conf 1.00
    stage 1  exact           exact hit on a LOINC name       conf 0.95
    stage 2  narrowing       specimen -> SYSTEM axis         (not a decision)
    stage 3  narrowed_fuzzy  rapidfuzz over the narrow set   conf = f(score)
    stage 4  llm             multiple choice, see llm.py     conf 0.85
             unmapped        -> review queue

Two invariants hold throughout:

1. Narrowing may never exclude the correct answer. Every filter falls back to
   the wider set when it empties the candidate list, because a filter that
   silently removes the right code turns a solvable row into a wrong one.
2. Nothing is auto-accepted for an analyte on CRITICAL_COMPONENTS unless it
   came from stage 0 or 1. Confidence is a statistical instrument and these
   failures are categorical -- see should_force_review().
"""

import re
from dataclasses import dataclass

from beanie import PydanticObjectId
from rapidfuzz import fuzz, process

from app.models.alias import Alias
from app.models.loinc import LoincEntry

# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------

# Suffixes labs append that carry specimen, not identity. Stripped from the
# name because the specimen is tracked separately and would otherwise make
# "FERRITIN" and "FERRITIN, SERUM" two different alias keys.
_SPECIMEN_SUFFIX = re.compile(
    r",?\s*\b(SERUM|PLASMA|SER/PLAS|SERUM/PLASMA|BLOOD|WHOLE BLOOD|URINE|"
    r"RANDOM URINE|24 HR URINE|CSF|STOOL|PLAS)\b\s*$", re.IGNORECASE)
_NOISE = re.compile(r"\b(LEVEL|TEST|RESULT|QUANT|QUANTITATIVE|SERUM LEVEL)\b\s*$", re.IGNORECASE)
_PUNCT = re.compile(r"[^\w\s%/+.-]")
_WS = re.compile(r"\s+")


def normalize(name: str) -> str:
    """Fold a printed test name to its alias key: upper, de-punctuated, unsuffixed."""
    s = (name or "").upper().strip()
    s = _PUNCT.sub(" ", s)
    for _ in range(2):  # "FERRITIN, SERUM LEVEL" -> "FERRITIN"
        s = _SPECIMEN_SUFFIX.sub("", s).strip(" ,-")
        s = _NOISE.sub("", s).strip(" ,-")
    return _WS.sub(" ", s).strip()


# --------------------------------------------------------------------------
# stage 2 narrowing keys
# --------------------------------------------------------------------------

# Printed specimen -> acceptable LOINC SYSTEM values. Serum and plasma are
# deliberately cross-listed: most chemistry codes are modelled as "Ser/Plas"
# and a lab printing either should reach them.
SYSTEM_MAP: dict[str, tuple[str, ...]] = {
    "SERUM": ("Ser", "Ser/Plas", "Plas"),
    "PLASMA": ("Plas", "Ser/Plas", "Ser"),
    "SERUM/PLASMA": ("Ser/Plas", "Ser", "Plas"),
    "BLOOD": ("Bld", "Ser/Plas", "Ser", "Plas", "Bld/Tiss"),
    "WHOLE BLOOD": ("Bld", "Bld/Tiss"),
    "URINE": ("Urine",),
    "CSF": ("CSF",),
    "STOOL": ("Stool",),
    "SALIVA": ("Saliva",),
}

# Printed unit -> LOINC PROPERTY. Used only as a ranking *boost*, never a
# filter: unit conventions vary enough that a hard property filter would
# sometimes drop the right code.
PROPERTY_HINTS: dict[str, tuple[str, ...]] = {
    "mg/dl": ("MCnc",), "g/dl": ("MCnc",), "ng/ml": ("MCnc",), "pg/ml": ("MCnc",),
    "ug/dl": ("MCnc",), "ug/l": ("MCnc",), "mcg/dl": ("MCnc",), "g/l": ("MCnc",),
    "mg/l": ("MCnc",), "ng/dl": ("MCnc",),
    "mmol/l": ("SCnc",), "umol/l": ("SCnc",), "nmol/l": ("SCnc",),
    "pmol/l": ("SCnc",), "µmol/l": ("SCnc",),
    "%": ("MFr", "NFr", "CFr", "VFr"),  # VFr: hematocrit is a volume fraction
    "x10e3/ul": ("NCnc",), "x10e6/ul": ("NCnc",), "k/ul": ("NCnc",),
    "m/ul": ("NCnc",), "/ul": ("NCnc",), "cells/ul": ("NCnc",),
    "iu/l": ("CCnc",), "u/l": ("CCnc",), "iu/ml": ("ACnc",),
    "uiu/ml": ("ACnc",), "miu/l": ("ACnc",), "miu/ml": ("ACnc",),
    "fl": ("EntVol",), "pg": ("EntMass",),
}

# Analytes whose mis-mapping changes dosing or triggers an emergency. A
# probabilistic stage never auto-accepts these, whatever its confidence says.
CRITICAL_COMPONENTS = {
    "potassium", "sodium", "calcium", "magnesium", "glucose", "creatinine",
    "troponin i.cardiac", "troponin t.cardiac", "inr", "prothrombin time",
    "digoxin", "lithium", "phenytoin", "vancomycin", "gentamicin",
    "hemoglobin", "hemoglobin a1c", "platelets", "neutrophils", "lactate",
    "ammonia", "bilirubin.total", "thyrotropin",
}

FUZZY_ACCEPT = 92.0   # rapidfuzz score required to auto-accept at stage 3
FUZZY_MARGIN = 6.0    # ...and how far it must beat the runner-up
FUZZY_FLOOR = 60.0    # below this a candidate is not worth showing a human


@dataclass
class Candidate:
    """One scored LOINC code offered to a human or to the adjudicator."""

    loinc_code: str
    display: str
    score: float
    common_rank: int
    system: str
    property: str
    # Name-similarity before boosts. The auto-accept threshold is checked
    # against this so a ranking boost can never manufacture an acceptance.
    raw_score: float = 0.0
    why: str = ""


@dataclass
class MappingResult:
    """The outcome of the cascade for a single row, with its provenance."""

    stage: str = "unmapped"
    loinc_code: str | None = None
    loinc_display: str | None = None
    confidence: float = 0.0
    candidates: list[Candidate] = None          # top-N, for review or the LLM
    candidates_considered: int = 0
    component: str | None = None
    note: str | None = None

    def __post_init__(self):
        if self.candidates is None:
            self.candidates = []


# --------------------------------------------------------------------------
# stage 0 - learned aliases
# --------------------------------------------------------------------------

async def _stage_alias(norm: str, specimen: str | None,
                       user_id: PydanticObjectId | None) -> Alias | None:
    """Look up a confirmed alias.

    User-specific aliases win over global ones, and an exact specimen match
    wins over a specimen-agnostic one.
    """
    scopes = [user_id, None] if user_id else [None]
    for scope in scopes:
        for spec in ([specimen, None] if specimen else [None]):
            hit = await Alias.find_one(
                Alias.user_id == scope,
                Alias.normalized_name == norm,
                Alias.specimen == spec,
            )
            if hit:
                return hit
    return None


# --------------------------------------------------------------------------
# stage 1 - exact name hit
# --------------------------------------------------------------------------

def _name_variants(e: LoincEntry, include_related: bool = True) -> list[str]:
    """RELATEDNAMES2 is a recall device, not an identity claim.

    It lists names *associated* with a code, not only names *for* it: "HCT" is
    a related name of Reticulocyte production index (RPI is calculated from the
    hematocrit), and "Phos" is a related name of Alkaline phosphatase. Treating
    those as exact identity produced confident, wrong mappings at conf 0.95 --
    the single most dangerous failure this system can have.

    So: related names widen the candidate pool (include_related=True, used for
    fuzzy scoring) but never decide an exact match (include_related=False).
    """
    parts = [e.long_common_name, e.shortname, e.display_name,
             e.consumer_name, e.component]
    if include_related:
        parts += list((e.related_names or "").split(";"))
    return [normalize(p) for p in parts if p and p.strip()]


async def _stage_exact(norm: str, systems: tuple[str, ...] | None,
                       unit: str | None = None) -> list[LoincEntry]:
    """Find exact name matches, split into primary and related-name tiers.

    Mongo text search, then verify exact normalized equality in Python.

    The text index is a recall device too: it happily returns MCHC for "HGB"
    because LOINC models MCHC's component as Hemoglobin. The equality check
    below is what actually decides.
    """
    if not norm:
        return []
    query: dict = {"$text": {"$search": f'"{norm}"'}, "auto_matchable": True}
    if systems:
        query["system"] = {"$in": list(systems)}
    found = await LoincEntry.find(query).limit(200).to_list()

    primary, corroborated = [], []
    for e in found:
        in_system = (not systems) or e.system in systems
        if norm in _name_variants(e, include_related=False):
            primary.append(e)
        # Tier B: matched only via RELATEDNAMES2, which is associative rather
        # than identifying. Accept only when BOTH the specimen and the unit's
        # implied property independently agree -- that is what separates
        # "HGB" -> Hemoglobin from "HCT" -> Reticulocyte production index and
        # "PHOS" -> Alkaline phosphatase.
        elif (norm in _name_variants(e, include_related=True)
                and in_system and _property_boost(e, unit) > 0):
            corroborated.append(e)

    def rank(e: LoincEntry):
        in_system = bool(systems) and e.system in systems
        return (not in_system, -_property_boost(e, unit), e.common_rank or 10**9)

    primary.sort(key=rank)
    corroborated.sort(key=rank)
    return primary, corroborated


# --------------------------------------------------------------------------
# stage 2 - narrowing, stage 3 - fuzzy
# --------------------------------------------------------------------------

async def _narrow(norm: str, systems: tuple[str, ...] | None) -> list[LoincEntry]:
    """Build the recall-oriented candidate pool for fuzzy scoring.

    Invariant: never return empty when a wider query would have found
    something.
    """
    base: dict = {"auto_matchable": True}
    token = norm.split()[0] if norm else ""

    attempts: list[dict] = []
    if systems:
        attempts.append({**base, "system": {"$in": list(systems)},
                         "$text": {"$search": norm}})
    attempts.append({**base, "$text": {"$search": norm}})
    if token and token != norm:
        attempts.append({**base, "$text": {"$search": token}})

    for q in attempts:
        found = await LoincEntry.find(q).limit(400).to_list()
        if found:
            return found
    return []


def _property_boost(entry: LoincEntry, unit: str | None) -> float:
    if not unit:
        return 0.0
    props = PROPERTY_HINTS.get(unit.strip().lower())
    if not props:
        return 0.0
    return 3.0 if entry.property in props else -2.0


def _rank_boost(entry: LoincEntry) -> float:
    """Nudge scoring toward commonly ordered tests.

    Small enough that it only ever breaks near-ties, never overturns a
    clearly better name match.
    """
    r = entry.common_rank or 0
    if r == 0:
        return 0.0
    return 2.0 if r <= 100 else 1.0 if r <= 2000 else 0.0


RELATED_ONLY_CAP = FUZZY_ACCEPT - 1.0


def _best(norm: str, variants: list[str]) -> float:
    if not variants:
        return 0.0
    hit = process.extractOne(norm, variants, scorer=fuzz.token_set_ratio)
    return float(hit[1]) if hit else 0.0  # extractOne -> (choice, score, index)


def _score(norm: str, entries: list[LoincEntry], unit: str | None) -> list[Candidate]:
    out: list[Candidate] = []
    for e in entries:
        primary = _name_variants(e, include_related=False)
        related = [v for v in _name_variants(e, include_related=True) if v not in primary]
        p_score, r_score = _best(norm, primary), _best(norm, related)
        if max(p_score, r_score) == 0.0:
            continue
        # Same rule as stage 1: a related name is evidence for *considering* a
        # code, never for accepting it. Capping below FUZZY_ACCEPT keeps such a
        # candidate near the top of the review shortlist while making it
        # structurally impossible for it to auto-accept.
        score = p_score if p_score >= r_score else min(r_score, RELATED_ONLY_CAP)
        total = score + _property_boost(e, unit) + _rank_boost(e)
        out.append(Candidate(
            loinc_code=e.loinc_num,
            display=e.long_common_name or e.shortname or e.component,
            score=round(total, 2), common_rank=e.common_rank,
            system=e.system, property=e.property, raw_score=round(score, 2),
            why=f"fuzzy {score:.0f}"
                + (f", specimen {e.system}" if e.system else "")
                + (f", rank {e.common_rank}" if e.common_rank else ""),
        ))
    out.sort(key=lambda c: (-c.score, c.common_rank or 10**9))
    return out


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------

def should_force_review(stage: str, component: str | None) -> bool:
    """Categorical override on top of the confidence floor.

    Stages 0 and 1 are exact and trusted. Anything probabilistic -- fuzzy or
    LLM -- is not auto-accepted for an analyte that drives dosing, no matter
    how confident it claims to be.
    """
    if stage in ("alias", "exact"):
        return False
    if stage == "related_corroborated":
        return True  # associative evidence: always propose, never auto-accept
    return (component or "").strip().lower() in CRITICAL_COMPONENTS


async def resolve(
    raw_name: str,
    raw_unit: str | None = None,
    specimen: str | None = None,
    user_id: PydanticObjectId | None = None,
    top_n: int = 5,
) -> MappingResult:
    """Run the full cascade for one printed row.

    Returns the resolved code plus the provenance needed to explain it. A
    result with `loinc_code=None` still carries `candidates` so the review
    queue always has something to offer a human.
    """
    norm = normalize(raw_name)
    systems = SYSTEM_MAP.get((specimen or "").upper()) if specimen else None

    # stage 0
    if alias := await _stage_alias(norm, specimen, user_id):
        entry = await LoincEntry.find_one(LoincEntry.loinc_num == alias.loinc_code)
        return MappingResult(
            stage="alias", loinc_code=alias.loinc_code,
            loinc_display=entry.long_common_name if entry else None,
            confidence=1.0, candidates_considered=1,
            component=entry.component if entry else None,
            note=f"confirmed {alias.confirmed_count}x",
        )

    # stage 1
    primary, related = await _stage_exact(norm, systems, raw_unit)
    if not primary and not related and systems:
        # invariant 1: widen, don't drop
        primary, related = await _stage_exact(norm, None, raw_unit)

    if primary:
        e = primary[0]
        return MappingResult(
            stage="exact", loinc_code=e.loinc_num,
            loinc_display=e.long_common_name, confidence=0.95,
            candidates_considered=len(primary), component=e.component,
        )
    if related:
        # Corroborated related-name hit. Measured on 40 real lab abbreviations
        # this is right ~88% of the time -- good enough to propose, not good
        # enough to accept silently, because the 12% are indistinguishable from
        # the 88% without looking (HCT resolving to Reticulocyte production
        # index looks exactly as confident as HGB resolving to Hemoglobin).
        # So it becomes a pre-filled review item, and confirming it writes an
        # alias that makes the same row stage 0 forever after.
        e = related[0]
        return MappingResult(
            stage="related_corroborated", loinc_code=e.loinc_num,
            loinc_display=e.long_common_name, confidence=0.85,
            candidates=[Candidate(x.loinc_num, x.long_common_name, 0.0,
                                  x.common_rank, x.system, x.property,
                                  why="related name + specimen + unit agree")
                        for x in related[:top_n]],
            candidates_considered=len(related), component=e.component,
        )

    # stage 2 + 3
    pool = await _narrow(norm, systems)
    ranked = _score(norm, pool, raw_unit)
    shortlist = [c for c in ranked if c.score >= FUZZY_FLOOR][:top_n]

    if ranked:
        best = ranked[0]
        runner_up = ranked[1].score if len(ranked) > 1 else 0.0
        # Boosts help ranking but must not manufacture an auto-accept, so the
        # threshold is checked against the raw name score.
        if best.raw_score >= FUZZY_ACCEPT and (best.score - runner_up) >= FUZZY_MARGIN:
            entry = await LoincEntry.find_one(LoincEntry.loinc_num == best.loinc_code)
            return MappingResult(
                stage="narrowed_fuzzy", loinc_code=best.loinc_code,
                loinc_display=best.display,
                confidence=round(min(best.score, 100.0) / 100.0 * 0.9, 3),
                candidates=shortlist, candidates_considered=len(pool),
                component=entry.component if entry else None,
            )

    # residue -> stage 4 (llm) or the review queue
    return MappingResult(
        stage="unmapped", candidates=shortlist, candidates_considered=len(pool),
        note="ambiguous" if ranked else "no candidates",
    )
