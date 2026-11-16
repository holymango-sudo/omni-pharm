"""'증상·질환으로 약 찾기': self-care medicine options for a symptom or a named condition, screened for THIS patient.

v2.21 (user request: "허리디스크, 감기, 무릎 십자인대 파열처럼 아픈 것으로 검색해서 맞는 약을 넣고 싶다"):
  * free-text search over ~50 symptoms / conditions with everyday names (search())
  * per condition: whether self-care is reasonable, a doctor comes first (ligament tear, disc, shingles, flu ...) or
    it is an emergency (chest pain, paralysis, vomiting blood: no medicine suggestions at all), what to do without
    medicine, and when to see a doctor
  * options: tablets (standard self-care references, first choice / alternative), creams / patches, and the real
    over-the-counter products whose MFDS-approved indication (효능·효과, 10,771 일반의약품) names the complaint
  * prescription medicines doctors typically use: reference only, in the expert view, never as self-care
Every candidate is checked with the same evidence the verdict uses - MFDS DUR, label interactions and clinical rules
against the medicines already entered, same ingredient / class duplication, the patient's diseases, age, kidneys,
liver and pregnancy - and shown as 적합 / 주의 / 피하세요 with the reason. These are options to discuss with a
pharmacist, not a diagnosis or a prescription.
"""

from __future__ import annotations

import re
from functools import cache

import pandas as pd

from omnipharm.clinical import Patient, assess
from omnipharm.config import settings
from omnipharm.knowledge import DRUG_CLASSES, drug_class, drug_groups

# ---------------------------------------------------------------------------------------------- the condition table
# id -> ko, group, icon, aliases (everyday words people type), care ('self' | 'doctor_first' | 'emergency'),
# doctor (when to see one), tips (what helps without medicine), options / topical [(ingredient, 1 first choice /
# 2 alternative, what it does)], rx [(ingredient, what it is for)] - prescription, expert view only,
# terms (words of the MFDS 효능·효과 text to find OTC products), includes (a condition made of several symptoms)
_NSAID = [("ibuprofen", 1, "염증과 통증을 줄입니다 — 식후 복용, 위장 장애 주의"),
          ("naproxen", 2, "효과가 오래 가는 소염진통제(하루 2번)"),
          ("acetaminophen", 2, "염증은 못 줄이지만 위 부담이 적은 진통제")]
_PATCH = [("ketoprofen", 1, "붙이는 파스·바르는 겔 — 아픈 곳에만 작용(붙인 곳 햇빛 노출 주의)"),
          ("diclofenac", 1, "바르는 겔·파스 — 먹는 약보다 위장 부담이 적음"),
          ("flurbiprofen", 2, "붙이는 파스"), ("felbinac", 2, "붙이는 파스·바르는 약")]
