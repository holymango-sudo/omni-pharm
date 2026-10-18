# 외부 기준 검증 (개발 측이 만들지 않은 기준)

측정: 2026-10-18 18:08

## (a) 식약처 DUR 병용금기 — DUR 끈 상태, 시험(test) 절반

DUR 쌍을 중심 약물 단위로 개발/시험 절반으로 나누고, 규칙 보완은 개발 절반의 놓친 쌍만 보고 했습니다. 시험 절반은 보완 전후 성적만 잽니다(scripts/validate_external.py).

**시험 절반 472쌍: 위험 55.3%, 위험 또는 주의 77.3%** (보완 전 51.5% / 74.2%). 정상 운영(DUR 켬)에서는 100%.

## (b) AGS Beers 기준 — 노인에게 피해야 할 약물 상호작용 (24개 조합, 75세 여성)

출처: American Geriatrics Society Beers Criteria Update Expert Panel. American Geriatrics Society 2023 updated AGS Beers Criteria for potentially inappropriate medication use in older adults. J Am Geriatr Soc. 2023;71(7):2052-2081. Table 5: Potentially clinically important drug-drug interactions that should be avoided in older adults (most entries unchanged since the 2019 update).

**주의 이상 24/24 = 100% (95% CI 86%–100%), 위험 8/24 = 33% (95% CI 18%–53%)**

> Entered by the developers from the published table (one or two representative drugs per class). CHECK EVERY ROW AGAINST THE ORIGINAL TABLE before citing these numbers; the list was not used to write or tune any rule before it was measured.

