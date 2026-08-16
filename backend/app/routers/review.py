"""The review queue: where a human turns a proposal into a permanent fact.

Confirming a row writes an Alias, so the same printed name resolves at stage 0
next time -- deterministically, with no fuzzy matching and no LLM call. This is
the loop that makes the system converge: the LLM's share of rows falls every
time the queue is worked.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app import access, repo
from app.audit import record
from app.deps import current_user
from app.models.alias import Alias
from app.models.document import LabDocument
from app.models.loinc import LoincEntry
from app.models.observation import Observation
from app.models.user import User, utcnow
from app.pipeline.mapping import Candidate, normalize, resolve
from app.worker import finalize_values

router = APIRouter(prefix="/api/review", tags=["review"])


class CandidateOut(BaseModel):
    """A LOINC code offered as an answer, with why it surfaced."""

    loinc_code: str
    display: str
    score: float
    common_rank: int
    system: str
    why: str

    @classmethod
    def of(cls, c: Candidate) -> "CandidateOut":
        """Project a scored candidate to its API shape."""
        return cls(loinc_code=c.loinc_code, display=c.display, score=c.score,
                   common_rank=c.common_rank, system=c.system, why=c.why)


class ReviewItem(BaseModel):
    """One row awaiting a human decision, with everything needed to decide."""

    observation_id: str
    document_id: str
    collected_at: datetime | None
    raw_name: str
    raw_value: str
    raw_unit: str | None
    raw_specimen: str | None
    raw_ref_range: str | None
    page: int
    proposed_loinc: str | None
    proposed_display: str | None
    stage: str
    confidence: float
    reason: str
    candidates: list[CandidateOut]


class SearchHit(BaseModel):
    """A LOINC code found by manual search."""

    loinc_code: str
    display: str
    component: str
    system: str
    property: str
    common_rank: int
    auto_matchable: bool


class ConfirmIn(BaseModel):
    """The code a human picked."""

    loinc_code: str = Field(min_length=3, max_length=12)
    remember: bool = True


class ReviewResult(BaseModel):
    """Outcome of a decision, including the document's new state."""

    observation_id: str
    loinc_code: str | None
    loinc_display: str | None
    review_status: str
    alias_written: bool
    document_status: str
    remaining_pending: int


def _reason(obs: Observation) -> str:
    """Explain, in the user's terms, why this row needs a human."""
    if obs.loinc_code is None:
        return "no confident match — pick a candidate or search"
    if obs.mapping.stage == "related_corroborated":
        return "matched on an associated name, not an exact one — please confirm"
    if obs.mapping.stage == "llm":
        return f"resolved by {obs.mapping.llm_model or 'the model'} — confirm before charting"
    return "critical analyte — always confirmed by a human"


async def _refresh_document(document_id) -> tuple[str, int]:
    """Recompute a document's status from its rows. Returns (status, pending)."""
    pending = await Observation.find(
        Observation.document_id == document_id,
        Observation.review_status == "pending",
    ).count()
    doc = await LabDocument.get(document_id)
    if doc and doc.status in ("needs_review", "done"):
        doc.status = "needs_review" if pending else "done"
        await doc.save()
    return (doc.status if doc else "unknown"), pending


# --- the aliases those confirmations wrote -----------------------------------

class AliasOut(BaseModel):
    """One learned mapping: what a printed name means to this account."""

    id: str
    printed_name: str
    specimen: str | None
    loinc_code: str
    loinc_display: str | None
    confirmed_count: int
    created_at: datetime
    # How many stored results this alias is currently deciding. The number that
    # makes a wrong one worth fixing rather than merely noting.
    uses: int


class AliasIn(BaseModel):
    """Correct a learned mapping."""

    loinc_code: str = Field(min_length=3, max_length=12)


async def _rows_for_alias(user: User, alias: Alias) -> list[Observation]:
    """Every stored result this alias decided, on records the user may confirm on.

    Scoped by reachability rather than by alias ownership: the alias is the
    user's, but the rows it touched belong to patients, and correcting somebody
    else's record is not a right that owning an alias confers.
    """
    reachable = []
    for pid in await access.reachable_patient_ids(user):
        if await access.role_for(user, pid) in ("clinician", "owner"):
            reachable.append(pid)
    if not reachable:
        return []
    rows = await Observation.find({"patient_id": {"$in": reachable}}).to_list()
    return [
        o for o in rows
        if normalize(o.raw_name) == alias.normalized_name
        and o.raw_specimen == alias.specimen
    ]


