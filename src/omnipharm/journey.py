"""Drug journey through the body - the data behind the animated body view of the UI.

For one analysis it lays out, step by step, where each ingredient goes after it is taken (stomach, gut, liver
enzymes, blood, its target, the organs where the combination causes trouble, the kidneys) using ONLY the
evidence the verdict already uses: FDA CYP index tables, ChEMBL in-vitro CYP / mechanism data, curated class
targets, clinical pair rules, MFDS DUR, label interaction text, AI organ signals, disease and dose checks and the
patient's kidney / liver function. Nothing is invented for effect - a stage without findings says so.
"""

from __future__ import annotations

import re

from omnipharm.dosing import DOSE_LIMITS, PPI
from omnipharm.knowledge import BIOLOGICS, CYP_FDA, DRUG_CLASSES, PRODRUGS_ACTIVATED_BY, drug_groups
from omnipharm.release import RELEASE_KO

# body sites the UI can draw (frontend/src/components/BodyJourney.jsx has a position for each)
SITES = {
    "brain": "뇌", "eye": "눈", "thyroid": "갑상선", "lung": "폐", "heart": "심장", "vessel": "혈관·혈액",
    "stomach": "위", "liver": "간", "pancreas": "췌장·혈당", "intestine": "장", "kidney": "콩팥", "bladder": "방광",
    "muscle": "근육", "joint": "관절·염증 부위", "skin": "피부", "bone": "뼈", "reproductive": "생식기",
    "infection": "감염 부위(병원체)",
}

# what an ingredient binds and what that does (class level). First match = main target.
GROUP_TARGETS: list[tuple[str, str, str, str]] = [
    ("opioid", "brain", "μ-오피오이드 수용체", "통증 신호를 막고 호흡 중추를 누릅니다"),
    ("sedative", "brain", "GABA-A 수용체", "뇌 신경을 가라앉혀 잠·진정을 유도합니다"),
    ("gabapentinoid", "brain", "칼슘 통로 α2δ", "흥분된 신경 신호를 줄입니다"),
    ("ssri", "brain", "세로토닌 수송체(SERT)", "세로토닌 재흡수를 막아 기분을 조절합니다"),
    ("sedating_antihistamine", "brain", "H1 수용체(뇌까지 들어감)", "알레르기를 막고 뇌에서 졸음을 일으킵니다"),
    ("antihistamine2", "skin", "H1 수용체", "히스타민(알레르기 물질)이 붙지 못하게 막습니다"),
    ("antitussive", "brain", "연수 기침 중추", "기침 반사를 누릅니다"),
    ("decongestant", "vessel", "α 아드레날린 수용체", "코 점막 혈관을 좁혀 코막힘을 풉니다 (혈압↑)"),
    ("dopamine_antagonist", "brain", "도파민 D2 수용체", "도파민 신호를 막습니다"),
    ("anticholinergic", "bladder", "무스카린 수용체", "방광·장 근육의 수축을 줄입니다"),
    ("nsaid", "joint", "COX-1·COX-2 효소", "통증·염증 물질(프로스타글란딘)을 만들지 못하게 합니다"),
    ("acetaminophen", "brain", "뇌의 통증·체온 중추", "열을 내리고 통증을 줄입니다"),
    ("warfarin", "liver", "비타민K 재활용 효소(VKORC1)", "간에서 응고 인자가 만들어지는 것을 줄입니다"),
    ("anticoagulant", "vessel", "응고 인자·혈소판", "피가 굳는 것을 막습니다"),
    ("statin", "liver", "HMG-CoA 환원효소", "간에서 콜레스테롤 합성을 막습니다"),
    ("ace_arb", "vessel", "안지오텐신 경로(ACE·AT1 수용체)", "혈관을 넓혀 혈압을 낮춥니다"),
    ("nondhp_ccb", "heart", "L형 칼슘 통로", "심박수와 혈압을 낮춥니다"),
    ("nonselective_beta_blocker", "heart", "β 아드레날린 수용체", "심박수와 혈압을 낮춥니다"),
    ("thiazide", "kidney", "Na-Cl 수송체", "소금·물을 소변으로 빼 혈압을 낮춥니다"),
    ("loop_diuretic", "kidney", "Na-K-2Cl 수송체", "물을 강하게 빼냅니다"),
    ("k_sparing", "kidney", "알도스테론 수용체·Na 통로", "칼륨을 몸에 남깁니다"),
    ("digoxin", "heart", "Na⁺/K⁺ 펌프", "심장 수축을 강하게 합니다"),
    ("metformin", "liver", "간 포도당 생성(AMPK)", "간에서 당이 만들어지는 것을 줄입니다"),
    ("sulfonylurea", "pancreas", "췌장 β세포 SUR1", "인슐린을 분비시킵니다"),
    ("insulin", "muscle", "인슐린 수용체", "당을 세포 안으로 넣어 혈당을 낮춥니다"),
    ("glp1", "pancreas", "GLP-1 수용체", "인슐린 분비↑·위 배출 지연·식욕 억제"),
    ("thiazolidinedione", "muscle", "PPAR-γ", "인슐린이 잘 듣게 합니다"),
    ("corticosteroid", "vessel", "글루코코르티코이드 수용체", "온몸의 염증·면역 반응을 누릅니다"),
    ("tnf_inhibitor", "joint", "TNF-α 단백질", "염증 신호 단백질을 붙잡아 없앱니다"),
    ("immunosuppressant", "joint", "면역 세포", "면역 반응을 누릅니다"),
    ("fluoroquinolone", "infection", "세균 DNA 회전효소", "세균의 DNA 복제를 막습니다"),
    ("macrolide", "infection", "세균 리보솜(50S)", "세균의 단백질 합성을 막습니다"),
    ("tetracycline", "infection", "세균 리보솜(30S)", "세균의 단백질 합성을 막습니다"),
    ("aminoglycoside", "infection", "세균 리보솜(30S)", "세균을 죽입니다"),
    ("metronidazole", "infection", "혐기성 세균 DNA", "세균 DNA를 끊습니다"),
    ("pde5", "vessel", "PDE5 효소", "혈관 평활근을 풀어 혈관을 넓힙니다"),
    ("levothyroxine", "thyroid", "갑상선 호르몬 수용체", "부족한 갑상선 호르몬을 채웁니다"),
    ("bisphosphonate", "bone", "파골세포", "뼈가 깎이는 것을 막습니다"),
    ("oral_contraceptive", "reproductive", "에스트로겐·프로게스테론 수용체", "배란을 막습니다"),
    ("botulinum", "muscle", "신경-근육 접합부(SNAP-25)", "아세틸콜린 방출을 막아 근육을 이완시킵니다"),
    ("neuromuscular_blocker", "muscle", "니코틴성 아세틸콜린 수용체", "근육을 마비시킵니다"),
    ("potassium_supplement", "kidney", "혈중 칼륨", "칼륨을 보충합니다"),
    ("licorice", "kidney", "11β-HSD2 효소", "칼륨 배출·나트륨 저류를 늘립니다"),
    ("bleeding_herb", "vessel", "혈소판", "혈소판 응집을 약하게 합니다"),
    ("st_johns_wort", "brain", "세로토닌계", "기분을 조절하고 간 효소(CYP3A4)를 늘립니다"),
    ("ginseng", "vessel", "혈관·혈소판", "혈소판·혈당에 영향을 줍니다"),
    ("polyvalent_cation", "intestine", "장 점막(미네랄 흡수)", "미네랄을 보충합니다"),
    # food, drink, smoking
    ("alcohol", "brain", "GABA-A 수용체", "뇌 활동을 가라앉힙니다 (진정 효과 증폭)"),
    ("grapefruit", "intestine", "장벽 CYP3A4", "장에서 약을 분해하는 효소를 망가뜨립니다"),
    ("dairy", "stomach", "칼슘 이온", "일부 약과 엉겨 붙어 흡수를 막습니다"),
    ("vitamin_k_food", "liver", "비타민K 의존 응고 인자", "간에서 응고 인자 생성을 늘립니다"),
    ("smoking", "lung", "타르(다환 방향족 탄화수소)", "간의 CYP1A2 효소를 늘려 일부 약을 빨리 분해합니다"),
]
DRUG_TARGETS: dict[str, tuple[str, str, str]] = {
    "caffeine": ("brain", "아데노신 수용체", "졸음 신호를 막아 각성시킵니다"),
    "minoxidil": ("vessel", "칼륨 통로(KATP) 개방", "혈관을 넓혀 모낭 혈류를 늘립니다"),
    "finasteride": ("skin", "5α-환원효소(모낭)", "탈모를 일으키는 DHT 생성을 막습니다"),
    "dutasteride": ("skin", "5α-환원효소(모낭)", "탈모를 일으키는 DHT 생성을 막습니다"),
    "isotretinoin": ("skin", "레티노산 수용체(피지선)", "피지선을 줄입니다"),
    "melatonin": ("brain", "멜라토닌 수용체", "수면 리듬을 맞춥니다"),
    "famotidine": ("stomach", "H2 수용체", "위산 분비를 줄입니다"),
    "amlodipine": ("vessel", "L형 칼슘 통로", "혈관을 넓혀 혈압을 낮춥니다"),
    "nifedipine": ("vessel", "L형 칼슘 통로", "혈관을 넓혀 혈압을 낮춥니다"),
    "felodipine": ("vessel", "L형 칼슘 통로", "혈관을 넓혀 혈압을 낮춥니다"),
    "metoprolol": ("heart", "β1 수용체", "심박수와 혈압을 낮춥니다"),
    "bisoprolol": ("heart", "β1 수용체", "심박수와 혈압을 낮춥니다"),
    "atenolol": ("heart", "β1 수용체", "심박수와 혈압을 낮춥니다"),
    "nebivolol": ("heart", "β1 수용체", "심박수와 혈압을 낮춥니다"),
    "allopurinol": ("joint", "잔틴 산화효소", "요산 생성을 줄입니다"),
    "febuxostat": ("joint", "잔틴 산화효소", "요산 생성을 줄입니다"),
    "colchicine": ("joint", "미세소관(백혈구)", "통풍 염증을 가라앉힙니다"),
    "lithium": ("brain", "신경 신호 전달(이노시톨 경로)", "기분 변동을 안정시킵니다"),
    "montelukast": ("lung", "류코트리엔 수용체", "기관지 염증·수축을 막습니다"),
    "salbutamol": ("lung", "β2 수용체", "기관지를 넓힙니다"),
    "albuterol": ("lung", "β2 수용체", "기관지를 넓힙니다"),
    "theophylline": ("lung", "PDE·아데노신 수용체", "기관지를 넓힙니다"),
    "tamsulosin": ("bladder", "α1 수용체(전립선)", "소변 길을 넓힙니다"),
    "donepezil": ("brain", "아세틸콜린 분해효소", "기억 신호를 늘립니다"),
    "levodopa": ("brain", "도파민 보충", "움직임을 부드럽게 합니다"),
    "ondansetron": ("brain", "5-HT3 수용체", "구토 신호를 막습니다"),
    "domperidone": ("stomach", "도파민 D2 수용체(위장)", "위 운동을 돕고 구역을 줄입니다"),
    "metoclopramide": ("stomach", "도파민 D2 수용체(위장·뇌)", "위 운동을 돕고 구역을 줄입니다"),
    "levosulpiride": ("stomach", "도파민 D2 수용체(위장)", "위 운동을 돕습니다"),
    "loperamide": ("intestine", "장 오피오이드 수용체", "장 운동을 늦춰 설사를 멈춥니다"),
    "sumatriptan": ("brain", "5-HT1B/1D 수용체", "뇌혈관을 좁혀 편두통을 멈춥니다"),
    "methotrexate": ("joint", "엽산 대사 효소(DHFR)", "면역·세포 증식을 누릅니다"),
    "hydroxychloroquine": ("joint", "면역 세포(리소좀)", "자가면역 염증을 누릅니다"),
    "tranexamic acid": ("vessel", "플라스미노겐", "피가 녹는 것을 막아 지혈합니다"),
    "sildenafil": ("vessel", "PDE5 효소", "혈관 평활근을 풀어 혈관을 넓힙니다"),
}
# what goes wrong when the level of a drug in this group rises / its effect is overdone
TOX_GROUPS: list[tuple[str, str, str]] = [
    ("statin", "muscle", "근육 손상(횡문근융해)"),
    ("opioid", "lung", "호흡 억제"),
    ("sedative", "brain", "과도한 진정·호흡 억제"),
    ("gabapentinoid", "brain", "졸음·호흡 억제"),
    ("anticoagulant", "vessel", "출혈"),
    ("qt_prolonging", "heart", "QT 연장·부정맥"),
    ("digoxin", "heart", "디곡신 중독(부정맥)"),
    ("pde5", "vessel", "저혈압·실신"),
    ("sulfonylurea", "pancreas", "저혈당"),
    ("insulin", "pancreas", "저혈당"),
    ("nondhp_ccb", "heart", "서맥·저혈압"),
    ("immunosuppressant", "kidney", "신장 독성·면역 과억제"),
    ("ssri", "brain", "세로토닌 과잉"),
    ("nsaid", "stomach", "위장 출혈"),
    ("sedating_antihistamine", "brain", "과도한 졸음"),
    ("seizure_threshold", "brain", "경련"),
    ("decongestant", "heart", "두근거림·혈압 상승"),
    ("acetaminophen", "liver", "간 손상"),
    ("hepatotoxic", "liver", "간 손상"),
    ("k_sparing", "kidney", "고칼륨혈증"),
    ("ace_arb", "kidney", "고칼륨혈증"),
    ("metformin", "liver", "젖산산증"),
    ("glp1", "pancreas", "저혈당·위장 장애"),
]
DRUG_TOX: dict[str, tuple[str, str]] = {
    "colchicine": ("muscle", "콜히친 중독(근육·위장)"), "theophylline": ("heart", "두근거림·경련"),
    "caffeine": ("heart", "두근거림·불면"), "lithium": ("brain", "리튬 중독(떨림·혼돈)"),
    "amlodipine": ("vessel", "저혈압·부종"), "felodipine": ("vessel", "저혈압·부종"), "nifedipine": ("vessel", "저혈압"),
    "tacrolimus": ("kidney", "신장 독성"), "cyclosporine": ("kidney", "신장 독성"), "methotrexate": ("vessel", "골수 억제"),
    "clozapine": ("brain", "경련·과진정"), "tizanidine": ("vessel", "저혈압·과진정"), "quetiapine": ("brain", "과진정"),
    "alprazolam": ("brain", "과도한 진정"), "midazolam": ("brain", "과도한 진정·호흡 억제"),
    "triazolam": ("brain", "과도한 진정"), "buspirone": ("brain", "어지러움·진정"),
}
# where a class duplication hurts (DRUG_CLASSES ids)
CLASS_TOX = {"nsaid": ("stomach", "위장 출혈·신장 손상"), "sedating_antihistamine": ("brain", "과도한 졸음·혼돈"),
             "antihistamine2": ("brain", "졸음"), "decongestant": ("heart", "두근거림·혈압 상승"),
             "antitussive": ("brain", "진정·호흡 억제"), "sedative": ("brain", "과도한 진정·호흡 억제"),
             "opioid": ("lung", "호흡 억제"), "pde5": ("vessel", "심한 저혈압"), "anticoagulant": ("vessel", "출혈"),
             "glp1": ("pancreas", "저혈당·위장 장애")}
