"""A whole panel on one time axis.

The claim to earn is narrow and worth stating: the *only* thing shared between
these analytes is when they were drawn. Everything else — values, units,
reference bands — stays per-analyte, because there is no honest common scale
for potassium and cholesterol and inventing one would invite reading the height
of one against the other.
"""

from pathlib import Path

import pytest

from app.models.observation import Observation
from app.worker import process_document

pytestmark = pytest.mark.asyncio

PDF = (Path(__file__).parent / "fixtures" / "quest_style.pdf").read_bytes()


async def loaded(client, account):
    """One processed document. Returns (headers, patient_id)."""
    h, pid = account
    doc_id = (await client.post(f"/api/documents/{pid}", headers=h,
              files={"file": ("q.pdf", PDF, "application/pdf")})).json()["id"]
    await process_document({}, doc_id)
    return h, pid


async def test_a_panel_returns_every_analyte_it_holds(client, account):
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/panel-trends?panel=metabolic",
                          headers=h)).json()

    assert d["panel"] == "metabolic"
    assert d["panel_label"] == "Metabolic panel"
    assert d["tracks"], "the fixture holds metabolic analytes"
    for t in d["tracks"]:
        assert t["loinc_code"]
        # No normalised field, ever. Values stay in the analyte's own unit.
        assert "normalised" not in t and "normalized" not in t


async def test_an_analyte_with_nothing_chartable_is_still_returned(client, account):
    """A panel that quietly shrinks to the rows that happened to convert is the
    silent-omission failure the pipeline exists to prevent. An analyte that
    dropped out entirely is a finding, so it comes back with zero points and a
    count of what was excluded."""
    h, pid = await loaded(client, account)

    # Everything awaiting review is excluded from a chart by the same rule the
    # single-analyte series uses, so a fresh document has such rows.
    d = (await client.get(f"/api/observations/{pid}/panel-trends?panel=cbc",
                          headers=h)).json()
    empty = [t for t in d["tracks"] if not t["points"]]
    assert empty, "the fixture leaves some CBC rows pending review"
    for t in empty:
        assert t["excluded"] > 0, f"{t['loinc_code']} vanished without saying why"


async def test_the_time_axis_spans_every_track(client, account):
    """One x scale for the whole panel — that is the entire point of the view."""
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/panel-trends?panel=lipids",
                          headers=h)).json()

    dates = [p["collected_at"] for t in d["tracks"] for p in t["points"]
             if p["collected_at"]]
    assert dates
    assert d["first_at"][:19] == min(dates)[:19]
    assert d["last_at"][:19] == max(dates)[:19]


async def test_a_panel_agrees_with_the_single_analyte_chart(client, account):
    """Both go through `_charted`. Two screens disagreeing about whether a
    point is comparable would be worse than either being wrong alone: the
    reader would have no way to tell which to believe."""
    h, pid = await loaded(client, account)
    d = (await client.get(f"/api/observations/{pid}/panel-trends?panel=lipids",
                          headers=h)).json()
    track = next(t for t in d["tracks"] if t["points"])

    series = (await client.get(
        f"/api/observations/{pid}/series?loinc={track['loinc_code']}",
        headers=h)).json()

    assert [p["value"] for p in track["points"]] == [p["value"] for p in series["points"]]
    assert track["excluded"] == len(series["excluded"])
    assert track["unit"] == series["unit"]


async def test_a_pending_row_is_charted_by_neither(client, account):
    h, pid = await loaded(client, account)
    obs = await Observation.find_one(Observation.review_status == "pending",
                                     Observation.loinc_code != None)  # noqa: E711
    assert obs, "the fixture must leave something pending"

    d = (await client.get(
        f"/api/observations/{pid}/panel-trends?panel={_panel_of(obs.loinc_code)}",
        headers=h)).json()
    track = next((t for t in d["tracks"] if t["loinc_code"] == obs.loinc_code), None)
    assert track is not None
    assert all(p["observation_id"] != str(obs.id) for p in track["points"])


def _panel_of(code):
    from app.data.panels import panel_for
    return panel_for(code)[0]


async def test_an_unknown_panel_is_404_not_an_empty_chart(client, account):
    """An empty chart would read as "you have no results for this", which is a
    different and wrong statement."""
    h, pid = account
    r = await client.get(f"/api/observations/{pid}/panel-trends?panel=nonsense",
                         headers=h)
    assert r.status_code == 404


async def test_panel_trends_needs_a_grant(client, account):
    _, pid = await loaded(client, account)
    other = await client.post("/api/auth/register", json={
        "email": "nosy@example.com", "name": "N", "password": "correct-horse-battery"})
    h2 = {"Authorization": f"Bearer {other.json()['access_token']}"}

    r = await client.get(f"/api/observations/{pid}/panel-trends?panel=cbc", headers=h2)
    assert r.status_code == 404  # 404, never 403 — see access.py
