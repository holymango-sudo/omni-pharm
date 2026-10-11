# OMNI-PHARM v2.10 — 개인화 다중 약물 상호작용 분석 시스템

함께 복용하는 약들이 **이 환자에게** 얼마나 위험한지를 환자 상태(나이·키·몸무게·신장/간 기능·기저질환·임신)와
약물의 **분자 구조**를 함께 분석해 판정합니다. 근거(식약처 DUR·허가 문서·임상 규칙·실제 부작용 보고·AI 예측)를 구분해 보여주는
**완전 로컬** 시스템이며, 한국·미국·캐나다·유럽·프랑스·일본 시판 제품·의약외품 12만여 개와 세계 승인약 4,225종,
**음식·술·흡연**(술, 자몽주스, 우유, 녹색 채소, 커피, 흡연)과의 상호작용까지 확인합니다.

> ⚠️ 의사결정 **보조** 도구입니다. 의료기기 인허가를 받지 않았으며 의사·약사의 판단을 대체하지 않습니다.
> 판정 기준과 한계: [docs/MODEL_CARD.md](docs/MODEL_CARD.md)

| 문서 | 대상 |
|---|---|
| [docs/USER_GUIDE.md](docs/USER_GUIDE.md) | 일반 사용자 — 켜기, 3단계 사용법, 결과 읽기, FAQ |
| [docs/MODEL_CARD.md](docs/MODEL_CARD.md) | 의료 전문가 — 용도, 판정 규칙, 검증 성능, 한계, 개인정보 |
| [docs/DATA_LICENSES.md](docs/DATA_LICENSES.md) | 배포 담당자 — 데이터·소프트웨어 라이선스(⚠ 상업 이용 확인 항목) |
| [docs/REVIEW.md](docs/REVIEW.md) | 개발 이력 — 이전 버전 점검과 변경 내역 |
| [docs/PRESENTATION.md](docs/PRESENTATION.md) | 3분 시연 대본 |

---

## 1. 바로 사용하기
| 작업 | 방법 |
|---|---|
| 켜기 | **`OMNI-PHARM 실행.bat`** 더블클릭 → http://localhost:8000 자동 열림 |
| 휴대폰·태블릿·다른 PC에서 | **`OMNI-PHARM 네트워크 공유 실행.bat`** (먼저 사용자 계정(`scripts\users.py`) 또는 `.env`의 `OMNIPHARM_ACCESS_PIN` 설정 권장 — `.env.example` 참고) |
| 끄기 | **`OMNI-PHARM 종료.bat`** |

명령줄: `powershell -ExecutionPolicy Bypass -File .\start_servers.ps1 [-Lan] [-NoBrowser]` / `stop_servers.ps1`

- 서버 하나(FastAPI, 8000번)가 API와 화면을 함께 제공합니다. 기동 10~20초.
- Ollama가 있으면 AI 소견서(`gemma4:12b`, Apache-2.0)를 로컬 GPU로 생성하고, 없으면 규칙 기반 소견서로 대체합니다.
  모델 선택 근거: `scripts/bench_llm.py` — 이 PC(RTX 5070 Ti)에서 5개 모델 비교, 아래 표.

| 모델 | GPU 적재 | 속도 | 첫 글자 | 날조 수치 | 판정 명시 |
|---|---|---|---|---|---|
| **gemma4:12b (채택)** | 100% | **145 tok/s** | 5.5초 | 0 | 4/4 |
| qwen3:8b (이전) | 100% | 101 tok/s | 3.0초 | 0 | 3/4 |
| qwen3:14b | 100% | 71 tok/s | 4.1초 | 0 | 3/4 |
| gemma4:26b | 76% | 102 tok/s | 26.9초 | 0 | 4/4 |
| qwen3.8:27b | 71% | 15 tok/s | 15.9초 | 0 | 3/4 |
| qwen3:30b-a3b | 76% | — | — | 영어 사고과정 노출로 탈락 | |
- 상태 확인: `GET /api/v1/health` · API 문서: http://localhost:8000/docs