_RICE = "다친 직후 2~3일: 쉬기·얼음찜질(15~20분씩)·압박붕대·다친 곳을 심장보다 높이 — 그 뒤에는 온찜질과 가벼운 움직임"
CONDITIONS: dict[str, dict] = {
    # ---- 근육·관절·뼈
    "low_back": {"ko": "허리 통증(요통·삐끗함)", "group": "근육·관절·뼈", "icon": "🧍", "care": "self",
                 "aliases": ["요통", "허리통증", "허리가아파", "허리아파", "허리삐끗", "허리삠", "허리를삐", "허리근육", "허리"],
                 "doctor": "다리에 힘이 빠지거나 감각이 둔해짐, 대소변 장애(즉시 응급실), 열·체중 감소, 6주 넘는 통증은 진료",
                 "tips": "누워만 있기보다 아프지 않은 범위에서 걷기, 무거운 물건 들기 피하기, 온찜질",
                 "options": _NSAID, "topical": _PATCH, "terms": ["요통", "허리"],
                 "rx": [("ing:eperisone", "근육 이완제"), ("celecoxib", "위 부담이 적은 처방 소염진통제"),
                        ("tramadol", "중등도 이상 통증의 진통제")]},
    "disc": {"ko": "허리디스크(추간판 탈출증)", "group": "근육·관절·뼈", "icon": "🦴", "care": "doctor_first",
             "aliases": ["허리디스크", "디스크", "추간판", "좌골신경통", "다리저림", "다리로뻗치", "엉덩이에서다리"],
             "doctor": "다리 힘 빠짐·감각 저하·대소변 장애는 즉시 응급실. 다리로 뻗치는 통증이 1~2주 넘거나 심하면 정형외과·신경외과 진료"
                       "(MRI·주사·수술 여부 판단) — 아래 약은 진료 전까지 통증을 줄이는 용도",
             "tips": "오래 앉아 있기·허리 숙여 물건 들기 피하기, 아프지 않은 범위의 가벼운 걷기, 온찜질",
             "options": _NSAID, "topical": _PATCH, "terms": ["요통", "신경통", "좌골"],
             "rx": [("pregabalin", "다리로 뻗치는 신경통"), ("ing:eperisone", "근육 이완제"),
                    ("celecoxib", "위 부담이 적은 처방 소염진통제"), ("tramadol", "중등도 이상 통증의 진통제")]},
    "neck": {"ko": "목 통증·목디스크·담 결림", "group": "근육·관절·뼈", "icon": "🧣", "care": "self",
             "aliases": ["목디스크", "목통증", "목이아파", "거북목", "담걸", "담이걸", "목결림", "어깨결림"],
             "doctor": "팔로 뻗치는 저림·힘 빠짐, 열이 나며 목이 뻣뻣함(뇌수막염 의심 — 응급실), 2주 넘는 통증은 진료",
             "tips": "온찜질, 고개 숙이고 휴대전화 보기 줄이기, 가벼운 스트레칭",
             "options": _NSAID, "topical": _PATCH, "terms": ["어깨결림", "근육통", "경부"],
             "rx": [("ing:eperisone", "근육 이완제"), ("pregabalin", "팔로 뻗치는 신경통")]},
    "knee_ligament": {"ko": "무릎 인대 손상(십자인대·반월상연골)", "group": "근육·관절·뼈", "icon": "🦵", "care": "doctor_first",
                      "aliases": ["십자인대", "전방십자", "후방십자", "무릎인대", "인대파열", "인대손상", "반월상", "연골판",
                                  "무릎다침", "무릎부상", "무릎을다쳐"],
                      "doctor": "다칠 때 '뚝' 소리, 빠르게 붓거나 무릎이 빠지는 느낌이면 정형외과 진료(MRI로 파열·수술 여부 판단) — "
                                "아래 약은 진료 전까지 통증·부기를 줄이는 용도이며 인대를 낫게 하지는 않습니다",
                      "tips": _RICE + ", 진료 전까지 다친 다리에 체중 싣지 않기(목발)",
                      "options": _NSAID, "topical": _PATCH, "terms": ["염좌", "타박", "관절통"],
                      "rx": [("celecoxib", "위 부담이 적은 처방 소염진통제"), ("tramadol", "중등도 이상 통증의 진통제")]},
    "sprain": {"ko": "삠(염좌)·타박상·멍", "group": "근육·관절·뼈", "icon": "🩹", "care": "self",
               "aliases": ["발목삠", "발목을삐", "삐었", "삐끗", "접질", "염좌", "타박", "멍이", "멍들", "부딪", "손목삠", "인대늘"],
               "doctor": "다친 곳을 디딜 수 없거나 뼈를 누르면 아픔(골절 의심), 감각 이상, 심한 부기는 진료",
               "tips": _RICE, "options": _NSAID, "topical": _PATCH, "terms": ["염좌", "타박", "삔"]},
    "knee_oa": {"ko": "무릎·관절 통증(퇴행성 관절염)", "group": "근육·관절·뼈", "icon": "🦿", "care": "self",
                "aliases": ["관절염", "퇴행성", "무릎통증", "무릎이아파", "무릎아파", "무릎관절", "관절통", "손가락관절"],
                "doctor": "붓고 열나는 관절, 갑자기 생긴 심한 통증, 아침에 1시간 넘게 뻣뻣함(류마티스 의심)은 진료",
                "tips": "체중 줄이기, 허벅지 근력 운동, 계단·쪼그려 앉기 줄이기",
                "options": [("acetaminophen", 1, "관절 통증에 무난한 진통제 — 하루 총량 지키기"),
                            ("ibuprofen", 2, "염증이 있을 때 — 고령자는 짧게만"), ("naproxen", 2, "오래 가는 소염진통제")],
                "topical": [("diclofenac", 1, "무릎·손 관절염에 먼저 권하는 바르는 약(특히 고령자)"),
                            ("ketoprofen", 2, "붙이는 파스")],
                "terms": ["관절통", "관절염"],
                "rx": [("celecoxib", "위 부담이 적은 처방 소염진통제"), ("duloxetine", "만성 관절 통증")]},
    "shoulder": {"ko": "어깨 통증·오십견", "group": "근육·관절·뼈", "icon": "💪", "care": "self",
                 "aliases": ["오십견", "어깨통증", "어깨가아파", "어깨아파", "회전근개", "유착성", "팔을못들"],
                 "doctor": "팔을 들 수 없을 만큼 아프거나 힘이 빠짐, 2~4주 넘는 통증은 정형외과 진료",
                 "tips": "통증 범위 안의 어깨 스트레칭, 온찜질", "options": _NSAID, "topical": _PATCH,
                 "terms": ["견비통", "어깨", "오십견"]},
    "muscle": {"ko": "근육통·몸살", "group": "근육·관절·뼈", "icon": "🏋️", "care": "self",
               "aliases": ["근육통", "근육이아파", "알배", "뭉침", "몸살", "운동후통증"],
               "doctor": "붓고 열나는 관절, 다친 뒤 심한 통증, 소변이 콜라색(근육 손상)이면 진료",
               "options": [("ibuprofen", 1, "염증과 통증을 줄입니다"), ("naproxen", 2, "오래 가는 소염진통제"),
                           ("acetaminophen", 2, "염증은 없고 통증만 있을 때")],
               "topical": _PATCH, "terms": ["근육통"]},
    "gout": {"ko": "통풍 발작", "group": "근육·관절·뼈", "icon": "🦶", "care": "doctor_first",
             "aliases": ["통풍", "엄지발가락", "요산"],
             "doctor": "처음 생긴 발작, 열이 나는 관절(감염 의심), 자주 재발하면 진료 — 콜히친·요산 낮추는 약은 처방이 필요합니다",
             "tips": "얼음찜질, 물 많이, 술(특히 맥주)·내장류 피하기. 아스피린은 요산을 올릴 수 있어 진통제로 쓰지 않습니다",
             "options": [("naproxen", 1, "통풍 발작의 통증·염증을 줄입니다"), ("ibuprofen", 1, "통증·염증을 줄입니다")],
             "terms": ["통풍"],
             "rx": [("colchicine", "발작 치료(신장·간 기능·상호작용 확인 필수)"), ("prednisolone", "소염진통제를 못 쓸 때")]},
    "shingles": {"ko": "대상포진", "group": "근육·관절·뼈", "icon": "⚡", "care": "doctor_first",
                 "aliases": ["대상포진", "띠모양", "한쪽에물집", "포진"],
                 "doctor": "물집이 생기고 3일(72시간) 안에 항바이러스제를 먹어야 효과가 큽니다 — 바로 진료. 눈 주위·얼굴은 더 서둘러",
                 "options": [("acetaminophen", 1, "진료 전까지 통증 완화")], "terms": [],
                 "rx": [("valacyclovir", "항바이러스제"), ("famciclovir", "항바이러스제"), ("pregabalin", "신경통")]},
    "headache": {"ko": "두통", "group": "통증", "icon": "🤕", "care": "self",
                 "aliases": ["두통", "머리가아파", "머리아파", "골이아파", "긴장성두통"],
                 "doctor": "갑자기 생긴 극심한 두통, 마비·말 어눌함·시야 이상, 열과 목 뻣뻣함은 즉시 응급실",
                 "options": [("acetaminophen", 1, "두통에 무난한 진통제"), ("ibuprofen", 1, "긴장성 두통에 효과적"),
                             ("naproxen", 2, "효과가 오래 갑니다(하루 2번)")],
                 "terms": ["두통"]},
    "migraine": {"ko": "편두통", "group": "통증", "icon": "🌀", "care": "self",
                 "aliases": ["편두통", "한쪽머리", "욱신거리는두통"],
                 "doctor": "한 달에 4번 넘거나 진통제로 조절되지 않으면 진료(예방약), 처음 생긴 심한 두통은 응급실",
                 "tips": "어둡고 조용한 곳에서 쉬기, 진통제는 아프기 시작할 때 일찍. 진통제를 한 달에 10일 넘게 먹으면 오히려 두통이 늘 수 있음",
                 "options": [("ibuprofen", 1, "초기에 먹으면 효과적"), ("naproxen", 1, "오래 가는 소염진통제"),
                             ("acetaminophen", 1, "위가 약할 때")],
                 "terms": ["편두통"], "rx": [("sumatriptan", "편두통 전용 약(트립탄)")]},
    "period": {"ko": "생리통", "group": "통증", "icon": "🩸", "care": "self",
               "aliases": ["생리통", "월경통", "생리할때배"],
               "doctor": "진통제로 조절되지 않거나 점점 심해지면 진료(자궁내막증 등)",
               "options": [("ibuprofen", 1, "통증 물질(프로스타글란딘)을 줄여 생리통에 특히 효과적"),
                           ("naproxen", 1, "오래 가는 소염진통제"), ("acetaminophen", 2, "위가 약할 때")],
               "terms": ["생리통", "월경통"]},
    "toothache": {"ko": "치통·잇몸 통증", "group": "통증", "icon": "🦷", "care": "doctor_first",
                  "aliases": ["치통", "이가아파", "이빨", "잇몸", "사랑니"],
                  "doctor": "진통제는 임시 — 치과 진료 필요. 얼굴이 붓고 열나면 바로 진료",
                  "options": [("ibuprofen", 1, "잇몸·치아 염증 통증에 효과적"), ("acetaminophen", 1, "통증을 줄입니다")],
                  "terms": ["치통", "치은염", "잇몸"]},
    # ---- 감기·호흡기
    "cold": {"ko": "감기", "group": "감기·호흡기", "icon": "🤧", "care": "self",
             "aliases": ["감기", "코감기", "목감기", "감기몸살", "몸살감기", "감기기운"],
             "includes": ["fever", "sore_throat", "runny_nose", "stuffy_nose", "cough", "phlegm"],
             "doctor": "3일 넘는 고열, 숨참·가슴 통증, 10일 넘게 낫지 않거나 좋아지다 다시 나빠지면 진료",
             "tips": "물을 충분히, 휴식. 종합감기약은 필요 없는 성분까지 들어 있어 다른 약과 겹치기 쉬움 — 있는 증상의 성분만"},
    "fever": {"ko": "발열·몸살", "group": "감기·호흡기", "icon": "🤒", "care": "self",
              "aliases": ["열", "발열", "고열", "미열", "열이나"],
              "doctor": "39도 이상, 3일 넘는 열, 숨이 차거나 의식이 흐려지면 진료",
              "options": [("acetaminophen", 1, "열을 내리고 통증을 줄입니다 — 위 부담이 적어 가장 무난"),
                          ("ibuprofen", 2, "열·통증·염증을 줄입니다 — 식후 복용, 위장 장애 주의"),
                          ("ing:dexibuprofen", 2, "이부프로펜의 활성 성분 — 적은 용량으로 같은 효과")],
              "terms": ["해열", "발열"]},
    "sore_throat": {"ko": "목 아픔", "group": "감기·호흡기", "icon": "😣", "care": "self",
                    "aliases": ["목아파", "목이아파", "인후통", "목따가", "편도", "목이부었"],
                    "doctor": "침을 삼키기 힘들 만큼 붓거나 고열·발진이 있으면 진료",
                    "options": [("ibuprofen", 1, "목의 염증과 통증을 함께 줄입니다"), ("acetaminophen", 1, "통증과 열을 줄입니다")],
                    "terms": ["인후통", "인두염", "편도"]},
    "runny_nose": {"ko": "콧물·재채기", "group": "감기·호흡기", "icon": "💧", "care": "self",
                   "aliases": ["콧물", "재채기", "코가흘"],
                   "doctor": "누런 콧물·얼굴 통증이 10일 넘으면 진료(축농증)",
                   "options": [("cetirizine", 1, "콧물·재채기를 막는 2세대 항히스타민 — 졸음 적음"),
                               ("fexofenadine", 1, "졸음이 가장 적은 항히스타민"),
                               ("loratadine", 1, "하루 1번, 졸음 거의 없음"),
                               ("chlorpheniramine", 2, "효과가 빠르지만 졸음·입마름 — 운전 전·고령자 피함")],
                   "terms": ["콧물", "재채기"]},
    "stuffy_nose": {"ko": "코막힘", "group": "감기·호흡기", "icon": "👃", "care": "self",
                    "aliases": ["코막힘", "코가막", "코막혀"],
                    "doctor": "1주 넘게 계속되면 진료",
                    "options": [("pseudoephedrine", 1, "코 점막 혈관을 좁혀 코막힘을 풉니다 — 혈압·불면 주의"),
                                ("oxymetazoline", 2, "코 스프레이 — 3일 이내만(오래 쓰면 더 막힘)")],
                    "terms": ["코막힘"]},
    "cough": {"ko": "마른기침", "group": "감기·호흡기", "icon": "😷", "care": "self",
              "aliases": ["기침", "마른기침", "잔기침"],
              "doctor": "3주 넘는 기침, 피 섞인 가래, 숨참·쌕쌕거림이 있으면 진료",
              "options": [("dextromethorphan", 1, "뇌의 기침 중추를 진정시킵니다 — 가래 없는 기침에"),
                          ("ing:levodropropizine", 2, "기관지 신경에 작용 — 졸음이 적음")],
              "terms": ["진해", "기침"]},
    "phlegm": {"ko": "가래", "group": "감기·호흡기", "icon": "🫁", "care": "self",
               "aliases": ["가래", "객담"],
               "doctor": "누렇거나 피 섞인 가래, 열이 계속되면 진료",
               "options": [("acetylcysteine", 1, "가래를 묽게 해 잘 나오게 합니다"), ("ambroxol", 1, "가래 배출을 돕습니다"),
                           ("guaifenesin", 2, "가래를 묽게 합니다")],
               "terms": ["거담", "가래", "객담"]},
    "flu": {"ko": "독감(인플루엔자)", "group": "감기·호흡기", "icon": "🦠", "care": "doctor_first",
            "aliases": ["독감", "인플루엔자", "플루"],
            "doctor": "증상 시작 48시간 안에 항바이러스제를 쓰면 효과가 큽니다 — 65세 이상·임신·만성질환·어린이는 바로 진료",
            "tips": "물 충분히, 휴식, 다른 사람과 거리 두기",
            "options": [("acetaminophen", 1, "열·몸살을 줄입니다"), ("ibuprofen", 2, "열·통증을 줄입니다")],
            "terms": [], "rx": [("oseltamivir", "항바이러스제(5일)"), ("baloxavir marboxil", "항바이러스제(1회)")]},
    "covid": {"ko": "코로나19", "group": "감기·호흡기", "icon": "😷", "care": "doctor_first",
              "aliases": ["코로나", "코비드", "covid", "확진"],
              "doctor": "65세 이상·기저질환·면역저하는 먹는 치료제 대상일 수 있어 바로 진료. 숨참·가슴 통증은 응급실",
              "options": [("acetaminophen", 1, "열·몸살을 줄입니다"), ("ibuprofen", 2, "열·통증을 줄입니다")],
              "terms": [], "rx": [("nirmatrelvir", "먹는 치료제(팍스로비드) — 다른 약과 상호작용이 매우 많아 약 목록 확인 필수")]},
    "allergic_rhinitis": {"ko": "알레르기 비염", "group": "감기·호흡기", "icon": "🌼", "care": "self",
                          "aliases": ["비염", "알레르기비염", "꽃가루", "코가간지", "환절기콧물"],
                          "doctor": "약으로 조절되지 않거나 숨이 차고 쌕쌕거리면(천식 동반) 진료",
                          "tips": "외출 후 씻기, 실내 습도·먼지 관리, 식염수 코 세척",
                          "options": [("cetirizine", 1, "콧물·재채기·가려움 — 졸음 적음"), ("fexofenadine", 1, "졸음이 가장 적음"),
                                      ("loratadine", 1, "하루 1번")],
                          "terms": ["알레르기성비염", "비염"],
                          "rx": [("mometasone", "코에 뿌리는 스테로이드"), ("montelukast", "비염·천식"),
                                 ("levocetirizine", "처방 항히스타민")]},
    "sinusitis": {"ko": "축농증(부비동염)", "group": "감기·호흡기", "icon": "🤯", "care": "doctor_first",
                  "aliases": ["축농증", "부비동염", "누런콧물", "코뒤로넘어"],
                  "doctor": "10일 넘는 누런 콧물·얼굴 통증, 고열, 눈 주위 부기는 진료(항생제 필요 여부 판단)",
                  "tips": "식염수 코 세척, 물 충분히",
                  "options": [("acetaminophen", 1, "얼굴 통증·열을 줄입니다"), ("pseudoephedrine", 2, "코막힘 — 혈압·불면 주의")],
                  "terms": ["부비동염", "축농"]},
    # ---- 소화기
    "heartburn": {"ko": "속쓰림·역류성 식도염", "group": "소화기", "icon": "🔥", "care": "self",
                  "aliases": ["속쓰림", "속이쓰", "위산", "역류", "신물", "가슴쓰림", "식도염"],
                  "doctor": "검은 변, 피 토함, 삼키기 힘듦, 체중 감소, 2주 넘는 증상은 진료",
                  "tips": "식후 바로 눕지 않기, 야식·술·커피 줄이기",
                  "options": [("famotidine", 1, "위산 분비를 줄입니다 — 몇 시간 지속"),
                              ("calcium carbonate", 1, "제산제 — 위산을 바로 중화(짧게 작용)"),
                              ("magnesium hydroxide", 2, "제산제 — 설사가 날 수 있음")],
                  "terms": ["속쓰림", "위산과다", "역류"], "rx": [("esomeprazole", "위산 억제제(PPI)")]},
    "gastritis": {"ko": "위염·명치 통증·체함", "group": "소화기", "icon": "🫃", "care": "self",
                  "aliases": ["위염", "위통", "명치", "위가아파", "배가아파", "복통", "체했", "체한", "위경련"],
                  "doctor": "검은 변·피 토함, 심한 복통(특히 오른쪽 아래·배 전체가 딱딱함 — 응급실), 50세 이후 처음 생긴 위통은 진료",
                  "tips": "소염진통제·술이 위를 더 상하게 함 — 통증엔 아세트아미노펜",
                  "options": [("famotidine", 1, "위산을 줄여 위 점막 통증을 덜어 줍니다"), ("calcium carbonate", 1, "제산제"),
                              ("trimebutine", 2, "위장 운동을 조절해 복통·더부룩함을 줄입니다")],
                  "terms": ["위염", "복통", "위통", "위경련"], "rx": [("rebamipide", "위 점막 보호제"), ("esomeprazole", "위산 억제제")]},
    "indigestion": {"ko": "소화불량·더부룩함·가스", "group": "소화기", "icon": "🍽", "care": "self",
                    "aliases": ["소화불량", "더부룩", "가스", "소화가안", "배에가스", "복부팽만"],
                    "doctor": "체중 감소·구토·삼키기 힘듦이 있으면 진료",
                    "options": [("simethicone", 1, "장내 가스를 없앱니다"), ("trimebutine", 1, "위장 운동을 조절합니다"),
                                ("pancreatin", 2, "소화 효소를 보충합니다")],
                    "terms": ["소화불량", "복부팽만", "가스"]},
    "diarrhea": {"ko": "설사", "group": "소화기", "icon": "💩", "care": "self",
                 "aliases": ["설사", "배탈", "물똥", "장염"],
                 "doctor": "피 섞인 설사, 고열, 2일 넘는 설사, 소변이 줄 만큼 탈수는 진료",
                 "tips": "물·전해질 보충(이온 음료·경구 수액), 기름진 음식 피하기",
                 "options": [("loperamide", 1, "장 운동을 늦춥니다 — 열·혈변이 있으면 쓰지 마세요")], "terms": ["설사", "지사"]},
    "constipation": {"ko": "변비", "group": "소화기", "icon": "🚽", "care": "self",
                     "aliases": ["변비", "변이안", "화장실을못", "대변이딱딱"],
                     "doctor": "갑자기 생긴 변비, 혈변, 심한 복통·구토, 체중 감소는 진료",
                     "tips": "물·채소·과일 충분히, 걷기",
                     "options": [("magnesium oxide", 1, "장에 수분을 끌어들여 변을 부드럽게 — 콩팥이 약하면 주의"),
                                 ("lactulose", 2, "변을 부드럽게 합니다"), ("bisacodyl", 2, "장을 자극 — 단기간만")],
                     "terms": ["변비"]},
    "hemorrhoid": {"ko": "치질(치핵)", "group": "소화기", "icon": "🪑", "care": "self",
                   "aliases": ["치질", "치핵", "항문", "똥꼬"],
                   "doctor": "출혈이 계속되거나 덩어리가 들어가지 않음, 심한 통증, 50세 이후 처음 생긴 혈변(대장 검사)은 진료",
                   "tips": "좌욕(따뜻한 물 5~10분), 변비 예방, 변기에 오래 앉지 않기",
                   "options": [], "terms": ["치질", "치핵", "항문"]},
    "nausea": {"ko": "멀미·메스꺼움", "group": "소화기", "icon": "🚌", "care": "self",
               "aliases": ["멀미", "차멀미", "배멀미", "메스꺼", "울렁", "구역"],
               "doctor": "구토가 계속되어 물도 못 마심, 피·커피색 구토, 심한 두통·복통이 함께 있으면 진료",
               "options": [("dimenhydrinate", 1, "멀미 — 출발 30분~1시간 전에 복용"), ("meclizine", 2, "멀미 — 효과가 오래 감")],
               "terms": ["멀미", "구역"]},
    # ---- 피부
    "athletes_foot": {"ko": "무좀(발·손·사타구니)", "group": "피부", "icon": "🦶", "care": "self",
                      "aliases": ["무좀", "발무좀", "손발톱무좀", "발톱무좀", "백선", "완선", "사타구니가려"],
                      "doctor": "발톱 무좀은 바르는 약으로 잘 낫지 않아 진료(먹는 약 — 간 검사·상호작용 확인), 당뇨 환자의 발 상처는 진료",
                      "tips": "씻은 뒤 발가락 사이까지 말리기, 증상이 없어진 뒤에도 2~4주 더 바르기",
                      "options": [], "topical": [("terbinafine", 1, "바르는 무좀약 — 하루 1~2번"),
                                                 ("clotrimazole", 1, "바르는 항진균제")],
                      "terms": ["무좀", "백선", "족부"], "rx": [("terbinafine", "먹는 무좀약(발톱)")]},
    "eczema": {"ko": "습진·피부염·피부 가려움", "group": "피부", "icon": "🖐", "care": "self",
               "aliases": ["습진", "피부염", "접촉성", "피부가려", "가려워", "아토피", "땀띠"],
               "doctor": "넓게 퍼지거나 진물·고름, 2주 넘게 낫지 않으면 피부과",
               "tips": "보습제 자주, 뜨거운 물·때밀이 피하기",
               "options": [("cetirizine", 2, "가려움으로 잠을 못 잘 때")],
               "topical": [("hydrocortisone", 1, "약한 스테로이드 연고 — 얼굴은 짧게, 1주 이내"), ("crotamiton", 2, "가려움을 줄이는 연고")],
               "terms": ["습진", "피부염", "가려움"]},
    "allergy": {"ko": "두드러기", "group": "피부", "icon": "🌸", "care": "self",
                "aliases": ["두드러기", "알레르기", "음식알레르기"],
                "doctor": "입술·혀·목이 붓거나 숨이 차면 즉시 119",
                "options": [("cetirizine", 1, "가려움·두드러기를 줄입니다"), ("fexofenadine", 1, "졸음이 가장 적음"),
                            ("loratadine", 1, "하루 1번")],
                "terms": ["두드러기"]},
    "insect_bite": {"ko": "벌레 물림", "group": "피부", "icon": "🦟", "care": "self",
                    "aliases": ["모기", "벌레물", "벌레에물", "진드기", "벌에쏘"],
                    "doctor": "벌에 쏘인 뒤 숨참·온몸 두드러기·어지러움은 즉시 119, 물린 곳이 점점 붉게 퍼지고 열나면 진료",
                    "tips": "긁지 않기, 차갑게 식히기",
                    "options": [("cetirizine", 2, "가려움이 심할 때")],
                    "topical": [("hydrocortisone", 1, "가려움·부기를 줄이는 약한 스테로이드"), ("crotamiton", 1, "가려움 완화")],
                    "terms": ["벌레", "가려움"]},
    "acne": {"ko": "여드름", "group": "피부", "icon": "🔴", "care": "self",
             "aliases": ["여드름", "뾰루지", "트러블"],
             "doctor": "붉고 아픈 덩어리(결절)·흉터가 생기면 피부과(먹는 약은 처방)",
             "options": [], "topical": [("benzoyl peroxide", 1, "여드름균을 줄이는 바르는 약 — 처음엔 조금씩")],
             "terms": ["여드름", "심상성좌창"]},
    "cold_sore": {"ko": "입술 물집(입술 헤르페스)", "group": "피부", "icon": "👄", "care": "self",
                  "aliases": ["입술물집", "입술포진", "헤르페스", "입술에물집"],
                  "doctor": "눈 주위에 생기거나 1~2주 넘게 낫지 않으면 진료",
                  "options": [], "topical": [("acyclovir", 1, "따끔거릴 때부터 일찍 바를수록 효과적")], "terms": ["입술포진", "헤르페스"]},
    "mouth_ulcer": {"ko": "입병(구내염)", "group": "피부", "icon": "👅", "care": "self",
                    "aliases": ["입병", "구내염", "입안이헐", "혓바늘", "입안상처"],
                    "doctor": "2주 넘게 낫지 않거나 자주 재발, 고열이 함께 있으면 진료",
                    "options": [], "terms": ["구내염", "아프타"]},
    "wound_burn": {"ko": "작은 상처·가벼운 화상", "group": "피부", "icon": "🩹", "care": "self",
                   "aliases": ["상처", "찰과상", "긁혔", "까졌", "화상", "데었", "베였"],
                   "doctor": "깊거나 벌어진 상처, 물집이 큰 화상, 얼굴·손·관절·생식기 화상, 붉게 퍼지며 열나면 진료",
                   "tips": "화상은 흐르는 찬물에 15~20분 식히기(얼음 직접 X), 상처는 깨끗한 물로 씻기",
                   "options": [], "topical": [("mupirocin", 2, "상처 감염이 걱정될 때 바르는 항생제 연고")],
                   "terms": ["화상", "상처", "찰과상"]},
    # ---- 눈·기타
    "dry_eye": {"ko": "눈 건조·눈 피로", "group": "눈·기타", "icon": "👁", "care": "self",
                "aliases": ["안구건조", "눈이뻑뻑", "눈건조", "눈피로", "인공눈물", "눈이피곤"],
                "doctor": "눈 통증, 시력 저하, 빛이 눈부심, 심한 충혈은 안과",
                "tips": "화면 20분마다 먼 곳 20초 보기, 실내 습도",
                "options": [], "terms": ["안구건조", "눈의건조", "눈의피로"]},
    "pink_eye": {"ko": "눈 충혈·결막염", "group": "눈·기타", "icon": "😵", "care": "doctor_first",
                 "aliases": ["결막염", "눈충혈", "눈이빨개", "눈곱", "눈병"],
                 "doctor": "눈곱·통증·시력 저하·빛 번짐이 있으면 안과 진료(세균·바이러스 구분)",
                 "tips": "눈 비비지 않기, 수건 따로 쓰기",
                 "options": [], "terms": ["결막염", "충혈"]},
    "cystitis": {"ko": "방광염(소변 볼 때 통증)", "group": "눈·기타", "icon": "🚻", "care": "doctor_first",
                 "aliases": ["방광염", "소변볼때", "배뇨통", "빈뇨", "잔뇨", "오줌"],
                 "doctor": "항생제가 필요할 수 있어 진료 — 열·옆구리 통증(신우신염), 임신 중, 남성, 피 섞인 소변은 바로 진료",
                 "tips": "물 충분히, 소변 참지 않기",
                 "options": [("acetaminophen", 2, "진료 전까지 통증 완화")], "terms": []},
    "insomnia": {"ko": "가끔 잠이 안 옴", "group": "눈·기타", "icon": "🌙", "care": "self",
                 "aliases": ["불면", "잠이안", "잠을못", "수면"],
                 "doctor": "2주 넘는 불면, 코골이·숨 멈춤, 우울감이 있으면 진료",
                 "tips": "같은 시간에 자고 일어나기, 낮잠·카페인 줄이기",
                 "options": [("doxylamine", 1, "수면을 유도하는 항히스타민 — 가끔만, 다음날 졸림 주의"),
                             ("diphenhydramine", 2, "수면 유도 — 고령자는 피함")],
                 "terms": ["불면"]},
    "fatigue": {"ko": "피로·기운 없음", "group": "눈·기타", "icon": "🔋", "care": "self",
                "aliases": ["피로", "피곤", "기운이없", "무기력"],
                "doctor": "3주 넘는 심한 피로, 체중 감소, 숨참·창백함은 진료(빈혈·갑상선 등)",
                "options": [], "terms": ["피로", "자양강장"]},
    # ---- emergencies: no medicine suggestions at all
    "em_chest": {"ko": "가슴 통증·숨이 참", "group": "응급", "icon": "🚑", "care": "emergency",
                 "aliases": ["가슴통증", "흉통", "가슴이아파", "가슴이답답", "숨이차", "숨쉬기힘", "호흡곤란", "숨이막"],
                 "doctor": "지금 바로 119 또는 응급실 — 심근경색·폐색전증·심한 천식일 수 있습니다. 약을 사서 기다리지 마세요"},
    "em_stroke": {"ko": "마비·말이 어눌함·의식 저하", "group": "응급", "icon": "🚑", "care": "emergency",
                  "aliases": ["마비", "말이어눌", "한쪽힘", "얼굴이비뚤", "의식이없", "의식을잃", "쓰러졌"],
                  "doctor": "지금 바로 119 — 뇌졸중은 몇 시간 안의 치료가 결과를 바꿉니다"},
    "em_bleed": {"ko": "피를 토함·검은 변·많은 출혈", "group": "응급", "icon": "🚑", "care": "emergency",
                 "aliases": ["피를토", "토혈", "각혈", "검은변", "짜장변", "피가안멈"],
                 "doctor": "지금 바로 119 또는 응급실 — 위장관 출혈일 수 있습니다"},
    "em_allergy": {"ko": "입술·혀·목이 부음(심한 알레르기)", "group": "응급", "icon": "🚑", "care": "emergency",
                   "aliases": ["입술이부", "혀가부", "목이부어숨", "아나필락시스"],
                   "doctor": "지금 바로 119 — 아나필락시스일 수 있습니다(에피네프린 자가주사기가 있으면 사용)"},
    "em_seizure": {"ko": "경련", "group": "응급", "icon": "🚑", "care": "emergency",
                   "aliases": ["경련", "발작을", "몸이떨리며의식"],
                   "doctor": "지금 바로 119 — 주변의 위험한 물건을 치우고, 입에 아무것도 넣지 마세요"},
    "em_overdose": {"ko": "약을 너무 많이 먹음", "group": "응급", "icon": "🚑", "care": "emergency",
                    "aliases": ["과다복용", "많이먹었", "한통다", "삼켰"],
                    "doctor": "지금 바로 119 — 먹은 약 상자를 챙겨 가세요. 억지로 토하게 하지 마세요"},
}
POPULAR = ["cold", "fever", "sore_throat", "runny_nose", "cough", "headache", "low_back", "disc", "knee_ligament",
           "sprain", "muscle", "period", "heartburn", "indigestion", "diarrhea", "constipation", "allergy",
           "allergic_rhinitis", "athletes_foot", "insomnia", "nausea"]