@router.get("/aliases", response_model=list[AliasOut])
async def list_aliases(user: User = Depends(current_user)):
    """Every mapping this account has taught the system.

    A confirmation is permanent and silent — it decides the same printed name
    forever, at stage zero, with no further review. Three wrong ones were found
    sitting in a real account (`FERRTN SER` as Serine, `HCT` as Reticulocyte
    production index, `VIT B-12` as Thiamine), and there was no screen on which
    they could be seen, let alone corrected. This is that screen.
    """
    aliases = await Alias.find(Alias.user_id == user.id).sort("+normalized_name").to_list()
    out = []
    for a in aliases:
        entry = await LoincEntry.find_one(LoincEntry.loinc_num == a.loinc_code)
        out.append(AliasOut(
            id=str(a.id), printed_name=a.normalized_name, specimen=a.specimen,
            loinc_code=a.loinc_code,
            loinc_display=(entry.long_common_name or entry.component) if entry else None,
            confirmed_count=a.confirmed_count, created_at=a.created_at,
            uses=len(await _rows_for_alias(user, a)),
        ))
    return out


@router.patch("/aliases/{alias_id}", response_model=list[ReviewResult])
async def correct_alias(
    alias_id: str, body: AliasIn, user: User = Depends(current_user)
):
    """Point an alias at the right code, and repair what the wrong one wrote.

    Correcting the rule without correcting the history would leave the mistake
    exactly where it does harm — on the chart — while making the settings
    screen look fixed. Every affected row is re-coded and its units and
    reference interval recomputed, because both are keyed by LOINC and a
    corrected code invalidates the pair.
    """
    alias = await Alias.get(alias_id)
    if alias is None or alias.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such alias")
    entry = await LoincEntry.find_one(LoincEntry.loinc_num == body.loinc_code)
    if entry is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unknown LOINC code: {body.loinc_code}")

    affected = await _rows_for_alias(user, alias)
    alias.loinc_code = entry.loinc_num
    alias.last_used_at = utcnow()
    await alias.save()

    results = []
    by_patient: dict = {}
    for obs in affected:
        obs.loinc_code = entry.loinc_num
        obs.loinc_display = entry.long_common_name or entry.component
        obs.review_status = "confirmed"
        obs.mapping.confirmed_by_user_at = utcnow()
        obs.mapping.confidence = 1.0
        await obs.save()
        by_patient.setdefault(obs.patient_id, []).append(obs)

    for patient_id, rows in by_patient.items():
        await finalize_values(rows, patient_id)
        await record("confirm", "observation", patient_id=patient_id)

    for obs in affected:
        results.append(ReviewResult(
            observation_id=str(obs.id), loinc_code=obs.loinc_code,
            loinc_display=obs.loinc_display, review_status=obs.review_status,
            alias_written=True, document_status="done", remaining_pending=0,
        ))
    return results