### 설정 (`.env`)
| 키 | 기본값 | 설명 |
|---|---|---|
| `OMNIPHARM_ACCESS_PIN` | 없음 | 설정하면 모든 API 호출에 PIN 필요(화면이 한 번 물어봄) |
| `OMNIPHARM_LLM_MODEL` | `gemma4:12b` | Ollama 모델명 |
| `OMNIPHARM_ONLINE_LOOKUP` | `true` | 로컬 DB에 없는 약 이름의 PubChem 조회(약 이름만 전송) |
| `OMNIPHARM_DATA_GO_KR_API_KEY` | 없음 | 식약처 제품·DUR DB를 새로 받을 때만 필요(`pipeline.py mfds`, `dur`) |
| `OMNIPHARM_COMMERCIAL_SAFE` | `false` | `true`: 연구용 라이선스 데이터(TWOSIDES 약물쌍 모델·신호, KEGG 일본어 변환) 끔 — 상업 배포용 |

### 병원·약국에서 쓸 때 (계정·감사 로그·EMR 연동)
| 기능 | 방법 |
|---|---|
| 사용자 계정 | `python scripts\users.py add 홍길동 --role admin` (비밀번호는 입력창에서, 해시로만 저장). 사용자가 1명 이상이면 모든 사용에 로그인 필요 |
| 감사 로그 | `logs\audit\YYYY-MM.jsonl` — 누가·언제·어떤 약 이름으로 분석했고 판정이 무엇이었는지. 환자 정보는 기록하지 않음. 관리자는 `GET /api/v1/audit` |
| EMR/OCS 연동 | `POST /api/v1/fhir/assess` — HL7 FHIR R4 Bundle(Patient·Observation(LOINC)·Condition(ICD-10/KCD)·MedicationRequest) → 판정 + FHIR `DetectedIssue` 목록 |

## 1-1. 백업과 버전 관리 (v2.9)
코드는 git 저장소로 관리합니다(`runtime\git` — 휴대용 Git for Windows, C: 설치 없음). 데이터·모델·런타임·`.env`(API 키)·계정 해시는
저장소에서 제외됩니다(`.gitignore`).

