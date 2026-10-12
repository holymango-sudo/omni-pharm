"""처방전·약봉투 사진으로 약 입력: a photo -> the local vision LLM reads the medicine lines -> each line is matched to a
product of the drug database (OCR slips such as '레보스투스정' are expected) -> the user confirms what is added.

The photo never leaves this PC and is not stored: it is decoded in memory, EXIF (GPS, camera) is dropped by re-encoding,
and the model is told to output medicine lines only - no patient name, ID number or clinic.
"""

from __future__ import annotations

import base64
import difflib
import io
import re
import unicodedata

from omnipharm.engine import ResolveError

MAX_SIDE = 1600  # px: enough for printed Korean text, small enough for a fast answer

PROMPT = ("이 사진은 한국의 처방전, 약국 약봉투, 복약안내문 또는 약 포장입니다. 사진에 적힌 의약품만 찾아 JSON으로 답하세요.\n"
          "- name: 사진에 적힌 약 이름 그대로(제품명, 함량, 괄호 안 성분명 포함). 글자가 흐려도 보이는 대로 쓰고, 없는 약을 지어내지 마세요.\n"
          "- per_dose: 1회 투약량(예: '1정', '1캡슐', '1포', '10mL', '0.5'), times_per_day: 1일 투여 횟수, days: 총 투약일수,\n"
          "  timing: 용법(예: '식후 30분', '아침 식후', '필요시')\n"
          "- 사진에 적혀 있지 않은 값은 넣지 마세요(추측 금지).\n"
          "- 환자 이름, 주민등록번호, 병원·약국 이름, 전화번호, 의사 이름은 절대 출력하지 마세요.\n"
          "- document: '처방전', '약봉투', '약 포장', '기타' 중 하나. 약이 없으면 medicines는 빈 배열.")
SCHEMA = {"type": "object", "required": ["medicines"], "properties": {
    "document": {"type": "string"},
    "medicines": {"type": "array", "items": {"type": "object", "required": ["name"], "properties": {
        "name": {"type": "string"}, "per_dose": {"type": "string"}, "times_per_day": {"type": "number"},
        "days": {"type": "number"}, "timing": {"type": "string"}}}}}}


def prepare_image(data: str, meta: dict | None = None) -> str:
    """Base64 / data-URL image -> base64 JPEG, upright (EXIF orientation applied), MAX_SIDE px, no EXIF.
    A smaller photo is enlarged and lightly sharpened (meta["enlarged"] = True): on the photo evaluation set
    (scripts/eval_rx_photo.py, tuned on the dev set) this raised the lines read from photos taken from far away
    from 62% to 93%. Raises ValueError for anything that is not an image."""
    from PIL import Image, ImageFilter, ImageOps, UnidentifiedImageError

    raw = data.split(",", 1)[1] if data.startswith("data:") else data
    try:
        img = Image.open(io.BytesIO(base64.b64decode(raw, validate=False)))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except (UnidentifiedImageError, ValueError, OSError) as e:
        raise ValueError("사진 파일을 읽을 수 없습니다 (JPG·PNG 사진을 올려 주세요)") from e
    if min(img.size) < 200:
        raise ValueError("사진이 너무 작습니다 — 약 이름이 크게 보이게 다시 찍어 주세요")
    if max(img.size) < MAX_SIDE:  # a small photo (taken from far away, or a messenger thumbnail): enlarge the text
        f = MAX_SIDE / max(img.size)
        img = img.resize((round(img.width * f), round(img.height * f)), Image.LANCZOS)
        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
        if meta is not None:
            meta["enlarged"] = True
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=88)  # re-encoding drops EXIF (location, device)
    return base64.b64encode(out.getvalue()).decode()


# ---------------------------------------------------------------------------------------------------- dose
_UNIT = {"정": "unit", "알": "unit", "캡슐": "unit", "포": "unit", "봉": "unit", "병": "unit", "앰플": "unit", "개": "unit",
         "ml": "mL", "밀리리터": "mL", "cc": "mL", "g": "g", "그램": "g", "mg": "mg", "밀리그램": "mg"}


def parse_dose(item: dict) -> dict | None:
    """{'per_dose': '1정', 'times_per_day': 3, 'days': 3, 'timing': '식후'} -> a Dose dict (None if no amount)."""
    m = re.search(r"(\d+(?:\.\d+)?|½|1/2)\s*([가-힣a-zA-Z]*)", str(item.get("per_dose") or ""))
    if not m:
        return None
    amount = 0.5 if m[1] in ("½", "1/2") else float(m[1])
    if not 0 < amount <= 1000:
        return None
    unit = _UNIT.get(m[2].lower(), "unit")
    per_day = item.get("times_per_day")
    days = item.get("days")
    timing = str(item.get("timing") or "")
    dose = {"amount": amount, "unit": unit, "per_day": float(per_day) if per_day and 0 < per_day <= 24 else 1.0,
            "days": int(days) if days and 0 < days <= 3650 else None, "prn": bool(re.search(r"필요\s*시|통증\s*시|열\s*날", timing))}
    return dose