DISEASE_SITE = {"renal": "kidney", "renal_severe": "kidney", "hepatic": "liver", "cirrhosis": "liver",
                "heart_failure": "heart", "qt_long": "heart", "arrhythmia": "heart", "cardiac": "heart",
                "hypertension": "vessel", "diabetes": "pancreas", "peptic_ulcer": "stomach", "bleeding": "vessel",
                "asthma": "lung", "glaucoma": "eye", "bph": "bladder", "epilepsy": "brain", "dementia": "brain",
                "parkinson": "brain", "pregnancy": "reproductive", "hyperthyroid": "thyroid", "gout": "joint",
                "myasthenia": "muscle", "thyroid_cancer": "thyroid", "pancreatitis": "pancreas", "infection": "infection"}
ORGAN_SITE = {"brain": "brain", "heart": "heart", "lung": "lung", "liver": "liver", "gi": "stomach", "kidney": "kidney",
              "blood": "vessel", "systemic": "skin"}
CONDITION_SITE = {"Rhabdomyolysis": "muscle", "Hypoglycaemia": "pancreas", "Pancreatitis": "pancreas",
                  "Diarrhoea": "intestine", "Constipation": "intestine", "Abdominal pain": "intestine",
                  "Hypotension": "vessel", "Hypertension": "vessel", "Syncope": "vessel"}
# free text (Korean rule / DUR text, English label sentences) -> sites, in the order they are mentioned
TEXT_SITES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), s) for p, s in (
    (r"QT|부정맥|torsade|심장 리듬|arrhythm|hERG|심근경색|두근|디곡신 독성|digoxin toxicity|서맥|bradycardia", "heart"),
    (r"위장(관)? 출혈|위궤양|속쓰림|검은 변|gastrointestinal (bleeding|hemorrhage)|GI bleeding|위 배출|구토|홍조|"
     r"disulfiram|디설피람", "stomach"),
    (r"호흡 ?억제|respiratory depression|호흡곤란|호흡", "lung"),
    (r"횡문근|근육|myopathy|rhabdomyolysis|힘줄|tendon|근력", "muscle"),
    (r"출혈|INR|bleeding|hemorrhage|haemorrhage|혈전|thrombo|멍", "vessel"),
    (r"세로토닌|serotonin syndrome|진정|졸음|sedation|CNS depression|혼수|경련|seizure|섬망|혼돈|낙상|의식|기억|"
     r"profound sedation|coma", "brain"),
    (r"저혈당|고혈당|혈당|hypoglyc", "pancreas"),
    (r"젖산|lactic acidosis|간 손상|간독성|간이 손상|hepatotox|liver (injury|damage|failure)|황달", "liver"),
    (r"칼륨|potassium|hyperkal|나트륨|전해질|신장|콩팥|신기능|renal|nephrotox|kidney", "kidney"),
    (r"저혈압|혈압|실신|hypotension|blood pressure", "vessel"),
    (r"흡수|absorption", "intestine"),
    (r"감염|infection|면역|거부반응", "infection"),
    (r"피임|태아|contracepti", "reproductive"),
)]
LEVEL_WORDS = re.compile(r"농도|concentration|exposure|AUC|plasma level", re.I)
# the clinical outcome named in a finding, most serious first - the last step of the mechanism chain
HARM_WORDS: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), h) for p, h in (
    (r"호흡 ?억제|respiratory depression", "호흡 억제"), (r"횡문근융해|근육병증|근육 손상|rhabdomyolysis|myopathy", "횡문근융해"),
    (r"세로토닌 증후군", "세로토닌 증후군"), (r"QT|돌연 부정맥|torsade", "QT 연장·부정맥"), (r"부정맥|디곡신 독성", "부정맥"),
    (r"위장관 출혈|위장 출혈", "위장관 출혈"), (r"출혈|bleeding", "출혈"), (r"INR", "INR 변동(출혈·혈전)"),
    (r"저혈당", "저혈당"), (r"고칼륨혈증", "고칼륨혈증"), (r"저칼륨혈증", "저칼륨혈증"), (r"젖산산증", "젖산산증"),
    (r"경련|seizure", "경련"), (r"저혈압|실신", "저혈압·실신"), (r"간 손상|간독성|hepatotox", "간 손상"),
    (r"디설피람|구토", "디설피람 유사 반응"), (r"혈전", "혈전"), (r"근육 마비|근력 약화", "근력 약화"),
    (r"힘줄", "힘줄 파열"), (r"요폐|소변", "요폐"), (r"안압|녹내장", "안압 상승"), (r"혈압을 올|혈압 상승", "혈압 상승"),
    (r"거부반응", "거부반응"), (r"감염", "감염"), (r"흡수", "흡수 감소"), (r"효과가 떨어|효과를 떨어|효과를 줄", "효과 감소"),
    (r"진정|졸음|혼수", "과도한 진정"), (r"태아|기형", "태아 위험"),
)]
# the shape of the protein a drug binds, for the molecular close-up (receptor / channel / transporter / enzyme ...)
PROTEIN_KIND: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), k) for p, k in (
    (r"세균|리보솜|병원체|바이러스|virus|bacter|DNA 회전|DNA$|혐기성", "pathogen"),
    (r"인슐린 수용체", "enzyme"),
    (r"GABA|니코틴|5-HT3|글리신|통로|channel|α2δ", "channel"),
    (r"수송체|펌프|ATPase|transporter|SERT", "transporter"),
    (r"글루코코르티코이드|에스트로겐|프로게스테론|갑상선 호르몬|PPAR|레티노산|안드로겐|광물코르티코이드|알도스테론|비타민 ?D", "nuclear"),
    (r"수용체|receptor", "gpcr"),
    (r"효소|COX|PDE|키나아제|kinase|분해효소|산화효소|환원효소|VKORC1|HMG|AMPK|응고 인자|플라스미노겐|SUR1|11β|CYP|DHFR", "enzyme"),
)]


