"""LLM fact sheet + rule-based fallback report (no Ollama needed)."""

from __future__ import annotations

from omnipharm.models import llm


def test_prune_drops_empty_fields():
    assert llm._prune({"a": None, "b": [], "c": {"d": ""}, "e": [1, None, []], "f": "x"}) == {"e": [1], "f": "x"}


def test_template_report_handles_minimal_facts():
    text = llm.template_report({"종합등급": "낮음"})
    assert "비교적 안전" in text and "### 4." in text


def test_template_report_full():
    facts = {"종합등급": "높음", "판정이유": ["A와 B는 식약처 DUR 병용금기 성분 조합입니다."],
             "권고": ["A와 B를 함께 복용하지 마세요."],
             "약물조합": [{"조합": "A + B", "등급": "높음", "식약처DUR병용금기": "QT 연장",
                       "FDA라벨": ["[상호작용] ... avoid ..."], "임상규칙·약리기전": ["QT 연장 약 병용"]}],
             "주요이상반응(환자모델)": [{"이상반응": "QT 간격 연장", "환자맞춤확률": "5.0%", "일반보고대비": "10.0배",
                                  "표준환자대비": "1.2배"}]}
    text = llm.template_report(facts)
    assert "위험" in text and "DUR 병용금기: QT 연장" in text and "함께 복용하지 마세요" in text