COLD_NOTE = "종합감기약은 여러 성분이 섞여 있어 다른 약과 같은 성분이 겹치기 쉽습니다 — 필요한 증상의 성분만 고르는 것이 안전합니다."
SEVERE_CLASSES = {"nsaid", "opioid", "sedative", "anticoagulant", "pde5"}
SEDATING = {"chlorpheniramine", "doxylamine", "diphenhydramine", "dimenhydrinate", "meclizine"}
NAME_KO = {"calcium carbonate": "탄산칼슘", "magnesium hydroxide": "수산화마그네슘", "magnesium oxide": "산화마그네슘",
           "lactulose": "락툴로오스", "ing:dexibuprofen": "덱시부프로펜", "ing:levodropropizine": "레보드로프로피진",
           "ing:eperisone": "에페리손"}
_NOT_ORAL = re.compile(r"주사|주$|주\(|주\d|앰플|바이알|수액|프리필드")
_TOPICAL = re.compile(r"플라스타|카타플라스마|파프|겔|젤|크림|연고|로션|패취|패치|스프레이|외용|파스|액$|에어로솔|폼")
_EYE_NOSE = re.compile(r"점안|점비|안약|비강|나잘|분무")
_CHILD_FORM = re.compile(r"시럽|현탁|어린이|키즈|츄어블|과립|산\d|산$|산\(")