def dose_text(item: dict) -> str:
    parts = [f"1회 {item['per_dose']}" if item.get("per_dose") else None,
             f"하루 {item['times_per_day']:g}회" if item.get("times_per_day") else None,
             f"{item['days']:g}일" if item.get("days") else None, item.get("timing") or None]
    return " · ".join(p for p in parts if p)


# ---------------------------------------------------------------------------------------------------- matching
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").lower()
    s = s.replace("밀리그람", "밀리그램").replace("mg", "밀리그램").replace("ml", "밀리리터")
    return re.sub(r"[\s\-·,./]", "", s)


def _base(s: str) -> str:
    return _norm(re.sub(r"\(.*?\)|\[.*?\]", "", s))


def _score(raw: str, cand: str) -> float:
    """Similarity of the PRODUCT names (brackets removed): a shared '(레보드로프로피진)' must not make 레보스정 look like
    레보투스정. The bracketed ingredient only adds a small bonus when the candidate contains it."""
    rb, cb = _base(raw), _base(cand)
    s = difflib.SequenceMatcher(None, rb, cb).ratio() if rb and cb else \
        difflib.SequenceMatcher(None, _norm(raw), _norm(cand)).ratio()
    if rb and cb.startswith(rb) and len(rb) >= 3:  # '노바스크' -> '노바스크정5밀리그람': a short name on a medicine bag
        s = max(s, 0.82)
    inner = [_norm(x) for x in re.findall(r"\(([^)]*)\)", raw) if len(x.strip()) >= 3]
    if any(i[:4] in _norm(cand) for i in inner):
        s += 0.05
    return min(s, 1.0)


def match_name(eng, raw: str) -> dict:
    """One medicine line -> {"status": "ok"|"check"|"none", "name": product to add, "candidates": [...], ...}."""
    raw = re.sub(r"\s+", " ", raw or "").strip()
    inner = [x.strip() for x in re.findall(r"\(([^)]*)\)", raw) if x.strip()]
    base = re.sub(r"\(.*?\)|\[.*?\]", "", raw).strip()
    pool: dict[str, dict] = {}
    b = re.sub(r"\s", "", base)
    # prefixes find '노바스크…'; 3-letter pieces from inside find the product when an OCR slip sits at the front
    # ('레보스투스정' -> '투스정' -> 레보투스정); the ingredient in brackets finds its products
    windows = [b[i:i + 3] for i in range(max(len(b) - 2, 0))]
    for q in dict.fromkeys([raw, base, b[:5], b[:4], b[:3], b[:2], *windows, *inner]):
        if len(q) >= 2:
            for h in eng.search(q, 25):
                pool.setdefault(h["name"], h)
    ranked = sorted(pool, key=lambda n: -_score(raw, n))
    best = ranked[0] if ranked else None
    score = _score(raw, best) if best else 0.0
    out = {"raw": raw, "candidates": [{"name": n, "sub": pool[n].get("sub", ""), "type": pool[n].get("type", "")}
                                      for n in ranked[:6]]}
    # two different products almost equally close ('레보스정' / '레보투스정'): the user must choose
    close = len(ranked) > 1 and _score(raw, ranked[1]) >= score - 0.04 and _base(ranked[1]) != _base(best)
    if best and score >= 0.6:
        try:
            ings, info = eng.resolve_full(best)
        except ResolveError as e:  # a short brand on a bag ('노바스크'): the closest of its products
            cands = [c["name"] for c in e.candidates]
            if not cands:
                return {**out, "status": "none", "name": None, "score": round(score, 2)}
            best = max(cands, key=lambda n: _score(raw, n))
            ings, info = eng.resolve_full(best)
        return {**out, "status": "ok" if score >= 0.8 and not close else "check", "name": info.get("product") or best,
                "ingredients": [eng.ko(g) for g in ings], "score": round(score, 2)}
    for ing in inner:  # the product is unknown but the ingredient in brackets is ('…정(아세클로페낙)')
        try:
            ings, _ = eng.resolve_full(ing)
            return {**out, "status": "check", "name": ing, "ingredients": [eng.ko(g) for g in ings], "score": 0.6,
                    "by_ingredient": True}
        except ResolveError:
            continue
    return {**out, "status": "none", "name": None, "score": round(score, 2)}


def match_all(eng, medicines: list[dict]) -> list[dict]:
    seen, out = set(), []
    for m in medicines[:20]:
        name = str(m.get("name") or "").strip()
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        hit, dose = match_name(eng, name), parse_dose(m)
        # a bare '10' on a syrup line means 10 mL (a bare '1' on an oral-liquid vial is 1 vial)
        if dose and dose["amount"] >= 2 and not re.search(r"[가-힣a-zA-Z]", str(m.get("per_dose") or "")) \
                and re.search(r"시럽|현탁|액|드롭", hit.get("name") or name):
            dose["unit"] = "mL"
        out.append({**hit, "dose": dose, "dose_text": dose_text(m)})
    return out