@router.delete("/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
async def forget_alias(alias_id: str, user: User = Depends(current_user)):
    """Forget a learned mapping and send what it decided back for review.

    Deliberately *not* the same as correcting it. "This is wrong and I know the
    right answer" and "this is wrong and I do not" are different admissions,
    and answering the second by guessing would put the system back exactly
    where it started.
    """
    alias = await Alias.get(alias_id)
    if alias is None or alias.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such alias")

    affected = await _rows_for_alias(user, alias)
    await alias.delete()

    for obs in affected:
        obs.loinc_code = None
        obs.loinc_display = None
        obs.canonical_value = None
        obs.canonical_unit = None
        obs.unit_conversion_factor = None
        obs.review_status = "pending"
        obs.mapping.confidence = 0.0
        obs.mapping.confirmed_by_user_at = None
        await obs.save()
        await record("reject", "observation", obs.id, patient_id=obs.patient_id)

    for document_id in {o.document_id for o in affected}:
        await _refresh_document(document_id)


@router.get("/{patient_id}", response_model=list[ReviewItem])
async def queue(
    patient_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
):
    """List rows awaiting confirmation for one patient, newest first."""
    patient, q = await repo.patient_observations(user, patient_id)
    rows = await q.find(
        Observation.review_status == "pending"
    ).sort("-collected_at", "+page", "+line_no").limit(limit).to_list()
    await record("list", "review", patient_id=patient.id)

    items: list[ReviewItem] = []
    for obs in rows:
        # Candidates are recomputed, not stored: both the LOINC table and the
        # alias table move, and a stale list would offer answers the cascade
        # would no longer give.
        m = await resolve(obs.raw_name, obs.raw_unit, obs.raw_specimen, user.id)
        candidates = [CandidateOut.of(c) for c in m.candidates]

        # The current proposal must always be on the list, even when the
        # cascade reached it by a path that produces no candidate array.
        if obs.loinc_code and not any(c.loinc_code == obs.loinc_code for c in candidates):
            entry = await LoincEntry.find_one(LoincEntry.loinc_num == obs.loinc_code)
            if entry:
                candidates.insert(0, CandidateOut(
                    loinc_code=entry.loinc_num,
                    display=entry.long_common_name or entry.component,
                    score=0.0, common_rank=entry.common_rank,
                    system=entry.system, why="current proposal",
                ))

        items.append(ReviewItem(
            observation_id=str(obs.id), document_id=str(obs.document_id),
            collected_at=obs.collected_at, raw_name=obs.raw_name,
            raw_value=obs.raw_value, raw_unit=obs.raw_unit,
            raw_specimen=obs.raw_specimen, raw_ref_range=obs.raw_ref_range,
            page=obs.page, proposed_loinc=obs.loinc_code,
            proposed_display=obs.loinc_display, stage=obs.mapping.stage,
            confidence=obs.mapping.confidence, reason=_reason(obs),
            candidates=candidates,
        ))
    return items


@router.get("/loinc/search", response_model=list[SearchHit])
async def search(
    q: str = Query(min_length=2, max_length=80),
    limit: int = Query(default=20, ge=1, le=50),
    _user: User = Depends(current_user),
):
    """Search the whole LOINC table by name.

    Deliberately not restricted to `auto_matchable`: the 40k codes the cascade
    will never auto-match must still be reachable here, or a rare-but-real test
    would be unresolvable even by a human -- which would make the review queue
    a dead end rather than a backstop.
    """
    hits = await LoincEntry.find(
        {"$text": {"$search": q}}
    ).sort("+common_rank").limit(limit).to_list()
    # Unranked codes sort last: rank 0 means "never seen in the wild".
    hits.sort(key=lambda e: (e.common_rank == 0, e.common_rank or 10**9))
    return [
        SearchHit(
            loinc_code=e.loinc_num,
            display=e.long_common_name or e.shortname or e.component,
            component=e.component, system=e.system, property=e.property,
            common_rank=e.common_rank, auto_matchable=e.auto_matchable,
        )
        for e in hits
    ]


@router.post("/item/{observation_id}/confirm", response_model=ReviewResult)
async def confirm(
    observation_id: str, body: ConfirmIn, user: User = Depends(current_user)
):
    """Accept a LOINC code for this row, and remember it for next time."""
    obs = await repo.get_observation(user, observation_id, access.CAN_CONFIRM)
    entry = await LoincEntry.find_one(LoincEntry.loinc_num == body.loinc_code)
    if entry is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"Unknown LOINC code: {body.loinc_code}")

    obs.loinc_code = entry.loinc_num
    obs.loinc_display = entry.long_common_name or entry.component
    obs.review_status = "confirmed"
    obs.mapping.confirmed_by_user_at = utcnow()
    obs.mapping.confidence = 1.0
    await obs.save()

    # Units and ranges are keyed by LOINC code, so a corrected code invalidates
    # both. Recompute rather than leaving a value converted under the old one.
    await finalize_values([obs], obs.patient_id)

    alias_written = False
    if body.remember:
        key = normalize(obs.raw_name)
        # Scoped to this user: one person's confirmation must never rewrite
        # another's history.
        existing = await Alias.find_one(
            Alias.user_id == user.id,
            Alias.normalized_name == key,
            Alias.specimen == obs.raw_specimen,
        )
        if existing:
            existing.loinc_code = entry.loinc_num
            existing.confirmed_count += 1
            existing.last_used_at = utcnow()
            await existing.save()
        else:
            await Alias(
                user_id=user.id, normalized_name=key,
                specimen=obs.raw_specimen, loinc_code=entry.loinc_num,
                source="user_confirmed",
            ).insert()
        alias_written = True

    await record("confirm", "observation", obs.id, patient_id=obs.patient_id)
    doc_status, pending = await _refresh_document(obs.document_id)
    return ReviewResult(
        observation_id=str(obs.id), loinc_code=obs.loinc_code,
        loinc_display=obs.loinc_display, review_status=obs.review_status,
        alias_written=alias_written, document_status=doc_status,
        remaining_pending=pending,
    )


@router.post("/item/{observation_id}/reject", response_model=ReviewResult)
async def reject(observation_id: str, user: User = Depends(current_user)):
    """Mark a row as unmappable.

    Clears the proposed code rather than keeping it: a rejected guess left on
    the record would still be charted.
    """
    obs = await repo.get_observation(user, observation_id, access.CAN_CONFIRM)
    obs.loinc_code = None
    obs.loinc_display = None
    obs.canonical_value = None
    obs.canonical_unit = None
    obs.unit_conversion_factor = None
    obs.review_status = "rejected"
    obs.mapping.confirmed_by_user_at = utcnow()
    obs.mapping.confidence = 0.0
    await obs.save()

    await record("reject", "observation", obs.id, patient_id=obs.patient_id)
    doc_status, pending = await _refresh_document(obs.document_id)
    return ReviewResult(
        observation_id=str(obs.id), loinc_code=None, loinc_display=None,
        review_status=obs.review_status, alias_written=False,
        document_status=doc_status, remaining_pending=pending,
    )