def _norm(s: str) -> str:
    return re.sub(r"[\s·,./()\-~!?…'\"]+", "", (s or "").lower())


def catalog() -> list[dict]:
    """Every condition (for the search) - 'popular' ones are shown as buttons, emergencies are never buttons."""
    return [{"id": k, "ko": v["ko"], "group": v["group"], "icon": v["icon"], "care": v["care"],
             "popular": k in POPULAR} for k, v in CONDITIONS.items()]


def search(q: str, limit: int = 6) -> dict:
    """Everyday words -> conditions, most specific first ('무릎 십자인대 파열' -> knee ligament injury); an emergency
    is always first. No match: the words are searched in the approved indications of OTC products instead."""
    qn = _norm(q)
    if len(qn) < 2:
        return {"matches": [], "free": False}
    scored = []
    for cid, c in CONDITIONS.items():
        best = 0
        for a in [c["ko"].split("(")[0], *c.get("aliases", [])]:
            an = _norm(a)
            if len(an) < 2:
                continue
            if an in qn:  # the whole word is in what was typed: the longer, the more specific
                best = max(best, 2 * len(an) + (100 if c["care"] == "emergency" else 0))
            elif qn in an:  # still typing ('허리' of '허리디스크'): a weak match
                best = max(best, len(qn))
        if best:
            scored.append((best, cid))
    scored.sort(key=lambda x: -x[0])
    out = [{"id": cid, "ko": CONDITIONS[cid]["ko"], "icon": CONDITIONS[cid]["icon"], "care": CONDITIONS[cid]["care"]}
           for _, cid in scored[:limit]]
    return {"matches": out, "free": not out}


