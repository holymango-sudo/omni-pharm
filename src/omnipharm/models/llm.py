"""Clinical report generation with a local LLM (Ollama), streamed token by token.

The LLM only *writes up* facts computed by the engine; the prompt hands it a compact fact sheet
and forbids inventing numbers. If Ollama is not running the same fact sheet is rendered by a
deterministic template, so the demo never shows an empty panel.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator

import httpx

from omnipharm.config import settings

log = logging.getLogger(__name__)

SYSTEM = """당신은 병원 임상약제부의 수석 임상약사입니다. 의사·약사에게 전달할 약물 상호작용 소견서를 한국어로 작성합니다.
규칙 (반드시 지킬 것):
- [분석 근거]에 있는 사실과 수치만 사용하세요. 근거에 없는 용량(mg 등), 검사 수치, 연구 결과, 약물명은 절대 쓰지 마세요.
- 대체 약물을 구체적인 약물명으로 추천하지 마세요. '대체 약물 검토(담당 의사·약사 판단)'라고만 쓰세요.
- 용량은 구체적 숫자 없이 '감량 또는 투여 간격 조절 검토'처럼 쓰세요.
- 일반적으로 확립된 약리 기전(CYP 대사, 단백결합, 약력학적 상가작용 등)은 설명해도 되지만 '알려진 기전'임을 밝히세요.
- 근거 종류를 구분하세요: 식약처 DUR(국내 공식 금기) · FDA 허가 라벨(문서화된 사실) > 임상 규칙·약리 기전 >
  실제 보고 신호(FAERS/TWOSIDES, 연관성) > AI 예측(추정).
- '환자맞춤확률'은 FAERS 이상사례 보고 기반 추정치이며 실제 발생률이 아님을 한 번 밝히세요.
- 'AI구조예측'의 %는 '그 약물쌍에서 해당 이상반응 신호가 보고될 가능성 점수'이지 환자에게 일어날 확률이 아닙니다.
  발생 확률처럼 쓰지 말고, '1. 핵심 경고'에는 넣지 마세요(핵심 경고는 종합등급의 근거인 DUR·허가 문서·임상 규칙·용량·질환 문제 위주).
- 종합등급이 '낮음'이면 불안을 키우지 말고, 상호작용이 없거나 작다는 점과 일반적인 복약 안내 위주로 짧게 쓰세요.
- '판정이유'·'권고'는 이 시스템의 결론입니다. 소견서는 이와 어긋나지 않게 쓰세요.
- 식약처 DUR 병용금기나 허가 문서 병용금기가 있으면 권고는 '병용을 피하고 대체 약물 검토'로 쓰세요(감량으로 해결된다고 쓰지 마세요).
- 근거 출처는 [분석 근거]에 실제로 있는 것만 밝히세요(예: '식약처DUR병용금기' 항목이 없으면 DUR을 언급하지 마세요).
- 형식: 마크다운. 섹션 제목은 '### '로 시작. 불릿은 '- '. 전체 300단어 이내."""

SECTIONS = """다음 4개 섹션으로 작성하세요:
### 1. 핵심 경고
### 2. 상호작용 기전
### 3. 환자 맞춤 분석 (체형·신장·간 기능, 연령, '질환별약물위험'의 기저질환-약 충돌, '용량점검'의 하루 총량·기간 문제를 반드시 설명)
### 4. 권고 사항 (모니터링 항목, 용량 조절, 대체 약물 검토 — 불릿 목록)"""
SECTIONS_SAFE = """종합등급이 '낮음'입니다. 다음 3개 섹션으로 짧게(150단어 이내) 작성하세요:
### 1. 결론 (의미 있는 상호작용이 확인되지 않았다는 점, 근거가 있으면 그 근거)
### 2. 참고 사항 (각 약의 흔한 부작용이나 '참고사항' 중 알아둘 것 1~3개 — 불안을 키우지 않게)
### 3. 복약 안내 (정해진 용법·용량 지키기, 새 증상이 생기면 상담 — 불릿 목록)"""


def ratio(x: float) -> str:
    """Ratios against tiny base rates explode (0.03% -> 20% = x700); beyond x50 the exact value is noise."""
    return "50배 이상" if x >= 50 else f"{x:.1f}배"


