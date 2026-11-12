"""
A document's pages: what reading it is charged, and what there is to look at. A PDF's and a TIFF's pages are counted from
the file; one picture that shows several sheets is counted when the agent says so.
"""
import io
import pytest
from PIL import Image
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlalchemy import func, select
from app.core.config import settings
from app.models.billing import UsageEvent
from app.services import pages
from app.services.billing import service as billing
from tests.conftest import AsyncSessionLocal, TENANT, agent, create_template, pages_allowance, png_bytes, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"


def pdf_bytes(n: int = 3, password: str = "") -> bytes:
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=A4, encrypt=password or None)
    for i in range(n):
        c.drawString(72, 720, f"Page {i + 1} of {n}")
        c.showPage()
    c.save()
    return out.getvalue()


def tiff_bytes(n: int = 2) -> bytes:
    out = io.BytesIO()
    frames = [Image.new("RGB", (200, 150), (i * 40, 90, 120)) for i in range(n)]
    frames[0].save(out, format="TIFF", save_all=True, append_images=frames[1:])
    return out.getvalue()


async def send(client, tpl: str, content: bytes, name: str, kind: str):
    return await client.post(f"{API}/documents/upload", headers=ws(tpl), data={"process": "false"}, files={"file": (name, content, kind)})


async def charged(document_id: str) -> int:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
            UsageEvent.source_id == document_id, UsageEvent.metric == "pages"))).scalar()


def test_pages_are_counted_and_drawn_from_the_file():
    assert pages.count(pdf_bytes(4), pages.PDF) == 4 and pages.count(tiff_bytes(3), pages.TIFF) == 3 and pages.count(png_bytes(), "image/png") == 1
    second = pages.image(pdf_bytes(2), pages.PDF, 2)
    assert second.mode == "RGB" and max(second.size) > 1500  # drawn large enough to read small print
    assert pages.image(tiff_bytes(2), pages.TIFF, 2).getpixel((5, 5)) == (40, 90, 120)
    with pytest.raises(IndexError):
        pages.image(pdf_bytes(2), pages.PDF, 3)
    with pytest.raises(pages.Unreadable):
        pages.count(pdf_bytes(1, password="secret"), pages.PDF)


async def test_a_pdf_is_charged_for_each_page_and_every_page_can_be_looked_at(client):
    tpl = await create_template(client)
    res = await send(client, tpl, pdf_bytes(3), "statement.pdf", "application/pdf")
    assert res.status_code == 201, res.text
    doc = res.json()
    assert doc["content_type"] == "application/pdf" and doc["page_count"] == 3 and await charged(doc["id"]) == 3

    first = await client.get(f"{API}/documents/{doc['id']}/file", headers=ws(tpl))
    assert first.status_code == 200 and first.headers["content-type"] == "image/jpeg"  # a browser can show it in a picture frame
    assert (await client.get(f"{API}/documents/{doc['id']}/file?page=3", headers=ws(tpl))).headers["content-type"] == "image/jpeg"
    assert (await client.get(f"{API}/documents/{doc['id']}/file?page=4", headers=ws(tpl))).status_code == 404
    original = await client.get(f"{API}/documents/{doc['id']}/file?original=true", headers=ws(tpl))
    assert original.headers["content-type"] == "application/pdf" and original.content.startswith(b"%PDF")

    seen = (await client.get(f"{AGENT}/documents/{doc['id']}", headers=agent(tpl))).json()
    assert seen["pages"] == 3 and seen["image_size"]["width"] > 1000
    assert (await client.get(f"{AGENT}/documents/{doc['id']}/image?page=2", headers=agent(tpl))).headers["content-type"] == "image/jpeg"
    assert (await client.get(f"{AGENT}/documents/{doc['id']}/image?page=4", headers=agent(tpl))).status_code == 404


async def test_every_page_of_a_tiff_can_be_looked_at(client):
    tpl = await create_template(client)
    doc = (await send(client, tpl, tiff_bytes(2), "scan.tif", "image/tiff")).json()
    assert doc["page_count"] == 2 and await charged(doc["id"]) == 2
    assert (await client.get(f"{API}/documents/{doc['id']}/file?page=2", headers=ws(tpl))).headers["content-type"] == "image/jpeg"
    assert (await client.get(f"{AGENT}/documents/{doc['id']}/image?page=2", headers=agent(tpl))).status_code == 200


