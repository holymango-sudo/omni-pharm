# OMNI-PHARM 구조 (Architecture)

이 문서는 처음 보는 개발자가 하루 안에 코드를 따라갈 수 있도록 쓴 지도입니다. 사용 목적·대상은 [INTENDED_USE.md](INTENDED_USE.md),
기여 절차는 [CONTRIBUTING.md](CONTRIBUTING.md), 모델 성능은 [MODEL_CARD.md](MODEL_CARD.md)를 보세요.

## 1. 한눈에 보기

```
브라우저 (frontend/, React + Vite)            휴대폰 약사 검수 (/review, static/review.html)
        │  HTTP / SSE
        ▼
api.py (FastAPI :8000) ── security.py(계정·감사 로그) ── fhir.py(EMR 입력)
        │
        ├─ engine.Engine.predict()  ← 판정의 전부. 아래 '2. 판정이 만들어지는 순서'
        ├─ models/llm.py           ← 로컬 LLM(Ollama gemma4:12b): 리포트·복약 상담 문장만 씀(판정은 안 바꿈)
        │     assistant.py(상담 전 사실 조회) · chat_checks.py(근거 없는 주장 표시)
        ├─ rx_photo.py              ← 처방전 사진 → 약 이름(비전 LLM) → 엔진 이름 해석
        ├─ recommend.py             ← 증상으로 일반약 찾기(이 환자에게 맞지 않는 약 제외)
        └─ review.py                ← 약사 블라인드 검수 사례·답 저장
```

## 2. 판정이 만들어지는 순서 (`Engine.predict`)

1. **이름 해석** (`engine_resolve.py`, ResolveMixin): 한국·해외 제품명, 성분명, 일본어, 음식, 규제 물질, SMILES → 성분(`Ingredient`).
2. **환자 상태** (`clinical.py`): 나이·체중·크레아티닌·간수치 → 신기능(CrCl)·간기능 단계, 모델 입력 벡터(`models/patient.py`).
3. **성분 중복·계열 중복**: 같은 성분, 같은 계열(knowledge.DRUG_CLASSES), 식약처 DUR 효능군 중복, WHO ATC 4단계.
4. **약 쌍마다 문서 근거** (`engine_evidence.py`, EvidenceMixin)
   - 식약처 DUR 병용금기(`data/processed/dur.json`)
   - FDA 허가 문서 전문 검색(`labels.db`) + 문장 해석(`engine_labels.py`: 상호작용 경고인가, 금기인가, 알레르기·자기 계열·참조 문구·
     의도된 병용인가)
   - 임상 규칙(`rules/pair_rules.json`), FDA CYP 지표표(knowledge.CYP_FDA), 식약처 허가사항 '함께 복용하지 말 것'(kr_labels)
   - 약리 기전(ChEMBL: CYP 억제, hERG, 같은 작용) — 기전만으로는 '주의'까지
5. **AI** (engine.py): 환자 맞춤 다약제 모델(FAERS) → 이상반응별 확률·단독 대비 배수·약물별 기여도, 약물쌍 구조 모델(TWOSIDES,
   GNN+지문). **AI는 '위험'을 만들지 않습니다**: 조합 신호(단독 대비 ×3 이상, 1% 이상, 중대)만 '주의'까지 올리고, 환자 특성만의
   신호는 '알아두기'.
6. **질환·연령·임신·용량** (`engine_clinical.py`, `dosing.py`): `rules/drug_disease_rules.json`, FDA 금기 문구, DUR 연령·임부·노인,
   하루 총량.
7. **요약** (`engine_clinical.py` `_summary`): 판정(위험/주의/비교적 안전), 조치 수준(즉시 상담/확인 필요/알아두기/조치 없음),
   쉬운 이유와 할 일, 인체 화면 데이터(`journey.py`).

판정 원칙(코드 곳곳의 불변 조건, `scripts/sweep_random.py`가 매번 검사):
'위험'은 문서·규칙 근거에서만 / AI는 '주의'까지 / 처방약을 '멈추라'고 하지 않음 / 내부 키·NaN·템플릿 노출 금지.