def patient_facts(p: dict, patient: dict) -> dict:
    """The patient part of the fact sheet. p = clinical.assess() of the patient, patient = the raw input."""
    pt = {k: patient.get(k) for k in ("age", "sex", "weight_kg", "height_cm", "ast", "alt", "total_bilirubin",
                                      "diagnosis", "symptoms") if patient.get(k) not in (None, "")}
    if p.get("body") and p["body"].get("bmi"):
        pt["체형"] = f"BMI {p['body']['bmi']} ({p['body']['bmi_cat']})"
    if p.get("kidney"):
        k = p["kidney"]
        pt["신기능"] = f"{k['clearance_kind']} {k['clearance']} mL/min (정상의 약 {k['pct_normal']}%), eGFR {k['egfr']}"
    if p.get("liver"):
        pt["간기능"] = p["liver"]["desc"]
    if p.get("capacity"):
        pt["약물처리능력"] = f"{p['capacity']['level']} ({', '.join(p['capacity']['factors']) or '특이사항 없음'})"
    pt["기저질환"] = p.get("diseases", [])
    return pt


def fact_sheet(res: dict, patient: dict) -> dict:
    """Compact, LLM-sized summary of an engine result."""
    pt = patient_facts(res["patient"], patient)
    top = [{"이상반응": e["ko"], "환자맞춤확률": f"{e['prob']:.1%}", "일반보고대비": ratio(e["lift"]),
            "표준환자대비": ratio(e["patient_factor"]), "주요기여약물": e.get("driver")}
           for e in res["events"][:6] if e["lift"] >= 1.2]
    def label_line(e: dict) -> str | None:
        if e.get("context") == "co_therapy":
            return None  # co-therapy guidance / cross-references are not interaction evidence
        tag = "[상호작용 없음으로 기재] " if e.get("context") == "no_interaction" else ""
        return f"{tag}[{e['section']}] {e['excerpt'][:350]}"

    pairs = []
    for r in res["pairs"][:6]:
        labels = [x for x in (label_line(e) for e in r["label_evidence"]) if x][:2]
        pairs.append({"조합": f"{r['a']} + {r['b']}", "등급": r["level"],
                      "식약처DUR병용금기": (r["dur"].get("content") or "해당") if r.get("dur") else None,
                      "FDA라벨": labels,
                      "임상규칙·약리기전": [m["text"] for m in r.get("mechanism", [])][:3],
                      "FAERS관찰신호(참고)": [e["ko"] for e in r["observed"]][:8],
                      "AI구조예측(참고)": ("분자 구조가 없는 성분(생물의약품·생약 등)이라 대상 아님" if r.get("biologic") else
                                     [f"{m['ko']} (신호 점수 {m['prob']:.0%})" for m in r["model"][:5]])})
    h = res.get("headline")
    if h:
        h = {**h, "lift": ratio(h["lift"]), "patient_factor": ratio(h["patient_factor"]), "prob": f"{h['prob']:.1%}"}
    s = res.get("summary") or {}
    facts = {"종합등급": res["overall_level"],
             # the engine's own conclusions: the report must agree with them
             "판정이유": [x["text"] for x in s.get("reasons", []) if x["sev"] != "low"],
             "참고사항": [x["text"] for x in s.get("reasons", []) if x["sev"] == "low"],
             "권고": s.get("advice", []), "헤드라인": h, "환자": pt, "환자경고": res["alerts"],
             "같은계열중복": [f"{d['class']}: {', '.join(d['ingredients'])}" for d in res.get("class_duplicates", [])],
             "질환별약물위험": [f"[{d['severity']}] {d['disease']} + {d['drug']}: {d['reason']}"
                          for d in res.get("disease_alerts", [])][:8],
             "용량점검": [f"{r['display']} 하루 {r['daily_mg']:g}mg (1일 최대 {r['max_mg']:g}mg, {r['status']})"
                      for r in (res.get("dosing") or {}).get("ingredients", []) if r.get("max_mg")]
                     + [f"[{a['sev']}] {a['text']}" for a in (res.get("dosing") or {}).get("alerts", [])],
             "주요이상반응(환자모델)": top, "약물조합": pairs}
    return _prune(facts)


