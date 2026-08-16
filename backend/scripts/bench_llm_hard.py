"""Larger stage-4 benchmark: 40 real-world messy lab names.

    python scripts/bench_llm_hard.py

These are the abbreviations labs actually print -- "SGPT", "TBILI", "% SAT",
bare element symbols -- run through the real stage 2/3 narrowing so the
candidate lists are the ones production would build.

Three outcomes are reported separately, because they have different fixes:
  RECALL MISS  the correct code was never offered  -> stage 2/3 bug
  WRONG        offered and the model picked another -> model quality
  NONE         model declined                       -> goes to review queue
"""

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import close_db, init_db
from app.models.loinc import LoincEntry
from app.pipeline.llm import adjudicate
from app.pipeline.mapping import resolve, should_force_review

MODELS = ["gemini-2.5-pro", "gemini-3.5-flash", "gemini-3.5-flash-lite"]

# (printed name, unit, specimen, acceptable LOINC codes)
CASES = [
    ("HGB",              "g/dL",      "WHOLE BLOOD", {"718-7", "20509-6"}),
    ("HCT",              "%",         "WHOLE BLOOD", {"4544-3", "20570-8", "31100-1"}),
    ("PLT",              "x10E3/uL",  "WHOLE BLOOD", {"777-3", "26515-7"}),
    ("WBC",              "x10E3/uL",  "WHOLE BLOOD", {"6690-2", "26464-8"}),
    ("MCV",              "fL",        "WHOLE BLOOD", {"787-2", "30428-7"}),
    ("RDW",              "%",         "WHOLE BLOOD", {"788-0", "30385-9"}),
    ("NA",               "mmol/L",    "SERUM",       {"2951-2", "2947-0"}),
    ("K",                "mmol/L",    "SERUM",       {"2823-3", "6298-4"}),
    ("CL",               "mmol/L",    "SERUM",       {"2075-0"}),
    ("CO2",              "mmol/L",    "SERUM",       {"2028-9"}),
    ("BUN",              "mg/dL",     "SERUM",       {"3094-0", "6299-2"}),
    ("CREAT",            "mg/dL",     "SERUM",       {"2160-0"}),
    ("ALT (SGPT)",       "U/L",       "SERUM",       {"1742-6", "1743-4"}),
    ("AST (SGOT)",       "U/L",       "SERUM",       {"1920-8", "30239-8"}),
    ("ALK PHOS",         "U/L",       "SERUM",       {"6768-6", "1783-0"}),
    ("TBILI",            "mg/dL",     "SERUM",       {"1975-2"}),
    ("ALB",              "g/dL",      "SERUM",       {"1751-7", "61151-7"}),
    ("TOTAL PROTEIN",    "g/dL",      "SERUM",       {"2885-2"}),
    ("CALCIUM, TOTAL",   "mg/dL",     "SERUM",       {"17861-6", "2000-8"}),
    ("PHOS",             "mg/dL",     "SERUM",       {"2777-1"}),
    ("MG",               "mg/dL",     "SERUM",       {"2601-3", "19123-9"}),
    ("URIC ACID",        "mg/dL",     "SERUM",       {"3084-1"}),
    ("TSH",              "uIU/mL",    "SERUM",       {"3016-3", "11580-8"}),
    ("FREE T4",          "ng/dL",     "SERUM",       {"3024-7"}),
    ("FREE T3",          "pg/mL",     "SERUM",       {"3051-0"}),
    ("VIT D, 25-OH",     "ng/mL",     "SERUM",       {"1989-3", "62292-8", "14635-7"}),
    ("FOLATE",           "ng/mL",     "SERUM",       {"2284-8"}),
    ("IRON, TOTAL",      "ug/dL",     "SERUM",       {"2498-4"}),
    ("TIBC",             "ug/dL",     "SERUM",       {"2500-7", "14800-7"}),
    ("A1C",              "%",         "WHOLE BLOOD", {"4548-4", "17856-6"}),
    ("CRP",              "mg/L",      "SERUM",       {"1988-5", "30522-7"}),
    ("ESR",              "mm/hr",     "WHOLE BLOOD", {"4537-7", "30341-2"}),
    ("PSA, TOTAL",       "ng/mL",     "SERUM",       {"2857-1", "19195-7"}),
    ("TROPONIN I",       "ng/mL",     "SERUM",       {"10839-9", "42757-5", "89579-7"}),
    ("INR",              "",          "PLASMA",      {"6301-6", "34714-6"}),
    ("PROTIME",          "sec",       "PLASMA",      {"5902-2"}),
    ("LDH",              "U/L",       "SERUM",       {"2532-0", "14804-9"}),
    ("CK, TOTAL",        "U/L",       "SERUM",       {"2157-6"}),
    ("LIPASE",           "U/L",       "SERUM",       {"3040-3"}),
    ("AMYLASE",          "U/L",       "SERUM",       {"1798-8"}),
]