## 3. 파일 지도

| 위치 | 내용 |
|---|---|
| `src/omnipharm/engine.py` | Engine: 데이터·모델 로딩, AI 부분, `predict()` |
| `engine_resolve.py` · `engine_evidence.py` · `engine_labels.py` · `engine_clinical.py` · `engine_base.py` | 위 2절의 단계별 모듈(v2.18에 engine.py 2,050줄을 나눔, API 동일) |
| `rules/*.json` | **임상 규칙 데이터** — 규칙마다 출처·근거 종류·추가 버전·약사 검토 기록. 코드가 아니라 여기를 고칩니다 |
| `knowledge.py` | 약 그룹·계열, 질환 키워드, CYP 지표표, 한국어 이름표 등 어휘 + rules 로더 |
| `recreational.py` | 규제 물질(위해 감소 정보) |
| `dosing.py` · `clinical.py` | 용량·기간 검사, 환자 상태 |
| `journey.py` | 인체 이동 화면 데이터(흡수·대사·배설·충돌 지점) |
| `models/` | 모델 구조(gnn.py), 환자 벡터, LLM 프롬프트·스트리밍 |
| `pipeline/` | 데이터 구축·학습 단계(`scripts/pipeline.py <stage>`) |
| `static/review.html` | 약사 블라인드 검수 화면 |
| `frontend/` | 화면(React). `npm run build` → `frontend/dist`를 api.py가 서빙 |
| `scripts/` | 평가·검증·운영 스크립트(아래 4절) |
| `tests/` | pytest. `-m "not slow"`는 데이터 없이 돎(GitHub CI), 나머지는 로컬 데이터 필요 |

## 4. 검증 도구 (무엇이 무엇을 지키는가)

| 도구 | 지키는 것 |
|---|---|
| `scripts/ci.ps1 quick` / pre-commit 훅 | lint, 규칙 파일 형식·출처, 빠른 테스트 |
| `scripts/ci.ps1 full` | + 전체 테스트, 감사 4종, 동작 스냅샷, 무작위 스윕, 프론트 빌드 |
| `scripts/audit_combos.py` | 개발 세트 109·환자 18·음식 22 정답 / `--holdout` 60은 **측정 전용**(규칙 조정 금지) |
| `scripts/snapshot_equivalence.py` | 996개 분석 결과의 해시 — 리팩터링이 결과를 바꾸지 않았음을 증명 |
| `scripts/sweep_random.py` · `alert_burden.py` | 숨은 오류(불변 조건) · 실제 처방 조합의 경고 부담 |
| `scripts/validate_external.py` · `external_benchmarks.py` | 개발 측이 만들지 않은 기준: 식약처 DUR(튜닝 안 한 TEST 절반), AGS Beers, 음성 대조군 |
| `scripts/eval_chat.py` | AI 상담 51문항 + 근거 없는 주장 지표 |
| `scripts/score_review.py` | 약사 검수 결과: 카파·위험 민감도(사람의 답이 있어야 의미) |
| `pipeline.py calibration` | AI 확률 보정(Brier·ECE)·하위집단 AUROC |

## 5. 데이터와 모델 (git에 없음)

`data/processed`, `artifacts/v2`는 수 GB이고 일부(TWOSIDES·OFFSIDES·KEGG)는 연구용 라이선스라 git에 넣지 않습니다
([DATA_LICENSES.md](DATA_LICENSES.md)). 처음부터 다시 만들려면 `scripts/download_data.py` 후 `scripts/pipeline.py all`
(공공데이터 API 키가 필요한 단계: mfds, kr-labels, supplements, food-nutrients). 백업: `scripts/backup.ps1 -Full -Target <다른 드라이브>`.

## 6. 실행 환경

Windows, D: 드라이브에만 설치(`install.ps1`): Python 3.11 venv(`.venv`), 휴대용 git·node(`runtime/`), Ollama(로컬 LLM).
`start_servers.ps1`(API + LLM, 시작 시 자동 스냅샷 커밋) / `stop_servers.ps1`. GPU가 없으면 CPU로 동작(느림).
