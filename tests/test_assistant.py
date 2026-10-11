"""AI 복약 상담 (chat grounded on one analysis) and '증상으로 약 찾기' (OTC suggestions screened for the patient)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from omnipharm import api, recommend
from omnipharm.clinical import Patient
from omnipharm.config import settings
from omnipharm.models import llm


def test_emergency_words_are_caught():
    for q in ("숨이 차요", "약을 한꺼번에 10알 먹었어요", "검은 변이 나와요", "입술이 부었어요", "죽고 싶어요", "가슴이 조여요"):
        assert llm.EMERGENCY.search(q), q
    for q in ("자몽주스 마셔도 되나요?", "언제 먹어요?", "술 마셔도 돼요?"):
        assert not llm.EMERGENCY.search(q), q


def test_fallback_answer_uses_the_facts_only():
    ctx = {"판정이유": ["심바스타틴 + 자몽: 자몽(주스)이 약의 혈중 농도를 크게 올립니다."], "권고": ["자몽을 피하세요."],
           "약동학(복용 후 시간)": ["심바스타틴: 복용 후 최고 농도 약 1.9시간"]}
    a = llm.chat_fallback(ctx, "자몽 마셔도 돼요?")
    assert "자몽" in a and "약사" in a
    assert "1.9시간" in llm.chat_fallback(ctx, "몇 시간 간격을 둬요?")


def test_symptom_catalog_and_api_validation():
    ids = {s["id"] for s in recommend.catalog()}
    assert {"fever", "cough", "runny_nose", "headache", "heartburn", "diarrhea"} <= ids
    c = TestClient(api.app)
    assert c.post("/api/v1/chat", json={"report_id": "zzzzzzzz", "question": "?"}).status_code in (404, 503)
    assert c.post("/api/v1/chat", json={"report_id": "zzzzzzzz", "question": "x" * 700}).status_code == 422


needs_models = pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(),
                                  reason="v2 artifacts not built")
ADULT = {"age": 40, "sex": "M", "weight_kg": 70, "height_cm": 175, "scr": 0.9}


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


def rec(eng, symptoms, drugs=(), **pt):
    cur = [{"input": n, "ingredients": eng.resolve(n)} for n in drugs]
    r = recommend.recommend(eng, symptoms, cur, Patient(**{**ADULT, **pt}))
    return {o["key"]: o for s in r["symptoms"] for o in s["options"]}, r


@needs_models
@pytest.mark.slow
def test_suggestions_are_screened_for_the_patient(eng):
    o, r = rec(eng, ["fever", "runny_nose", "cough"])
    assert o["acetaminophen"]["status"] == "ok" and o["acetaminophen"]["products"]
    assert any("종합감기약" in n for n in r["notes"])
    assert all(not any(w in p["full"] for w in ("주사", "앰플")) for v in o.values() for p in v["products"])
    assert o["dextromethorphan"]["products"] and o["dextromethorphan"]["products"][0]["combo"]  # sold only in combinations
    o, _ = rec(eng, ["fever"], ["warfarin"])
    assert o["ibuprofen"]["status"] in ("caution", "avoid") and "출혈" in o["ibuprofen"]["reasons"][0]["text"]
    o, _ = rec(eng, ["fever"], ["타이레놀정500밀리그람"])
    assert o["acetaminophen"]["status"] == "avoid"  # the same ingredient is already taken
    o, _ = rec(eng, ["headache"], conditions=["peptic_ulcer"])
    assert o["ibuprofen"]["status"] == "avoid" and o["acetaminophen"]["status"] == "ok"
    o, _ = rec(eng, ["runny_nose", "insomnia"], age=78)
    assert o["chlorpheniramine"]["status"] == "avoid" and o["doxylamine"]["status"] == "avoid"
    assert o["cetirizine"]["status"] != "avoid"
    o, _ = rec(eng, ["period"], sex="F", conditions=["pregnancy"])
    assert o["ibuprofen"]["status"] == "avoid" and o["acetaminophen"]["status"] == "ok"


def test_fallback_shows_the_quick_checks_first():
    found = [{"확인한 제품": "박카스디액 = 카페인(caffeine)", "함께 먹을 때 판정": "주의",
              "이 제품 관련 이유": ["와파린(warfarin)과(와) 카페인(caffeine)은(는) 서로 영향을 주는 조합"]}]
    a = llm.chat_fallback({"판정이유": ["다른 이유"]}, "박카스 먹어도 돼요?", found)
    assert "박카스디액" in a and "주의" in a and "카페인" in a and "다른 이유" not in a
    assert "[방금 확인한 결과]" in llm.lookup_block(found) and llm.lookup_block([]) == ""


def test_general_chat_needs_no_analysis():
    ctx = llm.general_context({"diseases": ["고혈압"]}, {"age": 70})
    assert "분석하지 않았" in ctx["상태"] and ctx["환자"]["age"] == 70


@needs_models
@pytest.mark.slow
def test_items_named_in_a_question_are_found(eng):
    from omnipharm import assistant

    f = assistant.find_items
    assert f(eng, "박카스 먹어도 되나요?")[0].startswith("박카스")
    assert f(eng, "박카스랑 같이 마셔도 돼요?")[0].startswith("박카스")
    assert f(eng, "술 한잔은 괜찮아요?") == ["술"]
    assert f(eng, "자몽주스 마셔도 돼요?") == ["자몽주스"]
    assert f(eng, "와파린 먹는데 아스피린 먹어도 돼요?") == ["와파린", "아스피린"]
    for q in ("감기약 먹어도 되나요?", "비타민 먹어도 돼요?", "언제 먹어야 해요?", "부작용이 뭐예요?", "오늘 날씨 어때?"):
        assert f(eng, q) == [], q


@needs_models
@pytest.mark.slow
def test_quick_check_runs_the_engine_with_the_analysis(eng):
    from omnipharm import assistant

    base = [{"input": n, "ingredients": eng.resolve(n)} for n in ("warfarin", "acetaminophen")]
    old = Patient(**{**ADULT, "age": 70})
    c = assistant.quick_check(eng, base, "아스피린", old, "주의")
    assert c["함께 먹을 때 판정"].startswith(("주의", "위험"))
    assert any("출혈" in x for x in c["이 제품 관련 이유"])
    assert all("아스피린" in x for x in c["이 제품 관련 이유"])  # only findings that involve the new item
    assert "노인주의 — ." not in " ".join(c["이 제품 관련 이유"])  # empty DUR content gets a sentence
    alc = assistant.quick_check(eng, base, "술", old, "주의")
    assert any("와파린" in x for x in alc["상호작용"]) and "임상 규칙" in alc["근거종류"]
    assert assistant.quick_check(eng, base, "타이레놀", old) is None  # already in the analysis
    alone = assistant.quick_check(eng, [], "박카스", Patient(**ADULT))
    assert alone["확인한 제품"].startswith("박카스") and "하나만" in alone["함께 먹을 때 판정"]
    assert assistant.lookups(eng, "오늘 날씨 어때?", base, old) == []


@needs_models
@pytest.mark.slow
def test_chat_context_is_grounded(eng):
    prods = [{"input": n, "ingredients": eng.resolve(n)} for n in ("simvastatin", "자몽주스", "acetaminophen")]
    res = eng.predict(prods, Patient(**ADULT))
    res["products"] = [{"input": p["input"], "product": None, "ingredients": [eng.ko(g) for g in p["ingredients"]]} for p in prods]
    ctx = llm.chat_context(res, ADULT)
    assert any("4,000mg" in x for x in ctx["1일최대량(허가기준·이 환자)"])
    assert "식약처 DUR" not in ctx["근거종류(이것만 출처로 쓸 것)"]  # grapefruit is not a DUR item: may not be cited
    assert any("심바스타틴" in x and "반감기" in x for x in ctx["약동학(복용 후 시간)"])