def _prune(x):
    """Drop empty / None fields: an empty 'DUR' key invites the model to mention DUR anyway."""
    if isinstance(x, dict):
        return {k: v for k, v in ((k, _prune(v)) for k, v in x.items()) if v not in (None, "", [], {})}
    if isinstance(x, list):
        return [v for v in (_prune(i) for i in x) if v not in (None, "", [], {})]
    return x


def template_report(facts: dict) -> str:
    """Rule-based report when no local LLM is running (same facts, no free text)."""
    grade = {"높음": "위험", "주의": "주의", "낮음": "비교적 안전"}.get(facts.get("종합등급"), facts.get("종합등급"))
    lines = ["### 1. 핵심 판정", f"- 종합 판정: **{grade}**"]
    lines += [f"- {t}" for t in facts.get("판정이유", [])]
    lines += [f"- {a}" for a in facts.get("환자경고", [])]
    lines.append("### 2. 근거 요약")
    for p in facts.get("약물조합", []):
        if p.get("등급") == "낮음" and not p.get("FDA라벨"):
            continue
        lines.append(f"- **{p['조합']}** [{p['등급']}]")
        if p.get("식약처DUR병용금기"):
            lines.append(f"  - 식약처 DUR 병용금기: {p['식약처DUR병용금기']}")
        lines += [f"  - FDA 라벨: {t[:220]}" for t in p.get("FDA라벨", [])]
        lines += [f"  - 임상 규칙·기전: {t}" for t in p.get("임상규칙·약리기전", [])]
    lines += [f"- 같은 계열 중복: {d}" for d in facts.get("같은계열중복", [])]
    lines.append("### 3. 환자 맞춤 분석")
    lines += [f"- {d}" for d in facts.get("질환별약물위험", [])]
    lines += [f"- 용량: {d}" for d in facts.get("용량점검", [])]
    for e in facts.get("주요이상반응(환자모델)", [])[:4]:
        lines.append(f"- {e['이상반응']}: {e['환자맞춤확률']} (일반 대비 {e['일반보고대비']}, "
                     f"표준 환자 대비 {e['표준환자대비']}) — FAERS 보고 기반 추정치(실제 발생률 아님)")
    lines.append("### 4. 권고 사항")
    lines += [f"- {a}" for a in facts.get("권고", [])] or ["- 정해진 용법·용량을 지키세요."]
    lines += ["", "_로컬 LLM이 실행 중이 아니어서 규칙 기반 요약으로 대체되었습니다._"]
    return "\n".join(lines)


_THINKING: dict[str, bool] = {}


async def _thinks(model: str) -> bool:
    """Does the Ollama model have a thinking mode (then it must be switched off for the report)?"""
    if model not in _THINKING:
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                r = await c.post(f"{settings.ollama_url}/api/show", json={"model": model})
                _THINKING[model] = "thinking" in r.json().get("capabilities", [])
        except Exception:
            return model.startswith(("qwen3", "gemma4"))
    return _THINKING[model]


async def stream_report(facts: dict) -> AsyncIterator[str]:
    sections = SECTIONS_SAFE if facts.get("종합등급") == "낮음" else SECTIONS
    prompt = (f"[분석 근거]\n```json\n{json.dumps(facts, ensure_ascii=False, indent=1)}\n```\n\n{sections}")
    body = {"model": settings.llm_model, "stream": True, "keep_alive": "30m",
            "options": {"temperature": 0.2, "num_ctx": 6144, "num_predict": 900},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}]}
    if await _thinks(settings.llm_model):  # reasoning models (qwen3, gemma4 ...): answer directly
        body["think"] = False
    import asyncio

    for attempt in range(2):  # the first call right after start-up can fail while the model is still loading
        sent = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0)) as c:
                async with c.stream("POST", f"{settings.ollama_url}/api/chat", json=body) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"Ollama HTTP {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        msg = json.loads(line)
                        tok = msg.get("message", {}).get("content", "")
                        if tok:
                            sent = True
                            yield tok
                        if msg.get("done"):
                            return
        except Exception as e:
            if attempt == 0 and not sent:
                await asyncio.sleep(4)
                continue
            log.warning(f"LLM unavailable ({e}); using template report")
            if not sent:
                yield template_report(facts)
            return


