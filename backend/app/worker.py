"""Background document processing (arq).

    arq app.worker.WorkerSettings

Upload returns 202 immediately; this does the slow work. Extraction is CPU-bound
and can take tens of seconds on a large scan, and a crash mid-parse must not lose
the upload -- so it does not belong in the request path.
"""

import logging
from datetime import UTC, date
from typing import ClassVar

from arq import cron
from arq.connections import RedisSettings
from beanie import PydanticObjectId

from app.config import settings
from app.db import close_db, init_db
from app.models.document import LabDocument
from app.models.loinc import LoincEntry
from app.models.observation import MappingProvenance, Observation
from app.models.patient import Patient
from app.models.user import utcnow
from app.pipeline.extract import extract
from app.pipeline.llm import LLMUnavailableError, adjudicate
from app.pipeline.mapping import resolve, should_force_review
from app.pipeline.ranges import age_on, flag_against_range, resolve_range
from app.pipeline.retention import purge_expired_blobs
from app.pipeline.units import parse_value, to_canonical
from app.security import decrypt_field, decrypt_str, encrypt_field

logger = logging.getLogger("labledger.worker")


async def process_document(_ctx: dict, document_id: str) -> str:
    """Extract, map, and persist every row of one uploaded PDF."""
    doc = await LabDocument.get(PydanticObjectId(document_id))
    if doc is None:
        return "missing"

    # Log ids only. Never filenames: "oncology_panel.pdf" is a diagnosis.
    logger.info("extract start doc=%s patient=%s", doc.id, doc.patient_id)
    doc.status = "extracting"
    await doc.save()

    try:
        result = extract(decrypt_field(doc.blob_enc))
    except Exception as exc:  # noqa: BLE001 - any parser failure marks the doc, never kills the worker
        doc.status = "failed"
        doc.error = f"{type(exc).__name__}: {exc}"[:300]
        await doc.save()
        logger.warning("extract failed doc=%s %s", doc.id, type(exc).__name__)
        return "failed"

    doc.page_count = result.page_count
    doc.lab_name = result.lab_name
    doc.extraction_method = result.method
    doc.raw_text_enc = encrypt_field(result.text)
    if result.collected_at:
        doc.collected_at = result.collected_at.replace(tzinfo=UTC)
        doc.date_source = result.date_source

    # Re-processing a document replaces its rows rather than duplicating them.
    await Observation.find(Observation.document_id == doc.id).delete()

    observations = [
        Observation(
            patient_id=doc.patient_id,
            document_id=doc.id,
            raw_name=r.raw_name,
            raw_value=r.raw_value,
            raw_unit=r.raw_unit,
            raw_ref_range=r.raw_ref_range,
            raw_specimen=r.raw_specimen,
            raw_flag=r.raw_flag,
            page=r.page,
            line_no=r.line_no,
            collected_at=doc.collected_at,
            mapping=MappingProvenance(stage="unmapped"),
        )
        for r in result.rows
    ]
    if observations:
        # insert one-by-one: insert_many does not populate ids on the objects,
        # and the mapping pass below needs to save each row back.
        for o in observations:
            await o.insert()

    doc.row_count = len(observations)
    if not observations:
        doc.status = "failed"
        doc.error = "No result rows found (scanned PDF or unsupported layout)"
        doc.processed_at = utcnow()
        await doc.save()
        logger.info("extract done doc=%s rows=0 method=%s", doc.id, result.method)
        return "0 rows"

    doc.status = "mapping"
    await doc.save()
    pending = await map_observations(observations, doc.uploaded_by)
    await finalize_values(observations, doc.patient_id)

    # A file can yield rows that look tabular and still be nothing to do with a
    # laboratory — a résumé, an invoice, a statement. The tell is that not one
    # row resolves to any LOINC code out of 58,252. Saying "done" there sends
    # the user to an empty results screen wondering what they did wrong, so the
    # document is marked failed with a reason instead.
    resolved = sum(1 for o in observations if o.loinc_code)
    if resolved == 0:
        doc.status = "failed"
        doc.error = ("No recognisable lab tests found. This may not be a lab "
                     "report, or its layout is not one LabLedger can read yet.")
        doc.processed_at = utcnow()
        await doc.save()
        logger.info("no resolvable tests doc=%s rows=%d", doc.id, len(observations))
        return f"{len(observations)} rows, none resolvable"

    doc.status = "needs_review" if pending else "done"
    doc.processed_at = utcnow()
    await doc.save()

    logger.info("done doc=%s rows=%d pending_review=%d method=%s",
                doc.id, len(observations), pending, result.method)
    return f"{len(observations)} rows, {pending} pending review"


