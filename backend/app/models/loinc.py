from typing import Annotated, ClassVar

import pymongo
from beanie import Document, Indexed
from pydantic import Field


class LoincEntry(Document):
    """One LOINC laboratory code. Reference data — never user-scoped, never PHI."""

    loinc_num: Annotated[str, Indexed(unique=True)]

    # The six axes. `system` and `property` are the deterministic narrowing keys
    # used by mapping stage 2 to cut candidates before any fuzzy or LLM work.
    component: str
    property: str = ""
    time_aspct: str = ""
    system: str = ""
    scale_typ: str = ""
    method_typ: str = ""
    loinc_class: str = ""

    long_common_name: str = ""
    shortname: str = ""
    display_name: str = ""
    consumer_name: str = ""       # plain-language label for the UI
    related_names: str = ""       # synonym blob — the main stage-1 hit source
    example_ucum_units: str = ""

    # 0 = never observed in the LOINC frequency survey. Kept for review-queue
    # search but excluded from auto-matching; also breaks stage-3 fuzzy ties
    # toward the more commonly ordered test.
    common_rank: int = 0

    # Lowercased concatenation of every name variant, text-indexed.
    search_blob: str = ""

    auto_matchable: bool = Field(default=False)

    class Settings:
        name = "loinc"
        indexes: ClassVar = [
            [("search_blob", pymongo.TEXT)],
            [("auto_matchable", pymongo.ASCENDING), ("system", pymongo.ASCENDING)],
            [("component", pymongo.ASCENDING), ("system", pymongo.ASCENDING)],
            [("common_rank", pymongo.ASCENDING)],
        ]