# ---- time course (hours after the dose)
DEFAULT_PK = (1.5, 6.0)  # typical oral tmax / half-life when a label gives no numbers
FOOD_PK = {  # (tmax, how long the effect lasts as a 'half-life'), with a plain note
    "alcohol": (0.75, 3.0, "술은 30분~1시간이면 혈중 최고치에 이르고, 한 잔을 분해하는 데 약 1~2시간 걸립니다"),
    "grapefruit": (2.0, 24.0, "자몽이 망가뜨린 장 효소는 새로 만들어질 때까지 최대 72시간 효과가 남습니다"),
    "smoking": (0.1, 168.0, "흡연으로 늘어난 간 효소는 금연 후 약 1주에 걸쳐 원래대로 돌아옵니다"),
    "dairy": (1.0, 2.0, "우유·칼슘은 함께 먹은 뒤 약 2시간 동안 흡수를 방해합니다"),
    "vitamin k foods": (6.0, 48.0, "비타민K는 응고 인자 생성에 며칠에 걸쳐 영향을 줍니다"),
}
# adult reference values (tmax h, half-life h) for common ingredients whose labels give no numbers (OTC "Drug Facts")
# or describe another population (caffeine citrate labels are for newborns: half-life 3-4 days)
PK_REFERENCE = {
    "acetaminophen": (0.75, 2.5), "caffeine": (0.75, 5.0), "diphenhydramine": (2.0, 8.0), "chlorpheniramine": (2.5, 20.0),
    "pseudoephedrine": (2.0, 6.0), "phenylephrine": (1.0, 2.5), "dextromethorphan": (2.5, 3.5), "guaifenesin": (0.5, 1.0),
    "naproxen": (2.0, 14.0), "aspirin": (1.0, 2.5), "loxoprofen": (0.5, 1.3), "dexibuprofen": (1.5, 2.0),
    "cetirizine": (1.0, 8.0), "levocetirizine": (0.9, 8.0), "loratadine": (1.3, 8.0), "fexofenadine": (2.6, 14.0),
    "famotidine": (2.0, 3.0), "lithium": (1.5, 24.0), "nirmatrelvir": (3.0, 6.0), "simvastatin": (1.9, 2.0),
    "clopidogrel": (1.0, 6.0), "warfarin": (4.0, 40.0), "levothyroxine": (2.0, 168.0), "finasteride": (2.0, 6.0),
    "minoxidil": (1.0, 4.2), "doxylamine": (2.5, 10.0), "triprolidine": (2.0, 4.0), "methylephedrine": (2.0, 6.0),
    "dihydrocodeine": (1.5, 4.0), "codeine": (1.0, 3.0), "isopropylantipyrine": (1.0, 1.5), "ethenzamide": (1.0, 2.0),
}
# release-form specific adult values (tmax h, half-life h) that win over the label medians: common drugs whose labels
# mix populations / metabolites (bupropion, theophylline) or carry no numbers for the extended-release product
PK_FORM_REFERENCE = {
    ("bupropion", "IR"): (3.0, 21.0), ("bupropion", "ER"): (5.0, 21.0),
    ("theophylline", "IR"): (2.0, 8.0), ("theophylline", "ER"): (6.0, 8.0),
    ("nifedipine", "ER"): (6.0, 7.0), ("metformin", "ER"): (7.0, 6.2), ("venlafaxine", "ER"): (5.5, 5.0),
    ("paroxetine", "ER"): (8.0, 15.0), ("diclofenac", "DR"): (2.5, 2.0), ("diclofenac", "ER"): (5.0, 2.0),
    ("acetaminophen", "ER"): (2.0, 3.0), ("tamsulosin", "ER"): (6.0, 10.0), ("gabapentin", "ER"): (8.0, 6.0),
    ("pregabalin", "ER"): (8.0, 6.3), ("tramadol", "ER"): (12.0, 7.9), ("oxycodone", "ER"): (4.0, 4.5),
    ("zolpidem", "ER"): (1.5, 2.8), ("alprazolam", "ER"): (9.0, 11.0), ("quetiapine", "ER"): (6.0, 7.0),
    ("methylphenidate", "ER"): (7.0, 3.5), ("glipizide", "ER"): (8.0, 4.0), ("carbamazepine", "ER"): (12.0, 35.0),
    ("eperisone", "IR"): (1.6, 1.9), ("eperisone", "ER"): (5.0, 3.0), ("aceclofenac", "IR"): (1.5, 4.0),
    ("aceclofenac", "ER"): (4.0, 4.0), ("cilostazol", "IR"): (3.0, 11.0), ("cilostazol", "ER"): (6.0, 11.0),
    ("omeprazole", "DR"): (2.0, 0.75), ("pantoprazole", "DR"): (2.5, 1.0), ("esomeprazole", "DR"): (1.5, 1.3),
    ("aspirin", "DR"): (4.0, 2.5), ("mesalamine", "DR"): (6.0, 12.0), ("doxazosin", "ER"): (9.0, 20.0),
    ("dexibuprofen", "ER"): (4.0, 2.0), ("ibuprofen", "ER"): (4.0, 2.0), ("lithium", "ER"): (4.0, 24.0),
}
# outcomes that appear around the peak concentration vs. after days of repeated dosing
ACUTE_HARMS = {"호흡 억제", "과도한 진정", "저혈압·실신", "저혈당", "QT 연장·부정맥", "부정맥", "세로토닌 증후군", "경련",
               "디설피람 유사 반응", "흡수 감소", "과다 복용", "두근거림·혈압 상승", "졸음·호흡 억제", "과도한 졸음·혼돈"}


def fmt_h(h: float) -> str:
    """hours -> plain Korean duration ('45분', '1시간 30분', '2.5일', '2주')."""
    if h < 1:
        return f"{max(1, round(h * 60))}분"
    if h < 48:
        hh, mm = int(h), round((h - int(h)) * 12) * 5  # 5-minute steps read better than '1시간 51분'
        if mm == 60:
            hh, mm = hh + 1, 0
        return f"{hh}시간" + (f" {mm}분" if mm and hh < 10 else "")
    d = h / 24
    if d < 14:
        return f"{d:.1f}일".replace(".0일", "일")
    return f"{round(d / 7)}주"


def _protein(label: str) -> str:
    return next((k for rx, k in PROTEIN_KIND if rx.search(label or "")), "other")


def _short(text: str, n: int = 70) -> str:
    t = re.sub(r"\s*\((FDA|허가|식약처)[^)]*\)", "", (text or "").split(" — ")[0]).strip()
    return t if len(t) <= n else t[: n - 1] + "…"