# ------------------------------------------------------------------------- OTC products by approved indication text
@cache
def _otc_labels() -> pd.DataFrame:
    """item_seq + 효능·효과 of every Korean OTC medicine (stage 'kr-labels')."""
    f = settings.processed_dir / "kr_labels.parquet"
    if not f.exists():
        return pd.DataFrame(columns=["item_seq", "efficacy", "eff"])
    k = pd.read_parquet(f, columns=["item_seq", "kind", "efficacy"])
    k = k[k["kind"].eq("일반의약품") & k["efficacy"].astype(bool)].copy()
    k["eff"] = k["efficacy"].str.replace(r"\s+", "", regex=True)
    return k[["item_seq", "efficacy", "eff"]]


def _snippet(text: str, terms: list[str]) -> str:
    """The part of the indication that names the complaint (terms are space-free; the text has spaces)."""
    flat = " ".join(str(text).split())
    where = [i for i, ch in enumerate(flat) if not ch.isspace()]  # space-free position -> position in flat
    squeezed = "".join(flat[i] for i in where)
    for t in terms:
        k = squeezed.find(t)
        if k >= 0:
            j = max(0, where[k] - 25)
            return ("…" if j else "") + flat[j:j + 90] + ("…" if j + 90 < len(flat) else "")
    return flat[:90] + ("…" if len(flat) > 90 else "")


