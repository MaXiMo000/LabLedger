"""Plain-language background on what each test measures.

This is education, not interpretation of anyone's results. Each entry says what
the analyte is, and what an out-of-range value is *commonly associated with* in
general medical literature — phrased as associations, never as a finding about
the reader.

Why the phrasing is laboured: naming a condition next to a person's own number
is the step that turns a data tool into clinical decision support. Keeping every
statement general, reviewable, and attributed means a clinician can check the
basis rather than trust the output — which is the distinction the FDA's Cures
Act carve-out for CDS software turns on, and the distinction a careful reader
deserves regardless of regulation.

Content is summarised for a lay reader. It is not sourced from a single
document and carries no clinical authority; `SOURCE_NOTE` says so in the
payload, and the interface repeats it. MedlinePlus (US National Library of
Medicine) publishes a free, per-test reference and is linked for each analyte.
"""

SOURCE_NOTE = (
    "General background only. Written for a lay reader and not specific to your "
    "results. Reference intervals are defined so that roughly 1 in 20 healthy "
    "people fall outside them, so a single out-of-range value is often not "
    "meaningful on its own. Only a clinician who knows your history can say what "
    "any of this means for you."
)

MEDLINEPLUS = "https://medlineplus.gov/lab-tests/"


def _mlp(slug: str) -> str:
    return f"{MEDLINEPLUS}{slug}/"