| 조합 | 분류 | 위험 | 판정 | 첫 번째 이유 |
|---|---|---|---|---|
| lisinopril + losartan | RAS 억제제 + 다른 RAS 억제제 | 고칼륨혈증 | 주의 | 리시노프릴(lisinopril)과(와) 로사르탄(losartan)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때 관찰  |
| lisinopril + amiloride | RAS 억제제 + 칼륨 보존 이뇨제 | 고칼륨혈증 | 주의 | 리시노프릴(lisinopril)과(와) 염산아미로라이드(amiloride)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을  |
| losartan + triamterene | RAS 억제제 + 칼륨 보존 이뇨제 | 고칼륨혈증 | 주의 | 로사르탄(losartan)과(와) 트리암테렌(triamterene)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때 관찰 |
| oxycodone + lorazepam | 오피오이드 + 벤조디아제핀 | 과다복용(호흡 억제) | 위험 | 옥시코돈(oxycodone)과(와) 로라제팜(lorazepam)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| tramadol + alprazolam | 오피오이드 + 벤조디아제핀 | 과다복용(호흡 억제) | 위험 | 트라마돌(tramadol)과(와) 알프라졸람(alprazolam)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| oxycodone + gabapentin | 오피오이드 + 가바펜티노이드 | 진정·호흡 억제 | 위험 | 옥시코돈(oxycodone)과(와) 가바펜틴(gabapentin)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| tramadol + pregabalin | 오피오이드 + 가바펜티노이드 | 진정·호흡 억제 | 위험 | 트라마돌(tramadol)과(와) 프레가발린(pregabalin)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| oxybutynin + diphenhydramine | 항콜린제 + 항콜린제 | 인지 저하 | 주의 | 고령(65세 이상) 환자에게 옥시부티닌(oxybutynin): 혼돈·변비·소변 장애·낙상을 일으키기 쉽습니다. |
| amitriptyline + oxybutynin | 항콜린제 + 항콜린제 | 인지 저하 | 주의 | 고령(65세 이상) 환자에게 아미트립틸린(amitriptyline): 혼돈·변비·소변 장애·낙상을 일으키기 쉽습니다. |
| sertraline + zolpidem + tramadol | 중추신경계 작용 약 3개 이상 | 낙상·골절 | 위험 | 졸피뎀(zolpidem)과(와) 트라마돌(tramadol)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| quetiapine + lorazepam + gabapentin | 중추신경계 작용 약 3개 이상 | 낙상·골절 | 주의 | 고령(65세 이상) 환자에게 로라제팜(lorazepam): 낙상·골절과 인지 저하 위험이 큽니다. |
| prednisone + naproxen | 전신 코르티코스테로이드 + NSAID | 위장관 출혈·궤양 | 주의 | 신장질환(CKD) 환자에게 나프록센(naproxen): 신장 혈류를 줄여 신기능을 더 떨어뜨릴 수 있습니다 — 오래 먹지 마세요. |
| prednisolone + ibuprofen | 전신 코르티코스테로이드 + NSAID | 위장관 출혈·궤양 | 주의 | 신장질환(CKD) 환자에게 이부프로펜(ibuprofen): 신장 혈류를 줄여 신기능을 더 떨어뜨릴 수 있습니다 — 오래 먹지 마세요. |
| lithium + lisinopril | 리튬 + ACE 억제제 | 리튬 독성 | 주의 | 리튬(lithium)과(와) 리시노프릴(lisinopril)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때 관찰 필요) |
| lithium + losartan | 리튬 + ARB | 리튬 독성 | 주의 | 리튬(lithium)과(와) 로사르탄(losartan)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때 관찰 필요). |
| lithium + furosemide | 리튬 + 루프 이뇨제 | 리튬 독성 | 위험 | 리튬(lithium)과(와) 푸로세미드(furosemide)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| doxazosin + furosemide | 말초 알파1 차단제 + 루프 이뇨제 | 요실금(노인 여성) | 주의 | 독사조신(doxazosin)과(와) 푸로세미드(furosemide)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때 관찰 |
| phenytoin + sulfamethoxazole | 페니토인 + TMP-SMX | 페니토인 독성 | 주의 | 페니토인(phenytoin)과(와) 설파메톡사졸(sulfamethoxazole)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께  |
| theophylline + cimetidine | 테오필린 + 시메티딘 | 테오필린 독성 | 주의 | 테오필린(theophylline)과(와) 시메티딘(cimetidine)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때  |
| theophylline + ciprofloxacin | 테오필린 + 시프로플록사신 | 테오필린 독성 | 위험 | 테오필린(theophylline)과(와) 시프로플록사신(ciprofloxacin)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있 |
| warfarin + amiodarone | 와파린 + 아미오다론 | 출혈 | 위험 | 와파린(warfarin)과(와) 아미오다론(amiodarone)은(는) 허가 문서에 '피하거나 용량을 줄여야 하는' 중대한 상호작용으로 적혀 있습니다. |
| warfarin + ciprofloxacin | 와파린 + 시프로플록사신 | 출혈 | 주의 | 와파린(warfarin)과(와) 시프로플록사신(ciprofloxacin)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 때 |
| warfarin + clarithromycin | 와파린 + 마크롤리드(아지트로마이신 제외) | 출혈 | 주의 | 와파린(warfarin)과(와) 클래리트로마이신(clarithromycin)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 |
| warfarin + sulfamethoxazole | 와파린 + TMP-SMX | 출혈 | 주의 | 와파린(warfarin)과(와) 설파메톡사졸(sulfamethoxazole)은(는) 서로 영향을 주는 조합으로 허가 문서에 기재돼 있습니다 (규칙적으로 함께 먹을 |

## (c) 음성 대조군 — 허가 문서에 '임상적으로 의미 있는 상호작용 없음'인 쌍 (125쌍)

실제 FAERS 처방 조합 3,000건에서 찾은, 허가 문서가 상호작용이 없다고 적은 약 쌍(다른 문서 근거 없음).
**거짓 경고(주의 이상) 4/125 = 3% (95% CI 1%–8%)** — 남은 경고는 규칙·약리 기전 근거에서 온 것입니다.

- 가바펜틴(gabapentin) + 프레가발린(pregabalin): 주의 (근거: PD)
- 리스페리돈(risperidone) + 미르타자핀(mirtazapine): 주의 (근거: PD)
- 에베롤리무스(everolimus) + 타크로리무스(tacrolimus): 주의 (근거: PD)
- 리스페리돈(risperidone) + 플루복사민(fluvoxamine): 주의 (근거: QT)

## 한계

- (b)는 개발 측이 논문 표를 옮겨 적은 것이라 원문 대조가 필요합니다. 대표 약 1~2개씩만 넣었습니다.
- 약사 검수 1차 결과(약사 2명 × 200건)는 docs/validation/약사검수_결과.md. 상용 상호작용 DB 대조는 아직 하지 않았습니다.
- 소요 87초.