def label_products(eng, terms: list[str], limit: int = 8) -> list[dict]:
    """Marketed Korean OTC medicines whose approved indication (효능·효과) names one of the terms: one product per
    ingredient combination, focused products first (more terms matched, fewer ingredients, shorter indication)."""
    terms = [_norm(t) for t in terms if len(_norm(t)) >= 2]
    lab = _otc_labels()
    if not terms or not len(lab):
        return []
    hit = lab.assign(score=sum(lab["eff"].str.contains(t, regex=False).astype(int) for t in terms))
    hit = hit[hit["score"] > 0]
    P = eng.products
    P = P[(P["country"] == "KR") & P["active"] & P["otc"] & ~P["name"].str.contains(_NOT_ORAL)]
    m = hit.merge(P[["item_seq", "name", "ingredients"]], on="item_seq")
    if not len(m):
        return []
    m = m.assign(n_ing=m["ingredients"].map(len), n_eff=m["eff"].str.len(), n_name=m["name"].str.len())
    m = m.sort_values(["score", "n_ing", "n_eff", "n_name"], ascending=[False, True, True, True])
    out, seen = [], set()
    for r in m.itertuples():
        combo = frozenset(r.ingredients)
        if combo in seen:
            continue
        seen.add(combo)
        out.append({"name": r.name.split("(")[0].strip(), "full": r.name, "ingredients": list(r.ingredients),
                    "snippet": _snippet(r.efficacy, terms), "topical": bool(_TOPICAL.search(r.name))})
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------------------------- product suggestions
def _products(eng, key: str, limit: int = 3, child: bool = False, topical: bool | None = False,
              otc_only: bool = False) -> list[dict]:
    """Korean marketed products with this ingredient: single-ingredient first (else simple 2-3 ingredient products),
    no injections, OTC first; for children the syrups / chewables. topical=True: creams / patches only, False: no
    creams / patches / eye or nose drops (tablets), None: either."""
    P = eng.products
    base = P[(P["country"] == "KR") & P["active"] & ~P["name"].str.contains(_NOT_ORAL)]
    if topical is True:
        base = base[base["name"].str.contains(_TOPICAL) & ~base["name"].str.contains(_EYE_NOSE)]
    elif topical is False:
        base = base[~base["name"].str.contains(_TOPICAL)]
    if otc_only:
        base = base[base["otc"]]
    rows = base[base["ingredients"].map(lambda v: list(v) == [key])]
    combo = False
    if not len(rows):  # e.g. dextromethorphan is sold only in combinations
        rows = base[base["ingredients"].map(lambda v: key in v and len(v) <= 3)]
        combo = True
    if not len(rows):
        return []
    rows = rows.assign(_n=rows["name"].str.len(), _c=rows["name"].str.contains(_CHILD_FORM) == child)
    rows = rows.sort_values(["otc", "_c", "_n"], ascending=[False, False, True])
    out, seen = [], set()
    for r in rows.itertuples():
        name = r.name.split("(")[0].strip()
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "full": r.name, "otc": bool(r.otc), "combo": combo})
        if len(out) >= limit:
            break
    return out