# --------------------------------------------------------------------------- AI 복약 상담 (questions about one analysis)
CHAT_SYSTEM = """당신은 OMNI-PHARM의 'AI 복약 상담'입니다. 친절하고 유능한 동네 약사처럼 한국어로 자연스럽게 대화합니다.
약·건강·증상·음식과 술·생활 습관·이 프로그램 사용법은 물론 가벼운 일상 대화까지 무엇이든 성의 있게 답하세요.
'답할 수 없다'며 거절하지 마세요. 약과 무관한 질문도 짧게 답한 뒤, 필요하면 복약 이야기로 자연스럽게 이어 가세요.
당신은 의사·약사가 아니며 진단·처방을 하지 않습니다.

근거 쓰는 법:
- [분석 결과]는 이 사람이 입력한 약 조합에 대해 이 시스템이 내린 결론입니다. 이 사람의 약에 대한 판단은 이와 어긋나면 안 됩니다.
- 질문 아래의 [방금 확인한 결과]는 질문에 나온 약·음식을 이 사람의 약(과 몸 상태)과 함께 방금 상호작용 엔진으로 검사한 것입니다.
  '○○ 먹어도 되나요?' 같은 질문은 이 결과의 판정·이유·할 일을 중심으로 분명하게 답하세요:
  위험 → 함께 먹지 말라고, 주의 → 무엇을 왜 조심할지, 비교적 안전 → 괜찮지만 지킬 점과 함께.
  확인한 제품이 질문한 이름과 다르면(예: 박카스 → 박카스디액) 어느 제품 기준인지 밝히세요.
- 질문에 나온 약이 [방금 확인한 결과]에 없으면 일반 지식으로 설명하되 이 사람에게 괜찮다고 단정하지 말고,
  약 목록에 추가해 '예측 시작'으로 확인해 보라고 권하세요.
- 작용 원리, 흔한 부작용, 복용 요령, 질환·증상 설명, 생활 습관 같은 일반 지식은 자유롭게 설명하되 일반 정보임을 드러내세요.

안전 규칙 (반드시):
- 숨이 차거나 의식이 흐림, 경련, 가슴 통증, 피를 토함·검은 변, 심한 알레르기(입술·혀 부음), 과량 복용, 자해 생각
  → 무엇보다 먼저 119 또는 응급실을 안내하세요.
- 처방약을 끊거나 바꾸라고 하지 마세요. '의사·약사와 상의'로 안내하세요. 단, 분석 결과에 병용금기가 있으면
  '임의로 끊지 마세요'보다 '이 두 약은 함께 먹으면 안 되는 조합'이라는 점을 먼저 분명히 말하고, 어느 약을 어떻게 할지
  (잠시 쉬는 것 포함)는 처방한 의사·약사에게 바로 확인하라고 안내하세요.
- 위험 판정(병용금기·DUR·피해야 함)은 그대로 전하세요. 괜찮다고 안심시키지 마세요. 병용금기 조합은 용량을 줄이거나
  복용 간격을 두면 된다고 말하지 말고, 함께 쓰지 않아야 하며 대신할 방법은 처방한 의사·약사와 정하라고 안내하세요.
- 근거에 없는 구체적 용량(mg·알 수)·검사 수치를 지어내지 마세요. 용량은 '1일최대량(허가기준·이 환자)'의 숫자나
  '제품 설명서의 용법'으로 말하고, 여러 제품의 같은 성분은 합쳐서 계산한다고 알려 주세요.
- 증상에 맞는 약을 고르려는 질문에는 성분 계열 정도만 일반적으로 설명하고, 화면의 '🤒 증상으로 약 찾기'로
  이 사람에게 맞는 후보를 확인하라고 안내하세요.
- 모르는 것은 모른다고 하고 약사·의사에게 확인하라고 하세요.

- '하루 몇 개(병·알)까지?' 질문은 '제품 설명서 용법(식약처)'이 있으면 그 용법(예: 1일 1회 1병, 15세 미만 복용 금지)을
  그대로 먼저 알려 주고, '1개당 함량'과 '1일최대량'이 있으면 그것으로 계산해 덧붙이세요. '스스로 사 먹을 때' 기준이
  있으면 그 숫자를 먼저 알려 주세요(더 높은 숫자는 의사 지시가 있을 때의 상한). 근거 줄에는 '허가 용량 기준'으로 쓰세요. 같은 성분이 든 다른 것과
  합쳐서 세야 한다고 알려 주되, 실제로 그 성분이 든 것만 예로 드세요(카페인 → 커피·에너지음료, 아세트아미노펜 → 종합감기약
  등 다른 해열진통제). 근거가 하나도 없으면 '제품 설명서의 용법'을 따르라고 하세요.

형식: 쉬운 말로 핵심부터. 짧은 질문에는 짧게, 보통 250단어 이내. 필요하면 짧은 불릿('- ').
대화 중이므로 매 답변을 인사('안녕하세요')나 자기소개로 시작하지 말고, 자신의 말투·역할을 설명하지 마세요.
LaTeX 수식($...$)은 쓰지 말고 화살표는 '→'로 쓰세요.
약·건강에 대한 답이면 마지막 줄에 '근거: ...'를 적되, '근거종류' 목록에 있는 이름만 쓰고(없는 출처를 지어내지 말 것,
[분석 결과]·[방금 확인한 결과] 같은 자료 묶음 이름도 쓰지 말 것)
일반 지식으로 답한 부분은 '일반 복약 상식'이라고 쓰세요. 일상 대화에는 근거 줄을 쓰지 마세요."""
EMERGENCY = re.compile(r"숨[이을]? ?(차|막|못)|호흡곤란|의식|정신을 잃|경련|발작|가슴[이 ]*(아프|통증|조여)|흉통|피를 토|토혈|"
                       r"검은 ?변|혈변|입술.*부|혀.*부|목이 붓|과다 ?복용|많이 먹었|한꺼번에.*먹|통째로.*먹었|죽고 싶|자살|자해")
