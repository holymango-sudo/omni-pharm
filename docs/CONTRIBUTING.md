# 기여·운영 절차 (Contributing)

혼자 만든 프로그램은 만든 사람이 없으면 멈춥니다. 이 문서는 다른 사람이 규칙을 고치고, 검증하고, 릴리스할 수 있게 하는 절차입니다.
구조는 [ARCHITECTURE.md](ARCHITECTURE.md)를 먼저 보세요.

## 1. 준비 (한 번)

```
powershell -ExecutionPolicy Bypass -File install.ps1          # D:에 Python·git·node·Ollama 설치
powershell -ExecutionPolicy Bypass -File scripts\install_hooks.ps1   # 커밋마다 빠른 검사(약 15초)
```

데이터·모델은 git에 없습니다. 백업(`scripts\backup.ps1 -Full`)에서 복사하거나 `scripts/pipeline.py all`로 다시 만듭니다.

## 2. 매일 쓰는 검사

| 언제 | 명령 | 시간 |
|---|---|---|
| 커밋할 때(자동) | `scripts\ci.ps1 quick` — lint, 규칙 파일, 빠른 테스트 | 약 15초 |
| 판정에 영향을 주는 변경 뒤, 릴리스 전 | `scripts\ci.ps1 full` — 전체 테스트, 감사 4종, 동작 스냅샷, 무작위 스윕, 프론트 빌드 | 약 9분 |
| 화면·상담을 바꿨을 때 | `scripts/eval_chat.py`(Ollama 필요), `scripts/eval_rx_photo.py` | 각 수 분 |

GitHub에 올리면 `.github/workflows/ci.yml`이 quick 부분과 프론트 빌드를 돌립니다(데이터가 필요 없는 테스트만).

## 3. 임상 규칙 추가·수정 (가장 흔한 작업)

규칙은 코드가 아니라 `src/omnipharm/rules/*.json`에 있습니다.

1. **근거부터**: 허가사항(식약처·FDA·EMA) 병용금기·경고, 박스경고, 규제기관 서한, 가이드라인(Beers 등)을 찾습니다.
   근거를 못 찾으면 `"source": "출처 확인 필요"`, `"evidence": "출처 확인 필요"` — 이 경우 등급은 **'주의'만** 가능합니다
   (테스트가 막습니다). '높음'(→ 판정 '위험')은 문서 근거가 있어야 합니다.
2. **항목 작성** (`pair_rules.json` 예):
   ```json
   {"a": "pde5", "b": "nitrate", "level": "높음",
    "reason": "화면에 보이는 한 줄 이유 — 처방약을 '멈추라'고 쓰지 않습니다",
    "source": "PDE5 억제제 허가사항 병용금기(질산염·NO 공여제)", "evidence": "병용금기(허가)",
    "since": "v2.19.0", "review": {"status": "미검토", "reviewer": null, "date": null, "note": null}}
   ```
   `a`, `b`는 `knowledge.drug_groups()`가 아는 그룹 id 또는 성분 이름입니다. 새 그룹이면 `knowledge.DRUG_GROUPS`에 성분 목록을 추가합니다.
   일반인용 쉬운 설명이 필요하면 `rule_details.json`에 같은 a, b로 short / why / do / signs를 씁니다.
3. **테스트**: `tests/test_label_reading.py` 또는 `tests/test_clinical_scenarios.py`에 그 조합의 기대 판정을 추가합니다.
4. **검사**: `scripts\ci.ps1 full`. 동작 스냅샷이 달라지면(당연히 달라짐) 목록을 보고 **의도한 결과만 바뀌었는지** 확인한 뒤
   `.venv\Scripts\python.exe scripts\snapshot_equivalence.py save`로 새 기준을 저장합니다.
5. **금지**: `audit_combos.py --holdout`(60개) 결과를 보고 규칙을 맞추지 마세요. 이 세트는 측정 전용입니다.
   외부 검증도 `validate_external.py`의 **DEV 절반**(`logs/validation/dur_missed_dev.csv`)만 보고 고치고, TEST 절반은 보고만 합니다.

## 4. 약사 검토 받기

1. `scripts/rules_review_sheet.py export` → `docs/validation/규칙_검토표.xlsx`를 약사에게 보냅니다.
   (판정 사례 블라인드 검수는 `/review` 화면 — `docs/validation/약사검수_안내.md`)
2. 돌려받은 파일: `scripts/rules_review_sheet.py import 파일.xlsx --reviewer "이름"` → 규칙마다 검토 기록이 남습니다.
3. '수정 필요'·'삭제 필요'가 있으면 **빌드가 실패합니다**(`tests/test_rules.py`). 의견대로 고친 뒤 그 규칙의 `review.status`를
   `"미검토"`(다시 검토 받을 것) 또는 근거가 명확하면 `"동의"`로 바꾸고 `note`에 무엇을 고쳤는지 남깁니다.

## 5. 코드 변경 원칙

- 판정 원칙: '위험'은 문서·규칙 근거에서만, AI 신호는 '주의'까지, 처방약을 '멈추라'고 하지 않음.
- 리팩터링(동작이 바뀌면 안 되는 변경)은 `snapshot_equivalence.py compare`가 0이어야 합니다(CPU로 고정해 비교).
- 한국어 문서·JSON은 UTF-8(BOM 없음), 줄바꿈 LF. PowerShell `Set-Content`로 한글 파일을 고치지 마세요(인코딩 깨짐).
- API 키(`.env`)는 출력·로그·커밋 금지. 새 데이터 다운로드는 라이선스를 확인하고 `docs/DATA_LICENSES.md`에 적습니다.

## 6. 릴리스

1. `src/omnipharm/config.py` `VERSION`, `pyproject.toml` `version`을 올립니다.
2. `scripts\ci.ps1 full` 통과, 필요하면 `eval_chat.py`·`external_benchmarks.py`·`alert_burden.py`로 수치를 갱신합니다.
3. `docs/REVIEW.md`에 버전 기록(무엇이 왜 바뀌었는지, 수치 전후), README·MODEL_CARD·USER_GUIDE 갱신.
4. 커밋 → `git tag vX.Y.Z`. 서버 재시작(`stop_servers.ps1` → `start_servers.ps1`). `scripts\backup.ps1 -Full -Target <다른 드라이브>`.