class _Ctx:
    """The patient and the medicines already entered, and a cache of screened ingredients."""

    def __init__(self, eng, current: list[dict], patient: Patient):
        self.eng, self.patient = eng, patient
        self.pa = assess(patient)
        self.U = {g.key: g for p in current for g in p["ingredients"]}
        self.owner = {g.key: p["input"] for p in current for g in p["ingredients"]}
        self.classes = {drug_class(k): k for k in self.U if drug_class(k)}
        self.age = patient.age
        self.pregnant = "pregnancy" in self.pa["diseases"]
        self.kid = (self.pa.get("kidney") or {}).get("clearance")
        self.child = self.age is not None and self.age < 12
        self.cache: dict[str, dict | None] = {}

    def ingredient(self, key: str):
        """An ingredient key of the table / of a product -> Ingredient (None if the database does not know it)."""
        for k in (key, f"ing:{key}", f"lab:{key}"):
            try:
                if k in self.eng.name_idx or k in self.eng.ext_ing or k.startswith(("lab:", "bio:")):
                    return self.eng._ext_ingredient(k)
            except Exception:
                continue
        return None

    def screen(self, key: str) -> dict | None:
        if key not in self.cache:
            g = self.ingredient(key)
            self.cache[key] = None if g is None else _screen(self, g)
        return self.cache[key]


