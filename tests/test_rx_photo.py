"""처방전·약봉투 사진 입력: image preparation, dose parsing, matching OCR'd names, and (when the local vision model runs)
reading the fake prescription / medicine-bag photos in tests/fixtures/rx."""

from __future__ import annotations

import asyncio
import base64
import io
from pathlib import Path

import httpx
import pytest

from omnipharm import rx_photo
from omnipharm.config import settings
from omnipharm.models import llm

FIX = Path(__file__).parent / "fixtures" / "rx"


def _png(w: int, h: int) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def test_prepare_image():
    out = base64.b64decode(rx_photo.prepare_image(_png(3000, 2000)))
    assert out[:3] == b"\xff\xd8\xff"  # re-encoded JPEG (EXIF dropped)
    from PIL import Image

    assert max(Image.open(io.BytesIO(out)).size) == rx_photo.MAX_SIDE
    with pytest.raises(ValueError):
        rx_photo.prepare_image("data:image/png;base64," + "A" * 300)  # not an image
    with pytest.raises(ValueError):
        rx_photo.prepare_image(_png(100, 80))  # too small to read


def test_parse_dose():
    d = rx_photo.parse_dose({"per_dose": "1정", "times_per_day": 3, "days": 5, "timing": "식후 30분"})
    assert d == {"amount": 1.0, "unit": "unit", "per_day": 3.0, "days": 5, "prn": False}
    assert rx_photo.parse_dose({"per_dose": "10mL", "times_per_day": 3})["unit"] == "mL"
    assert rx_photo.parse_dose({"per_dose": "½", "timing": "통증 시 필요시"}) == {
        "amount": 0.5, "unit": "unit", "per_day": 1.0, "days": None, "prn": True}
    assert rx_photo.parse_dose({"per_dose": "적당량"}) is None
    assert rx_photo.dose_text({"per_dose": "1", "times_per_day": 3, "days": 3}) == "1회 1 · 하루 3회 · 3일"


needs_models = pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(),
                                  reason="v2 artifacts not built")


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


@needs_models
@pytest.mark.slow
def test_ocr_names_are_matched(eng):
    meds = [{"name": n, "per_dose": "1", "times_per_day": 3, "days": 3} for n in (
        "에어탈정(아세클로페닉)", "레보스투스정(레보드로프로피진)", "노바스크정5밀리그램", "노바스크",
        "무코스탄정(레바미피드)", "알 수 없는 약정")]
    meds.append({"name": "코푸시럽", "per_dose": "10", "times_per_day": 3})
    r = {x["raw"]: x for x in rx_photo.match_all(eng, meds)}
    assert r["에어탈정(아세클로페닉)"]["status"] == "ok" and r["에어탈정(아세클로페닉)"]["name"].startswith("에어탈정")
    assert r["레보스투스정(레보드로프로피진)"]["name"].startswith("레보투스정")  # OCR slip, not the other 레보스정
    assert r["노바스크정5밀리그램"]["name"].startswith("노바스크정5밀리그람")  # 그램/그람 spelling
    assert r["노바스크"]["status"] == "ok"  # short name on a medicine bag
    assert r["무코스탄정(레바미피드)"]["status"] == "check"  # two products equally close: the user chooses
    assert any(c["name"].startswith("무코스타정") for c in r["무코스탄정(레바미피드)"]["candidates"])
    assert r["알 수 없는 약정"]["status"] == "none"
    assert r["코푸시럽"]["dose"]["unit"] == "mL"  # '10' on a syrup line


def _ollama_vision() -> bool:
    try:
        tags = httpx.get(f"{settings.ollama_url}/api/tags", timeout=2).json()
        return any(m["name"] == settings.llm_model for m in tags.get("models", []))
    except Exception:
        return False


@needs_models
@pytest.mark.slow
@pytest.mark.skipif(not _ollama_vision(), reason="local LLM (Ollama) not running")
@pytest.mark.parametrize("photo,expect", [
    ("prescription_photo.jpg", ["에어탈정", "무코스타정", "뮤테란캡슐", "레보투스정", "슈다페드정"]),
    ("medicine_bag_photo.jpg", ["노바스크정5", "아토르바정20", "리리카캡슐75"]),
])
def test_vision_reads_the_photos(eng, photo, expect):
    img = rx_photo.prepare_image(base64.b64encode((FIX / photo).read_bytes()).decode())
    data = asyncio.run(llm.vision_json(rx_photo.PROMPT, rx_photo.SCHEMA, img))
    items = rx_photo.match_all(eng, data["medicines"])
    names = [i["name"] or "" for i in items] + [c["name"] for i in items for c in i["candidates"][:3]]
    found = [e for e in expect if any(n.startswith(e) for n in names)]
    assert len(found) >= len(expect) - 1, (expect, [i["raw"] for i in items])  # one OCR miss is tolerated
    assert "홍길동" not in str(data) and "800101" not in str(data)  # no patient identifiers in the answer
    assert all(i["dose"] for i in items if i["status"] == "ok")