async def map_observations(observations: list[Observation],
                           user_id: PydanticObjectId) -> int:
    """Resolve each row to a LOINC code. Returns the count needing review."""
    pending = 0
    llm_down = False

    for obs in observations:
        m = await resolve(obs.raw_name, obs.raw_unit, obs.raw_specimen, user_id)

        # Stage 4: only the residue, and only while the LLM is answering.
        if m.stage == "unmapped" and m.candidates and not llm_down:
            try:
                code, model = await adjudicate(
                    obs.raw_name, obs.raw_unit, obs.raw_specimen, m.candidates)
            except LLMUnavailableError as exc:
                # Degrade to the review queue for the rest of this document
                # rather than retrying a down service once per row.
                logger.warning("llm unavailable (%s); remaining rows go to review", exc)
                llm_down = True
            else:
                if code:
                    chosen = next(c for c in m.candidates if c.loinc_code == code)
                    m.stage, m.loinc_code = "llm", code
                    m.loinc_display, m.confidence = chosen.display, 0.85
                    m.component = await _component_of(code)
                    m.note = model

        obs.loinc_code = m.loinc_code
        obs.loinc_display = m.loinc_display
        obs.mapping = MappingProvenance(
            stage=m.stage, confidence=m.confidence,
            candidates_considered=m.candidates_considered,
            llm_model=m.note if m.stage == "llm" else None,
            note=None if m.stage == "llm" else m.note,
        )

        needs_review = m.loinc_code is None or should_force_review(m.stage, m.component)
        obs.review_status = "pending" if needs_review else "auto"
        pending += needs_review

    for obs in observations:
        await obs.save()
    return pending


async def finalize_values(observations: list[Observation],
                          patient_id: PydanticObjectId) -> None:
    """Parse values, convert units, resolve reference ranges, and flag.

    Runs after mapping because both the unit table and the range table are
    keyed by LOINC code.
    """
    # Demographics come from the patient now, not the account holding them:
    # reference ranges belong to the body, not to whoever is signed in.
    patient = await Patient.get(patient_id)
    dob_raw = decrypt_str(patient.dob_enc) if patient else None
    dob = date.fromisoformat(dob_raw) if dob_raw else None
    sex = decrypt_str(patient.sex_at_birth_enc) if patient else None

    for obs in observations:
        obs.value_num, obs.value_operator, obs.value_text = parse_value(obs.raw_value)

        canonical, unit, factor = to_canonical(obs.loinc_code, obs.value_num, obs.raw_unit)
        obs.canonical_value = canonical
        obs.canonical_unit = unit
        obs.unit_conversion_factor = factor

        age = age_on(dob, obs.collected_at.date() if obs.collected_at else None)
        obs.ref_low, obs.ref_high, obs.ref_source = resolve_range(
            obs.raw_ref_range, obs.loinc_code, sex, age)
        # A printed range is in the lab's own units and a built-in one is in the
        # canonical unit, so which value to compare depends on which was used.
        # `flag_against_range` owns that rule — see it for what comparing the
        # wrong pair costs.
        obs.flag = flag_against_range(
            obs.value_num, obs.canonical_value,
            obs.ref_low, obs.ref_high, obs.ref_source, obs.raw_flag)

        await obs.save()


async def _component_of(loinc_code: str) -> str | None:
    entry = await LoincEntry.find_one(LoincEntry.loinc_num == loinc_code)
    return entry.component if entry else None


async def dispose_of_expired(_ctx: dict) -> str:
    """Nightly disposal of stored PDFs past their retention age.

    A no-op unless DOCUMENT_RETENTION_DAYS is set, which it is not by default.
    Runs here rather than on a request because it is bulk work nobody is
    waiting for, and the worker is where bulk work belongs.
    """
    purged = await purge_expired_blobs()
    return f"{purged} purged"


async def startup(_ctx: dict) -> None:
    """Open the database for the worker process."""
    await init_db()


async def shutdown(_ctx: dict) -> None:
    """Close the database when the worker stops."""
    await close_db()


class WorkerSettings:
    """arq entry point: `arq app.worker.WorkerSettings`."""

    functions: ClassVar = [process_document]
    # 03:12 rather than on the hour: nothing else here runs on a schedule, but
    # every cron in the world fires at :00 and this one has no reason to join
    # the queue behind them.
    cron_jobs: ClassVar = [cron(dispose_of_expired, hour=3, minute=12)]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    queue_name = settings.arq_queue_name
    max_jobs = 4
    job_timeout = 300