| 작업 | 방법 |
|---|---|
| 백업 | **`OMNI-PHARM 백업.bat`** — 바뀐 코드를 커밋하고, 전체 이력을 파일 하나(`.bundle`)로, 처리된 데이터·모델·설정을 `D:\OMNI-PHARM_backups`에 복사 |
| 다른 드라이브에 | `"OMNI-PHARM 백업.bat" -Target E:\OMNI-PHARM_backups` (SSD 고장 대비는 반드시 다른 드라이브·USB) |
| 자동 스냅샷 | 프로그램을 켤 때마다 코드가 바뀌었으면 자동으로 커밋 + 코드 묶음 저장(최근 30개 유지) |
| 이력 보기 | `runtime\git\cmd\git.exe log --oneline` |
| 이전 버전으로 | `runtime\git\cmd\git.exe checkout v2.8.0 -- src` (특정 폴더만), 전체 되돌리기는 `git checkout <커밋>` |
| 다른 곳에 복원 | `runtime\git\cmd\git.exe clone D:\OMNI-PHARM_backups\code\omnipharm_YYYYMMDD_HHmm.bundle OMNI-PHARM` 후 `full\` 폴더의 데이터·모델·설정을 복사 |

## 2. 다른 PC로 옮기기 (SSD째 이동)
이 폴더 하나에 실행에 필요한 것이 모두 들어 있습니다 — C: 드라이브를 쓰지 않습니다.

| 위치 | 내용 |
|---|---|
| `runtime\python` | Python 3.11 실행 파일 (`.venv`가 이것을 사용) |
| `runtime\ollama`, `runtime\ollama-models` | 로컬 LLM 실행기와 AI 소견서 모델(gemma4:12b, 7.5 GB) |
| `runtime\node` | Node.js (화면을 다시 빌드할 때만 필요) |
| `runtime\git` | Git for Windows 휴대용 (버전 관리·백업) |
| `.venv`, `data`, `artifacts`, `frontend\dist` | 패키지, 데이터베이스, 학습된 모델, 화면 |

SSD를 다른 PC에 꽂고 **`OMNI-PHARM 실행.bat`**만 누르면 됩니다. 드라이브 문자가 바뀌어도(D: → E:) 실행기가 경로를 자동으로 고칩니다.
새 PC에 필요한 것: Windows 10/11 64bit, (GPU 사용 시) 최신 NVIDIA 드라이버. GPU가 없으면 CPU로 동작합니다(느림).

## 2-1. 처음부터 새로 설치
요구 사항: Windows 10/11, Python 3.11, Node.js 20+, (권장) NVIDIA GPU, (선택) [Ollama](https://ollama.com)
```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1        # GPU / -Cpu: GPU 없는 PC
ollama pull gemma4:12b                                        # 선택: AI 소견서
```
`install.ps1`은 `requirements-lock.txt`의 고정 버전으로 설치하고 화면을 빌드합니다. 데이터·모델(`data\processed`,
`data\cache`, `artifacts\v2`)은 기존 설치본에서 복사하거나 4장의 파이프라인으로 다시 만듭니다.

## 3. 무엇을 하는가
```
 ┌──────────────── React 18 + React Three Fiber (frontend/) ─────────────────┐
 │ 01 환자 정보·약물 처리 능력   02 약 검색·3D 분자→몸속 이동   03 판정·이유·AI 소견 │
 └───────────────▲───────────────────────────────────────▲───────────────────┘
                 │ REST                                  │ SSE (토큰 스트리밍)
 ┌───────────────┴──────────── FastAPI (src/omnipharm/api.py) ───────────────┐
 │ engine.py   제품명/성분/SMILES 해석 (KR·US·CA·EU·FR·JP 제품 DB + PubChem) │
 │   A. 환자-다약제 모델   FAERS+캐나다 1,583만 보고 · 약물+환자 → 52개 이상반응 │
 │   B. 약물쌍 구조 모델   TWOSIDES 17만 쌍 · GNN(GINE) + Morgan 지문           │
 │   C0. 식약처 DUR        병용금기 1,250쌍·연령·임부·용량·기간·노인·효능군중복 │
 │   C. 문서·규칙 근거     FDA 라벨 8.4만 건 전문 검색(약명+계열명 문구),        │
 │                         FDA CYP 지표 약물표, 성분·계열 중복, 질환 규칙 41개    │
 │   D. 약리 기전          ChEMBL CYP 억제·hERG·작용기전 (시험관 → '주의'까지)   │
 │ clinical.py  CKD-EPI 2021, Cockcroft-Gault(보정체중), BMI, ULN → 약물 처리 능력 │
 │ models/llm.py 근거 JSON → 로컬 LLM 소견서 (용량·약명 날조 금지 규칙)          │
 └───────────────────────────────────────────────────────────────────────────┘