def _screen(cx: _Ctx, g) -> dict:
    eng, key = cx.eng, g.key
    reasons, status = [], "ok"

    def worse(st: str, why: str):
        nonlocal status
        reasons.append({"sev": st, "text": why})
        if st == "avoid" or (st == "caution" and status == "ok"):
            status = st

    if key in cx.U:
        worse("avoid", f"이미 넣은 '{cx.owner[key]}'에 같은 성분이 있습니다 — 함께 먹으면 과다 복용")
    cid = drug_class(key)
    if cid and cid in cx.classes and cx.classes[cid] != key:
        sev = "avoid" if cid in SEVERE_CLASSES else "caution"
        worse(sev, f"'{cx.owner[cx.classes[cid]]}'와 같은 계열({DRUG_CLASSES[cid][0]})이라 효과·부작용이 겹칩니다")
    for c in cx.U.values():
        if c.key == key:
            continue
        lvl, why = eng.pair_level(g, c)
        if lvl == "높음":
            worse("avoid", f"'{cx.owner[c.key]}'와 함께 먹으면 위험: {why}")
        elif lvl == "주의":
            worse("caution", f"'{cx.owner[c.key]}'와 상호작용: {why}")
    for d in eng._drug_disease([g], cx.pa, cx.patient):
        worse("avoid" if d["severity"] == "금기" else "caution", f"{d['disease']}: {d['reason']}".rstrip(" —"))
    groups = drug_groups(key)
    # self-care is stricter than a prescriber's 'caution': these are simply not good choices to pick up yourself
    if cx.pregnant and "nsaid" in groups:
        worse("avoid", "임신 중에는 소염진통제를 스스로 고르지 않습니다(특히 20주 이후 금기) — 아세트아미노펜을 상의하세요")
    if cx.age is not None and cx.age >= 65 and g.bare in SEDATING:
        worse("avoid", "65세 이상은 졸음·혼돈·낙상 위험이 커 피하는 약입니다(Beers 기준)")
    if cx.kid is not None and cx.kid < 30 and g.bare in ("magnesium oxide", "magnesium hydroxide"):
        worse("avoid", "콩팥 기능이 크게 떨어져 마그네슘이 쌓일 수 있습니다")
    if cx.child and g.bare in ("pseudoephedrine", "dextromethorphan", "doxylamine", "diphenhydramine", "chlorpheniramine",
                               "loperamide", "naproxen"):
        worse("caution", "어린이는 사용 연령·용량 제한이 있습니다 — 어린이용 제품으로 약사와 상의")
    seen, uniq = set(), []
    for r in reasons:
        if r["text"] not in seen:
            seen.add(r["text"])
            uniq.append(r)
    return {"name": NAME_KO.get(key) or eng.ko(g).split("(")[0], "status": status, "reasons": uniq}


_ORDER = {"ok": 0, "caution": 1, "avoid": 2}


def _options(cx: _Ctx, rows, topical: bool | None) -> list[dict]:
    opts = []
    for key, rank, does in rows:
        c = cx.screen(key)
        if c is None:
            continue
        g = cx.ingredient(key)
        opts.append({"key": key, "rank": rank, "does": does, **c,
                     "products": _products(cx.eng, g.key, child=cx.child, topical=topical,
                                           otc_only=topical is True)})
    opts.sort(key=lambda o: (_ORDER[o["status"]], o["rank"]))
    return [o for o in opts if o["products"] or topical is not True]  # a cream nobody sells is no option


def _label_cards(cx: _Ctx, terms: list[str], shown: set[str]) -> list[dict]:
    out = []
    for p in label_products(cx.eng, terms, limit=10):
        if set(p["ingredients"]) <= shown and len(p["ingredients"]) == 1:
            continue  # the same single ingredient is already suggested above
        status, reasons = "ok", []
        names = []
        for k in p["ingredients"]:
            c = cx.screen(k)
            g = cx.ingredient(k)
            names.append(NAME_KO.get(k) or (cx.eng.ko(g).split("(")[0] if g else k))
            if c:
                if _ORDER[c["status"]] > _ORDER[status]:
                    status = c["status"]
                reasons += [r for r in c["reasons"] if r not in reasons]
        out.append({**p, "ingredients_ko": names, "status": status, "reasons": reasons[:3]})
        if len(out) >= 6:
            break
    out.sort(key=lambda o: _ORDER[o["status"]])
    return out


def recommend(eng, symptom_ids: list[str], current: list[dict], patient: Patient, query: str | None = None,
              expert: bool = False) -> dict:
    """symptom_ids = condition ids (a condition made of symptoms - 감기 - adds its symptoms); query = free text that
    matched no condition: its words are searched in the OTC indications. current = [{"input", "ingredients"}]."""
    cx = _Ctx(eng, current, patient)
    ids: list[str] = []
    for sid in symptom_ids:
        if sid in CONDITIONS and sid not in ids:
            ids.append(sid)
            ids += [i for i in CONDITIONS[sid].get("includes", []) if i not in ids]
    cards = []
    for sid in ids:
        s = CONDITIONS[sid]
        card = {"id": sid, "ko": s["ko"], "icon": s["icon"], "care": s["care"], "doctor": s.get("doctor", ""),
                "tips": s.get("tips", ""), "options": [], "topical": [], "label_products": [], "rx": [],
                "parent": next((p for p in ids if sid in CONDITIONS[p].get("includes", [])), None)}
        if s["care"] != "emergency":  # an emergency gets no medicine suggestion at all
            card["options"] = _options(cx, s.get("options", []), topical=False)
            card["topical"] = _options(cx, s.get("topical", []), topical=True)
            shown = {cx.ingredient(o["key"]).key for o in card["options"] + card["topical"]}
            card["label_products"] = _label_cards(cx, s.get("terms", []), shown)
            if expert:
                card["rx"] = [{"key": k, "does": does, **c, "products": _products(eng, cx.ingredient(k).key, limit=2,
                                                                                  topical=None)}
                              for k, does in s.get("rx", []) if (c := cx.screen(k))]
        cards.append(card)
    if query and not cards:  # nothing in the table: the words themselves, in the approved OTC indications
        words = [w for w in re.split(r"[\s,·/]+", query) if len(_norm(w)) >= 2]
        terms = [re.sub(r"(이|가|을|를|은|는|에|의|이에요|예요|아파요|아파|통증|증상)$", "", w) for w in words] or [query]
        cards.append({"id": "free", "ko": f"'{query.strip()[:30]}'", "icon": "🔎", "care": "unknown",
                      "doctor": "진단명이 확실하지 않거나 증상이 심하면 먼저 약사·의사와 상의하세요",
                      "tips": "", "options": [], "topical": [], "rx": [], "parent": None,
                      "label_products": _label_cards(cx, terms, set())})
    notes = []
    if any(CONDITIONS.get(s, {}).get("group") == "감기·호흡기" for s in ids):
        notes.append(COLD_NOTE)
    if cx.child:
        notes.append("어린이는 나이·체중에 맞는 어린이용 제품과 용량을 써야 합니다 — 약사와 상의하세요.")
    if cx.pregnant:
        notes.append("임신 중에는 일반약도 먼저 의사·약사와 상의하세요(해열·진통은 보통 아세트아미노펜).")
    if any(c["care"] == "emergency" for c in cards):
        notes.insert(0, "🚑 응급일 수 있는 증상이 있습니다 — 약을 찾기 전에 119 또는 응급실로 가세요.")
    return {"symptoms": cards, "notes": notes,
            "disclaimer": "증상 완화를 위한 일반의약품 후보입니다. 진단·처방이 아니며, 복용 전 약사와 상의하세요."
                          + (" 처방약은 의사의 판단이 필요한 참고 정보입니다." if expert else "")}