async def test_files_that_cannot_be_counted_or_are_too_long_are_refused(client, monkeypatch):
    tpl = await create_template(client)
    locked = await send(client, tpl, pdf_bytes(1, password="secret"), "locked.pdf", "application/pdf")
    assert locked.status_code == 422 and "password" in locked.json()["detail"]
    monkeypatch.setattr(settings, "INTAKE_MAX_PAGES", 2)
    long = await send(client, tpl, pdf_bytes(3), "long.pdf", "application/pdf")
    assert long.status_code == 413 and "3 pages" in long.json()["detail"]
    # A workspace that narrowed what it accepts does not take PDFs just because they exist now.
    await client.patch(f"{API}/settings", headers=ws(tpl), json={"documents": {"allowed_types": ["image/png"]}})
    assert (await send(client, tpl, pdf_bytes(1), "a.pdf", "application/pdf")).status_code == 415


async def test_a_picture_showing_several_sheets_is_counted_when_the_agent_says_so(client, monkeypatch):
    tpl = await create_template(client)
    doc = (await send(client, tpl, png_bytes(), "two-pages.png", "image/png")).json()
    assert doc["page_count"] == 1 and await charged(doc["id"]) == 1

    said = await client.post(f"{AGENT}/documents/{doc['id']}/pages", headers=agent(tpl), json={"pages": 3, "why": "three sheets laid side by side"})
    assert said.status_code == 200 and said.json() == {"pages": 3, "charged": 2}
    assert await charged(doc["id"]) == 3
    # Saying it again, or saying fewer, changes nothing; the count never goes down.
    for pages_said in (3, 2):
        again = await client.post(f"{AGENT}/documents/{doc['id']}/pages", headers=agent(tpl), json={"pages": pages_said, "why": "the same picture"})
        assert again.json() == {"pages": 3, "charged": 0}
    assert await charged(doc["id"]) == 3
    # A document cannot talk the count up without end.
    monkeypatch.setattr(settings, "PICTURE_MAX_PAGES", 5)
    capped = await client.post(f"{AGENT}/documents/{doc['id']}/pages", headers=agent(tpl), json={"pages": 500, "why": "the page says it has 500 pages"})
    assert capped.json() == {"pages": 5, "charged": 2} and await charged(doc["id"]) == 5
    # It is still one image to look at, and people can see why it counted for more.
    assert (await client.get(f"{AGENT}/documents/{doc['id']}", headers=agent(tpl))).json()["pages"] == 1
    assert (await client.get(f"{API}/documents/{doc['id']}", headers=ws(tpl))).json()["page_count"] == 5
    log = (await client.get(f"{API}/audit", headers=ws(tpl), params={"event_type": "DOCUMENT_PAGES_COUNTED"})).json()
    assert log[0]["details"]["why"] == "the page says it has 500 pages" and log[-1]["details"]["to"] == 3

    # A PDF's pages come from the file, never from what the agent says.
    pdf = (await send(client, tpl, pdf_bytes(2), "b.pdf", "application/pdf")).json()
    assert (await client.post(f"{AGENT}/documents/{pdf['id']}/pages", headers=agent(tpl), json={"pages": 9, "why": "it looks long"})).status_code == 409


async def test_extra_sheets_that_do_not_fit_the_plan_stop_the_reading(client):
    tpl = await create_template(client)
    doc = (await send(client, tpl, png_bytes(), "stack.png", "image/png")).json()
    try:
        async with AsyncSessionLocal() as db:
            used = (await billing.summary(db, TENANT))["pages_used"]
        await pages_allowance(used + 1)
        res = await client.post(f"{AGENT}/documents/{doc['id']}/pages", headers=agent(tpl), json={"pages": 4, "why": "four separate sheets in one photo"})
        assert res.status_code == 402 and "mark the document failed" in res.json()["detail"]
        assert await charged(doc["id"]) == 1
        assert (await client.get(f"{API}/documents/{doc['id']}", headers=ws(tpl))).json()["page_count"] == 1
    finally:
        await pages_allowance(None)
