# 데이터·소프트웨어 라이선스

상업적·현업 배포 전에 아래 **⚠ 표시 항목**의 이용 조건을 반드시 확인하세요.
`.env`에 `OMNIPHARM_COMMERCIAL_SAFE=true`를 넣으면 ⚠ 데이터(TWOSIDES 약물쌍 모델·관찰 신호, OFFSIDES, KEGG 기반 일본 제품명)를 쓰지 않고 동작합니다
(식약처 DUR·FDA 라벨·규칙·FAERS 환자 모델은 그대로 — 판정 근거 대부분 유지).

## 데이터
| 데이터 | 용도 | 이용 조건 |
|---|---|---|
| FDA FAERS 분기 원자료 | 환자-다약제 모델 학습 | 미국 정부 공공저작물(Public domain) |
| openFDA Drug Label, NDC Directory | 라벨 근거, 미국 제품 | 미국 정부 공공저작물 / openFDA 이용약관 |
| ⚠ TWOSIDES (nSIDES, Tatonetti Lab) | 약물쌍 모델 학습, 관찰 신호 | 연구용 공개. **상업적 이용은 권리자 확인 필요** |
| ⚠ OFFSIDES (nSIDES, Tatonetti Lab) | '약 자체 위험' 문장의 재확인(단일 약 신호, Evans 기준) | 연구용 공개. **상업적 이용은 권리자 확인 필요** (상업 모드에서는 쓰지 않음) |
| Health Canada Canada Vigilance 부작용 DB | 환자-다약제 모델 학습·외부 검증 | Open Government Licence – Canada |
| ChEMBL 37 전체 덤프 (EMBL-EBI) | CYP·hERG 시험관 활성, 작용 기전, 대사 효소, 약의 적응증(적응증 교란 걸러내기) | CC BY-SA 3.0 (출처 표시, 파생 데이터 동일 조건 공유) |
| FDA Drug Development and Drug Interactions 표 | CYP 지표 약물(억제제·유도제·민감 기질) | 미국 정부 공공저작물 |
| PubChem (NCBI) | 성분명 → 화학 구조 | NCBI 이용 정책(일반적으로 제한 없음). 대량 조회 시 초당 5회 제한 준수 |
| 식약처 의약품 제품 허가정보 (공공데이터포털, 목록 + 상세) | 한국 제품, 함량, 한글 허가사항(용법·주의사항·연령 제한·'함께 복용하지 말 것') | 공공데이터포털 이용허락 조건에 따름(출처 표시) |
| 식약처 의약품안전사용서비스(DUR) 성분정보 | 병용·연령·임부 금기, 용량·기간·노인 주의, 효능군중복 | 이용허락범위 제한 없음 (공공데이터포털) |
| 식약처 의약외품 제품 허가 정보 | 의약외품 제품 | 공공데이터포털 이용허락 조건에 따름 |
| Health Canada Drug Product Database | 캐나다 제품 | Open Government Licence – Canada |
| EMA Medicines report | 유럽 제품 | EMA 재사용 정책(출처 표시) |
| BDPM (ANSM, 프랑스) | 프랑스 제품 | Licence Ouverte / Open Licence (Etalab) |
| 후생노동성 약가기준 수재품목 리스트 | 일본 제품 | 일본 정부 웹사이트 이용규약(출처 표시) |
| ⚠ KEGG DRUG (교토대) | 일본어 성분명 → 영어 | **학술 이용 무료, 상업적 이용은 별도 라이선스(Pathway Solutions) 필요** |

## 소프트웨어
| 구성요소 | 라이선스 |
|---|---|
| PyTorch, RDKit, scikit-learn, NumPy, pandas | BSD 계열 |
| PyTorch Geometric, FastAPI, React, three.js, React Three Fiber, Vite | MIT |
| Ollama | MIT |
| Git for Windows (휴대용, `runtime\git` — 백업·버전 관리 도구, 프로그램과 함께 배포하지 않으면 무관) | GPL-2.0 |
| Gemma 4 / Qwen3 (로컬 LLM) | Apache-2.0 |
| ⚠ EXAONE 3.5 (선택 LLM) | EXAONE AI Model License — **비상업적 이용만 허용** |
| Noto Sans KR, Orbitron, JetBrains Mono (폰트) | SIL Open Font License |

## 출처 표시 문구 (배포 시)
> 본 프로그램은 식품의약품안전처(의약품·의약외품 허가정보, DUR), 미국 FDA(FAERS·openFDA), Health Canada(Canada Vigilance), Tatonetti Lab(TWOSIDES·OFFSIDES), EMBL-EBI ChEMBL, NCBI PubChem, Health Canada,
> European Medicines Agency, ANSM(BDPM), 후생노동성, KEGG의 공개 자료를 이용했습니다.