# loinc -> reference entry
REFERENCE: dict[str, dict] = {
    "2276-4": {
        "name": "Ferritin",
        "measures": "The protein that stores iron. It is the usual way to gauge how much iron the body has in reserve.",
        "low": "Commonly associated with depleted iron stores, which can follow blood loss, low dietary iron, pregnancy, or poor absorption. Ferritin usually falls before anaemia appears on a blood count.",
        "high": "Commonly associated with inflammation, infection, liver conditions, heavy alcohol use, or iron overload. Ferritin is an acute-phase protein, so it rises with inflammation even when iron stores are normal — which is why a high value is harder to read than a low one.",
        "link": _mlp("ferritin-test"),
    },
    "718-7": {
        "name": "Haemoglobin",
        "measures": "The protein in red blood cells that carries oxygen. It is the core measure of whether blood can move enough oxygen around the body.",
        "low": "Commonly associated with anaemia, which has many causes — iron, B12 or folate deficiency, blood loss, chronic illness, or reduced red cell production.",
        "high": "Commonly associated with dehydration (which concentrates the blood), living at altitude, smoking, lung conditions, or less often a bone marrow disorder.",
        "link": _mlp("hemoglobin-test"),
    },
    "4544-3": {
        "name": "Haematocrit",
        "measures": "The proportion of blood volume made up of red blood cells. It moves closely with haemoglobin and is read alongside it.",
        "low": "Commonly associated with anaemia, blood loss, or over-hydration.",
        "high": "Commonly associated with dehydration, altitude, smoking, or chronic lung conditions.",
        "link": _mlp("hematocrit-test"),
    },
    "2823-3": {
        "name": "Potassium",
        "measures": "An electrolyte essential to nerve signalling and muscle contraction, including the heart. The body holds it in a narrow range.",
        "low": "Commonly associated with diuretics, vomiting or diarrhoea, or certain kidney and hormonal conditions.",
        "high": "Commonly associated with reduced kidney function or certain medications. It is also the single most common false result on a blood test: if red cells break during collection or transit, potassium leaks out of them and the sample reads high while the person is fine. A high potassium with no symptoms is usually repeated before it is believed.",
        "link": _mlp("potassium-blood-test"),
    },
    "2951-2": {
        "name": "Sodium",
        "measures": "The main electrolyte governing the body's fluid balance. It is regulated tightly, so even small shifts carry meaning.",
        "low": "Commonly associated with excess fluid, certain medications, or heart, liver, kidney and hormonal conditions.",
        "high": "Commonly associated with dehydration or insufficient water intake.",
        "link": _mlp("sodium-blood-test"),
    },
    "2160-0": {
        "name": "Creatinine",
        "measures": "A waste product from normal muscle turnover, cleared by the kidneys. Because production is fairly steady, the blood level reflects how well the kidneys are filtering.",
        "low": "Commonly associated with low muscle mass; on its own it is rarely a concern.",
        "high": "Commonly associated with reduced kidney filtration, dehydration, or certain medications. It also runs higher in people with more muscle, so it is read against body size and against eGFR rather than alone.",
        "link": _mlp("creatinine-test"),
    },
    "2345-7": {
        "name": "Glucose",
        "measures": "The sugar circulating in blood, the body's immediate energy supply.",
        "low": "Commonly associated with fasting, certain diabetes medications, or less often hormonal conditions.",
        "high": "Commonly associated with diabetes or prediabetes — but also simply with having eaten recently. A glucose result means something quite different fasting than it does after a meal, so the collection conditions matter as much as the number.",
        "link": _mlp("blood-glucose-test"),
    },
    "41995-2": {
        "name": "Haemoglobin A1c",
        "measures": "The share of haemoglobin with glucose bound to it, which reflects average blood sugar over roughly the previous three months rather than the moment of the draw.",
        "low": "Uncommon; can accompany conditions that shorten red cell survival.",
        "high": "Commonly associated with prediabetes or diabetes. Because it averages three months, it cannot move quickly — a large jump between nearby draws usually points at the sample rather than at a real change.",
        "link": _mlp("hemoglobin-a1c-hba1c-test"),
    },
    "3016-3": {
        "name": "Thyrotropin (TSH)",
        "measures": "The pituitary hormone that tells the thyroid how much to produce. It moves opposite to thyroid activity, which is why the direction reads backwards at first glance.",
        "low": "Commonly associated with an overactive thyroid, or with over-replacement in someone taking thyroid medication.",
        "high": "Commonly associated with an underactive thyroid, or with under-replacement in someone taking thyroid medication.",
        "link": _mlp("tsh-thyroid-stimulating-hormone-test"),
    },
    "2132-9": {
        "name": "Vitamin B12",
        "measures": "A vitamin needed to make red blood cells and to maintain nerve function. The body cannot produce it, so it comes from diet or supplements.",
        "low": "Commonly associated with low dietary intake (more common on a vegetarian or vegan diet), absorption problems, or some long-term medications.",
        "high": "Usually reflects supplementation. Rarely investigated on its own.",
        "link": _mlp("vitamin-b12-test"),
    },
    "2093-3": {
        "name": "Total cholesterol",
        "measures": "All cholesterol carried in the blood, across every particle type. It is read alongside the HDL and LDL fractions rather than alone.",
        "low": "Rarely a concern by itself.",
        "high": "Commonly discussed as one contributor to cardiovascular risk, though the split between HDL and LDL matters more than the total.",
        "link": _mlp("cholesterol-levels"),
    },
    "2085-9": {
        "name": "HDL cholesterol",
        "measures": "Cholesterol carried in high-density particles, which move cholesterol away from tissue and back to the liver.",
        "low": "Commonly discussed as associated with higher cardiovascular risk.",
        "high": "Generally regarded as favourable.",
        "link": _mlp("cholesterol-levels"),
    },
    "13457-7": {
        "name": "LDL cholesterol",
        "measures": "Cholesterol carried in low-density particles. Most reports calculate rather than measure it, from the other lipid values.",
        "low": "Generally regarded as favourable.",
        "high": "Commonly discussed as associated with higher cardiovascular risk, and is the value most lipid treatment targets are written against.",
        "link": _mlp("cholesterol-levels"),
    },
    "2571-8": {
        "name": "Triglycerides",
        "measures": "The main form in which fat is carried and stored.",
        "low": "Rarely a concern by itself.",
        "high": "Commonly associated with recent eating, alcohol, weight, and blood sugar control. Because a meal moves this substantially, many labs ask for a fast before measuring it.",
        "link": _mlp("triglycerides-test"),
    },
    "6690-2": {
        "name": "White blood cells",
        "measures": "The cells of the immune system, counted per volume of blood.",
        "low": "Commonly associated with some viral infections, certain medications, or reduced bone marrow production.",
        "high": "Commonly associated with infection, inflammation, physical stress, or certain medications such as steroids.",
        "link": _mlp("white-blood-count-wbc"),
    },
    "777-3": {
        "name": "Platelets",
        "measures": "The cell fragments that form clots and stop bleeding.",
        "low": "Commonly associated with certain infections and medications, liver conditions, or reduced production. Clumping in the tube can also produce a falsely low count.",
        "high": "Commonly associated with inflammation, iron deficiency, or recovery after bleeding.",
        "link": _mlp("platelet-tests"),
    },
    "786-4": {
        "name": "MCHC",
        "measures": "The average concentration of haemoglobin inside red blood cells. It is a derived index from the blood count rather than a direct measurement.",
        "low": "Commonly seen alongside iron deficiency.",
        "high": "Uncommon; often a laboratory artefact rather than a physiological finding.",
        "link": _mlp("red-blood-cell-rbc-indices"),
    },
}


def reference_for(loinc_code: str | None) -> dict | None:
    """Background for one analyte, or None when nothing is held for it."""
    if not loinc_code:
        return None
    entry = REFERENCE.get(loinc_code)
    if entry is None:
        return None
    return {**entry, "source_note": SOURCE_NOTE}
