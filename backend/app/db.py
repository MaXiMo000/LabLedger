from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient

from app.config import settings
from app.models.alias import Alias
from app.models.audit import AuditEntry
from app.models.document import LabDocument
from app.models.invite import Invite
from app.models.loinc import LoincEntry
from app.models.observation import Observation
from app.models.patient import Access, Patient
from app.models.session import Session
from app.models.user import User

DOCUMENT_MODELS = [User, LoincEntry, LabDocument, Observation, Alias, AuditEntry,
                   Patient, Access, Session, Invite]

_client: AsyncIOMotorClient | None = None


async def init_db(db_name: str | None = None) -> None:
    """Connect Motor and register every Beanie document model."""
    global _client
    # tz_aware: BSON stores UTC but hands it back with no tzinfo, so anything
    # read from the database compares naive against an aware `utcnow()` and
    # raises. Set on the client rather than patched at each comparison — the
    # idle timeout and grant expiry both do date arithmetic on stored values,
    # and the next one to be added would hit the same wall.
    _client = AsyncIOMotorClient(settings.mongo_uri, tz_aware=True)
    await init_beanie(
        database=_client[db_name or settings.mongo_db_name],
        document_models=DOCUMENT_MODELS,
    )


async def close_db() -> None:
    """Close the Motor client. Safe to call when already closed."""
    global _client
    if _client is not None:
        _client.close()
        _client = None
