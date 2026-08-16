"""PDF -> raw lab result rows.

Cheapest, most exact method first; OCR is a last resort that most digital lab
PDFs never reach.

    1. pdfplumber.extract_tables()   ruled tables (Quest, LabCorp digital reports)
    2. text layer + token classifier borderless layouts (hospital portals)
    3. OCR                           not implemented; see PLAN.md phase 9

Why a token *classifier* and not a positional regex: column order is not stable
across labs. Quest prints  NAME  VALUE  FLAG  UNIT  REF, LabCorp prints
NAME  VALUE  FLAG  REF  UNIT. A regex keyed on position silently mis-assigns
the unit and the reference range for one of them -- which is exactly the class
of error that produces a plausible, wrong chart. Classifying each token by its
own shape is order-independent.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pdfplumber

MAX_PAGES = 100


class UnreadablePDFError(ValueError):
    """The bytes passed the magic-byte check but no PDF parser can read them."""


@dataclass
class RawRow:
    """One result row exactly as printed. Never mutated: the audit anchor."""

    raw_name: str
    raw_value: str
    raw_unit: str | None = None
    raw_ref_range: str | None = None
    raw_specimen: str | None = None
    raw_flag: str | None = None
    page: int = 1
    line_no: int = 0


@dataclass
class ExtractResult:
    """Everything one PDF yielded: rows plus document-level metadata."""

    rows: list[RawRow] = field(default_factory=list)
    text: str = ""
    page_count: int = 0
    lab_name: str | None = None
    collected_at: datetime | None = None
    date_source: str = "none"
    method: str = "none"


# --------------------------------------------------------------------------
# token shapes
# --------------------------------------------------------------------------

RE_RANGE = re.compile(
    r"^(?:"
    r"[\d.]+\s*[-–—]\s*[\d.]+"          # 24-336, 0.4 - 4.5
    r"|[<>≤≥]\s*[\d.]+"                  # <150, >=40
    r"|[\d.]+\s*[-–—]\s*$"               # open-ended
    r")$"
)
RE_NUMBER = re.compile(r"^[<>≤≥]?\s*-?[\d,]+\.?\d*$")
RE_FLAG = re.compile(r"^(H|L|HH|LL|A|AB|N|HIGH|LOW|ABNORMAL|CRITICAL)$", re.IGNORECASE)
# Units are short and made of letter/slash/percent/micro symbols. Deliberately
# strict: an unrecognised token is left unclassified rather than guessed at.
RE_UNIT = re.compile(r"^[A-Za-zµμ%/()·^0-9.\-]{1,15}$")
UNIT_HINT = re.compile(r"(/|%|^m?g$|^ng$|^pg$|^ug$|^µg$|L$|dL$|mL$|mol|IU|U$|ratio|sec|mm)", re.IGNORECASE)

QUALITATIVE = {
    "NEGATIVE", "POSITIVE", "NONREACTIVE", "NON-REACTIVE", "REACTIVE",
    "NOT DETECTED", "DETECTED", "NORMAL", "ABNORMAL", "TRACE", "NONE SEEN",
    "CLEAR", "YELLOW", "AMBER", "STRAW", "CLOUDY", "TURBID", "FEW", "MANY",
    "RARE", "MODERATE", "OCCASIONAL", "INDETERMINATE", "EQUIVOCAL", "PENDING",
}

SPECIMEN_WORDS = {
    "SERUM": "SERUM", "PLASMA": "PLASMA", "SERUM/PLASMA": "SERUM",
    "WHOLE BLOOD": "WHOLE BLOOD", "BLOOD": "BLOOD", "URINE": "URINE",
    "URINALYSIS": "URINE", "CSF": "CSF", "STOOL": "STOOL", "SALIVA": "SALIVA",
    "HEMATOLOGY": "WHOLE BLOOD", "CBC": "WHOLE BLOOD", "CHEMISTRY": "SERUM",
    "LIPID": "SERUM", "THYROID": "SERUM", "IRON": "SERUM",
}

# Lines that are structure, not results.
RE_NOISE = re.compile(
    r"^\s*(page \d|patient|name\s*:|dob|date of birth|mrn|accession|specimen id"
    r"|ordering|physician|provider|client|account|report(ed)?\s*(date|:)"
    r"|collected|received|printed|fasting|comment|note|performed at|final|test\s+name"
    r"|reference|result\s+(range|units)|©|copyright|all rights reserved|end of report"
    r"|this test|methodology|analyte)\b",
    re.IGNORECASE,
)

RE_COLLECTED = re.compile(
    r"(collect(?:ed|ion)?(?:\s*date)?|drawn|specimen\s+date)\s*[:\-]?\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE)
RE_REPORTED = re.compile(
    r"(report(?:ed)?(?:\s*date)?|printed|released)\s*[:\-]?\s*"
    r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4}|\d{4}-\d{2}-\d{2})", re.IGNORECASE)

LAB_NAMES = [
    ("quest", "Quest Diagnostics"), ("labcorp", "LabCorp"),
    ("laboratory corporation", "LabCorp"), ("sonora", "Sonora Quest"),
    ("bioreference", "BioReference"), ("mayo", "Mayo Clinic Laboratories"),
    ("arup", "ARUP Laboratories"), ("kaiser", "Kaiser Permanente"),
    ("sutter", "Sutter Health"), ("stanford", "Stanford Health Care"),
]


def _parse_date(s: str) -> datetime | None:
    """Parse a printed date, stamping UTC at the boundary.

    Collection dates carry no zone, so this is where they get one: no naive
    datetime should ever escape into the pipeline.
    """
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


def detect_specimen(line: str) -> str | None:
    """Section headers like 'CHEMISTRY (SERUM)' scope every row beneath them.

    This single field is what lets mapping stage 2 cut LOINC candidates by an
    order of magnitude before any fuzzy or LLM work happens.
    """
    upper = line.strip().upper()
    if len(upper) > 60 or not upper:
        return None
    inner = re.search(r"\(([^)]{3,20})\)", upper)
    for token in ([inner.group(1).strip()] if inner else []) + [upper]:
        for word, canon in SPECIMEN_WORDS.items():
            # A header is mostly-not-digits; a result row is not a header.
            if (re.search(rf"\b{re.escape(word)}\b", token)
                    and sum(c.isdigit() for c in upper) <= 2):
                return canon
    return None


def classify_tokens(tokens: list[str]) -> tuple[str | None, str | None, str | None, str | None]:
    """-> (value, unit, ref_range, flag). Order-independent."""
    value = unit = ref = flag = None
    for tok in tokens:
        t = tok.strip()
        if not t:
            continue
        if ref is None and RE_RANGE.match(t):
            ref = t
            continue
        if flag is None and RE_FLAG.match(t):
            flag = t.upper()
            continue
        if value is None and RE_NUMBER.match(t.replace(" ", "")):
            value = t.replace(",", "").replace(" ", "")
            continue
        if value is None and t.upper() in QUALITATIVE:
            value = t.upper()
            continue
        # Require a unit-ish shape so stray words don't become units.
        if (unit is None and RE_UNIT.match(t) and not RE_NUMBER.match(t)
                and (UNIT_HINT.search(t) or t in {"pH", "ratio", "index"})):
            unit = t
            continue
    return value, unit, ref, flag


def parse_line(line: str, page: int, line_no: int, specimen: str | None) -> RawRow | None:
    """Parse one text line into a RawRow, or None if it is not a result row."""
    if not line or RE_NOISE.match(line):
        return None
    # Name is the leading run of text before a column gap.
    m = re.match(r"^\s*(?P<name>\S.*?)\s{2,}(?P<rest>\S.*)$", line)
    if not m:
        return None
    name = re.sub(r"\s+", " ", m.group("name")).strip(" .:-")
    if not (2 <= len(name) <= 60) or not re.search(r"[A-Za-z]{2}", name):
        return None

    rest = m.group("rest")
    tokens = re.split(r"\s{2,}", rest)
    if len(tokens) == 1:
        tokens = rest.split()
    value, unit, ref, flag = classify_tokens(tokens)
    if value is None:
        return None
    return RawRow(name, value, unit, ref, specimen, flag, page, line_no)


# --------------------------------------------------------------------------
# extraction strategies
# --------------------------------------------------------------------------

def _rows_from_tables(pdf) -> list[RawRow]:
    rows: list[RawRow] = []
    for pno, page in enumerate(pdf.pages, 1):
        specimen = None
        for table in page.extract_tables() or []:
            for line_no, cells in enumerate(table):
                cells = [(c or "").strip() for c in cells]
                if not any(cells):
                    continue
                joined = " ".join(cells)
                found = detect_specimen(joined)
                if found and sum(bool(c) for c in cells) <= 2:
                    specimen = found
                    continue
                if RE_NOISE.match(joined):
                    continue
                name, rest = cells[0], cells[1:]
                name = re.sub(r"\s+", " ", name).strip(" .:-")
                if not (2 <= len(name) <= 60) or not re.search(r"[A-Za-z]{2}", name):
                    continue
                value, unit, ref, flag = classify_tokens(rest)
                if value is None:
                    continue
                rows.append(RawRow(name, value, unit, ref, specimen, flag, pno, line_no))
    return rows


def _rows_from_text(text_by_page: list[str]) -> list[RawRow]:
    rows: list[RawRow] = []
    for pno, text in enumerate(text_by_page, 1):
        specimen = None
        for line_no, line in enumerate(text.splitlines()):
            found = detect_specimen(line)
            if found:
                specimen = found
                continue
            row = parse_line(line, pno, line_no, specimen)
            if row:
                rows.append(row)
    return rows


def _document_meta(text: str) -> tuple[str | None, datetime | None, str]:
    lab = next((n for k, n in LAB_NAMES if k in text.lower()), None)
    if (m := RE_COLLECTED.search(text)) and (d := _parse_date(m.group(2))):
        return lab, d, "collected"
    if (m := RE_REPORTED.search(text)) and (d := _parse_date(m.group(2))):
        return lab, d, "reported"
    return lab, None, "none"


def extract(pdf_bytes: bytes) -> ExtractResult:
    """Never raises on malformed input: returns a result with method='none'."""
    try:
        pdf_ctx = pdfplumber.open(io.BytesIO(pdf_bytes))
    except Exception as exc:
        raise UnreadablePDFError(f"cannot open PDF: {type(exc).__name__}") from exc

    with pdf_ctx as pdf:
        if len(pdf.pages) > MAX_PAGES:
            raise UnreadablePDFError(f"PDF has {len(pdf.pages)} pages (limit {MAX_PAGES})")
        page_count = len(pdf.pages)
        text_by_page = [(p.extract_text() or "") for p in pdf.pages]
        rows = _rows_from_tables(pdf)
        method = "tables"
        if not rows:
            # layout=True is required, not cosmetic: plain extract_text() collapses
            # every column gap to a single space, which makes "VITAMIN B12 412"
            # impossible to split into name and value. layout mode pads with the
            # real x-offsets so column boundaries survive as multi-space runs.
            rows = _rows_from_text([(p.extract_text(layout=True) or "") for p in pdf.pages])
            method = "text"

    text = "\n".join(text_by_page)
    lab, collected, date_source = _document_meta(text)
    if not rows:
        method = "none"  # scanned PDF or unparseable layout -> OCR territory
    return ExtractResult(
        rows=rows, text=text, page_count=page_count, lab_name=lab,
        collected_at=collected, date_source=date_source, method=method,
    )