async def main() -> None:
    await init_db()

    # 1. Ground-truth sanity: do the expected codes exist at all?
    bad_truth = []
    for name, _, _, want in CASES:
        present = {e.loinc_num for e in
                   await LoincEntry.find({"loinc_num": {"$in": list(want)}}).to_list()}
        if not present:
            bad_truth.append(name)
    if bad_truth:
        print(f"ground-truth codes missing from the table for: {bad_truth}\n")

    # 2. Run the deterministic cascade. Only auto-accepted stages count as
    #    resolved; anything force-reviewed is a proposal, not a decision.
    auto_ok = auto_wrong = proposed_ok = proposed_wrong = 0
    det_wrong, cases = [], []
    for name, unit, spec, want in CASES:
        m = await resolve(name, unit, spec)
        correct = m.loinc_code in want
        if m.stage in ("alias", "exact", "narrowed_fuzzy") and not should_force_review(m.stage, m.component):
            auto_ok += correct
            auto_wrong += not correct
            if not correct:
                det_wrong.append((name, m.stage, m.loinc_code, m.loinc_display))
        elif m.loinc_code:
            proposed_ok += correct
            proposed_wrong += not correct
        elif m.candidates:
            cases.append((name, unit, spec, want, m.candidates))

    total = len(CASES)
    print(f"AUTO-ACCEPTED   {auto_ok + auto_wrong}/{total} = {(auto_ok+auto_wrong)/total:.0%}  "
          f"correct {auto_ok}, WRONG {auto_wrong}")
    if det_wrong:
        for n, s, c, d in det_wrong:
            print(f"     WRONG: {n:<16} {s:<15} -> {c} {(d or '')[:40]}")
    print(f"PROPOSED (review required) {proposed_ok + proposed_wrong}  "
          f"correct {proposed_ok}, wrong {proposed_wrong}")
    print(f"reached stage 4: {len(cases)}\n")

    if not cases:
        print("no residue to benchmark")
        await close_db()
        return

    recall_miss = [c[0] for c in cases
                   if not any(x.loinc_code in c[3] for x in c[4])]
    if recall_miss:
        print(f"RECALL MISS ({len(recall_miss)}) — correct code never offered, "
              f"no model can fix these: {recall_miss}\n")

    # 3. Models, run concurrently per model.
    print(f"{'model':<24}{'correct':>9}{'wrong':>7}{'none':>6}{'err':>5}{'wall s':>8}")
    print("-" * 59)
    results = {}
    for model in MODELS:
        t0 = time.perf_counter()
        outs = await asyncio.gather(*[
            adjudicate(n, u, s, cand, model=model) for n, u, s, _, cand in cases
        ], return_exceptions=True)
        wall = time.perf_counter() - t0

        ok = wrong = none = err = 0
        misses = []
        for (n, _, _, want, _cand), out in zip(cases, outs, strict=True):
            if isinstance(out, Exception):
                err += 1
                continue
            code, _m = out
            if code is None:
                none += 1
                misses.append(f"{n}=NONE")
            elif code in want:
                ok += 1
            else:
                wrong += 1
                misses.append(f"{n}->{code}")
        results[model] = (ok, wrong, none, err, misses)
        print(f"{model:<24}{ok:>9}{wrong:>7}{none:>6}{err:>5}{wall:>8.1f}")

    print()
    for model, (_, _, _, _, misses) in results.items():
        if misses:
            print(f"{model} missed: {', '.join(misses[:8])}")

    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