ATC_SITES: list[tuple[str, str, str]] = [
    ("A02", "stomach", "위산 분비"), ("A03", "intestine", "위장 운동"), ("A04", "brain", "구토 중추"),
    ("A06", "intestine", "장 운동"), ("A07", "intestine", "장"), ("A10", "pancreas", "혈당 조절"),
    ("B01", "vessel", "혈액 응고"), ("B03", "vessel", "조혈"), ("C01", "heart", "심장"), ("C02", "vessel", "혈압"),
    ("C03", "kidney", "이뇨"), ("C07", "heart", "β 수용체"), ("C08", "vessel", "칼슘 통로"),
    ("C09", "vessel", "레닌-안지오텐신계"), ("C10", "liver", "지질 대사"), ("G03", "reproductive", "성호르몬"),
    ("G04BE", "vessel", "PDE5 효소"), ("G04", "bladder", "비뇨기"), ("H02", "vessel", "부신피질 호르몬 수용체"),
    ("H03", "thyroid", "갑상선"), ("J", "infection", "병원체"), ("L01", "vessel", "암세포"), ("L04", "joint", "면역 세포"),
    ("M01", "joint", "염증"), ("M03", "muscle", "근육 이완"), ("M04", "joint", "요산"), ("M05", "bone", "뼈"),
    ("N01", "brain", "신경 전달"), ("N02", "brain", "통증 신호"), ("N03", "brain", "신경 흥분"), ("N04", "brain", "도파민계"),
    ("N05", "brain", "정신·수면"), ("N06", "brain", "기분·인지"), ("N07", "brain", "신경계"), ("P", "infection", "기생충"),
    ("R01", "vessel", "코 점막"), ("R03", "lung", "기관지"), ("R05", "lung", "기침·가래"), ("R06", "skin", "알레르기(H1)"),
    ("D", "skin", "피부"), ("S01", "eye", "눈"),
]
MOA_SITES: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), s) for p, s in (
    (r"bacterial|virus|viral|fungal|plasmodium|parasit|mycobacter|penicillin-binding|ribosom|gyrase|topoisomerase iv|"
     r"dihydropteroate|neuraminidase|cytochrome p450 51|lanosterol", "infection"),
    (r"cyclooxygenase", "joint"),
    (r"serotonin|dopamine|gaba|opioid|nmda|glutamate|melatonin|orexin|acetylcholinesterase|monoamine|adenosine|"
     r"norepinephrine transporter|cannabinoid|sodium channel|glycine receptor", "brain"),
    (r"hmg-coa|bile acid|pcsk9|cholesterol|peroxisome proliferator-activated receptor alpha", "liver"),
    (r"potassium-transporting atpase|histamine h2", "stomach"),
    (r"sodium-chloride|chloride cotransporter|mineralocorticoid|sodium/glucose|carbonic anhydrase|vasopressin", "kidney"),
    (r"dipeptidyl|sulfonylurea|glucagon-like|insulin receptor|gamma agonist|kir6", "pancreas"),
    (r"coagulation|thrombin|factor x|p2y12|vitamin k epoxide|platelet", "vessel"),
    (r"calcium channel|angiotensin|endothelin|guanylate|phosphodiesterase 5|alpha-1", "vessel"),
    (r"beta-1 adrenergic|hcn channel", "heart"),
    (r"beta-2 adrenergic|leukotriene|muscarinic acetylcholine receptor m3|phosphodiesterase 4", "lung"),
    (r"glucocorticoid", "vessel"),
    (r"estrogen|progesterone|androgen|gonadotropin|5-alpha|steroid 5", "reproductive"),
    (r"thyroid", "thyroid"),
    (r"retino", "skin"), (r"histamine h1", "skin"),
    (r"xanthine|solute carrier family 22 member 12", "joint"),
    (r"tubulin|dna|kinase|cyclin|proteasome|thymidylate|topoisomerase", "vessel"),
)]
# mostly cleared unchanged by the kidneys (dose is cut when kidney function falls)
RENAL_CLEARED = frozenset({
    "metformin", "gabapentin", "pregabalin", "dabigatran", "digoxin", "lithium", "levetiracetam", "allopurinol",
    "famotidine", "cetirizine", "levocetirizine", "sitagliptin", "rivaroxaban", "edoxaban", "apixaban", "ciprofloxacin",
    "levofloxacin", "gentamicin", "tobramycin", "amikacin", "vancomycin", "enoxaparin", "tramadol", "baclofen",
    "atenolol", "memantine", "valacyclovir", "acyclovir", "oseltamivir", "colchicine", "methotrexate", "rosuvastatin",
    "fexofenadine", "meloxicam", "ibuprofen", "clarithromycin", "nirmatrelvir", "metoclopramide", "paroxetine",
    "amoxicillin", "cefuroxime", "spironolactone", "lisinopril", "enalapril", "ramipril", "sotalol", "dofetilide"})
INJECTED = frozenset(BIOLOGICS)  # every curated biologic is given by injection (oral semaglutide aside)


def _first(pats: list[tuple[re.Pattern, str]], text: str) -> list[str]:
    """Sites named in a text, in the order they are mentioned."""
    found = []
    for rx, site in pats:
        m = rx.search(text or "")
        if m:
            found.append((m.start(), site))
    return list(dict.fromkeys(s for _, s in sorted(found)))


def _enz(name: str) -> str:
    return "CYP3A4" if name == "CYP3A" else name


