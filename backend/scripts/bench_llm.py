"""Benchmark stage-4 models on the real mapping residue.

    python scripts/bench_llm.py

Decides which Gemini model to pin for LOINC adjudication, on the rows the
deterministic cascade actually fails to resolve -- not on hand-picked easy
cases. Every case is a genuine unmapped row plus the candidate list stage 2/3
really produced, so the ceiling here is the ceiling in production.
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import close_db, init_db
from app.pipeline.extract import extract
from app.pipeline.llm import adjudicate
from app.pipeline.mapping import resolve

MODELS = [
    "gemini-2.5-pro",
    "gemini-3-pro-preview",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash-lite",
]

# Correct answers, verified against the LOINC table.
EXPECTED = {
    ("HDL CHOLESTEROL", "SERUM"): {"2085-9"},
    ("LDL-CHOLESTEROL", "SERUM"): {"2089-1", "13457-7", "18262-6"},
    ("HEMATOCRIT", "WHOLE BLOOD"): {"4544-3", "20570-8", "31100-1"},
    ("MCHC", "WHOLE BLOOD"): {"786-4", "28540-3"},
}


async def main() -> None:
    await init_db()

    rows = extract((Path(__file__).resolve().parent.parent
                    / "tests/fixtures/quest_style.pdf").read_bytes()).rows

    cases = []
    for r in rows:
        m = await resolve(r.raw_name, r.raw_unit, r.raw_specimen)
        if m.stage == "unmapped" and m.candidates:
            cases.append((r, m))

    print(f"{len(cases)} residue rows reached stage 4\n")
    for r, m in cases:
        want = EXPECTED.get((r.raw_name, r.raw_specimen), set())
        reachable = any(c.loinc_code in want for c in m.candidates)
        print(f"  {r.raw_name} ({r.raw_specimen}) — {len(m.candidates)} candidates, "
              f"correct answer offered: {'YES' if reachable else 'NO — recall bug, not a model problem'}")
        for i, c in enumerate(m.candidates):
            star = " <-- correct" if c.loinc_code in want else ""
            print(f"     {i}. [{c.loinc_code:<8}] {c.display[:58]}{star}")
        print()

    print(f"{'model':<24}{'correct':>9}{'wrong':>7}{'none':>6}{'err':>5}{'p50 ms':>9}{'tok':>7}")
    print("-" * 68)
    for model in MODELS:
        ok = wrong = none = err = 0
        lat = []
        for r, m in cases:
            want = EXPECTED.get((r.raw_name, r.raw_specimen), set())
            t0 = time.perf_counter()
            try:
                code, _ = await adjudicate(r.raw_name, r.raw_unit, r.raw_specimen,
                                           m.candidates, model=model)
                lat.append((time.perf_counter() - t0) * 1000)
                if code is None:
                    none += 1
                elif code in want:
                    ok += 1
                else:
                    wrong += 1
            except Exception as exc:
                err += 1
                if err == 1:
                    print(f"{model:<24} ERROR {type(exc).__name__}: {str(exc)[:34]}")
        if lat:
            lat.sort()
            p50 = lat[len(lat) // 2]
            print(f"{model:<24}{ok:>9}{wrong:>7}{none:>6}{err:>5}{p50:>9.0f}{'~':>7}")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