EMERGENCY_TEXT = ("**⚠ 응급 신호일 수 있습니다.** 지금 숨이 차거나, 의식이 흐리거나, 경련·가슴 통증·피를 토하거나 검은 변, "
                  "입술·혀가 붓는 증상, 또는 약을 너무 많이 먹었다면 **바로 119에 전화하거나 응급실로 가세요.** "
                  "(약 중독 상담: 119 구급상황관리센터)\n\n")


def chat_context(res: dict, patient: dict) -> dict:
    """What the chat may rely on: the report facts + the time course / release form / suggestions of this analysis."""
    facts = fact_sheet(res, patient)
    j = res.get("pathway") or {}
    pk = []
    for d in j.get("drugs", []):
        p, r = d.get("pk") or {}, d.get("regimen") or {}
        rel = {"ER": "서방형", "DR": "장용형"}.get(d.get("release"), "")
        name = f"{d['name']}({rel})" if rel else d["name"]
        freq = "주 1회" if r.get("weekly") else f"하루 {r.get('per_day')}회"
        pk.append(f"{name}: 복용 후 최고 농도 약 {p.get('tmax_h')}시간, 반감기 약 {p.get('t_half_h')}시간, "
                  f"{freq}({r.get('source', '')}) [{p.get('source', '')}]")
    facts["약동학(복용 후 시간)"] = pk
    facts["몸속단계"] = [f"{s['title']}: {(s.get('time') or {}).get('label', '')} — {s['text'][:160]}" for s in j.get("stages", [])]
    c = j.get("clock") or {}
    if c:
        facts["시간축기준"] = (f"{c.get('name')} 기준, 반감기 {c.get('t_half_h')}시간"
                          + (f" → 이 조합에서 {c.get('t_half_eff_h')}시간" if abs((c.get('fold') or 1) - 1) > 0.15 else ""))
    facts["복용제품"] = [f"{p['input']} = {', '.join(p['ingredients'])}" for p in res.get("products", [])]
    # official Korean usage of quasi-drugs (박카스 '1일 1회 1병') - the per-bottle amount is not published
    facts["제품 설명서 용법(식약처)"] = [f"{p['input']}: {p['usage'][:200]}" for p in res.get("products", [])
                                if p.get("usage")]
    # labelled daily maximum for THIS patient (kidney / liver / age adjusted), so 'how many a day?' has a grounded answer
    from omnipharm.dosing import effective_limit, limit_text

    pa = res.get("patient") or {}
    ctx = {"crcl": (pa.get("kidney") or {}).get("clearance"), "age": patient.get("age"),
           "sex": (patient.get("sex") or "").upper()[:1] or None, "weight": patient.get("weight_kg"),
           "hepatic": bool((pa.get("flags") or {}).get("hepatic"))}
    form = {d["key"]: d.get("release") for d in j.get("drugs", [])}
    limits = []
    for ing in res.get("ingredients", []):
        lim = effective_limit(ing["key"], ctx, er=form.get(ing["key"]) == "ER")
        if lim:
            limits.append(limit_text(ing["display"], lim))
    facts["1일최대량(허가기준·이 환자)"] = limits
    # which kinds of evidence this analysis actually has: the answer may cite only these
    src = set()
    for p in res.get("pairs", []):
        if p["level"] == "낮음":
            continue
        if p.get("dur"):
            src.add("식약처 DUR")
        if any(e.get("context") != "co_therapy" for e in p.get("label_evidence", [])):
            src.add("FDA 허가 라벨")
        for m in p.get("mechanism", []):
            src.add("임상 규칙" if m["kind"] == "규칙" else "FDA CYP 지표표" if m["kind"] == "CYP-FDA" else "약리 기전(ChEMBL)")
    for d in res.get("disease_alerts", []):
        src.add("식약처 허가사항(제품 설명서)" if d.get("key") == "age_label" else "질환별 금기 기준(Beers·허가사항·DUR)")
    if facts["제품 설명서 용법(식약처)"]:
        src.add("식약처 허가사항(제품 설명서)")
    if (res.get("dosing") or {}).get("alerts") or limits:
        src.add("허가 용량 기준")
    if any(e.get("drives_verdict") for e in res.get("events", [])):
        src.add("AI 예측(부작용 보고 학습)")
    if j.get("drugs"):
        src.add("FDA 라벨 약동학(Tmax·반감기)")
    facts["근거종류(이것만 출처로 쓸 것)"] = sorted(src)
    return _prune(facts)