class Journey:
    def __init__(self, eng, U, pair_rows, about, cyp_p: float, prods_of: dict | None = None) -> None:
        self.eng, self.U, self.about, self.cyp_p = eng, U, about, cyp_p
        self.by_key = {g.key: g for g in U}
        self.pairs = list(pair_rows)
        self.prods_of = prods_of or {}

    def designed(self, a: str, b: str) -> bool:
        """Both ingredients come from the same single product (Paxlovid: ritonavir boosts nirmatrelvir on purpose)."""
        pa, pb = self.prods_of.get(a, set()), self.prods_of.get(b, set())
        return len(pa) == 1 and pa == pb

    # ---------------------------------------------------------------- per ingredient
    def name(self, g) -> str:
        return self.eng.ko(g).split("(")[0].strip() or g.bare

    def route(self, g) -> str:
        if g.food:
            return "inhaled" if g.bare == "smoking" else "oral"
        return "injection" if g.key.startswith("bio:") and g.bare in INJECTED else "oral"

    def kind(self, g) -> str:
        if g.food:
            return "food"
        if g.key.startswith("bio:"):
            return "bio"
        if g.key.startswith("lab:"):
            return "label"
        return "drug"

    def targets(self, g) -> list[dict]:
        out: list[dict] = []
        if g.bare in DRUG_TARGETS:
            s, t, a = DRUG_TARGETS[g.bare]
            out.append({"site": s, "label": t, "action": a, "source": "약리 분류"})
        groups = drug_groups(g.key) | ({"ppi"} if g.bare in PPI else set())
        if "ppi" in groups:
            out.append({"site": "stomach", "label": "위벽 양성자 펌프(H⁺/K⁺-ATPase)", "action": "위산 분비를 막습니다",
                        "source": "약리 분류"})
        for grp, s, t, a in GROUP_TARGETS:
            if grp in groups and not any(o["site"] == s for o in out):
                out.append({"site": s, "label": t, "action": a, "source": "약리 분류"})
        ph = self.eng.pharm.get(g.key) or {}
        for m in ph.get("moa", []) if not out else []:  # curated class targets first (ChEMBL wording is generic)
            text = str(m.get("text") or "")
            sites = _first(MOA_SITES, text)
            if sites and text and text != "Unknown" and not any(o["site"] == sites[0] for o in out):
                out.append({"site": sites[0], "label": _moa_ko(text), "action": "ChEMBL 작용 기전",
                            "source": "ChEMBL", "detail": text})
        if not out:
            for code in sorted(self.eng.atc_of.get(g.bare, []), key=lambda c: c.startswith(("D", "S", "A01"))):
                hit = next(((s, t) for p, s, t in ATC_SITES if code.startswith(p)), None)
                if hit:
                    out.append({"site": hit[0], "label": hit[1], "action": f"WHO ATC {code}", "source": "ATC"})
                    break
        for o in out:
            o["protein"] = _protein(o["label"])
        return out[:3]

    def pk(self, g, form: str = "IR") -> dict:
        """time to peak / half-life (hours) of one ingredient in this release form, with where the numbers come from"""
        if g.food and g.bare in FOOD_PK:
            t, h, note = FOOD_PK[g.bare]
            return {"tmax_h": t, "t_half_h": h, "source": "일반 지식", "note": note}
        base = self._pk_ir(g)
        if form == "IR":
            return base
        ref = PK_FORM_REFERENCE.get((g.bare, form))
        if ref:
            return {"tmax_h": ref[0], "t_half_h": ref[1], "source": f"{RELEASE_KO[form]} 성인 표준 참고치", "measured": True}
        v = ((getattr(self.eng, "pk", {}) or {}).get(g.bare) or {}).get(form) or {}
        if v.get("tmax_h"):  # the product-form labels: the half-life falls back to the ordinary tablet's
            return {"tmax_h": v["tmax_h"], "t_half_h": v.get("t_half_h") or base["t_half_h"],
                    "source": f"FDA {RELEASE_KO[form]} 라벨 {max(v.get('n_tmax', 0), v.get('n_thalf', 0))}건 중앙값",
                    "measured": True}
        # no numbers for this form: extended release spreads absorption (peak ~3x later, 4-12 h);
        # delayed release starts dissolving in the intestine (~2 h lag)
        t = min(12.0, max(4.0, base["tmax_h"] * 3)) if form == "ER" else base["tmax_h"] + 2
        return {"tmax_h": t, "t_half_h": base["t_half_h"], "source": f"{RELEASE_KO[form]} 추정 (일반형 수치 기반)",
                "measured": False}

    def _pk_ir(self, g) -> dict:
        ref2 = PK_FORM_REFERENCE.get((g.bare, "IR"))
        if ref2:
            return {"tmax_h": ref2[0], "t_half_h": ref2[1], "source": "성인 표준 참고치", "measured": True}
        v = (getattr(self.eng, "pk", {}) or {}).get(g.bare) or {}
        t, h = v.get("tmax_h"), v.get("t_half_h")
        ref = PK_REFERENCE.get(g.bare)
        if ref and (g.bare == "caffeine" or not (t and h)):  # fill the gaps (caffeine: the labels are neonatal)
            src = "FDA 라벨 + 성인 표준 참고치" if (t or h) and g.bare != "caffeine" else "성인 표준 참고치"
            t, h = (t if g.bare != "caffeine" and t else ref[0]), (h if g.bare != "caffeine" and h else ref[1])
            return {"tmax_h": t, "t_half_h": h, "source": src, "measured": True}
        n = max(v.get("n_tmax", 0), v.get("n_thalf", 0))
        src = f"FDA 라벨 {n}건 중앙값" if (t or h) else "일반값(수치 없음)"
        return {"tmax_h": t or DEFAULT_PK[0], "t_half_h": h or DEFAULT_PK[1], "source": src, "measured": bool(t or h)}

    def tox(self, g) -> tuple[str, str] | None:
        if g.bare in DRUG_TOX:
            return DRUG_TOX[g.bare]
        groups = drug_groups(g.key)
        return next(((s, t) for grp, s, t in TOX_GROUPS if grp in groups), None)

    def enzymes(self, g) -> list[dict]:
        """CYP roles: FDA index tables (clinical), ChEMBL (in vitro), well-known food effects."""
        out: dict[tuple[str, str], dict] = {}

        def add(enz, role, strength, source):
            k = (_enz(enz), role)
            if k not in out:
                out[k] = {"enzyme": _enz(enz), "role": role, "strength": strength, "source": source}

        n = g.bare
        for enz, t in CYP_FDA.items():
            if n in t["sensitive"] or n in t["moderate_sub"]:
                add(enz, "substrate", "sensitive" if n in t["sensitive"] else "moderate", "FDA")
            if n in t["strong_inh"] or n in t["moderate_inh"]:
                add(enz, "inhibitor", "strong" if n in t["strong_inh"] else "moderate", "FDA")
            if n in t["strong_ind"] or n in t["moderate_ind"]:
                add(enz, "inducer", "strong" if n in t["strong_ind"] else "moderate", "FDA")
        if n in PRODRUGS_ACTIVATED_BY:
            add(PRODRUGS_ACTIVATED_BY[n], "activation", "required", "FDA")
        ph = self.eng.pharm.get(g.key) or {}
        for c in sorted(ph.get("sub", [])):
            add(c, "substrate", "data", "ChEMBL")
        for c, p in sorted((ph.get("inh") or {}).items()):
            if p and p >= self.cyp_p:
                add(c, "inhibitor", "in_vitro", "ChEMBL")
        groups = drug_groups(g.key)
        if "grapefruit" in groups:
            add("CYP3A4", "inhibitor", "gut", "음식")
        if "smoking" in groups:
            add("CYP1A2", "inducer", "moderate", "흡연")
        if "alcohol" in groups:
            add("CYP2E1", "substrate", "data", "음식")
        if "st_johns_wort" in groups:
            add("CYP3A4", "inducer", "strong", "생약")
        return list(out.values())

    # ---------------------------------------------------------------- build
    def build(self, *, events, disease_alerts, class_dups, duplicates, dosing, pa, overall, patient=None,
              products=None) -> dict:
        U = self.U
        # release form and dosing schedule of each ingredient (from the product it comes in and the dose entered)
        form_of: dict[str, str] = {}
        reg_of: dict[str, dict] = {}
        controlled: set[str] = set()  # recreational substances: no repeated-dosing schedule in the body view
        for p in products or []:
            rel = p.get("release") or "IR"
            d = p.get("dose")
            for g in p["ingredients"]:
                if p.get("controlled"):
                    controlled.add(g.key)
                if rel == "ER" or g.key not in form_of:
                    form_of[g.key] = rel
                if d is not None and g.key not in reg_of:
                    reg_of[g.key] = {"per_day": d.per_day, "days": d.days, "prn": d.prn, "weekly": d.weekly, "source": "입력값"}
        sex = (getattr(patient, "sex", None) or "").upper()[:1] or None
        bmi = (pa.get("body") or {}).get("bmi")
        subject = {"sex": sex if sex in ("F", "M") else None, "age": getattr(patient, "age", None),
                   "height_cm": getattr(patient, "height_cm", None), "weight_kg": getattr(patient, "weight_kg", None),
                   "bmi": round(bmi, 1) if bmi else None, "pregnant": "pregnancy" in (pa.get("diseases") or []),
                   "kidney_pct": (pa.get("kidney") or {}).get("pct_normal")}
        info = {g.key: {"key": g.key, "name": self.name(g), "kind": self.kind(g), "route": self.route(g),
                        "targets": self.targets(g), "enzymes": self.enzymes(g), "release": form_of.get(g.key, "IR"),
                        "pk": self.pk(g, form_of.get(g.key, "IR")),
                        "renal": g.bare in RENAL_CLEARED or any(c.startswith("crcl") for c, _, _ in
                                                               getattr(DOSE_LIMITS.get(g.bare), "adjust", []))}
                for g in U}
        for k, v in info.items():
            v["regimen"] = reg_of.get(k) or default_regimen(v["release"], v["pk"]["t_half_h"], v["route"], v["kind"])
            v["controlled"] = k in controlled
        pair_keys = [(self.by_key.get(r["a_key"]), self.by_key.get(r["b_key"]), r) for r in self.pairs]
        pair_keys = [(a, b, r) for a, b, r in pair_keys if a is not None and b is not None]
        levels: list[dict] = []
        problems: list[dict] = []

        # ---- 1. stomach: chelation, slowed emptying
        stomach: list[dict] = []
        for a, b, _ in pair_keys:
            for x, y in ((a, b), (b, a)):
                gx, gy = drug_groups(x.key), drug_groups(y.key)
                if "polyvalent_cation" in gx and gy & {"tetracycline", "fluoroquinolone", "levothyroxine", "bisphosphonate"}:
                    stomach.append({"sev": "mid", "drugs": [x.key, y.key], "kind": "chelation",
                                    "text": f"{self.name(x)}의 금속 이온(칼슘·철·마그네슘 등)이 {self.name(y)}과(와) 엉겨 "
                                            "붙어(킬레이트) 흡수가 막힙니다 — 시간 간격을 두세요"})
                    levels.append({"drug": y.key, "dir": "down", "why": f"{self.name(x)}과(와) 엉겨 흡수 감소", "sev": "mid",
                                   "fold": 0.6, "fold_label": "흡수 ↓"})
                if "glp1" in gx and y.key != x.key and self.route(y) == "oral" and not y.food \
                        and gy & {"oral_contraceptive", "warfarin"}:
                    stomach.append({"sev": "mid", "drugs": [x.key, y.key], "kind": "emptying",
                                    "text": f"{self.name(x)}이(가) 위 배출을 늦춰 {self.name(y)}의 흡수가 달라질 수 있습니다"})
        if any("alcohol" in drug_groups(g.key) for g in U) and any("nsaid" in drug_groups(g.key) for g in U):
            stomach.append({"sev": "mid", "drugs": [g.key for g in U if drug_groups(g.key) & {"alcohol", "nsaid"}],
                            "kind": "mucosa", "text": "술과 소염진통제가 함께 위 점막 보호막을 약하게 합니다"})
        for k, v in info.items():  # how the tablet itself releases the drug
            if v["release"] == "ER":
                stomach.append({"sev": "low", "drugs": [k], "kind": "er",
                                "text": f"{v['name']}은(는) 서방형 — 장을 지나며 약 {fmt_h(v['pk']['tmax_h'])}에 걸쳐 천천히 "
                                        "녹아 나옵니다. 쪼개거나 씹으면 한꺼번에 흡수돼 위험할 수 있으니 통째로 삼키세요"})
            elif v["release"] == "DR":
                stomach.append({"sev": "low", "drugs": [k], "kind": "dr",
                                "text": f"{v['name']}은(는) 장용형 — 위산을 피해 장에서 녹습니다(쪼개거나 씹지 마세요)"})

        # ---- 2. intestine: gut-wall CYP3A4 (grapefruit), P-gp
        gut: list[dict] = []
        for a, b, _ in pair_keys:
            for x, y in ((a, b), (b, a)):
                gx, gy = drug_groups(x.key), drug_groups(y.key)
                if "grapefruit" in gx and gy & {"grapefruit_major", "grapefruit_moderate"}:
                    major = "grapefruit_major" in gy
                    gut.append({"sev": "high" if major else "mid", "drugs": [x.key, y.key], "kind": "gut_cyp",
                                "enzyme": "CYP3A4",
                                "text": f"자몽 성분(푸라노쿠마린)이 장벽의 CYP3A4를 망가뜨려 {self.name(y)}이(가) 분해되지 않고 "
                                        f"{'대량으로' if major else '더 많이'} 흡수됩니다"})
                    levels.append({"drug": y.key, "dir": "up", "why": "자몽이 장 분해 효소를 막음",
                                   "sev": "high" if major else "mid", "fold": 3.5 if major else 1.6,
                                   "fold_label": "수 배 ↑" if major else "↑"})
                if "dabigatran" in gx and "strong_pgp_inh" in gy:
                    gut.append({"sev": "high", "drugs": [y.key, x.key], "kind": "pgp",
                                "text": f"{self.name(y)}이(가) 장의 배출 펌프(P-당단백)를 막아 {self.name(x)}이(가) 훨씬 많이 "
                                        "흡수됩니다"})
                    levels.append({"drug": x.key, "dir": "up", "why": "P-당단백 펌프 억제", "sev": "high",
                                   "fold": 2.5, "fold_label": "↑↑"})

        # ---- 3. liver: enzyme bindings and clashes
        enzymes: dict[str, dict] = {}
        for g in U:
            for e in info[g.key]["enzymes"]:
                if e["strength"] == "gut":  # grapefruit acts on the gut wall (stage 2), not the liver
                    continue
                slot = enzymes.setdefault(e["enzyme"], {"name": e["enzyme"], "substrates": [], "inhibitors": [],
                                                        "inducers": [], "activates": [], "sev": "normal"})
                key = {"substrate": "substrates", "inhibitor": "inhibitors", "inducer": "inducers",
                       "activation": "activates"}[e["role"]]
                if g.key not in slot[key]:
                    slot[key].append(g.key)
        clashes: list[dict] = []
        for a, b, _ in pair_keys:
            for v, w in ((a, b), (b, a)):  # victim, perpetrator
                ev = {(e["enzyme"], e["role"]): e for e in info[v.key]["enzymes"]}
                for e in info[w.key]["enzymes"]:
                    if e["role"] not in ("inhibitor", "inducer") or e["strength"] == "gut":
                        continue
                    enz = e["enzyme"]
                    sub, act = ev.get((enz, "substrate")), ev.get((enz, "activation"))
                    if not sub and not act:
                        continue
                    fda = e["source"] in ("FDA", "흡연", "생약") and (sub or act)["source"] == "FDA"
                    if not fda and not (sub and e["source"] == "ChEMBL"):
                        continue
                    if not fda and any(c["victim"] == v.key and c["perpetrator"] == w.key for c in clashes):
                        continue
                    inhibit = e["role"] == "inhibitor"
                    # fold = schematic exposure change for the concentration-time sketch (category, not a measurement)
                    if act and inhibit:
                        direction, effect = "down", f"{self.name(v)}을(를) 활성형으로 바꾸지 못해 효과가 떨어집니다"
                        fold, flabel = 0.45, "활성형 ↓"
                    elif act:
                        direction, effect = "up", f"{self.name(v)}의 활성형이 더 많이 만들어집니다"
                        fold, flabel = 1.6, "활성형 ↑"
                    elif inhibit:
                        direction = "up"
                        big = fda and e["strength"] == "strong" and sub and sub["strength"] == "sensitive"
                        effect = (f"{self.name(v)}이(가) 분해되지 못하고 쌓입니다 "
                                  f"({'5배 이상' if big else '2~5배' if fda else '시험관 근거 · 상승 가능'})")
                        fold, flabel = (5.0, "AUC ≥5×") if big else (2.5, "AUC 2~5×") if fda else (1.3, "↑ (시험관)")
                    else:
                        direction, effect = "down", f"{self.name(v)}이(가) 너무 빨리 분해돼 효과가 떨어집니다"
                        fold, flabel = (0.3, "AUC ↓↓") if e["strength"] == "strong" else (0.55, "AUC ↓")
                    severe = bool(fda and inhibit and not act and e["strength"] == "strong" and sub
                                  and sub["strength"] == "sensitive")
                    sev = "high" if severe else "mid" if fda else "low"
                    by_design = self.designed(v.key, w.key)
                    if by_design:
                        sev, effect = "low", effect + " — 한 제품 안의 의도된 배합(약효 증강)"
                    clashes.append({"enzyme": enz, "perpetrator": w.key, "victim": v.key, "action": e["role"],
                                    "dir": direction, "sev": sev, "source": "FDA 지표 약물표" if fda else "ChEMBL 시험관",
                                    "by_design": by_design,
                                    "text": f"{self.name(w)}이(가) {enz} 효소를 {'막아' if inhibit else '늘려'} → {effect}"})
                    enzymes[enz]["sev"] = max(enzymes[enz]["sev"], sev, key=["normal", "low", "mid", "high"].index)
                    levels.append({"drug": v.key, "dir": direction, "sev": sev, "fold": fold, "fold_label": flabel,
                                   "why": f"{enz} {'억제' if inhibit else '유도'} ({self.name(w)})"})
        clashes.sort(key=lambda c: ["high", "mid", "low"].index(c["sev"]))
        liver = pa.get("liver") or {}
        if liver.get("impaired"):
            for g in U:
                if any(e["role"] == "substrate" and e["source"] == "FDA" for e in info[g.key]["enzymes"]):
                    levels.append({"drug": g.key, "dir": "up", "sev": "mid", "why": "간 기능 저하로 분해가 느림",
                                   "fold": 1.5, "fold_label": "분해 지연"})

        # ---- 4. blood: duplicates and dose
        for d in duplicates:
            if d["minor"]:
                continue
            g = next((g for g in U if self.eng.ko(g) == d["ingredient"]), None)
            if g:
                n = len(d["products"])
                levels.append({"drug": g.key, "dir": "up", "sev": "high", "fold": float(n), "fold_label": f"×{n} (중복)",
                               "why": f"같은 성분 {n}개 제품에서 중복"})
        for t in (dosing or {}).get("ingredients", []):
            if t.get("status") == "초과" and t["key"] in self.by_key:
                x = t["daily_mg"] / t["max_mg"] if t.get("max_mg") else 1.5
                levels.append({"drug": t["key"], "dir": "up", "sev": "high", "why": "1일 최대량 초과",
                               "fold": round(x, 2), "fold_label": f"최대량의 {x:.1f}배"})

        # ---- 6. kidney (computed before problems: accumulation feeds the blood levels)
        kid = pa.get("kidney") or {}
        renal = [g.key for g in U if info[g.key]["renal"]]
        clear = kid.get("clearance")
        if clear is not None and clear < 60:
            pct = kid.get("pct_normal") or clear
            for k in renal:
                levels.append({"drug": k, "dir": "up", "sev": "high" if clear < 30 else "mid",
                               "fold": round(min(4.0, 100 / max(pct, 10)), 2), "fold_label": f"배설 {pct}%",
                               "why": f"콩팥 배출 감소 (정상의 약 {pct}%)"})

        # ---- 5. targets: what binds where; overlapping effects at the same site
        binds = [{"drug": g.key, **t} for g in U for t in info[g.key]["targets"][:2]]
        overlaps: list[dict] = []
        by_site: dict[str, list[str]] = {}
        for b in binds:
            by_site.setdefault(b["site"], [])
            if b["drug"] not in by_site[b["site"]]:
                by_site[b["site"]].append(b["drug"])
        # an overlap is worth showing only between drugs that do interact (two antivirals of one combination
        # product - nirmatrelvir boosted by ritonavir - act together by design)
        risky = {frozenset((a.key, b.key)) for a, b, r in pair_keys
                 if r["level"] != "낮음" and not self.designed(a.key, b.key)}
        for site, ks in by_site.items():
            ks = [k for k in ks if any(frozenset((k, o)) in risky for o in ks if o != k)]
            if len(ks) >= 2:
                overlaps.append({"site": site, "drugs": ks,
                                 "text": f"{SITES[site]}에서 {', '.join(self.name(self.by_key[k]) for k in ks)}의 작용이 겹칩니다"})

        # ---- problems: every finding that drives or explains the verdict, placed on an organ
        def place(text: str, ks: list[str]) -> list[str]:
            """Organs named in the finding; else where these drugs do harm; else their target."""
            sites = [s for s in _first(TEXT_SITES, text) if not (s == "vessel" and LEVEL_WORDS.search(text))]
            if not sites:
                sites = [t[0] for t in (self.tox(self.by_key[k]) for k in ks if k in self.by_key) if t]
            if not sites:
                sites = [t["site"] for k in ks for t in info.get(k, {}).get("targets", [])[:1]]
            return list(dict.fromkeys(sites))[:2] or ["vessel"]

        def chain(cause: str, mechanism: str, site: str, harm: str | None, source: str) -> list[dict]:
            """cause -> mechanism -> organ outcome -> evidence: the 'why' of a finding as one line of steps."""
            return [{"k": "원인", "v": cause}, {"k": "기전", "v": _short(mechanism)},
                    {"k": "결과", "v": f"{SITES.get(site, site)} · {harm or '부작용 증가'}"}, {"k": "근거", "v": source}]

        def harm_of(text: str, ks: list[str]) -> str | None:
            hit = next((h for rx, h in HARM_WORDS if rx.search(text or "")), None)
            if hit:
                return hit
            tox = next((t for t in (self.tox(self.by_key[k]) for k in ks if k in self.by_key) if t), None)
            return tox[1] if tox else None

        sevmap = {"높음": "high", "주의": "mid"}
        for a, b, r in pair_keys:
            if r["level"] == "낮음":
                continue
            ks = [a.key, b.key]
            mech = r.get("mechanism") or []
            dur = r.get("dur") or {}
            rule = next((m for m in mech if m["kind"] in ("규칙", "식약처허가", "식약처표시", "PD", "QT")), None)
            cyp = next((m for m in mech if m["kind"] in ("CYP-FDA", "CYP")), None)
            clash = next((c for c in clashes if {c["victim"], c["perpetrator"]} == set(ks)), None)
            mechanism = None
            if dur:
                source, text = "식약처 DUR 병용금기", (dur.get("content") or "").strip(" -•·") or "병용금기 성분 조합"
                sites = place(text + " " + (rule or {}).get("text", ""), ks)
            elif rule:
                source = {"규칙": "임상 규칙", "식약처허가": "식약처 허가사항",
                          "식약처표시": "식약처 건강기능식품정보"}.get(rule["kind"], "약리 기전")
                text = rule["text"]
                sites = place(text, ks)
            elif cyp:
                # a CYP clash shows up where the accumulated drug does harm (simvastatin -> muscle)
                source, text = "FDA 지표 약물표" if cyp["kind"] == "CYP-FDA" else "ChEMBL 시험관", cyp["text"]
                victims = [c["victim"] for c in clashes if {c["victim"], c["perpetrator"]} == set(ks)]
                tox = [t for t in (self.tox(self.by_key[v]) for v in victims) if t]
                sites = list(dict.fromkeys([t[0] for t in tox] + place(text, victims or ks)))[:2]
                if tox and not re.search(r"효과가 떨어", text):
                    text = f"{text} → {tox[0][1]} 위험"
            else:
                source, text = "허가 문서(라벨)", "허가 문서에 상호작용으로 기재돼 있습니다"
                sites = place(" ".join(self.about(e) for e in r.get("label_evidence", [])[:4]), ks)
                mechanism = "허가 문서(라벨)의 상호작용 항목"
            if clash and clash["action"] in ("inhibitor", "inducer"):  # the pharmacokinetic route, step by step
                lvl = next((x for x in levels if x["drug"] == clash["victim"] and x["dir"] == clash["dir"]), {})
                mechanism = (f"{self.name(self.by_key[clash['perpetrator']])} → {clash['enzyme']} "
                             f"{'억제' if clash['action'] == 'inhibitor' else '유도'} → "
                             f"{self.name(self.by_key[clash['victim']])} {lvl.get('fold_label') or ''}").strip()
            overlap = next((o for o in overlaps if set(ks) <= set(o["drugs"])), None)
            if mechanism is None and overlap:  # pharmacodynamic: both act on the same place
                labels = [next((t["label"] for t in info[k]["targets"] if t["site"] == overlap["site"]), "") for k in ks]
                mechanism = (f"{SITES[overlap['site']]}: {' + '.join(dict.fromkeys(x for x in labels if x))} "
                             "동시 작용 → 효과 중첩")
            problems.append({"sev": sevmap[r["level"]], "sites": sites, "drugs": ks, "source": source,
                             "title": f"{self.name(a)} + {self.name(b)}", "text": text,
                             "chain": chain(f"{self.name(a)} + {self.name(b)}", mechanism or text.split(" — ")[0],
                                            sites[0], harm_of(text, [clash["victim"]] if clash else ks), source)})
        for e in [e for e in events if e.get("drives_verdict")][:3]:  # the AI signals that changed the verdict
            site = CONDITION_SITE.get(e["condition"]) or ORGAN_SITE.get(e["organ"], "vessel")
            drv = [g.key for g in U if self.eng.ko(g) == e.get("driver")]
            problems.append({"sev": "mid", "sites": [site], "drugs": drv, "source": "AI 예측(환자 모델)",
                             "title": f"{e['ko']} 위험 증가",
                             "text": f"실제 부작용 보고 학습 모델: 이 조합·이 환자에서 '{e['ko']}' 위험이 평소의 "
                                     f"{e['lift']:.1f}배입니다",
                             "chain": chain(", ".join(self.name(self.by_key[k]) for k in drv) or "약 조합",
                                            f"부작용 보고 1,583만 건 학습 모델 · 기준 대비 {e['lift']:.1f}배",
                                            site, e["ko"], "AI 예측(환자 모델)")})
        for d in class_dups:
            cid = next((c for c, (cn, _) in DRUG_CLASSES.items() if cn == d["class"]), None)
            ks = [g.key for g in U if self.eng.ko(g) in d["ingredients"]]
            hit = CLASS_TOX.get(cid) or next((t for t in (self.tox(self.by_key[k]) for k in ks) if t), None)
            if hit is None:
                first = info[ks[0]]["targets"][:1] if ks else []
                hit = (first[0]["site"] if first else "vessel", "효과·부작용 중복")
            site, harm = hit
            problems.append({"sev": "high" if d.get("severe") else "mid", "sites": [site], "drugs": ks,
                             "source": "같은 계열 중복", "title": f"같은 계열 중복: {d['class']}",
                             "text": f"{', '.join(d['ingredients'])} — 같은 곳에 겹쳐 작용해 {harm} 위험이 커집니다",
                             "chain": chain(" + ".join(self.name(self.by_key[k]) for k in ks), "같은 계열 → 같은 작용점에 이중 작용",
                                            site, harm, "같은 계열 중복")})
        for d in duplicates:
            ks = [g.key for g in U if self.eng.ko(g) == d["ingredient"]]
            tox = self.tox(self.by_key[ks[0]]) if ks else None
            site = tox[0] if tox else next((t["site"] for k in ks for t in info[k]["targets"][:1]), "vessel")
            name = d["ingredient"].split("(")[0]
            if d["minor"]:  # caffeine / vitamins / minerals in two products: check the daily total
                problems.append({"sev": "mid", "sites": [site], "drugs": ks, "source": "성분 중복(영양·카페인)",
                                 "title": f"함께 든 성분: {name}",
                                 "text": f"{' / '.join(d['products'])}에 함께 들어 있습니다 — 보통 양이면 괜찮지만 하루 총량을 "
                                         "확인하세요" + (f" (많으면 {tox[1]})" if tox else ""),
                                 "chain": chain(" / ".join(d["products"]), f"{name} 합산 섭취량 증가", site,
                                                tox[1] if tox else "과다 섭취", "성분 중복")})
                continue
            problems.append({"sev": "high", "sites": [site], "drugs": ks, "source": "성분 중복",
                             "title": f"성분 중복: {name}",
                             "text": f"{' / '.join(d['products'])}에 같은 성분 — 과다 복용"
                                     + (f" → {tox[1]} 위험" if tox else ""),
                             "chain": chain(" / ".join(d["products"]),
                                            f"같은 성분 {len(d['products'])}중 복용 → {name} 혈중 농도 ×{len(d['products'])}",
                                            site, tox[1] if tox else "과다 복용", "성분 중복")})
        for d in disease_alerts:
            ks = [g.key for g in U if self.eng.ko(g) == d["drug"]]
            site = DISEASE_SITE.get(d.get("key") or "") or place(d["reason"], ks)[0]
            problems.append({"sev": "high" if d["severity"] == "금기" else "mid", "sites": [site], "drugs": ks,
                             "source": d.get("source") or "질환 기준", "title": f"{d['disease']} × {d['drug'].split('(')[0]}",
                             "text": d["reason"],
                             "chain": chain(f"{d['drug'].split('(')[0]} × {d['disease']}", d["reason"], site,
                                            harm_of(d["reason"], ks) or "기저질환 악화", d.get("source") or "질환 기준")})
        for a in (dosing or {}).get("alerts", []):
            ks = [g.key for g in U if self.eng.ko(g).split("(")[0] and self.eng.ko(g).split("(")[0] in a["text"]]
            sites = place(a["text"], ks)
            problems.append({"sev": "high" if a["sev"] == "금기" else "mid", "sites": sites,
                             "drugs": ks, "source": "용량 기준", "title": "용량·기간", "text": a["text"],
                             "chain": chain(", ".join(self.name(self.by_key[k]) for k in ks) or "용량", a["text"], sites[0],
                                            harm_of(a["text"], ks), "용량 기준")})
        order = {"high": 0, "mid": 1, "low": 2}
        problems.sort(key=lambda p: order[p["sev"]])
        problems = problems[:10]

        # one entry per drug and direction, worst first
        seen, lv = set(), []
        for x in sorted(levels, key=lambda x: order.get(x["sev"], 2)):
            if (x["drug"], x["dir"]) not in seen and x["drug"] in self.by_key:
                seen.add((x["drug"], x["dir"]))
                lv.append(x)
        levels = lv

        # ---- per-site summary for the body (worst finding wins)
        site_state: dict[str, dict] = {}
        for p in problems:
            for s in p["sites"]:
                cur = site_state.get(s)
                if cur is None or order[p["sev"]] < order[cur["sev"]]:
                    site_state[s] = {"sev": p["sev"], "label": SITES.get(s, s), "reason": p["text"]}

        # ---- stages (plain-language captions)
        names = lambda ks: ", ".join(self.name(self.by_key[k]) for k in ks if k in self.by_key)  # noqa: E731
        oral = [g.key for g in U if self.route(g) == "oral"]
        inj = [g.key for g in U if self.route(g) == "injection"]
        inh = [g.key for g in U if self.route(g) == "inhaled"]
        intake_txt = []
        if oral:
            intake_txt.append(f"{names(oral)}을(를) 입으로 먹어 식도를 지나 위에 도착합니다")
        if inj:
            intake_txt.append(f"{names(inj)}은(는) 주사로 피부 아래에 들어가 곧바로 혈액으로 흡수됩니다")
        if inh:
            intake_txt.append(f"{names(inh)}의 성분은 폐로 들어가 혈액으로 퍼집니다")
        stages = [
            {"id": "intake", "site": "stomach", "title": "① 복용 · 위",
             "text": ". ".join(intake_txt) + "." + (" 위에서 문제가 생깁니다." if any(i["sev"] != "low" for i in stomach)
                                                   else " 위에서는 특별한 문제가 없습니다."),
             "items": stomach, "sev": max([i["sev"] for i in stomach if i["sev"] != "low"] or ["normal"], key=_rank)},
            {"id": "absorb", "site": "intestine", "title": "② 장 흡수",
             "text": ("소장 벽을 통과해 흡수된 성분은 문맥(門脈)을 타고 간으로 갑니다."
                      + (" 이 단계에서 흡수량이 달라집니다." if gut else "")),
             "items": gut, "sev": max([i["sev"] for i in gut] or ["normal"], key=_rank)},
            {"id": "liver", "site": "liver", "title": "③ 간 대사 · 효소 결합",
             "text": (f"간의 대사 효소(CYP)에 약이 결합해 분해됩니다. 함께 먹은 약이 같은 효소를 막거나 늘려 "
                      f"{len([c for c in clashes if c['sev'] != 'low'])}건의 충돌이 확인됐습니다."
                      if any(c["sev"] != "low" for c in clashes) else
                      "간의 대사 효소(CYP)에 약이 결합해 분해됩니다. 임상적으로 확인된 효소 충돌은 없습니다."
                      + (" 한 제품 안에서 효소를 일부러 막아 약효를 늘리는 배합이 있습니다."
                         if any(c["by_design"] for c in clashes) else
                         " (시험관 수준의 약한 신호만 있음)" if clashes else ""))
                     + (" 간 기능이 떨어져 분해가 느립니다." if liver.get("impaired") else ""),
             "enzymes": sorted(enzymes.values(), key=lambda e: (-_rank(e["sev"]), e["name"]))[:6],
             "clashes": clashes[:6],
             "sev": max([c["sev"] for c in clashes] or ["normal"], key=_rank)},
            {"id": "blood", "site": "heart", "title": "④ 혈류 · 혈중 농도",
             "text": ("심장이 약을 온몸으로 보냅니다. " + ("혈중 농도가 달라지는 약: " + ", ".join(
                 f"{self.name(self.by_key[x['drug']])} {'↑' if x['dir'] == 'up' else '↓'}" for x in levels[:5]) + "."
                 if levels else "혈중 농도를 크게 바꾸는 요인은 확인되지 않았습니다.")),
             "levels": levels[:8], "sev": max([x["sev"] for x in levels] or ["normal"], key=_rank)},
            {"id": "target", "site": binds[0]["site"] if binds else "vessel", "title": "⑤ 표적 결합 · 작용",
             "text": ("피를 타고 퍼진 약이 각자의 작용 부위에서 수용체·효소에 결합해 효과를 냅니다."
                      + (" " + " ".join(o["text"] + "." for o in overlaps[:2]) if overlaps else "")),
             "binds": binds[:10], "overlaps": overlaps[:4],
             "sev": "mid" if any(o for o in overlaps if any(p["sites"][0] == o["site"] for p in problems)) else "normal"},
            {"id": "problem", "site": problems[0]["sites"][0] if problems else "heart", "title": "⑥ 문제 발생 부위",
             "text": (f"위험 신호 {len(problems)}건 — 문제가 생기는 곳: "
                      + ", ".join(dict.fromkeys(SITES.get(s, s) for p in problems for s in p["sites"])) + "."
                      if problems else "문제가 생기는 장기는 확인되지 않았습니다. 정해진 용법을 지키세요."),
             "problems": problems, "sev": problems[0]["sev"] if problems else "normal"},
            {"id": "excrete", "site": "kidney", "title": "⑦ 배출",
             "text": _excrete_text(kid, [self.name(self.by_key[k]) for k in renal]),
             "renal": renal, "kidney": {k: kid.get(k) for k in ("clearance", "pct_normal", "stage", "desc")} if kid else None,
             "sev": ("high" if clear is not None and clear < 30 and renal else
                     "mid" if clear is not None and clear < 60 and renal else "normal")},
        ]
        clock = self.time_course(stages, info, clashes, problems, levels)
        return {"version": 1, "verdict": {"높음": "위험", "주의": "주의", "낮음": "비교적 안전"}[overall],
                "subject": subject, "clock": clock, "sites": SITES, "drugs": list(info.values()), "stages": stages,
                "site_state": site_state}

    def time_course(self, stages, info, clashes, problems, levels) -> dict | None:
        """When each step happens after a single dose, for the drug the story is about (the accumulating victim of an
        enzyme clash, else the first drug of the top finding). Tmax / half-life from its FDA labels; the half-life is
        stretched by the same evidence-category factor as the concentration sketch (enzyme block, kidney function)."""
        if not info:
            return None
        top = clashes[0]["victim"] if clashes and clashes[0]["sev"] != "low" else None
        cands = ([top] if top else []) + [k for p in problems for k in p["drugs"]] + [x["drug"] for x in levels] \
            + [k for k, v in info.items() if v["kind"] != "food"] + list(info)
        lead = next(k for k in cands if k in info)
        lp = info[lead]["pk"]
        T, H = float(lp["tmax_h"]), float(lp["t_half_h"])
        f = 1.0
        for x in levels:
            if x["drug"] == lead and x.get("fold") and not re.search(r"흡수|중복|최대량", x.get("why", "")):
                f = x["fold"]
        Hx = H * f
        a = min(0.5, max(0.08, T * 0.3))
        steady = 5 * Hx
        harm = None
        if problems and problems[0].get("chain"):
            harm = problems[0]["chain"][2]["v"].split(" · ", 1)[-1]
        acute = harm is None or harm in ACUTE_HARMS
        changed = (f" → 이 조합에서 {fmt_h(Hx)}" if abs(f - 1) > 0.15 else "")
        inj = info[lead]["route"] == "injection"
        dose = "투여" if inj else "복용"
        win = {
            "intake": (0.0, a, "주사 직후 — 피부 아래로 들어감" if inj else f"복용 직후 ~ {fmt_h(a)} — 위에서 녹아 풀려나옴"),
            "absorb": (a, T, f"{dose} 후 {fmt_h(a)} ~ {fmt_h(T)} — " + ("피하에서 혈액으로 흡수" if inj else "소장에서 흡수")),
            "liver": (a, T, "주사제는 간을 먼저 거치지 않고 바로 혈액으로 들어감" if inj else
                      f"복용 후 {fmt_h(a)} ~ {fmt_h(T)} — 흡수되면서 간을 처음 통과(초회 통과 대사)"),
            "blood": (T, T + Hx, f"{dose} 후 약 {fmt_h(T)}에 최고 혈중 농도(Tmax) · 반감기 {fmt_h(H)}{changed}"),
            "target": (T * 0.5, T + 2 * Hx, f"{dose} 후 {fmt_h(T * 0.5)}부터 작용 · 약 {fmt_h(T + 2 * Hx)}까지 지속"),
            "excrete": (T, 5 * Hx, f"{dose} 후 약 {fmt_h(5 * Hx)}이면 97% 배출"
                        + (f" (평소 {fmt_h(5 * H)}보다 늦음)" if f > 1.15 else "")),
        }
        if not problems:
            win["problem"] = (T, T + Hx, "확인된 위험 시점 없음")
        elif harm == "흡수 감소":
            win["problem"] = (0.0, T, "함께 먹는 순간부터 흡수 방해 — 2시간 이상 간격을 두면 피할 수 있음")
        elif harm == "효과 감소":
            win["problem"] = (T, max(steady, 24 * 3), "함께 먹는 동안 매번 약효가 떨어짐")
        elif acute:
            win["problem"] = (T * 0.7, T + Hx, f"최고 농도 무렵({dose} 후 약 {fmt_h(T)}) 위험이 가장 큼")
        else:
            win["problem"] = (min(steady, 24 * 3), max(steady * 1.6, 24 * 7),
                              f"매일 {dose}하면 약 {fmt_h(steady)} 뒤 정상상태 — 이후 며칠~몇 주에 걸쳐 위험이 쌓임")
        for s in stages:
            st, en, label = win[s["id"]]
            s["time"] = {"start_h": round(st, 3), "end_h": round(max(en, st + 0.05), 3), "label": label}
        return {"lead": lead, "name": info[lead]["name"], "tmax_h": T, "t_half_h": H, "t_half_eff_h": round(Hx, 2),
                "fold": f, "steady_h": round(steady, 2), "source": lp["source"], "acute": acute, "harm": harm,
                "note": lp.get("note"), "dose_word": dose}


