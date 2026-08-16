/**
 * The cascade, as it actually runs in app/pipeline/mapping.py.
 *
 * Every sample below is a real printed lab string, and every outcome is what
 * the deployed cascade returns for it. Nothing here is illustrative: the whole
 * point of the section is that the resolution path differs per name, and that
 * the model is reached last and rarely.
 */

export const STAGES = [
  {
    id: "alias",
    n: "00",
    name: "Alias",
    kicker: "Confirmed before",
    what: "A lookup in the names you have already confirmed.",
    why: "Every confirmation you make in the review queue lands here, so a name you have seen once never needs matching again.",
    cost: "~0 ms",
  },
  {
    id: "exact",
    n: "01",
    name: "Exact name",
    kicker: "Identical string",
    what: "Exact match against a LOINC code's own names.",
    why: "Only the code's primary names count. Related names are excluded here — they are associative, not identifying.",
    cost: "~1 ms",
  },
  {
    id: "related",
    n: "02",
    name: "Related name",
    kicker: "Needs corroboration",
    what: "A match on an associated name, accepted only if the specimen and the unit independently agree.",
    why: "“HCT” is listed under Reticulocyte production index, because that index is calculated from the hematocrit. Treating that as identity produced confident, wrong answers — so this stage proposes, and never decides.",
    cost: "~2 ms",
  },
  {
    id: "fuzzy",
    n: "03",
    name: "Narrowed fuzzy",
    kicker: "Specimen-scoped",
    what: "The specimen narrows 58,252 codes to a few dozen; similarity ranks what is left.",
    why: "Narrowing runs before scoring, and always widens rather than returning nothing. A filter that removes the right code turns a solvable row into a wrong one.",
    cost: "~8 ms",
  },
  {
    id: "llm",
    n: "04",
    name: "Model",
    kicker: "Multiple choice only",
    what: "The remainder go to Gemini as a numbered list. It returns one index, or none.",
    why: "It never generates a code. A reply outside the offered list is discarded, so the worst case is a wrong choice among plausible options — not an invented code.",
    cost: "~400 ms",
  },
];

export const REVIEW = {
  id: "review",
  name: "Review queue",
  what: "Anything unresolved, anything a model chose, and every critical analyte.",
};

/** Real strings, real outcomes. */
export const SAMPLES = [
  {
    printed: "FERRITIN, SERUM",
    specimen: "Serum",
    unit: "ng/mL",
    stops: "exact",
    code: "2276-4",
    display: "Ferritin [Mass/volume] in Serum or Plasma",
    confidence: 0.95,
    review: false,
    note: "Matches a primary LOINC name outright.",
    candidates: 1,
  },
  {
    printed: "HGB",
    specimen: "Whole blood",
    unit: "g/dL",
    stops: "related",
    code: "718-7",
    display: "Hemoglobin [Mass/volume] in Blood",
    confidence: 0.85,
    review: true,
    note: "Found via an associated name. Specimen and unit both agree, so it is proposed — but hemoglobin is a critical analyte, so you confirm it.",
    candidates: 3,
  },
  {
    printed: "HCT",
    specimen: "Whole blood",
    unit: "%",
    stops: "llm",
    code: "4544-3",
    display: "Hematocrit [Volume Fraction] of Blood by Automated count",
    confidence: 0.85,
    review: true,
    note: "The correct code does not carry “HCT” as a name at all, while the wrong one does. Only the model gets this right, and you still confirm it.",
    candidates: 5,
  },
  {
    printed: "TBILI",
    specimen: "Serum",
    unit: "mg/dL",
    stops: null,
    code: null,
    display: null,
    confidence: 0,
    review: true,
    note: "No stage reaches it and the model declines rather than guessing. It goes to you with a search box — this is the intended failure, not a bug.",
    candidates: 0,
  },
];

/** Which stages a sample passed through before stopping. */
export function pathFor(sample) {
  const end = sample.stops ? STAGES.findIndex((s) => s.id === sample.stops) : STAGES.length - 1;
  return STAGES.map((s, i) => ({
    ...s,
    state: i < end ? "passed" : i === end && sample.stops ? "caught" : "passed",
  }));
}