def general_context(assessed: dict, patient: dict) -> dict:
    """Chat before any analysis: only who the person is (age, kidney, liver, diseases)."""
    return _prune({"상태": "아직 약 조합을 분석하지 않았습니다 (약을 입력하고 '예측 시작'을 누르면 조합 분석이 생깁니다).",
                   "환자": patient_facts(assessed, patient), "근거종류(이것만 출처로 쓸 것)": []})


def lookup_block(found: list[dict]) -> str:
    """The quick checks of the medicines / foods named in the question, attached to that question."""
    if not found:
        return ""
    return "\n\n[방금 확인한 결과]\n```json\n" + json.dumps(_prune(found), ensure_ascii=False, indent=1) + "\n```"


def danger_note(ctx: dict, question: str) -> str:
    """A question about the analysed medicines of a '위험' analysis: the answer must open with that risk.
    (The small model sometimes answered 'do not stop it on your own' and left the contraindication out.)"""
    if ctx.get("종합등급") != "높음" or not ctx.get("판정이유"):
        return ""
    names = set()
    for p in ctx.get("복용제품", []):
        product, _, ings = p.partition(" = ")
        names.add(product.strip())
        names |= {i.split("(")[0].strip() for i in ings.split(",") if i.strip()}
    about = any(n and n in question for n in names) or re.search(r"이\s*약|두\s*약|약들|같이|함께", question)
    if not about:
        return ""
    risks = "\n".join(f"- {r}" for r in ctx["판정이유"][:2])
    return ("\n\n[이 질문은 분석한 약에 관한 것입니다. 답의 처음에 다음 위험(이 조합의 판정 '위험')을 분명히 전하고, "
            f"어떻게 할지는 처방한 의사·약사에게 바로 확인하라고 하세요:\n{risks}]")