_MOA_WORDS = [  # longest first: ChEMBL mechanism wording -> short Korean
    (r"severe acute respiratory syndrome coronavirus 2|sars coronavirus|sars-cov-2", "코로나바이러스"),
    (r"human immunodeficiency virus type 1", "HIV-1"), (r"hepatitis c virus", "C형 간염 바이러스"),
    (r"3c-like proteinase|3c-like protease", "3CL 단백분해효소"), (r"reverse transcriptase", "역전사효소"),
    (r"serine protease|protease|proteinase", "단백분해효소"), (r"bacterial", "세균"), (r"dna gyrase", "DNA 회전효소"),
    (r"topoisomerase", "위상이성질화효소"), (r"penicillin-binding protein", "페니실린 결합 단백질"),
    (r"70s ribosome|50s ribosome|30s ribosome|ribosome", "리보솜"), (r"cyclooxygenase", "COX 효소"),
    (r"serotonin", "세로토닌"), (r"dopamine", "도파민"), (r"histamine", "히스타민"), (r"adrenergic", "아드레날린"),
    (r"muscarinic acetylcholine", "무스카린"), (r"acetylcholinesterase", "아세틸콜린 분해효소"),
    (r"glucocorticoid", "글루코코르티코이드"), (r"mineralocorticoid", "광물코르티코이드"), (r"estrogen", "에스트로겐"),
    (r"progesterone", "프로게스테론"), (r"androgen", "안드로겐"), (r"voltage-gated", "전압 의존성"),
    (r"calcium", "칼슘"), (r"sodium", "나트륨"), (r"potassium", "칼륨"), (r"chloride", "염화물"),
    (r"transporter", "수송체"), (r"cotransporter", "공동수송체"), (r"tyrosine-protein kinase|kinase", "키나아제"),
    (r"receptor", "수용체"), (r"channel", "통로"), (r"subunit", "소단위"), (r"enzyme", "효소"),
    (r"positive allosteric modulator|positive modulator", "작용 증강"), (r"partial agonist", "부분 작용"),
    (r"inverse agonist", "역작용"), (r"agonist", "작용"), (r"antagonist", "차단"), (r"inhibitor", "억제"),
    (r"blocker", "차단"), (r"opener", "개방"), (r"activator", "활성화"), (r"modulator", "조절"),
    (r"releasing agent", "방출 촉진"), (r"type 1", "1형"), (r"type 2", "2형"),
]