```
- **판정 원칙**: '위험'은 문서·규칙 근거(식약처 DUR 병용금기·연령·임부 금기, 허가 금기·중대 상호작용 문구, 성분 중복, 질환 금기, 1일 최대량 초과, FDA 지표표의 강한 억제제×민감 기질)가 있을 때만.
  AI 통계 신호는 **조합·환자 때문에 늘어나는 위험**일 때만 '주의'로 반영하고, 약 자체의 부작용·약물쌍 통계 신호는 참고 정보로만 보여 줍니다(경보 피로 방지). ([MODEL_CARD](docs/MODEL_CARD.md) 2장)
- **복용량·기간**: 약마다 1회량·하루 횟수·일수·매일/필요시를 넣으면, 제품별 1정(포·mL)당 성분 함량(14만 개 제품, 각국 허가 DB에서 추출)으로
  성분별 **하루 총량을 제품 간 합산**해 신장·간·나이·성별·체중에 맞춘 1일 최대량(약 70개 성분)과 비교하고, 용량 의존 상호작용(심바스타틴+암로디핀 등),
  장기 복용 위험(NSAID 10일, PPI 8주 등), 주 1회 약의 매일 복용을 점검합니다 (`src/omnipharm/dosing.py`).
- **음식·술·흡연**: 약 검색창 아래 "함께 확인" 버튼(🍺 술 · 🍊 자몽주스 · 🥛 우유 · ☕ 커피 · 🥬 녹색채소 · 🚬 흡연)
  — 술+메트로니다졸·수면제·오피오이드=위험, 자몽+심바스타틴=위험, 우유+퀴놀론·테트라사이클린·갑상선약=간격 두기, 녹색 채소+와파린=일정량 유지 등.
- **화면**: 기본은 신호등 판정 + 쉬운 이유·할 일(판정 근거는 노랑·빨강, 참고 정보는 회색). 상단 **전문가 보기**를 켜면 확률·배수·공식 값과 근거 원문
  (허가 문서 문장에 '상호작용 없음'·'병용 치료 안내' 표시).
- **몸속 이동 화면(v2.8)**: 예측 시작을 누르면 분자 충돌 뒤 가운데 화면이 홀로그램 인체로 바뀌고, 약마다 다른 색의 입자가
  ① 복용·위 → ② 장 흡수 → ③ 간 대사 효소(CYP) 결합 → ④ 혈류·혈중 농도 → ⑤ 표적 수용체 결합 → ⑥ 문제 장기 → ⑦ 배출 순서로 움직입니다
  (단계마다 자동 재생·⏮⏯⏭·단계 버튼). 예: 심바스타틴+클래리트로마이신 = 간의 CYP3A4 고리가 🔒 잠기고 심바스타틴이 쌓여 혈중 농도 ▲ →
  허벅지 근육이 빨갛게 깜박임(횡문근융해), 우유+시프로플록사신 = 위에서 엉겨 붙어 흡수 ▼, 콩팥 기능 저하 = 메트포르민·가바펜틴이 콩팥에 쌓임.
  표현: X-ray(프레넬) 인체·골격·장기·동맥/정맥 혈류 + 블룸 후처리, 그리고 설명 패널 오른쪽에 단계별 **전문 시각화** —
  분자 확대(실제 약 분자 구조가 CYP 효소의 헴 활성부위·7회 막관통 수용체·이온 통로·수송체·바이러스/세균에 결합하거나 장 세포막을 통과하고
  칼슘 이온과 킬레이트를 만드는 장면), **혈중농도-시간 곡선**(근거 범주로 그린 개념도: 단독 vs 병용, 치료·독성 범위),
  **기전 경로**(원인 → 기전 → 결과 장기 → 근거).
  **복용 후 경과 시간**: 화면 왼쪽 위 시계가 단계마다 흘러가며(예: 최고 혈중 농도 '복용 후 1시간 50분', 횡문근융해 '매일 복용 시 2일 뒤
  정상상태 이후 며칠~몇 주') 설명 칸에 로그 시간 눈금(0 · 1시간 · 1일 · 1주)이 표시됩니다. 약별 최고농도 도달 시간(Tmax)·반감기는
  **FDA 라벨 2.2만 건에서 추출한 1,147개 성분의 중앙값**(`pipeline.py pk` → `pk.json`)이며, 라벨에 수치가 없는 일반약(아세트아미노펜 등)은
  성인 표준 참고치로 보완합니다. 효소 억제·신기능 저하 시 반감기는 농도 곡선과 같은 배수로 늘어납니다.
- **서방형(ER·SR·XR·CR·LP·서방정·이알)·장용형 구분(v2.9)**: 제품명으로 제형을 판별(`src/omnipharm/release.py`; 한국 서방형 1,725 · 장용 145,
  해외 서방형 1,196개 제품)하고 — 서방형 라벨의 Tmax(81개 성분) 또는 제형별 성인 참고치로 시간축·농도 곡선을 그리며,
  서방형 1일 최대량·1회량(메트포르민 XR 2,000mg, 트라마돌 ER 300mg, 졸피뎀 CR 12.5mg, 타이레놀 8시간 ER 1회 1,300mg 등)과
  복용 횟수(서방형 하루 1~2회, 8시간 ER 3회)를 따로 점검하고, "쪼개거나 씹지 말고 통째로 삼키세요"를 안내합니다. 약 칩에 '서방형' 표시.
- **💬 AI 복약 상담(v2.10)**: 분석 후 자유 질문 → 로컬 LLM(gemma4:12b)이 그 분석의 사실(판정 이유·권고·DUR·라벨·규칙·약동학·
  이 환자 기준 1일 최대량)만 근거로 스트리밍 답변(`POST /api/v1/chat`). 근거 출처는 실제 있는 종류만 쓰도록 제한, 응급 표현(숨참·과량 복용·
  검은 변·자해 등)은 규칙으로 감지해 119 안내를 먼저 출력, 진단·처방·임의 중단 금지, LLM이 없으면 사실 기반 대체 답변. 대화는 저장하지 않음.
- **🤒 증상으로 약 찾기(v2.10)**: 17개 증상 → 1차/대안 일반의약품 성분(`src/omnipharm/recommend.py`) → 이미 넣은 약과의 DUR·라벨·규칙
  상호작용, 성분·계열 중복, 질환·고령(Beers)·임신·신기능으로 '적합/주의/피하세요' 선별 → 국내 시판 제품(주사제 제외) 원클릭 추가.
  증상별 '병원에 가야 할 때' 안내(`POST /api/v1/recommend`).
- **반복 복용 그래프(v2.9)**: 농도 곡선 패널의 '1회 복용 / 반복 복용' 전환. 약마다 라벨 Tmax·반감기로 맞춘 1구획 모델을 복용 간격마다 더해
  정상상태까지 쌓이는 농도, 상호작용·신기능으로 늘어난 축적, '복용 중단' 후 배출을 그립니다(입력한 하루 횟수·일수, 없으면 반감기로 추정한 간격).
  화면은 판정과 **같은 근거만** 씁니다(FDA CYP 지표표·ChEMBL·임상 규칙·DUR·AI 신호·질환·용량·신기능 — `src/omnipharm/journey.py`):
  빨간 장기는 판정이 '위험'일 때만 나타나고, 근거가 없는 단계는 "문제 없음"으로 표시합니다(무작위 5천 조합에서 판정과 불일치 0건).
- **출력**: 환자용 안내문(큰 글씨·숫자 없음) / 전문가 리포트(DUR·규칙·근거 원문·용량 기준 출처 포함).
- **속도**: 약 15개(105쌍)도 1초 이내(첫 분석 0.7초, 반복 0.35초 — `scripts/bench_engine.py`).

### 인식하는 의약품
| 국가 | 출처 | 제품 수 | 분석 가능* |
|---|---|---|---|
| 한국 | 식약처 의약품 제품 허가정보 | 42,638 | 81% |
| 한국 의약외품 | 식약처 의약외품 제품 허가정보 (박카스·치약·소독제 등) | 22,958 | — |
| 세계 승인 의약품 | ChEMBL 승인약 4,225종의 상품명·성분명 2.9만 개 + WHO ATC 분류 | — | — |
| 미국 | FDA NDC Directory | 33,270 | 79% |
| 캐나다 | Health Canada DPD | 11,463 | 87% |
| 유럽 | EMA 중앙허가 | 2,274 | 62% |
| 프랑스 | BDPM (불어 INN 변환) | 14,778 | 80% |
| 일본 | 후생노동성 약가기준 리스트 + KEGG (일본어 → 영어) | 11,803 | 72% |
| 그 외 | PubChem 성분명·동의어 (인터넷 연결 시) | — | — |

**분자 구조가 없는 성분**(항체·펩타이드 등 생물의약품, 생약·한약 엑스, 비타민·미네랄, 미용 주사 성분)도 모두 받아들입니다.
구조 AI 대상은 아니므로 FDA 라벨 8.4만 건 전문 검색 + 임상 규칙으로 평가합니다 — 저혈당(GLP-1·인슐린), 피임약 흡수 저하, 신경근 차단(보톡스),
감초→저칼륨, 은행잎→출혈, 세인트존스워트→면역억제제 농도 저하, 칼슘·철·마그네슘→항생제·갑상선약 흡수 저하, 염화칼륨→고칼륨 등.
대표 의약품 346개(수면제·미용·비만·성기능·호르몬·정신과·감기·만성질환·항생제·생물의약품) 인식률 95% (`scripts/coverage_audit.py`; 나머지는
같은 이름 제품이 여럿이라 고르면 되는 경우, 건강기능식품·의약외품).

*구조 AI까지 적용되는 제품 비율(한국 81%). 나머지 제품도 라벨·규칙 근거로 평가됩니다.
검색어 문자로 국가가 자동 선택됩니다(한글→한국, 가나·한자→일본, 로마자→미·캐·유럽·프랑스).

## 4. 검증 결과
학습에 쓰지 않은 데이터로 측정 (`artifacts/v2/*_metrics.json`, 화면의 **모델 검증** 탭).

**A. 환자-다약제 모델** — 약물 1,829종, 이상반응 52종
| 평가 세트 | 보고 수 | macro AUROC | macro AUPRC (무작위) |
|---|---|---|---|
| **미래 시점** (2025Q3–2026Q2) | 616,731 | **0.810** | 0.060 (0.008) |
| └ 2개 이상 약물 | 231,110 | 0.796 | 0.073 (0.010) |
| **처음 보는 약물** 포함 | 4,005,200 | 0.753 | 0.059 (0.011) |
| **다른 나라 외부 검증** (캐나다 최근 4분기, 학습 제외) | 27,294 | **0.790** | 0.068 (0.009) |
| (비교) 환자 정보 제외 — 미래 시점 / 캐나다 | 616,731 / 27,294 | 0.790 / 0.770 | 0.052 / 0.058 |

환자 요인 민감도(같은 약물, 한 요인만 변경): 신장질환→신장 ×1.44, 간질환→간 ×1.54, 심장질환→심장 ×1.56, 80세 vs 30세→신장 ×1.70.

**B. 약물쌍 구조 모델** — TWOSIDES 172,429쌍 (macro AUROC)
| 평가 세트 | GNN | Morgan 지문 | 앙상블 |
|---|---|---|---|
| 기존 약물 조합 | 0.899 | 0.906 | **0.910** ★ |
| 처음 보는 약물 1개 | **0.714** ★ | 0.652 | 0.701 |
| 두 약물 모두 처음 | **0.598** ★ | 0.535 | 0.575 |

★ 배포 규칙: 학습된 약물끼리는 앙상블, 새 분자가 끼면 GNN 단독.

**임상 조합 감사** (`scripts/audit_combos.py`, 표준 참고서(FDA 라벨·Lexicomp/Micromedex 등급·Stockley·Beers)로 답이 정해진 조합, 건강한 성인 기준)

| 세트 | 결과 |
|---|---|
| 기준 세트 109개 (금기·중대 42 / 중등도 25 / 상호작용 없음 42) | 109/109 |
| **처음 보는 세트 60개** (규칙 조정에 쓰지 않음, `--holdout`) | **57/60 (95%)** 첫 측정 — 중대 20/20. 원인 확인 후 일반 규칙으로 고쳐 현재 60/60 |
| 환자별 시나리오 18개 (CKD·심부전·임신·고령·천식·녹내장·파킨슨·소아 …, `--patients`) | 18/18 |
| 음식·술·흡연 22개 (`--foods`) | 22/22 |
| **무작위 대량 점검** 3,000건 + 2,000건 (`scripts/sweep_random.py`: 6가지 환자 × 2~5개 약) | 오류 0건, 앞뒤 안 맞는 문구 0건, 몸속 화면과 판정 불일치 0건 |

모든 세트를 `tests/test_combo_audit.py`로 매 변경마다 자동 검사합니다.

**외부 검증 — 식약처 DUR 병용금기 목록** (`scripts/validate_external.py`, `logs/validation/`)
DUR 층을 끈 상태에서, FDA 라벨·규칙·AI만으로 식약처 공식 병용금기 1,250쌍을 얼마나 잡는지 측정(DUR은 이 근거들과 독립된 자료).

| 판정 | 비율 |
|---|---|
| '위험' | 47% (DUR 반영 전 33%) |
| '위험' 또는 '주의' | 67% (경보 피로를 줄이려 통계 신호 기준을 높인 뒤 84% → 67%: 정확도와 민감도의 균형) |
| 정상 운영(DUR 켬) | **100%** — 국내 공식 병용금기는 모두 '위험' |

**약사 검수표** (`docs/validation/약사_검수표.xlsx`, 실제 처방형 200건, 판정 숨김) — 약사가 판정을 적은 뒤
`python scripts/score_review.py 약사_검수표.xlsx`로 일치율·카파·위험 민감도를 계산합니다. (약사 검수 결과는 아직 없습니다.)

**C. 임상 시나리오 회귀 테스트** — 결론이 알려진 조합 14개(와파린+아미오다론=위험, 아세트아미노펜+와파린=주의,
팍스로비드+심바스타틴=위험, 케토코나졸+미다졸람=위험, 클로피도그렐+오메프라졸=위험, 성분 중복=위험, 위궤양+NSAID=위험 …)를 매 변경마다 검사합니다.

## 5. 데이터 파이프라인 (재현)
```powershell
$py = ".\.venv\Scripts\python.exe"
& $py scripts\download_data.py              # TWOSIDES + openFDA 라벨 (약 2.5 GB, SHA-256 기록)
& $py scripts\pipeline.py twosides          # 4,200만 행 → 약물쌍·양성 신호
& $py scripts\pipeline.py structures        # PubChem 구조 → drug_master 1,602종
& $py scripts\pipeline.py faers             # FAERS 2004Q1~최신 90여 분기 (구 AERS 형식 포함, 약 8 GB)
& $py scripts\pipeline.py canada            # Health Canada 부작용 DB 126만 건 (0.3 GB)
& $py scripts\pipeline.py extend            # FAERS 최신 저분자 227종 추가 → 1,829종
& $py scripts\pipeline.py labels            # 쌍 라벨 + FAERS 환자 테이블 (상품명 → 성분 해석)
& $py scripts\pipeline.py fdalabels         # FDA 라벨 인덱스 (약물당 대표 라벨)
& $py scripts\pipeline.py mol3d             # 3D 좌표
& $py scripts\pipeline.py train-pair        # 약 5분 (RTX 5070 Ti)
& $py scripts\pipeline.py train-patient     # 약 45분  (--no-patient: 비교 실험)
& $py scripts\pipeline.py mfds              # 한국 제품 (API 키 필요, 최초 30분)
& $py scripts\pipeline.py world             # 미·캐·유럽·프랑스·일본 제품 (최초 30분)
& $py scripts\pipeline.py chembl-drugs      # ChEMBL 승인약 4,225종: 상품명·ATC 분류
& $py scripts\pipeline.py strengths         # 제품별 1단위당 성분 함량 (한국 상세정보 포함, 최초 20분)
& $py scripts\pipeline.py dur               # 식약처 DUR 성분 규칙 + 의약외품 (API 키 + 활용신청 필요)
& $py scripts\pipeline.py chembl            # ChEMBL CYP·hERG 활성, 작용 기전 (REST, 캐시)
& $py scripts\pipeline.py labelsearch       # openFDA 라벨 전체 8.4만 건 전문 검색 DB (labels.db, 1분)
& $py scripts\pipeline.py pk                # 라벨의 약동학 항목 → 성분별 Tmax·반감기 (pk.json, 1분)
#   또는 한 번에:  & $py scripts\pipeline.py all   (위 순서 그대로)
& $py -m pytest                             # 테스트 267개
& $py scripts\audit_combos.py [--holdout|--patients|--foods]   # 임상 조합 감사
& $py scripts\sweep_random.py 3000          # 무작위 대량 점검
& $py scripts\validate_external.py          # 식약처 DUR 외부 검증
& $py scripts\bench_engine.py               # 속도 측정
```

## 6. 폴더
```
OMNI-PHARM 실행.bat / 종료.bat / 네트워크 공유 실행.bat   일반 사용자용 실행 파일
install.ps1, requirements-lock.txt                        새 PC 설치
src/omnipharm/   api.py, engine.py, clinical.py, knowledge.py, journey.py (몸속 이동 경로), config.py
                 chem/ (표준화·그래프·3D)  models/ (gnn, patient, llm)  pipeline/ (데이터·학습 단계)  data/ (다운로더)
frontend/        React + R3F 화면 (dist/ = 빌드 결과, 서버가 제공)
scripts/         pipeline.py (데이터·학습), users.py (계정), audit_combos.py · sweep_random.py · validate_external.py ·
                 review_sheet.py · score_review.py (검증), bench_engine.py · bench_llm.py (성능), coverage_audit.py
tests/           단위·API·보안·견고성·엔진·임상 시나리오·조합 감사·몸속 이동·서방형·상담 테스트 (267개)
runtime/         Python·Ollama·Node 실행 파일과 AI 모델 (폴더째 이동 가능)
config/          users.json (사용자 계정, 비밀번호 해시)
data/            raw / interim / processed / cache
artifacts/v2/    학습된 모델 + 지표
docs/            사용 설명서, 모델 카드, 라이선스, 점검 이력, 시연 대본
```