def chat_fallback(ctx: dict, question: str, found: list[dict] | None = None) -> str:
    """No local LLM: answer from the facts with simple keyword routing (no free text)."""
    q = question
    lines = []
    for f in found or []:
        lines.append(f"- **{f['확인한 제품']}** → 함께 먹을 때: **{f['함께 먹을 때 판정']}**")
        lines += [f"  - {x}" for x in (f.get("이 제품 관련 이유") or f.get("이 제품 관련 참고") or [])[:3]]
    if not found:
        if re.search(r"언제|몇 ?시간|간격|시간|얼마나|오래", q):
            lines += [f"- {x}" for x in ctx.get("약동학(복용 후 시간)", [])[:4]]
        if re.search(r"술|자몽|우유|커피|담배|흡연|음식", q):
            lines += [f"- {x}" for x in ctx.get("판정이유", []) if re.search(r"술|자몽|우유|커피|흡연|녹색", x)][:4]
        if not lines:
            lines += [f"- {x}" for x in ctx.get("판정이유", [])[:4]]
    lines += [f"- 할 일: {a}" for a in ctx.get("권고", [])[:3]]
    return ("로컬 AI가 실행 중이 아니어서 분석 결과에서 관련 내용을 찾아 보여 드립니다.\n\n" + "\n".join(lines or ["- 관련 정보가 없습니다."])
            + "\n\n자세한 내용은 약사·의사와 상의하세요.")


async def stream_chat(ctx: dict, history: list[dict], question: str,
                      found: list[dict] | None = None) -> AsyncIterator[str]:
    """found = quick checks of the medicines / foods the question names (assistant.lookups)."""
    if EMERGENCY.search(question):
        yield EMERGENCY_TEXT
    if found:  # the evidence kinds of the quick checks may be cited too
        ctx = {**ctx, "근거종류(이것만 출처로 쓸 것)": sorted({*ctx.get("근거종류(이것만 출처로 쓸 것)", []),
                                                         *(s for f in found for s in f.get("근거종류", []))})}
    system = CHAT_SYSTEM + "\n\n[분석 결과]\n```json\n" + json.dumps(ctx, ensure_ascii=False, indent=1) + "\n```"
    msgs = [{"role": "system", "content": system}]
    for h in history[-8:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            msgs.append({"role": h["role"], "content": str(h["content"])[:2000]})
    msgs.append({"role": "user", "content": question + danger_note(ctx, question) + lookup_block(found or [])})
    body = {"model": settings.llm_model, "stream": True, "keep_alive": "30m", "messages": msgs,
            "options": {"temperature": 0.4, "num_ctx": 8192, "num_predict": 800}}
    if await _thinks(settings.llm_model):
        body["think"] = False
    import asyncio

    for attempt in range(2):  # right after start-up the model may still be loading into VRAM
        sent = False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=3.0)) as c:
                async with c.stream("POST", f"{settings.ollama_url}/api/chat", json=body) as r:
                    if r.status_code != 200:
                        raise RuntimeError(f"Ollama HTTP {r.status_code}")
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        msg = json.loads(line)
                        tok = msg.get("message", {}).get("content", "")
                        if tok:
                            sent = True
                            yield tok
                        if msg.get("done"):
                            return
        except Exception as e:
            if attempt == 0 and not sent:
                await asyncio.sleep(4)
                continue
            log.warning(f"chat LLM unavailable ({e}); fact-based answer")
            if not sent:
                yield chat_fallback(ctx, question, found)
            return


async def llm_available() -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{settings.ollama_url}/api/tags")
            return any(m["name"] == settings.llm_model for m in r.json().get("models", []))
    except Exception:
        return False


async def warmup() -> None:
    """Load the model into VRAM at server start so the first report streams immediately."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            await c.post(f"{settings.ollama_url}/api/generate",
                         json={"model": settings.llm_model, "prompt": "", "keep_alive": "30m"})
    except Exception as e:
        log.warning(f"LLM warmup skipped: {e}")