def _moa_ko(text: str) -> str:
    """'Human immunodeficiency virus type 1 protease inhibitor' -> 'HIV-1 단백분해효소 억제' (shown on the body)."""
    out = text
    for pat, ko in _MOA_WORDS:
        out = re.sub(rf"\b(?:{pat})\b", ko, out, flags=re.I)
    out = re.sub(r"\s+", " ", out.replace(";", " ·").replace(",", " ·")).strip()
    return out if len(out) <= 40 else out[:38] + "…"


def default_regimen(form: str, t_half: float, route: str, kind: str) -> dict:
    """usual dosing interval when no dose was entered: by half-life and release form (concept, for the sketch)"""
    if kind == "food":
        n = 1
    elif route == "injection" and t_half >= 96:
        return {"per_day": 1, "days": None, "prn": False, "weekly": True, "source": "추정(주 1회 주사)"}
    elif form == "ER":
        n = 1 if t_half >= 10 else 2
    else:
        n = 1 if t_half >= 18 else 2 if t_half >= 8 else 3 if t_half >= 3 else 4
    return {"per_day": n, "days": None, "prn": False, "weekly": False, "source": "추정(반감기 기준 일반적 복용 간격)"}


def _rank(sev: str) -> int:
    return {"normal": 0, "low": 1, "mid": 2, "high": 3}.get(sev, 0)


def _excrete_text(kid: dict, renal: list[str]) -> str:
    clear = kid.get("clearance") if kid else None
    base = "간에서 분해된 찌꺼기와 일부 약은 콩팥에서 걸러져 소변으로, 나머지는 담즙(대변)으로 나갑니다."
    if clear is None:
        return base + (f" 콩팥으로 주로 배출되는 약: {', '.join(renal)} — 크레아티닌을 입력하면 쌓임 여부를 계산합니다."
                       if renal else "")
    if clear < 60 and renal:
        return (base + f" 콩팥 기능이 정상의 약 {kid.get('pct_normal')}%라 {', '.join(renal)}이(가) 잘 빠져나가지 못하고 "
                "몸에 쌓입니다 — 용량 조절이 필요합니다.")
    if clear < 60:
        return base + f" 콩팥 기능은 정상의 약 {kid.get('pct_normal')}%지만 이번 약들은 주로 간으로 처리됩니다."
    return base + f" 콩팥 기능(정상의 약 {kid.get('pct_normal')}%)이 충분해 정상적으로 배출됩니다."


def build_journey(eng, U, pair_rows, about, cyp_p, prods_of=None, **kw) -> dict:
    return Journey(eng, U, pair_rows, about, cyp_p, prods_of).build(**kw)
