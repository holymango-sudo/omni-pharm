"""Curated clinical knowledge tables.

* TARGET_CONDITIONS: clinically important adverse events (MedDRA preferred terms as
  spelled in TWOSIDES) -> (Korean name, organ system). Only these are modeled, so every
  prediction maps to an organ on the body map.
* KR_BRANDS / KR_INGREDIENTS: Korean product/ingredient names -> English generic names.
  Only single-ingredient brands whose composition is well established are listed.
  Combination products must be entered by ingredient (or resolved via data.go.kr).
"""

from __future__ import annotations

import re

ORGANS = {
    "brain": "뇌·신경계",
    "heart": "심장·순환계",
    "lung": "폐·호흡기",
    "liver": "간",
    "gi": "위장관",
    "kidney": "신장·전해질",
    "blood": "혈액·응고",
    "systemic": "피부·대사·전신",
}

TARGET_CONDITIONS: dict[str, tuple[str, str]] = {
    # heart
    "Atrial fibrillation": ("심방세동", "heart"),
    "Arrhythmia": ("부정맥", "heart"),
    "Electrocardiogram QT prolonged": ("QT 간격 연장", "heart"),
    "Torsade de pointes": ("염전성 심실빈맥", "heart"),
    "Bradycardia": ("서맥", "heart"),
    "Tachycardia": ("빈맥", "heart"),
    "Cardiac failure congestive": ("울혈성 심부전", "heart"),
    "Myocardial infarction": ("심근경색", "heart"),
    "Hypotension": ("저혈압", "heart"),
    "Hypertension": ("고혈압", "heart"),
    "Syncope": ("실신", "heart"),
    # brain
    "Convulsion": ("경련", "brain"),
    "Somnolence": ("졸음·진정", "brain"),
    "Dizziness": ("어지러움", "brain"),
    "Confusional state": ("혼돈", "brain"),
    "Serotonin syndrome": ("세로토닌 증후군", "brain"),
    "Cerebrovascular accident": ("뇌졸중", "brain"),
    "Tremor": ("떨림", "brain"),
    "Headache": ("두통", "brain"),
    "Fall": ("낙상", "brain"),
    # lung
    "Dyspnoea": ("호흡곤란", "lung"),
    "Respiratory failure": ("호흡부전", "lung"),
    "Pulmonary embolism": ("폐색전증", "lung"),
    "Pneumonia": ("폐렴", "lung"),
    # liver
    "Hepatic enzyme increased": ("간효소 상승", "liver"),
    "Alanine aminotransferase increased": ("ALT 상승", "liver"),
    "Aspartate aminotransferase increased": ("AST 상승", "liver"),
    "Hepatic failure": ("간부전", "liver"),
    "Jaundice": ("황달", "liver"),
    "Liver function test abnormal": ("간기능 검사 이상", "liver"),
    # gi
    "Gastrointestinal haemorrhage": ("위장관 출혈", "gi"),
    "Nausea": ("구역", "gi"),
    "Vomiting": ("구토", "gi"),
    "Diarrhoea": ("설사", "gi"),
    "Abdominal pain": ("복통", "gi"),
    "Pancreatitis": ("췌장염", "gi"),
    "Constipation": ("변비", "gi"),
    # kidney
    "Renal failure acute": ("급성 신부전", "kidney"),
    "Renal failure": ("신부전", "kidney"),
    "Renal impairment": ("신기능 저하", "kidney"),
    "Blood creatinine increased": ("크레아티닌 상승", "kidney"),
    "Hyperkalaemia": ("고칼륨혈증", "kidney"),
    "Hyponatraemia": ("저나트륨혈증", "kidney"),
    # blood
    "Anaemia": ("빈혈", "blood"),
    "Thrombocytopenia": ("혈소판 감소", "blood"),
    "Neutropenia": ("호중구 감소", "blood"),
    "International normalised ratio increased": ("INR 상승(출혈 경향)", "blood"),
    "Haemorrhage": ("출혈", "blood"),
    # systemic
    "Rash": ("발진", "systemic"),
    "Hypoglycaemia": ("저혈당", "systemic"),
    "Rhabdomyolysis": ("횡문근융해증", "systemic"),
    "Angioedema": ("혈관부종", "systemic"),
}

TARGET_LOWER = {k.lower(): k for k in TARGET_CONDITIONS}

# Potentially life-threatening events: used to rank warnings (a 2x lift in nausea is not a 2x lift in
# torsade de pointes).
SERIOUS_CONDITIONS = frozenset({
    "Electrocardiogram QT prolonged", "Torsade de pointes", "Arrhythmia", "Cardiac failure congestive",
    "Myocardial infarction", "Bradycardia", "Syncope", "Hypotension", "Convulsion", "Serotonin syndrome",
    "Cerebrovascular accident", "Respiratory failure", "Pulmonary embolism", "Hepatic failure", "Jaundice",
    "Gastrointestinal haemorrhage", "Pancreatitis", "Renal failure acute", "Renal failure", "Hyperkalaemia",
    "Hyponatraemia", "Thrombocytopenia", "Neutropenia", "International normalised ratio increased",
    "Haemorrhage", "Hypoglycaemia", "Rhabdomyolysis", "Angioedema", "Fall", "Confusional state",
    "Somnolence", "Atrial fibrillation",
})

# Korean ingredient names -> English generic (TWOSIDES / RxNorm naming)
KR_INGREDIENTS: dict[str, str] = {
    "아세트아미노펜": "acetaminophen", "이부프로펜": "ibuprofen", "덱시부프로펜": "dexibuprofen",
    "나프록센": "naproxen", "아스피린": "aspirin", "아세틸살리실산": "aspirin",
    "플루르비프로펜": "flurbiprofen", "셀레콕시브": "celecoxib", "디클로페낙": "diclofenac",
    "멜록시캄": "meloxicam", "트라마돌": "tramadol", "카페인": "caffeine",
    "와파린": "warfarin", "클로피도그렐": "clopidogrel", "아픽사반": "apixaban",
    "리바록사반": "rivaroxaban", "다비가트란": "dabigatran", "헤파린": "heparin",
    "아미오다론": "amiodarone", "디곡신": "digoxin", "아테놀롤": "atenolol",
    "메토프롤롤": "metoprolol", "카르베딜롤": "carvedilol", "비소프롤롤": "bisoprolol",
    "암로디핀": "amlodipine", "딜티아젬": "diltiazem", "베라파밀": "verapamil",
    "로사르탄": "losartan", "발사르탄": "valsartan", "텔미사르탄": "telmisartan",
    "리시노프릴": "lisinopril", "에날라프릴": "enalapril", "라미프릴": "ramipril",
    "푸로세미드": "furosemide", "히드로클로로티아지드": "hydrochlorothiazide",
    "스피로노락톤": "spironolactone", "아토르바스타틴": "atorvastatin",
    "로수바스타틴": "rosuvastatin", "심바스타틴": "simvastatin", "프라바스타틴": "pravastatin",
    "메트포르민": "metformin", "글리메피리드": "glimepiride", "글리피지드": "glipizide",
    "시타글립틴": "sitagliptin", "피오글리타존": "pioglitazone", "인슐린": "insulin",
    "레보티록신": "levothyroxine", "오메프라졸": "omeprazole", "에소메프라졸": "esomeprazole",
    "판토프라졸": "pantoprazole", "란소프라졸": "lansoprazole", "파모티딘": "famotidine",
    "라니티딘": "ranitidine", "메토클로프라미드": "metoclopramide", "온단세트론": "ondansetron",
    "시프로플록사신": "ciprofloxacin", "레보플록사신": "levofloxacin", "목시플록사신": "moxifloxacin",
    "아지트로마이신": "azithromycin", "클래리트로마이신": "clarithromycin",
    "에리트로마이신": "erythromycin", "아목시실린": "amoxicillin", "독시사이클린": "doxycycline",
    "메트로니다졸": "metronidazole", "설파메톡사졸": "sulfamethoxazole",
    "트리메토프림": "trimethoprim", "플루코나졸": "fluconazole", "케토코나졸": "ketoconazole",
    "리팜피신": "rifampin", "실데나필": "sildenafil", "타다라필": "tadalafil",
    "알프라졸람": "alprazolam", "로라제팜": "lorazepam", "디아제팜": "diazepam",
    "클로나제팜": "clonazepam", "졸피뎀": "zolpidem", "플루옥세틴": "fluoxetine",
    "설트랄린": "sertraline", "에스시탈로프람": "escitalopram", "파록세틴": "paroxetine",
    "시탈로프람": "citalopram", "벤라팍신": "venlafaxine", "둘록세틴": "duloxetine",
    "미르타자핀": "mirtazapine", "트라조돈": "trazodone", "아미트립틸린": "amitriptyline",
    "부프로피온": "bupropion", "쿠에티아핀": "quetiapine", "올란자핀": "olanzapine",
    "리스페리돈": "risperidone", "아리피프라졸": "aripiprazole", "할로페리돌": "haloperidol",
    "리튬": "lithium", "카르바마제핀": "carbamazepine", "발프로산": "valproic acid",
    "라모트리진": "lamotrigine", "레비티라세탐": "levetiracetam", "페니토인": "phenytoin",
    "가바펜틴": "gabapentin", "프레가발린": "pregabalin", "옥시코돈": "oxycodone",
    "모르핀": "morphine", "펜타닐": "fentanyl", "프레드니솔론": "prednisolone",
    "프레드니손": "prednisone", "덱사메타손": "dexamethasone", "세티리진": "cetirizine",
    "로라타딘": "loratadine", "펙소페나딘": "fexofenadine", "클로르페니라민": "chlorpheniramine",
    "디펜히드라민": "diphenhydramine", "덱스트로메토르판": "dextromethorphan",
    "슈도에페드린": "pseudoephedrine", "페닐에프린": "phenylephrine", "알로푸리놀": "allopurinol",
    "콜히친": "colchicine", "메토트렉세이트": "methotrexate", "타크로리무스": "tacrolimus",
    "사이클로스포린": "cyclosporine", "탐스로신": "tamsulosin", "피나스테리드": "finasteride",
    "몬테루카스트": "montelukast", "테오필린": "theophylline", "도네페질": "donepezil",
    # drugs approved after ~2010 (added via the FAERS extension stage)
    "니르마트렐비르": "nirmatrelvir", "리토나비르": "ritonavir", "사쿠비트릴": "sacubitril", "에독사반": "edoxaban",
    "다파글리플로진": "dapagliflozin", "엠파글리플로진": "empagliflozin", "티카그렐러": "ticagrelor",
    "리나글립틴": "linagliptin", "우파다시티닙": "upadacitinib", "토파시티닙": "tofacitinib", "바리시티닙": "baricitinib",
    "오시머티닙": "osimertinib", "미라베그론": "mirabegron", "보티옥세틴": "vortioxetine", "루라시돈": "lurasidone",
    "브렉스피프라졸": "brexpiprazole", "이바브라딘": "ivabradine", "피네레논": "finerenone", "몰누피라비르": "molnupiravir",
    "페북소스타트": "febuxostat", "솔리페나신": "solifenacin", "실로도신": "silodosin", "라코사마이드": "lacosamide",
    "에제티미브": "ezetimibe", "렘데시비르": "remdesivir",
    # common Korean OTC (cold / pain) ingredients - several exist only as Korean-market structures (kr:)
    "메틸에페드린": "methylephedrine", "펜톡시베린": "pentoxyverine", "구아이페네신": "guaifenesin",
    "이소프로필안티피린": "isopropylantipyrine", "에텐자미드": "ethenzamide", "노스카핀": "noscapine",
    "디히드로코데인": "dihydrocodeine", "코데인": "codeine", "티페피딘": "tipepidine", "벤프로페린": "benproperine",
    "레보드로프로피진": "levodropropizine", "카르비녹사민": "carbinoxamine", "트리프롤리딘": "triprolidine",
    "페니라민": "pheniramine", "브롬헥신": "bromhexine", "암브록솔": "ambroxol", "아세틸시스테인": "acetylcysteine",
    "에르도스테인": "erdosteine", "독실아민": "doxylamine", "록소프로펜": "loxoprofen", "케토프로펜": "ketoprofen",
    "아세클로페낙": "aceclofenac", "펠루비프로펜": "pelubiprofen", "레보세티리진": "levocetirizine",
    "베포타스틴": "bepotastine", "에바스틴": "ebastine", "메퀴타진": "mequitazine", "시메티딘": "cimetidine",
    # FDA CYP index drugs (interaction explanations)
    "미다졸람": "midazolam", "트리아졸람": "triazolam", "이트라코나졸": "itraconazole", "보리코나졸": "voriconazole",
    "포사코나졸": "posaconazole", "미코나졸": "miconazole", "테르비나핀": "terbinafine", "로바스타틴": "lovastatin",
    "부스피론": "buspirone", "부데소니드": "budesonide", "펠로디핀": "felodipine", "바데나필": "vardenafil",
    "아바나필": "avanafil", "시롤리무스": "sirolimus", "에베롤리무스": "everolimus", "이브루티닙": "ibrutinib",
    "이마티닙": "imatinib", "크리조티닙": "crizotinib", "베무라페닙": "vemurafenib", "드로네다론": "dronedarone",
    "다루나비르": "darunavir", "로피나비르": "lopinavir", "사퀴나비르": "saquinavir", "인디나비르": "indinavir",
    "넬피나비르": "nelfinavir", "티프라나비르": "tipranavir", "코비시스타트": "cobicistat", "에파비렌즈": "efavirenz",
    "에트라비린": "etravirine", "릴피비린": "rilpivirine", "아프레피탄트": "aprepitant", "보센탄": "bosentan",
    "모다피닐": "modafinil", "페노바르비탈": "phenobarbital", "프리미돈": "primidone", "리파부틴": "rifabutin",
    "리파펜틴": "rifapentine", "엔잘루타마이드": "enzalutamide", "아팔루타마이드": "apalutamide",
    "아비라테론": "abiraterone", "미토탄": "mitotane", "타목시펜": "tamoxifen", "플루복사민": "fluvoxamine",
    "이미프라민": "imipramine", "노르트립틸린": "nortriptyline", "데시프라민": "desipramine", "아토목세틴": "atomoxetine",
    "페르페나진": "perphenazine", "피모지드": "pimozide", "클로자핀": "clozapine", "퀴니딘": "quinidine",
    "프로파페논": "propafenone", "프로프라놀롤": "propranolol", "네비볼롤": "nebivolol", "멕실레틴": "mexiletine",
    "톨테로딘": "tolterodine", "다리페나신": "darifenacin", "시나칼셋": "cinacalcet", "메타돈": "methadone",
    "알펜타닐": "alfentanil", "티자니딘": "tizanidine", "라멜테온": "ramelteon", "멜라토닌": "melatonin",
    "로피니롤": "ropinirole", "피르페니돈": "pirfenidone", "피록시캄": "piroxicam", "톨부타미드": "tolbutamide",
    "클로바잠": "clobazam", "티클로피딘": "ticlopidine", "엘레트립탄": "eletriptan", "니솔디핀": "nisoldipine",
    # lifestyle / function-improving drugs (hair loss, ED, obesity, acne, skin, hormones)
    "두타스테리드": "dutasteride", "미녹시딜": "minoxidil", "유데나필": "udenafil", "미로데나필": "mirodenafil", "오르리스타트": "orlistat",
    "펜터민": "phentermine", "펜디메트라진": "phendimetrazine", "디에틸프로피온": "diethylpropion",
    "로카세린": "lorcaserin", "이소트레티노인": "isotretinoin", "트레티노인": "tretinoin", "비마토프로스트": "bimatoprost",
    "프로게스테론": "progesterone", "에스트라디올": "estradiol", "테스토스테론": "testosterone",
    "에티닐에스트라디올": "ethinyl estradiol", "드로스피레논": "drospirenone", "레보노르게스트렐": "levonorgestrel",
    "시프로테론": "cyproterone", "트라넥삼산": "tranexamic acid", "다폭세틴": "dapoxetine", "멜라토닌서방정": "melatonin",
    # herbal medicines / supplements (label- and rule-only ingredients)
    "감초": "glycyrrhiza", "감초추출물": "glycyrrhiza", "은행잎": "ginkgo biloba leaf extract",
    "은행잎추출물": "ginkgo biloba leaf extract", "징코": "ginkgo biloba leaf extract", "인삼": "ginseng", "홍삼": "ginseng",
    "마황": "ephedra herb", "세인트존스워트": "hypericum perforatum", "성요한풀": "hypericum perforatum",
    "밀크씨슬": "milk thistle", "실리마린": "silymarin", "쏘팔메토": "saw palmetto", "강황": "turmeric",
    "커큐민": "curcumin", "오메가3": "omega-3 fatty acids", "홍국": "red yeast rice", "마늘": "garlic",
    "녹차추출물": "green tea extract", "녹차": "green tea extract", "카테킨": "green tea extract",
    "흑마늘": "garlic", "울금": "turmeric", "흑삼": "ginseng", "오메가-3": "omega-3 fatty acids",
    "오메가쓰리": "omega-3 fatty acids", "크릴오일": "krill oil", "피쉬오일": "fish oil", "어유": "fish oil",
    "유산균": "probiotics", "프로바이오틱스": "probiotics", "락토바실러스": "lactobacillus", "비피더스": "bifidobacterium",
    "코엔자임Q10": "ubidecarenone", "코큐텐": "ubidecarenone", "크랜베리": "cranberry", "루테인": "lutein",
    "비오틴": "biotin", "프로폴리스": "propolis", "아르기닌": "arginine",
    "가르시니아": "garcinia cambogia", "칼슘": "calcium carbonate",
    "마그네슘": "magnesium oxide", "철분": "ferrous sulfate", "아연": "zinc", "염화칼륨": "potassium chloride",
    "철분제": "ferrous sulfate", "칼슘제": "calcium carbonate", "마그네슘제": "magnesium oxide",
    "비타민C": "ascorbic acid", "비타민D": "cholecalciferol", "비타민E": "tocopherol", "비타민K": "phytonadione",
    "비타민B12": "cyanocobalamin", "엽산": "folic acid", "글루타치온": "glutathione", "히알루론산": "hyaluronic acid",
    "콜라겐": "collagen", "태반": "human placenta hydrolysate", "멜스몬": "human placenta hydrolysate",
    "라에넥": "human placenta hydrolysate", "보톡스주사": "botulinum toxin",
    "커피": "caffeine", "에너지드링크": "caffeine", "핫식스": "caffeine", "레드불": "caffeine",
    # aesthetic injections / devices commonly asked about (ingredient-level; devices have little interaction data)
    "리쥬란": "polynucleotide", "쥬베룩": "polylactic acid", "엘라스틴": "elastin", "스킨부스터": "hyaluronic acid",
    "신데렐라주사": "thioctic acid", "백옥주사": "glutathione", "태반주사": "human placenta hydrolysate",
    "키블라": "deoxycholic acid", "트레티노인크림": "tretinoin", "스멕타": "dioctahedral smectite",
    "물광주사": "hyaluronic acid", "연어주사": "polynucleotide", "마늘주사": "fursultiamine", "비타민주사": "ascorbic acid",
}

# Single-ingredient brand names (composition well established)
KR_BRANDS: dict[str, list[str]] = {
    "타이레놀": ["acetaminophen"], "부루펜": ["ibuprofen"], "탁센": ["naproxen"],
    "아스피린프로텍트": ["aspirin"], "쿠마딘": ["warfarin"], "플라빅스": ["clopidogrel"],
    "엘리퀴스": ["apixaban"], "자렐토": ["rivaroxaban"], "프라닥사": ["dabigatran"],
    "코다론": ["amiodarone"], "노바스크": ["amlodipine"], "코자": ["losartan"],
    "디오반": ["valsartan"], "미카르디스": ["telmisartan"], "라식스": ["furosemide"],
    "알닥톤": ["spironolactone"], "리피토": ["atorvastatin"], "크레스토": ["rosuvastatin"],
    "조코": ["simvastatin"], "글루코파지": ["metformin"], "다이아벡스": ["metformin"],
    "아마릴": ["glimepiride"], "자누비아": ["sitagliptin"], "씬지로이드": ["levothyroxine"],
    "로섹": ["omeprazole"], "넥시움": ["esomeprazole"], "판토록": ["pantoprazole"],
    "가스터": ["famotidine"], "시프로바이": ["ciprofloxacin"], "크라비트": ["levofloxacin"],
    "지스로맥스": ["azithromycin"], "클래리시드": ["clarithromycin"], "디푸루칸": ["fluconazole"],
    "비아그라": ["sildenafil"], "시알리스": ["tadalafil"], "자낙스": ["alprazolam"],
    "아티반": ["lorazepam"], "바리움": ["diazepam"], "리보트릴": ["clonazepam"],
    "스틸녹스": ["zolpidem"], "프로작": ["fluoxetine"], "졸로푸트": ["sertraline"],
    "렉사프로": ["escitalopram"], "팍실": ["paroxetine"], "심발타": ["duloxetine"],
    "레메론": ["mirtazapine"], "웰부트린": ["bupropion"], "쎄로켈": ["quetiapine"],
    "자이프렉사": ["olanzapine"], "리스페달": ["risperidone"], "아빌리파이": ["aripiprazole"],
    "테그레톨": ["carbamazepine"], "라믹탈": ["lamotrigine"], "케프라": ["levetiracetam"],
    "뉴론틴": ["gabapentin"], "리리카": ["pregabalin"], "울트람": ["tramadol"],
    "쎄레브렉스": ["celecoxib"], "지르텍": ["cetirizine"], "클라리틴": ["loratadine"],
    "알레그라": ["fexofenadine"], "자이로릭": ["allopurinol"], "싱귤레어": ["montelukast"],
    "아리셉트": ["donepezil"], "하루날": ["tamsulosin"], "프로스카": ["finasteride"],
    "스트렙실인텐시브": ["flurbiprofen"],
    # combination products whose composition is fixed and well known
    "팍스로비드": ["nirmatrelvir", "ritonavir"], "엔트레스토": ["sacubitril", "valsartan"],
    "릭시아나": ["edoxaban"], "포시가": ["dapagliflozin"], "자디앙": ["empagliflozin"], "브릴린타": ["ticagrelor"],
    "트라젠타": ["linagliptin"], "린버크": ["upadacitinib"], "젤잔즈": ["tofacitinib"], "올루미언트": ["baricitinib"],
    "타그리소": ["osimertinib"], "베타미가": ["mirabegron"], "브린텔릭스": ["vortioxetine"], "페브릭": ["febuxostat"],
    "베시케어": ["solifenacin"], "빔팻": ["lacosamide"], "이지트롤": ["ezetimibe"], "라게브리오": ["molnupiravir"],
    "베믈리디": ["tenofovir alafenamide"], "콜킨": ["colchicine"],
    # lifestyle drugs
    "프로페시아": ["finasteride"], "아보다트": ["dutasteride"], "로아큐탄": ["isotretinoin"], "라티쎄": ["bimatoprost"],
    "제니칼": ["orlistat"], "큐시미아": ["phentermine", "topiramate"], "콘트라브": ["naltrexone", "bupropion"],
    "다이안느": ["cyproterone", "ethinyl estradiol"], "야즈": ["drospirenone", "ethinyl estradiol"],
    "야스민": ["drospirenone", "ethinyl estradiol"], "머시론": ["desogestrel", "ethinyl estradiol"],
    "자이데나": ["udenafil"], "엠빅스": ["mirodenafil"], "레비트라": ["vardenafil"], "프릴리지": ["dapoxetine"],
    "트란시노": ["tranexamic acid"],
    # sleep medicines (incl. brands marketed abroad)
    "벨솜라": ["suvorexant"], "큐비빅": ["daridorexant"], "데이비고": ["lemborexant"], "할시온": ["triazolam"],
    "로제렘": ["ramelteon"], "루네스타": ["eszopiclone"], "앰비엔": ["zolpidem"], "서카딘": ["melatonin"],
    "제피드": ["avanafil"], "조플루자": ["baloxavir marboxil"], "타미플루": ["oseltamivir"],
}


# Therapeutic classes for "same-class duplication" across different products - the most common real
# OTC danger (two cold medicines = two decongestants + two sedating antihistamines). Names are
# drug_master names or untrained-molecule names (without the "ing:" prefix).
DRUG_CLASSES: dict[str, tuple[str, frozenset[str]]] = {
    "nsaid": ("소염진통제(NSAID)", frozenset({
        "ibuprofen", "dexibuprofen", "naproxen", "aspirin", "ketoprofen", "dexketoprofen", "loxoprofen", "diclofenac",
        "celecoxib", "meloxicam", "flurbiprofen", "aceclofenac", "etodolac", "piroxicam", "indomethacin",
        "isopropylantipyrine", "propyphenazone", "nabumetone", "pelubiprofen", "zaltoprofen", "ketorolac"})),
    "sedating_antihistamine": ("1세대(진정성) 항히스타민", frozenset({
        "chlorpheniramine", "dexchlorpheniramine", "diphenhydramine", "doxylamine", "carbinoxamine", "triprolidine",
        "brompheniramine", "clemastine", "promethazine", "hydroxyzine", "cyproheptadine", "pheniramine",
        "dimenhydrinate", "mequitazine", "chlorphenamine"})),
    "antihistamine2": ("2세대 항히스타민", frozenset({
        "cetirizine", "levocetirizine", "loratadine", "desloratadine", "fexofenadine", "ebastine", "bepotastine",
        "olopatadine", "bilastine", "rupatadine", "epinastine", "azelastine"})),
    "decongestant": ("교감신경흥분 비충혈제거제", frozenset({
        "pseudoephedrine", "phenylephrine", "methylephedrine", "ephedrine", "phenylpropanolamine"})),
    "antitussive": ("중추성 진해제", frozenset({
        "dextromethorphan", "codeine", "dihydrocodeine", "pentoxyverine", "noscapine", "tipepidine", "benproperine",
        "levodropropizine", "cloperastine"})),
    "sedative": ("수면·진정제", frozenset({
        "zolpidem", "alprazolam", "lorazepam", "diazepam", "clonazepam", "triazolam", "etizolam", "eszopiclone",
        "zopiclone", "flurazepam"})),
    "opioid": ("오피오이드 진통제", frozenset({"tramadol", "oxycodone", "morphine", "fentanyl", "hydromorphone",
                                         "tapentadol", "hydrocodone"})),
    "pde5": ("발기부전 치료제(PDE5 억제제)", frozenset({
        "sildenafil", "tadalafil", "vardenafil", "avanafil", "udenafil", "mirodenafil"})),
    "anticoagulant": ("항응고·항혈소판제", frozenset({
        "warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "clopidogrel", "ticagrelor", "prasugrel",
        "cilostazol", "heparin"})),
}


# --------------------------------------------------------------------------- drug-disease interactions
# Additional drug groups used only by the drug-disease rules below.
DRUG_GROUPS: dict[str, frozenset[str]] = {
    "anticholinergic": frozenset({
        "oxybutynin", "tolterodine", "solifenacin", "darifenacin", "trospium", "fesoterodine", "scopolamine",
        "hyoscyamine", "atropine", "benztropine", "trihexyphenidyl", "amitriptyline", "imipramine",
        "nortriptyline", "doxepin", "clomipramine", "dicyclomine", "glycopyrrolate", "ipratropium", "tiotropium",
        "butylscopolamine", "orphenadrine"}),
    "nonselective_beta_blocker": frozenset({"propranolol", "nadolol", "sotalol", "timolol", "carvedilol",
                                            "labetalol", "pindolol"}),
    "corticosteroid": frozenset({"prednisone", "prednisolone", "methylprednisolone", "dexamethasone",
                                 "hydrocortisone", "betamethasone", "deflazacort", "triamcinolone"}),
    "fluoroquinolone": frozenset({"ciprofloxacin", "levofloxacin", "moxifloxacin", "ofloxacin", "norfloxacin",
                                  "gemifloxacin"}),
    "macrolide": frozenset({"azithromycin", "clarithromycin", "erythromycin", "roxithromycin"}),
    "aminoglycoside": frozenset({"gentamicin", "tobramycin", "amikacin", "neomycin", "streptomycin"}),
    "dopamine_antagonist": frozenset({
        "metoclopramide", "haloperidol", "risperidone", "olanzapine", "prochlorperazine", "chlorpromazine",
        "domperidone", "levosulpiride", "sulpiride", "perphenazine", "aripiprazole", "ziprasidone", "paliperidone"}),
    "qt_prolonging": frozenset({
        "amiodarone", "sotalol", "dofetilide", "dronedarone", "quinidine", "haloperidol", "ondansetron",
        "domperidone", "citalopram", "escitalopram", "methadone", "azithromycin", "clarithromycin", "erythromycin",
        "levofloxacin", "moxifloxacin", "ciprofloxacin", "hydroxychloroquine", "chloroquine", "quetiapine",
        "ziprasidone", "pimozide", "thioridazine", "fluconazole", "ketoconazole", "donepezil", "tamoxifen",
        "gemifloxacin", "imipramine", "amitriptyline", "clomipramine", "desipramine", "nortriptyline", "doxepin",
        "chlorpromazine", "droperidol", "pentamidine", "arsenic trioxide", "vandetanib", "nilotinib", "ivabradine",
        "propafenone", "flecainide", "disopyramide", "procainamide", "mesoridazine", "pasireotide", "levomethadyl"}),
    "seizure_threshold": frozenset({"tramadol", "bupropion", "theophylline", "meperidine", "clozapine",
                                    "tapentadol"}),
    "teratogen": frozenset({
        "warfarin", "valproate", "isotretinoin", "acitretin", "methotrexate", "mycophenolate mofetil",
        "mycophenolic acid", "thalidomide", "lenalidomide", "lisinopril", "enalapril", "ramipril", "captopril",
        "perindopril", "losartan", "valsartan", "telmisartan", "candesartan", "irbesartan", "olmesartan",
        "atorvastatin", "simvastatin", "rosuvastatin", "pravastatin", "lovastatin", "carbamazepine", "phenytoin",
        "topiramate", "lithium", "misoprostol", "finasteride", "dutasteride", "leflunomide", "ribavirin",
        "doxycycline", "tetracycline", "minocycline", "fluconazole"}),
    "thiazolidinedione": frozenset({"pioglitazone", "rosiglitazone"}),
    "nondhp_ccb": frozenset({"verapamil", "diltiazem"}),
    "thiazide": frozenset({"hydrochlorothiazide", "chlorthalidone", "indapamide", "metolazone"}),
    "metformin": frozenset({"metformin"}),
    "acetaminophen": frozenset({"acetaminophen"}),
    "hepatotoxic": frozenset({"valproate", "ketoconazole", "isoniazid", "methotrexate", "amiodarone",
                              "leflunomide", "nevirapine", "terbinafine"}),
}

# Conditions the user can tick or type. model_flag = the comorbidity feature of the patient model (if any).
DISEASES: dict[str, dict] = {
    "renal": {"ko": "신장질환(CKD)", "kw": ("신부전", "신장", "콩팥", "투석", "ckd", "renal", "kidney"), "flag": "renal"},
    "hepatic": {"ko": "간질환", "kw": ("간경변", "간경화", "간염", "간부전", "지방간", "간질환", "cirrhosis", "hepat",
                                    "liver"), "flag": "hepatic"},
    "heart_failure": {"ko": "심부전", "kw": ("심부전", "heart failure"), "flag": "cardiac"},
    "arrhythmia": {"ko": "부정맥·QT연장", "kw": ("부정맥", "심방세동", "qt", "arrhythm", "atrial fib"), "flag": "cardiac"},
    "cardiac": {"ko": "협심증·심근경색", "kw": ("협심증", "심근경색", "관상동맥", "angina", "coronary"), "flag": "cardiac"},
    "hypertension": {"ko": "고혈압", "kw": ("고혈압", "hypertens"), "flag": "hypertension"},
    "diabetes": {"ko": "당뇨", "kw": ("당뇨", "diabet"), "flag": "diabetes"},
    "peptic_ulcer": {"ko": "위궤양·위장출혈", "kw": ("위궤양", "십이지장궤양", "궤양", "위출혈", "위장출혈", "ulcer",
                                            "gi bleed"), "flag": None},
    "bleeding": {"ko": "출혈경향·혈소판감소", "kw": ("혈우병", "혈소판감소", "출혈경향", "출혈성", "hemophilia",
                                              "thrombocytopenia"), "flag": None},
    "asthma": {"ko": "천식·COPD", "kw": ("천식", "copd", "만성폐쇄성", "asthma"), "flag": None},
    "glaucoma": {"ko": "녹내장", "kw": ("녹내장", "glaucoma"), "flag": None},
    "bph": {"ko": "전립선비대", "kw": ("전립선비대", "전립선 비대", "bph", "prostat"), "flag": None},
    # note: not the bare word "간질" - it is also the prefix of 간질환 (liver disease)
    "epilepsy": {"ko": "뇌전증·경련", "kw": ("뇌전증", "간질발작", "경련", "epilep", "seizure"), "flag": None},
    "dementia": {"ko": "치매·인지저하", "kw": ("치매", "알츠하이머", "인지저하", "dementia", "alzheimer"), "flag": None},
    "parkinson": {"ko": "파킨슨병", "kw": ("파킨슨", "parkinson"), "flag": None},
    "pregnancy": {"ko": "임신(가능성)", "kw": ("임신", "임부", "pregnan"), "flag": None},
    "hyperthyroid": {"ko": "갑상선기능항진", "kw": ("갑상선기능항진", "그레이브스", "hyperthyroid", "graves"), "flag": None},
    "gout": {"ko": "통풍", "kw": ("통풍", "gout"), "flag": None},
    "myasthenia": {"ko": "중증근무력증", "kw": ("근무력", "myasthenia"), "flag": None},
}

# (disease, drug class/group/name, severity, plain-language reason). Severity "금기" = avoid, "주의" = caution.
# Sources: AGS Beers Criteria (2023) drug-disease table, FDA/MFDS labelling (contraindications & warnings).
DRUG_DISEASE_RULES: list[tuple[str, str, str, str]] = [
    ("peptic_ulcer", "nsaid", "금기", "위궤양·위장 출혈이 다시 생기거나 커질 수 있습니다"),
    ("peptic_ulcer", "anticoagulant", "주의", "위장관 출혈 위험이 커집니다"),
    ("peptic_ulcer", "corticosteroid", "주의", "궤양이 악화될 수 있습니다"),
    ("bleeding", "anticoagulant", "금기", "출혈이 멈추지 않을 위험이 큽니다"),
    ("bleeding", "nsaid", "주의", "혈소판 기능을 떨어뜨려 출혈 위험이 커집니다"),
    ("glaucoma", "sedating_antihistamine", "주의", "(폐쇄각) 녹내장의 안압을 올릴 수 있습니다"),
    ("glaucoma", "anticholinergic", "주의", "(폐쇄각) 녹내장의 안압을 올릴 수 있습니다"),
    ("glaucoma", "decongestant", "주의", "동공을 넓혀 (폐쇄각) 녹내장을 악화시킬 수 있습니다"),
    ("bph", "sedating_antihistamine", "주의", "소변이 막히는 요폐가 생길 수 있습니다"),
    ("bph", "anticholinergic", "주의", "소변이 막히는 요폐가 생길 수 있습니다"),
    ("bph", "decongestant", "주의", "방광 출구를 조여 소변이 잘 안 나올 수 있습니다"),
    ("asthma", "nonselective_beta_blocker", "금기", "기관지를 수축시켜 천식 발작을 일으킬 수 있습니다"),
    ("asthma", "nsaid", "주의", "아스피린·진통제 과민 천식을 유발할 수 있습니다"),
    ("heart_failure", "nsaid", "금기", "몸에 물이 차게 해 심부전을 악화시킵니다"),
    ("heart_failure", "thiazolidinedione", "금기", "체액 저류로 심부전을 악화시킵니다"),
    ("heart_failure", "nondhp_ccb", "주의", "심장 수축력을 떨어뜨려 심부전이 악화될 수 있습니다"),
    ("heart_failure", "decongestant", "주의", "심장에 부담을 줍니다"),
    ("hypertension", "decongestant", "주의", "혈압을 올립니다"),
    ("hypertension", "nsaid", "주의", "혈압을 올리고 혈압약 효과를 떨어뜨립니다"),
    ("arrhythmia", "qt_prolonging", "주의", "심장 리듬 이상(QT 연장·돌연 부정맥) 위험이 커집니다"),
    ("arrhythmia", "decongestant", "주의", "가슴 두근거림·부정맥을 일으킬 수 있습니다"),
    ("cardiac", "nsaid", "주의", "심근경색·뇌졸중 위험이 커질 수 있습니다"),
    ("cardiac", "decongestant", "주의", "심장 혈관을 수축시켜 협심증을 악화시킬 수 있습니다"),
    ("renal", "nsaid", "금기", "신장 혈류를 줄여 신기능을 더 떨어뜨립니다"),
    ("renal", "aminoglycoside", "주의", "신장 독성이 있어 신기능이 더 나빠질 수 있습니다"),
    ("hepatic", "acetaminophen", "주의", "간 손상 위험 — 하루 총량을 줄여야 합니다"),
    ("hepatic", "hepatotoxic", "주의", "간 독성이 있어 간 손상이 악화될 수 있습니다"),
    ("epilepsy", "seizure_threshold", "주의", "경련을 일으키기 쉽게 만듭니다"),
    ("epilepsy", "fluoroquinolone", "주의", "경련을 유발할 수 있습니다"),
    ("dementia", "sedating_antihistamine", "주의", "혼돈·섬망과 낙상을 일으키기 쉽습니다"),
    ("dementia", "anticholinergic", "주의", "기억력 저하·섬망을 악화시킵니다"),
    ("dementia", "sedative", "주의", "혼돈·낙상 위험이 큽니다"),
    ("parkinson", "dopamine_antagonist", "금기", "파킨슨 증상(떨림·경직)을 악화시킵니다"),
    ("pregnancy", "teratogen", "금기", "태아에게 기형·독성을 일으킬 수 있습니다"),
    ("pregnancy", "nsaid", "주의", "임신 20주 이후 태아 신장·심장에 해로울 수 있습니다"),
    ("hyperthyroid", "decongestant", "주의", "두근거림·혈압 상승이 심해질 수 있습니다"),
    ("gout", "thiazide", "주의", "요산을 올려 통풍 발작을 일으킬 수 있습니다"),
    ("diabetes", "corticosteroid", "주의", "혈당을 크게 올립니다"),
    ("diabetes", "fluoroquinolone", "주의", "저혈당·고혈당을 일으킬 수 있습니다"),
    ("myasthenia", "aminoglycoside", "금기", "근력 약화를 급격히 악화시킬 수 있습니다"),
    ("myasthenia", "fluoroquinolone", "금기", "근력 약화를 악화시킬 수 있습니다 (FDA 박스경고)"),
    ("myasthenia", "macrolide", "주의", "근력 약화를 악화시킬 수 있습니다"),
]

# English words to find the disease in FDA label contraindication / boxed-warning text
DISEASE_LABEL_TERMS: dict[str, str] = {
    "peptic_ulcer": r"peptic ulcer|gastrointestinal bleeding|active ulcer", "bleeding": r"bleeding disorder|hemophilia",
    "glaucoma": r"glaucoma", "bph": r"prostatic hypertrophy|urinary retention|bladder[- ]neck obstruction",
    "asthma": r"asthma|bronchospasm", "heart_failure": r"heart failure", "arrhythmia": r"qt prolongation|arrhythmia",
    # renal / hepatic deliberately absent: labels name "severe impairment" thresholds, which the CrCl / liver
    # rules and the patient model handle with the patient's actual values instead of a keyword
    "epilepsy": r"seizure", "pregnancy": r"pregnan", "myasthenia": r"myasthenia", "parkinson": r"parkinson",
    "hyperthyroid": r"hyperthyroid", "dementia": r"dementia",
    "hypertension": r"uncontrolled hypertension|severe hypertension",
}


# FDA "Drug Development and Drug Interactions: Table of Substrates, Inhibitors and Inducers" (clinical index
# drugs). Sensitive substrate = AUC rises >= 5-fold with a strong inhibitor; moderate = 2-5 fold.
CYP_FDA: dict[str, dict[str, frozenset[str]]] = {
    "CYP3A": {
        "sensitive": frozenset({"alfentanil", "avanafil", "buspirone", "conivaptan", "darifenacin", "darunavir", "ebastine",
                                "everolimus", "ibrutinib", "lomitapide", "lovastatin", "midazolam", "naloxegol", "nisoldipine",
                                "saquinavir", "simvastatin", "sirolimus", "tacrolimus", "tipranavir", "triazolam", "vardenafil",
                                "budesonide", "dronedarone", "eletriptan", "felodipine", "quetiapine", "ticagrelor"}),
        "moderate_sub": frozenset({"alprazolam", "aprepitant", "atorvastatin", "colchicine", "eliglustat", "pimozide",
                                   "rilpivirine", "rivaroxaban", "tadalafil", "sildenafil", "amlodipine", "apixaban",
                                   "fentanyl", "oxycodone", "zolpidem", "cyclosporine", "methadone"}),
        "strong_inh": frozenset({"clarithromycin", "cobicistat", "idelalisib", "indinavir", "itraconazole", "ketoconazole",
                                 "lopinavir", "nefazodone", "nelfinavir", "posaconazole", "ritonavir", "saquinavir",
                                 "telithromycin", "tipranavir", "voriconazole", "nirmatrelvir"}),
        "moderate_inh": frozenset({"aprepitant", "ciprofloxacin", "crizotinib", "cyclosporine", "diltiazem", "dronedarone",
                                   "erythromycin", "fluconazole", "fluvoxamine", "imatinib", "verapamil", "amiodarone"}),
        "strong_ind": frozenset({"apalutamide", "carbamazepine", "enzalutamide", "mitotane", "phenytoin", "rifampin",
                                 "rifapentine"}),
        "moderate_ind": frozenset({"bosentan", "efavirenz", "etravirine", "phenobarbital", "primidone", "modafinil",
                                   "rifabutin"}),
    },
    "CYP2D6": {
        "sensitive": frozenset({"atomoxetine", "desipramine", "dextromethorphan", "eliglustat", "nebivolol", "nortriptyline",
                                "perphenazine", "tolterodine", "venlafaxine"}),
        "moderate_sub": frozenset({"amitriptyline", "imipramine", "metoprolol", "propafenone", "propranolol", "tramadol",
                                   "codeine", "aripiprazole", "vortioxetine", "tamoxifen"}),
        "strong_inh": frozenset({"bupropion", "fluoxetine", "paroxetine", "quinidine", "terbinafine"}),
        "moderate_inh": frozenset({"duloxetine", "mirabegron", "cinacalcet", "abiraterone"}),
        "strong_ind": frozenset(), "moderate_ind": frozenset(),
    },
    "CYP2C9": {
        "sensitive": frozenset({"celecoxib", "glimepiride", "phenytoin", "tolbutamide"}),
        "moderate_sub": frozenset({"warfarin", "glipizide", "losartan", "ibuprofen", "meloxicam", "piroxicam"}),
        "strong_inh": frozenset(),
        "moderate_inh": frozenset({"amiodarone", "fluconazole", "miconazole", "fluvoxamine", "voriconazole"}),
        "strong_ind": frozenset(), "moderate_ind": frozenset({"rifampin", "carbamazepine", "enzalutamide"}),
    },
    "CYP2C19": {
        "sensitive": frozenset({"clopidogrel", "omeprazole", "lansoprazole", "diazepam", "citalopram", "escitalopram",
                                "clobazam"}),
        "moderate_sub": frozenset({"esomeprazole", "pantoprazole", "sertraline", "voriconazole"}),
        "strong_inh": frozenset({"fluconazole", "fluoxetine", "fluvoxamine", "ticlopidine"}),
        "moderate_inh": frozenset({"omeprazole", "esomeprazole", "felbamate", "voriconazole"}),
        "strong_ind": frozenset({"rifampin"}), "moderate_ind": frozenset({"efavirenz", "phenytoin"}),
    },
    "CYP1A2": {
        "sensitive": frozenset({"alosetron", "caffeine", "duloxetine", "melatonin", "ramelteon", "tasimelteon", "tizanidine",
                                "theophylline"}),
        "moderate_sub": frozenset({"clozapine", "olanzapine", "pirfenidone", "ropinirole"}),
        "strong_inh": frozenset({"ciprofloxacin", "enoxacin", "fluvoxamine"}),
        "moderate_inh": frozenset({"methoxsalen", "mexiletine", "vemurafenib"}),
        "strong_ind": frozenset(), "moderate_ind": frozenset({"phenytoin", "rifampin", "ritonavir"}),
    },
}
MAOI = frozenset({"selegiline", "rasagiline", "safinamide", "phenelzine", "tranylcypromine", "isocarboxazid",
                  "moclobemide", "linezolid", "methylene blue", "procarbazine"})
NITRATES = frozenset({"nitroglycerin", "isosorbide mononitrate", "isosorbide dinitrate", "nicorandil", "amyl nitrite",
                      "isobutyl nitrite"})
SEROTONERGIC = frozenset({"sertraline", "fluoxetine", "paroxetine", "citalopram", "escitalopram", "fluvoxamine",
                          "venlafaxine", "desvenlafaxine", "duloxetine", "milnacipran", "vortioxetine", "trazodone",
                          "mirtazapine", "tramadol", "tapentadol", "fentanyl", "meperidine", "methadone", "lithium",
                          "buspirone", "sumatriptan", "zolmitriptan", "rizatriptan", "naratriptan", "eletriptan",
                          "dextromethorphan", "linezolid", "amitriptyline", "clomipramine", "imipramine", "nortriptyline",
                          "st john's wort", "hypericum perforatum"})


PGP_INHIBITORS = frozenset({"ketoconazole", "itraconazole", "dronedarone", "amiodarone", "verapamil", "quinidine",
                            "clarithromycin", "erythromycin", "cyclosporine", "ritonavir", "lopinavir", "tacrolimus",
                            "ticagrelor", "carvedilol", "lapatinib", "glecaprevir", "velpatasvir", "cobicistat",
                            "propafenone", "ranolazine"})
PGP_INDUCERS = frozenset({"rifampin", "carbamazepine", "phenytoin", "st john's wort", "hypericum perforatum"})
BENZODIAZEPINES = frozenset({
    "alprazolam", "bromazepam", "brotizolam", "chlordiazepoxide", "clobazam", "clonazepam", "clorazepate",
    "clotiazepam", "cloxazolam", "diazepam", "estazolam", "ethyl loflazepate", "etizolam", "fludiazepam",
    "flunitrazepam", "flurazepam", "lorazepam", "lormetazepam", "mexazolam", "midazolam", "nitrazepam", "oxazepam",
    "prazepam", "quazepam", "remimazolam", "temazepam", "tofisopam", "triazolam"})


def _class_terms() -> dict[str, tuple[tuple[str, ...], frozenset[str]]]:
    """Drug classes that labels name instead of individual drugs ('avoid strong CYP3A4 inducers'):
    class id -> (label phrases, member ingredient names)."""
    c3 = CYP_FDA["CYP3A"]
    sjw = {"st john's wort", "hypericum perforatum"}
    return {
        "cyp3a_strong_inh": (("strong CYP3A4 inhibitors", "strong CYP3A inhibitors", "potent CYP3A4 inhibitors",
                              "strong inhibitors of CYP3A4", "strong inhibitors of CYP3A"), c3["strong_inh"]),
        "cyp3a_mod_inh": (("moderate CYP3A4 inhibitors", "moderate CYP3A inhibitors"), c3["moderate_inh"]),
        "cyp3a_strong_ind": (("strong CYP3A4 inducers", "strong CYP3A inducers", "potent CYP3A4 inducers",
                              "strong inducers of CYP3A4", "strong inducers of CYP3A", "CYP3A4 inducers"),
                             c3["strong_ind"] | frozenset(sjw)),
        "cyp2d6_strong_inh": (("strong CYP2D6 inhibitors", "potent CYP2D6 inhibitors"), CYP_FDA["CYP2D6"]["strong_inh"]),
        "cyp2c19_strong_inh": (("strong CYP2C19 inhibitors",), CYP_FDA["CYP2C19"]["strong_inh"]),
        # (a sentence must use it for a CLASS of drugs - engine.QT_CLASS_USE: 'Pimozide and thioridazine prolong the
        # QT interval' is about those two drugs)
        "qt": (("prolong the QT interval", "QT prolonging", "QT-prolonging", "prolong QT", "QTc prolonging",
                "prolong the QTc interval"), DRUG_GROUPS["qt_prolonging"]),
        "maoi": (("MAO inhibitors", "MAOIs", "MAOI", "monoamine oxidase inhibitors"), MAOI),
        "serotonergic": (("serotonergic drugs", "serotonergic agents"), SEROTONERGIC),
        "cns_depressant": (("CNS depressants", "central nervous system depressants"),
                           DRUG_CLASSES["sedative"][1] | DRUG_CLASSES["opioid"][1] | DRUG_GROUPS["gabapentinoid"]),
        # (a separate class: 'avoid benzodiazepines' is not about methadone)
        "benzodiazepine": (("benzodiazepines", "benzodiazepine"), BENZODIAZEPINES),
        "anticoagulant": (("anticoagulants", "antiplatelet agents", "antiplatelet drugs"),
                          DRUG_CLASSES["anticoagulant"][1]),
        "nsaid": (("NSAIDs", "nonsteroidal anti-inflammatory drugs"), DRUG_CLASSES["nsaid"][1]),
        "nitrate": (("nitrates", "nitric oxide donors", "organic nitrates"), NITRATES),
        "k_sparing": (("potassium-sparing diuretics", "potassium supplements", "potassium-containing"),
                      DRUG_GROUPS["k_sparing"] | {"potassium chloride", "potassium citrate"}),
        "pgp_inh": (("P-gp inhibitors", "P-glycoprotein inhibitors", "inhibitors of P-gp",
                     "inhibitors of P-glycoprotein"), PGP_INHIBITORS),
        "pgp_ind": (("P-gp inducers", "P-glycoprotein inducers", "inducers of P-gp"), PGP_INDUCERS),
        "contraceptive": (("oral contraceptives", "hormonal contraceptives", "estrogen-containing contraceptives",
                           "combined hormonal contraceptives", "COCs"), DRUG_GROUPS["oral_contraceptive"]),
        "corticosteroid": (("corticosteroids", "corticosteroid therapy", "systemic steroids"),
                           DRUG_GROUPS["corticosteroid"]),
        "loop_thiazide": (("loop diuretics", "thiazide diuretics", "potassium-depleting diuretics"),
                          DRUG_GROUPS["loop_diuretic"] | DRUG_GROUPS["thiazide"]),
    }


PRODRUGS_ACTIVATED_BY = {"clopidogrel": "CYP2C19", "codeine": "CYP2D6", "tramadol": "CYP2D6", "tamoxifen": "CYP2D6"}
# Combinations prescribed ON PURPOSE that no single marketed product in our data contains (those are found from the
# product data: engine.combo_pairs). The interaction is the point of the combination (boosting) or the regimen is the
# approved use; their labels' wording about each other is not a warning against using them together.
_BOOSTED = ("atazanavir", "darunavir", "lopinavir", "saquinavir", "fosamprenavir", "tipranavir", "elvitegravir",
            "nirmatrelvir")
INTENDED_COMBINATIONS: list[tuple[str, str, str]] = [
    *((b, d, "약동학 부스터: 함께 써서 혈중 농도를 올리는 것이 목적(리토나비르·코비시스타트 허가사항)")
      for b in ("ritonavir", "cobicistat") for d in _BOOSTED),
    ("stiripentol", "clobazam", "스티리펜톨은 클로바잠을 복용 중인 드라베증후군 환자에게 허가된 약(Diacomit 허가사항)"),
    ("dapsone", "rifampin", "한센병 다제요법(WHO MDT)의 표준 병용"),
    ("isoniazid", "rifampin", "결핵 표준 치료의 병용(복합제 Rifamate 등)"),
    ("pyrazinamide", "rifampin", "결핵 표준 치료의 병용"),
    ("ethambutol", "rifampin", "결핵 표준 치료의 병용"),
]
# sensitive substrates whose metabolite by that enzyme is equally active: an inhibitor shifts parent <-> metabolite
# but not the total active moiety (venlafaxine label: CYP2D6 inhibitors need no dose adjustment)
EQUIACTIVE_METABOLITE = {"venlafaxine": "CYP2D6"}
# FDA 'sensitive index substrates' are chosen for pharmacokinetic STUDIES, not for clinical danger: proton pump
# inhibitors have a wide therapeutic margin (labels: no dose adjustment with CYP2C19 inhibitors) - a higher level is
# not a dangerous one
WIDE_MARGIN_SUBSTRATES = frozenset({"omeprazole", "esomeprazole", "lansoprazole", "dexlansoprazole", "pantoprazole",
                                    "rabeprazole"})


def drug_groups(name: str) -> set[str]:
    """All class / group ids a drug belongs to (plus the drug name itself)."""
    n = name.removeprefix("ing:").removeprefix("bio:").removeprefix("lab:").removeprefix("food:")
    out = {n} | set(BIOLOGICS.get(n, {}).get("groups", ())) | set(FOODS.get(n, {}).get("groups", ()))
    for rx, groups in NAME_GROUPS:
        if rx.search(n):
            out |= groups
    for cid, (_, members) in DRUG_CLASSES.items():
        if n in members:
            out.add(cid)
    for gid, members in DRUG_GROUPS.items():
        if n in members:
            out.add(gid)
    return out


def disease_ids(conditions: list[str], text: str | None) -> set[str]:
    t = (text or "").lower()
    ids = {c for c in conditions if c in DISEASES}
    for did, d in DISEASES.items():
        if any(k in t for k in d["kw"]):
            ids.add(did)
    return ids


def drug_class(name: str) -> str | None:
    n = name.removeprefix("ing:").removeprefix("bio:").removeprefix("lab:")
    if any("decongestant" in g for rx, g in NAME_GROUPS if rx.search(n)):
        return "decongestant"  # ephedra herb
    for cid, (_, members) in DRUG_CLASSES.items():
        if n in members:
            return cid
    return None


def resolve_korean(name: str) -> list[str] | None:
    """Return English generic ingredient list for a Korean/English name, or None if unknown."""
    raw = name.split("(")[0].strip()
    key = raw.replace(" ", "")
    if key in KR_BRANDS:
        return KR_BRANDS[key]
    if key in KR_INGREDIENTS:
        return [KR_INGREDIENTS[key]]
    # "아세트아미노펜+카페인" style ingredient lists
    if "+" in key:
        parts = [p for p in key.split("+") if p]
        out = []
        for p in parts:
            if p in KR_INGREDIENTS:
                out.append(KR_INGREDIENTS[p])
            elif p.isascii():
                out.append(p.lower())
            else:
                return None
        return out
    if raw.isascii():
        return [raw.lower()]
    return None


# --------------------------------------------------------------------------- #
# FAERS (patient-level reports) vocabulary
# --------------------------------------------------------------------------- #

# Newer MedDRA versions renamed some preferred terms that TWOSIDES still uses.
PT_SYNONYMS: dict[str, str] = {
    "acute kidney injury": "Renal failure acute",
    "seizure": "Convulsion",
    "generalised tonic-clonic seizure": "Convulsion",
    "chronic kidney disease": "Renal failure",
    "drug-induced liver injury": "Hepatic failure",
    "acute hepatic failure": "Hepatic failure",
    "cardiac failure": "Cardiac failure congestive",
    "acute myocardial infarction": "Myocardial infarction",
    "torsade de pointes": "Torsade de pointes",
    "gastric haemorrhage": "Gastrointestinal haemorrhage",
    "upper gastrointestinal haemorrhage": "Gastrointestinal haemorrhage",
    "lower gastrointestinal haemorrhage": "Gastrointestinal haemorrhage",
    "ischaemic stroke": "Cerebrovascular accident",
    "haemorrhagic stroke": "Cerebrovascular accident",
}

# Comorbidity flags derived from the FAERS indication field (what the patient was
# being treated for). These are the patient features the model is trained on.
COMORBIDITY_PATTERNS: dict[str, str] = {
    "renal": r"renal failure|kidney disease|renal impairment|dialysis|nephropathy|renal disorder"
             r"|end stage renal|renal transplant|kidney transplant|nephrotic|glomerulonephritis",
    "hepatic": r"cirrhosis|hepatic failure|hepatitis|liver disorder|hepatic steatosis|liver transplant"
               r"|hepatic encephalopathy|hepatocellular|hepatic function abnormal|liver disease",
    "cardiac": r"cardiac failure|atrial fibrillation|myocardial|coronary|cardiomyopathy|angina"
               r"|arrhythmia|heart failure|atrial flutter",
    "diabetes": r"diabetes|diabetic",
    "hypertension": r"hypertension|blood pressure high",
}
COMORBIDITIES = list(COMORBIDITY_PATTERNS)

# Salt / hydrate words stripped when matching FAERS active-ingredient strings to drug names.
SALT_WORDS = frozenset("""
hydrochloride dihydrochloride hydrobromide sodium disodium potassium calcium magnesium sulfate sulphate
maleate tartrate bitartrate succinate besylate besilate mesylate mesilate fumarate hemifumarate citrate
phosphate acetate bromide chloride nitrate dihydrate monohydrate hemihydrate trihydrate anhydrous
hydrate dipropionate propionate furoate valerate medoxomil cilexetil axetil etexilate pivoxil tosylate
lactate gluconate pamoate embonate oxalate malate benzoate stearate decanoate palmitate hcl
bisulfate hydrogen xinafoate saccharate aspartate trifenatate hyclate monosodium dried mesilate
propanediol s-malate l-malate tromethamine meglumine erbumine dimesylate dimaleate esylate
""".split())

# International / alternative names -> the name used in TWOSIDES / drug_master
DRUG_SYNONYMS: dict[str, str] = {
    "valproic acid": "valproate", "divalproex": "valproate", "sodium valproate": "valproate",
    "paracetamol": "acetaminophen", "acetylsalicylic acid": "aspirin", "salbutamol": "albuterol",
    "frusemide": "furosemide", "glibenclamide": "glyburide", "rifampicin": "rifampin",
    "ciclosporin": "cyclosporine", "levothyroxine sodium": "levothyroxine", "adrenaline": "epinephrine",
    "noradrenaline": "norepinephrine", "lignocaine": "lidocaine", "pethidine": "meperidine",
    "chlorphenamine": "chlorpheniramine", "dexchlorphenamine": "dexchlorpheniramine", "aminophylline": "theophylline",
    "paracetamol (acetaminophen)": "acetaminophen",
}

# --------------------------------------------------------------------------- #
# Peptide / protein drugs ("bio:" keys). They have no small-molecule structure for the AI models, but FDA
# label text, the pair rules below, class duplication and drug-disease rules still apply.
#   ko = Korean ingredient name, brands = KR/US/EU product names, groups = rule groups,
#   label = FDA substance names (openFDA) of this ingredient, terms = words other labels use for it
# --------------------------------------------------------------------------- #
BIOLOGICS: dict[str, dict] = {
    "semaglutide": {"ko": "세마글루타이드", "brands": ["위고비", "오젬픽", "리벨서스", "wegovy", "ozempic", "rybelsus"],
                    "groups": {"glp1"}, "terms": ["semaglutide", "GLP-1 receptor agonist"]},
    "liraglutide": {"ko": "리라글루타이드", "brands": ["삭센다", "빅토자", "saxenda", "victoza"],
                    "groups": {"glp1"}, "terms": ["liraglutide", "GLP-1 receptor agonist"]},
    "tirzepatide": {"ko": "티르제파타이드", "brands": ["마운자로", "젭바운드", "mounjaro", "zepbound"],
                    "groups": {"glp1", "tirzepatide"}, "terms": ["tirzepatide"]},
    "dulaglutide": {"ko": "둘라글루타이드", "brands": ["트루리시티", "trulicity"], "groups": {"glp1"},
                    "terms": ["dulaglutide", "GLP-1 receptor agonist"]},
    "exenatide": {"ko": "엑세나타이드", "brands": ["바이에타", "바이듀리온", "byetta", "bydureon"], "groups": {"glp1"},
                  "terms": ["exenatide", "GLP-1 receptor agonist"]},
    "insulin glargine": {"ko": "인슐린글라진", "brands": ["란투스", "투제오", "베이사글라", "lantus", "toujeo", "basaglar"],
                         "groups": {"insulin"}, "terms": ["insulin"]},
    "insulin degludec": {"ko": "인슐린데글루덱", "brands": ["트레시바", "tresiba"], "groups": {"insulin"}, "terms": ["insulin"]},
    "insulin detemir": {"ko": "인슐린디터머", "brands": ["레버미어", "levemir"], "groups": {"insulin"}, "terms": ["insulin"]},
    "insulin aspart": {"ko": "인슐린아스파트", "brands": ["노보래피드", "피아스프", "novorapid", "novolog", "fiasp"],
                       "groups": {"insulin"}, "terms": ["insulin"]},
    "insulin lispro": {"ko": "인슐린리스프로", "brands": ["휴마로그", "륨젭", "humalog", "lyumjev", "admelog"],
                       "groups": {"insulin"}, "terms": ["insulin"]},
    "insulin human": {"ko": "인슐린", "brands": ["휴물린", "노보린", "humulin", "novolin"], "groups": {"insulin"},
                      "terms": ["insulin"]},
    "botulinum toxin": {"ko": "보툴리눔톡신",
                        "brands": ["보톡스", "디스포트", "제오민", "나보타", "보툴렉스", "메디톡신", "코어톡스", "이노톡스",
                                   "리즈톡스", "botox", "dysport", "xeomin", "jeuveau", "daxxify", "botulinum toxin type a"],
                        "label": ["onabotulinumtoxina", "abobotulinumtoxina", "incobotulinumtoxina",
                                  "prabotulinumtoxina", "daxibotulinumtoxina", "rimabotulinumtoxinb",
                                  "letibotulinumtoxina", "relabotulinumtoxina"],
                        "groups": {"botulinum"}, "terms": ["botulinum toxin", "botulinum toxins"]},
    "denosumab": {"ko": "데노수맙", "brands": ["프롤리아", "엑스지바", "prolia", "xgeva"], "groups": set(),
                  "terms": ["denosumab"]},
    "teriparatide": {"ko": "테리파라타이드", "brands": ["포스테오", "테리본", "forteo", "forsteo"], "groups": set(),
                     "terms": ["teriparatide"]},
    "adalimumab": {"ko": "아달리무맙", "brands": ["휴미라", "humira"], "groups": {"tnf_inhibitor"},
                   "terms": ["adalimumab", "TNF blocker"]},
    "etanercept": {"ko": "에타너셉트", "brands": ["엔브렐", "enbrel"], "groups": {"tnf_inhibitor"},
                   "terms": ["etanercept", "TNF blocker"]},
    "dupilumab": {"ko": "두필루맙", "brands": ["듀피젠트", "dupixent"], "groups": set(), "terms": ["dupilumab"]},
    "heparin": {"ko": "헤파린", "brands": [], "groups": set(), "terms": ["heparin"]},
    "enoxaparin": {"ko": "에녹사파린", "brands": ["크렉산", "lovenox"], "groups": set(),
                   "terms": ["enoxaparin", "low molecular weight heparin"]},
}
DRUG_SYNONYMS.setdefault("insulin", "insulin human")
BIOLOGIC_LABEL_NAMES = {s: k for k, v in BIOLOGICS.items() for s in v.get("label", [])}
for _k, _v in BIOLOGICS.items():
    KR_INGREDIENTS.setdefault(_v["ko"], _k)
    for _b in _v["brands"]:
        if _b.isascii():
            DRUG_SYNONYMS.setdefault(_b, _k)
        else:
            KR_BRANDS.setdefault(_b, [_k])
DRUG_CLASSES["glp1"] = ("GLP-1 계열 비만·당뇨 주사", frozenset(k for k, v in BIOLOGICS.items() if "glp1" in v["groups"]))
DRUG_CLASSES["anticoagulant"] = (DRUG_CLASSES["anticoagulant"][0],
                                 DRUG_CLASSES["anticoagulant"][1] | {"enoxaparin", "heparin"})
# opioids / benzodiazepines missing above, and the street ones (recreational.py)
DRUG_CLASSES["opioid"] = (DRUG_CLASSES["opioid"][0], DRUG_CLASSES["opioid"][1] | {
    "methadone", "buprenorphine", "meperidine", "heroin", "carfentanil", "isotonitazene"})
DRUG_CLASSES["sedative"] = (DRUG_CLASSES["sedative"][0], DRUG_CLASSES["sedative"][1] | {"flunitrazepam"})
DRUG_CLASSES["stimulant"] = ("중추신경 흥분제(암페타민·코카인 계열)", frozenset({
    "cocaine", "methamphetamine", "amphetamine", "dextroamphetamine", "lisdexamfetamine", "methylphenidate",
    "dexmethylphenidate", "phentermine", "phendimetrazine", "diethylpropion", "mazindol", "midomafetamine", "mda",
    "mephedrone", "cathinone", "alpha-pvp"}))
DRUG_GROUPS.update({
    # v2.18 (from the development half of the DUR external validation; each rule cites its label / regulator)
    "nitrate": NITRATES | frozenset({"molsidomine", "nitroprusside", "sodium nitroprusside"}),  # NO donors
    "anorectic": frozenset({"phentermine", "phendimetrazine", "diethylpropion", "mazindol", "sibutramine",
                            "benzphetamine"}),
    "retinoid_systemic": frozenset({"isotretinoin", "acitretin", "alitretinoin", "tretinoin"}),
    "ergot": frozenset({"ergotamine", "dihydroergotamine", "methylergonovine", "methylergometrine", "ergonovine",
                        "ergometrine"}),
    # strong CYP3A inhibitors + the macrolides the ergotamine boxed warning names (azithromycin is not one)
    "cyp3a_inh_ergot": CYP_FDA["CYP3A"]["strong_inh"] | frozenset({"erythromycin", "roxithromycin", "telithromycin",
                                                                  "troleandomycin"}),
    "cyp3a_strong_inh": CYP_FDA["CYP3A"]["strong_inh"],
    "domperidone": frozenset({"domperidone"}),
    "menatetrenone": frozenset({"menatetrenone"}),
    "calcineurin": frozenset({"cyclosporine", "tacrolimus", "voclosporin"}),
    "penicillin": frozenset({"amoxicillin", "ampicillin", "penicillin v", "phenoxymethylpenicillin", "penicillin g",
                             "benzylpenicillin", "dicloxacillin", "piperacillin", "nafcillin", "oxacillin"}),
    "gabapentinoid": frozenset({"gabapentin", "pregabalin", "mirogabalin"}),
    "dabigatran": frozenset({"dabigatran", "dabigatran etexilate"}),
    "metronidazole": frozenset({"metronidazole", "tinidazole", "secnidazole"}),
    "insulin": frozenset({"insulin glargine", "insulin degludec", "insulin detemir", "insulin aspart", "insulin lispro",
                          "insulin human"}),
    # grapefruit: avoid (labelled) / limit large amounts
    "grapefruit_major": frozenset({"simvastatin", "lovastatin", "felodipine", "nisoldipine", "lurasidone", "ivabradine",
                                   "dronedarone", "lomitapide", "ticagrelor", "triazolam", "midazolam"}),
    "grapefruit_moderate": frozenset({"atorvastatin", "nifedipine", "amlodipine", "cyclosporine", "tacrolimus",
                                      "sirolimus", "sildenafil", "tadalafil", "buspirone", "carbamazepine",
                                      "quetiapine", "colchicine", "apixaban", "rivaroxaban", "amiodarone"}),
    "smoking_cyp1a2": frozenset({"clozapine", "olanzapine", "theophylline", "duloxetine", "ropinirole", "tizanidine"}),
    "strong_pgp_inh": frozenset({"ketoconazole", "itraconazole", "dronedarone", "cyclosporine", "glecaprevir"}),
    "loop_diuretic": frozenset({"furosemide", "torsemide", "bumetanide", "ethacrynic acid"}),
    "digoxin": frozenset({"digoxin"}),
    "warfarin": frozenset({"warfarin"}),
    # direct oral anticoagulants: not vitamin K antagonists
    "doac": frozenset({"apixaban", "rivaroxaban", "dabigatran", "dabigatran etexilate", "edoxaban"}),
    "levothyroxine": frozenset({"levothyroxine", "liothyronine"}),
    "tetracycline": frozenset({"tetracycline", "doxycycline", "minocycline", "sarecycline", "omadacycline"}),
    "bisphosphonate": frozenset({"alendronate", "risedronate", "ibandronate", "etidronate"}),
    "ssri": frozenset({"sertraline", "fluoxetine", "paroxetine", "citalopram", "escitalopram", "fluvoxamine",
                       "venlafaxine", "duloxetine", "desvenlafaxine", "vortioxetine"}),
    "statin": frozenset({"atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "pitavastatin", "lovastatin",
                         "fluvastatin"}),
    "ace_arb": frozenset({"lisinopril", "enalapril", "ramipril", "captopril", "perindopril", "losartan", "valsartan",
                          "telmisartan", "candesartan", "irbesartan", "olmesartan", "fimasartan", "azilsartan",
                          "sacubitril"}),
    "k_sparing": frozenset({"spironolactone", "eplerenone", "amiloride", "triamterene", "finerenone"}),
    "sulfonylurea": frozenset({"glimepiride", "glipizide", "glyburide", "gliclazide", "tolbutamide"}),
    "oral_contraceptive": frozenset({"ethinyl estradiol", "levonorgestrel", "norethindrone", "drospirenone",
                                     "desogestrel", "norgestimate", "dienogest", "cyproterone", "norgestrel"}),
    "neuromuscular_blocker": frozenset({"rocuronium", "vecuronium", "succinylcholine", "cisatracurium",
                                        "tubocurarine"}),
    "immunosuppressant": frozenset({"methotrexate", "azathioprine", "cyclosporine", "tacrolimus", "tofacitinib",
                                    "baricitinib", "upadacitinib", "mycophenolate mofetil"}),
})
# antibacterials (probiotics are taken at least 2 hours apart from them)
DRUG_GROUPS["antibacterial"] = (DRUG_GROUPS["fluoroquinolone"] | DRUG_GROUPS["macrolide"] | DRUG_GROUPS["aminoglycoside"]
                                | DRUG_GROUPS["tetracycline"] | frozenset({
    "amoxicillin", "ampicillin", "penicillin v", "penicillin g", "clavulanic acid", "sultamicillin", "cefaclor",
    "cefadroxil", "cephalexin", "cefuroxime", "cefuroxime axetil", "cefixime", "cefdinir", "cefpodoxime",
    "cefditoren", "ceftriaxone", "cefazolin", "cefprozil", "clindamycin", "metronidazole", "sulfamethoxazole",
    "trimethoprim", "nitrofurantoin", "fosfomycin", "vancomycin", "linezolid", "rifaximin", "rifampin"}))
# recreational / controlled substances (recreational.py) and the medicines they combine dangerously with.
# Ingredient names: database names, or the curated keys of recreational.SUBSTANCES.
DRUG_GROUPS.update({
    "maoi": MAOI | {"harmine"},  # harmine: the MAO-A inhibitor of ayahuasca
    "serotonergic": SEROTONERGIC,
    "hiv_booster": frozenset({"ritonavir", "cobicistat"}),
    "street_stimulant": frozenset({"cocaine", "methamphetamine", "midomafetamine", "mda", "mephedrone", "cathinone",
                                   "alpha-pvp"}),
    "mdma_like": frozenset({"midomafetamine", "mda", "mephedrone"}),
    "psychedelic": frozenset({"lysergide", "psilocybin", "psilocin", "dimethyltryptamine", "5-meo-dmt", "mescaline",
                              "2c-b", "25i-nbome"}),
    "dissociative": frozenset({"ketamine", "phencyclidine", "methoxetamine"}),
    "ghb": frozenset({"sodium oxybate", "gamma-butyrolactone", "1,4-butanediol"}),
    "cannabinoid": frozenset({"dronabinol", "nabilone", "cannabidiol"}),
    "kratom": frozenset({"mitragynine"}),
    "gaba_depressant": frozenset({"phenibut", "muscimol"}),
    "harmala": frozenset({"harmine"}),
    "synthetic_cannabinoid": frozenset({"jwh-018"}),
})
# medicine classes named by Korean health functional food labels (stage "supplements")
DRUG_GROUPS.update({
    "antidiabetic": DRUG_GROUPS["insulin"] | DRUG_GROUPS["sulfonylurea"] | DRUG_GROUPS["metformin"]
    | DRUG_GROUPS["thiazolidinedione"] | frozenset({"sitagliptin", "linagliptin", "vildagliptin", "saxagliptin",
                                                    "gemigliptin", "teneligliptin", "evogliptin", "dapagliflozin",
                                                    "empagliflozin", "ertugliflozin", "semaglutide", "liraglutide",
                                                    "dulaglutide", "tirzepatide", "repaglinide", "nateglinide",
                                                    "acarbose", "voglibose"}),
    "antihypertensive": DRUG_GROUPS["ace_arb"] | DRUG_GROUPS["thiazide"] | DRUG_GROUPS["loop_diuretic"]
    | DRUG_GROUPS["nondhp_ccb"] | DRUG_GROUPS["k_sparing"] | DRUG_GROUPS["nonselective_beta_blocker"]
    | frozenset({"amlodipine", "nifedipine", "felodipine", "lercanidipine", "cilnidipine", "atenolol", "metoprolol",
                 "bisoprolol", "nebivolol", "doxazosin", "terazosin", "prazosin", "clonidine", "hydralazine"}),
    "antiarrhythmic": frozenset({"amiodarone", "dronedarone", "flecainide", "propafenone", "sotalol", "dofetilide",
                                 "digoxin", "pilsicainide"}),
})
DRUG_GROUPS["qt_prolonging"] = DRUG_GROUPS["qt_prolonging"] | {"ibogaine"}
DISEASES.update({
    "thyroid_cancer": {"ko": "갑상선수질암·MEN2", "kw": ("갑상선수질암", "수질암", "men2", "medullary thyroid"),
                       "flag": None},
    "pancreatitis": {"ko": "췌장염 병력", "kw": ("췌장염", "pancreatit"), "flag": None},
    "infection": {"ko": "활동성 감염·결핵", "kw": ("결핵", "활동성 감염", "패혈증", "tuberculosis", "sepsis"), "flag": None},
})
DRUG_DISEASE_RULES += [
    ("thyroid_cancer", "glp1", "금기", "갑상선 C세포 종양 위험이 있어 쓰지 않습니다 (FDA 박스경고)"),
    ("pancreatitis", "glp1", "주의", "췌장염이 다시 생길 수 있습니다"),
    ("pregnancy", "glp1", "금기", "임신 중에는 쓰지 않습니다 — 임신 계획 시 미리 중단 (허가사항)"),
    ("myasthenia", "botulinum", "금기", "근육 힘이 더 빠져 삼킴·호흡 곤란이 생길 수 있습니다 (허가사항)"),
    ("infection", "tnf_inhibitor", "금기", "결핵·중증 감염이 악화될 수 있습니다 (FDA 박스경고)"),
    ("heart_failure", "tnf_inhibitor", "주의", "심부전이 악화될 수 있습니다"),
    ("hypertension", "licorice", "주의", "감초(글리시리진)가 혈압을 올리고 칼륨을 떨어뜨립니다"),
    ("heart_failure", "licorice", "주의", "몸에 물이 차게 해 심부전을 악화시킬 수 있습니다"),
    ("renal", "potassium_supplement", "주의", "콩팥이 칼륨을 못 내보내 고칼륨혈증 위험이 있습니다"),
    ("bleeding", "bleeding_herb", "주의", "출혈 경향을 더할 수 있습니다"),
    # street stimulants (cocaine, methamphetamine, MDMA ...): coronary spasm, arrhythmia, seizures, placental harm
    ("cardiac", "street_stimulant", "금기", "관상동맥을 수축시켜 심근경색·돌연사 위험이 큽니다"),
    ("arrhythmia", "street_stimulant", "금기", "치명적인 부정맥을 일으킬 수 있습니다"),
    ("hypertension", "street_stimulant", "주의", "혈압을 급격히 올려 뇌출혈 위험이 있습니다"),
    ("epilepsy", "street_stimulant", "주의", "경련을 일으킬 수 있습니다"),
    ("pregnancy", "street_stimulant", "금기", "태반 혈류를 줄여 조산·태반 조기박리·태아 성장 지연 위험이 있습니다"),
    ("pregnancy", "cannabinoid", "주의", "태아 발달에 해로울 수 있어 임신 중에는 쓰지 않도록 권고됩니다"),
]
DISEASE_LABEL_TERMS.update({"thyroid_cancer": r"medullary thyroid", "pancreatitis": r"pancreatitis"})

# Pair rules that labels state by drug CLASS (so a name search cannot find them).
# (group a, group b, "높음"|"주의", plain-language reason)
PAIR_RULES: list[tuple[str, str, str, str]] = [
    ("qt_prolonging", "qt_prolonging", "주의", "두 약 모두 QT 간격을 늘려 돌연 부정맥(Torsade de Pointes) 위험이 더해집니다 — 심전도 확인"),
    ("opioid", "sedative", "높음", "함께 쓰면 깊은 진정·호흡 억제·혼수·사망 위험이 있습니다 (FDA 박스경고)"),
    ("opioid", "gabapentinoid", "주의", "호흡 억제 위험이 커집니다 (FDA 안전성 서한 2019)"),
    ("glp1", "insulin", "주의", "함께 쓰면 저혈당 위험이 커져 인슐린 감량이 필요할 수 있습니다 (허가사항)"),
    ("glp1", "sulfonylurea", "주의", "함께 쓰면 저혈당 위험이 커져 설포닐우레아 감량이 필요할 수 있습니다 (허가사항)"),
    ("insulin", "sulfonylurea", "주의", "둘 다 혈당을 낮춰 저혈당 위험이 커집니다"),
    ("tirzepatide", "oral_contraceptive", "주의", "위 배출이 늦어져 먹는 피임약 효과가 떨어질 수 있습니다 — 시작·증량 후 "
                                                "4주간 다른 피임법을 함께 쓰세요 (허가사항)"),
    ("glp1", "warfarin", "주의", "위 배출이 늦어져 흡수가 달라질 수 있어 INR을 더 자주 확인하세요"),
    ("botulinum", "aminoglycoside", "주의", "근육 마비 효과가 강해질 수 있습니다 (허가사항)"),
    # herbal / mineral interactions documented in labelling and standard references
    ("licorice", "thiazide", "주의", "감초가 칼륨을 더 떨어뜨려 저칼륨혈증·부정맥 위험이 커집니다"),
    ("licorice", "loop_diuretic", "주의", "감초가 칼륨을 더 떨어뜨려 저칼륨혈증·부정맥 위험이 커집니다"),
    ("licorice", "digoxin", "주의", "감초로 칼륨이 떨어지면 디곡신 독성이 커집니다"),
    ("dabigatran", "strong_pgp_inh", "높음", "강한 P-당단백 억제제가 다비가트란 농도를 크게 올려 출혈 위험이 큽니다 "
                                           "(유럽 허가사항 병용금기, 미국은 신기능 저하 시 회피)"),
    ("digoxin", "loop_diuretic", "주의", "이뇨제로 칼륨·마그네슘이 떨어지면 디곡신 독성(부정맥)이 생기기 쉽습니다 — 전해질 확인"),
    ("digoxin", "thiazide", "주의", "이뇨제로 칼륨·마그네슘이 떨어지면 디곡신 독성(부정맥)이 생기기 쉽습니다 — 전해질 확인"),
    ("fluoroquinolone", "corticosteroid", "주의", "힘줄염·힘줄 파열 위험이 커집니다 (퀴놀론 박스경고) — 특히 60세 이상"),
    # food, drink, smoking (labelling + standard references)
    ("alcohol", "metronidazole", "높음", "술과 함께 먹으면 구토·두근거림·홍조(디설피람 유사 반응)가 생깁니다 — 복용 중과 끝난 뒤 3일은 금주"),
    ("alcohol", "opioid", "높음", "술이 진정·호흡 억제를 크게 키웁니다 (FDA 박스경고) — 함께 마시지 마세요"),
    ("alcohol", "sedative", "높음", "술이 진정 작용을 크게 키워 호흡 억제·낙상·기억 상실 위험이 있습니다 — 함께 마시지 마세요"),
    ("alcohol", "gabapentinoid", "주의", "술이 졸음·어지러움·호흡 억제를 키웁니다"),
    ("alcohol", "sedating_antihistamine", "주의", "술이 졸음을 크게 키웁니다 — 운전·기계 조작 금지"),
    ("alcohol", "acetaminophen", "주의", "매일 3잔 이상 술을 마시면 아세트아미노펜으로 간이 손상될 위험이 커집니다"),
    ("alcohol", "nsaid", "주의", "술과 함께 먹으면 위장 출혈 위험이 커집니다"),
    ("alcohol", "metformin", "주의", "과음은 메트포르민의 젖산산증 위험을 키웁니다"),
    ("alcohol", "sulfonylurea", "주의", "술이 저혈당을 일으키거나 숨길 수 있습니다"),
    ("alcohol", "insulin", "주의", "술이 저혈당을 일으키거나 숨길 수 있습니다"),
    ("alcohol", "warfarin", "주의", "술이 와파린 효과(INR)를 흔들어 출혈·혈전 위험이 생깁니다 — 과음 금지"),
    # v2.18 - documented contraindications the label text could not show (Korean-only drugs, no FDA label)
    ("pde5", "nitrate", "높음", "질산염·NO 공여제(니트로글리세린·니코란딜 등)와 발기부전 치료제를 함께 쓰면 혈압이 급격히 떨어질 수 "
                              "있습니다 — 함께 쓰지 마세요 (PDE5 억제제 허가사항 병용금기)"),
    ("retinoid_systemic", "tetracycline", "높음", "먹는 레티노이드(이소트레티노인·아시트레틴 등)와 테트라사이클린계 항생제를 함께 "
                                                "쓰면 뇌압이 오르는 가성뇌종양 위험이 있습니다 — 함께 쓰지 마세요 (허가사항 병용금기)"),
    ("anorectic", "anorectic", "높음", "식욕억제제를 두 가지 이상 함께 쓰면 심장판막병·폐동맥고혈압 위험이 커집니다 — 함께 쓰지 "
                                    "마세요 (허가사항: 다른 체중 감량 약과 병용 금지)"),
    ("ergot", "cyp3a_inh_ergot", "높음", "맥각 알칼로이드(에르고타민 등)와 강한 CYP3A 억제제(마크롤리드·아졸·HIV 약 등)를 함께 "
                                       "쓰면 혈관 경련으로 손발·장기 허혈이 생길 수 있습니다 (에르고타민 박스경고·병용금기)"),
    ("domperidone", "qt_prolonging", "높음", "돔페리돈을 QT 간격을 늘리는 약과 함께 쓰면 심각한 부정맥(염전성 심실빈맥) 위험이 "
                                           "커집니다 — 함께 쓰지 마세요 (유럽 EMA 2014 권고·국내 허가사항 병용금기)"),
    ("domperidone", "cyp3a_strong_inh", "높음", "강한 CYP3A 억제제가 돔페리돈 농도를 크게 올려 심장 부정맥 위험이 커집니다 — 함께 "
                                              "쓰지 마세요 (EMA 2014 권고·국내 허가사항 병용금기)"),
    ("menatetrenone", "warfarin", "높음", "골다공증 치료제 메나테트레논(고용량 비타민K2)은 와파린 효과를 없앱니다 — 함께 쓰지 "
                                        "마세요 (메나테트레논 허가사항: 와파린 복용 환자 금기)"),
    ("calcineurin", "calcineurin", "높음", "사이클로스포린과 타크로리무스를 함께 쓰면 신장 독성이 겹칩니다 — 함께 쓰지 마세요 "
                                         "(허가사항: 병용 회피, 바꿀 때는 24시간 간격)"),
    ("iron_supplement", "tea_tannin", "주의", "차(녹차·홍차)의 탄닌이 철분과 결합해 흡수를 줄입니다 — 철분제는 차와 1~2시간 "
                                            "간격을 두세요 (철분제 허가사항)"),
    ("iron_supplement", "dairy", "주의", "우유·유제품의 칼슘이 철분 흡수를 줄입니다 — 철분제는 우유와 1~2시간 간격을 두세요 "
                                       "(철분제 허가사항)"),
    # warfarin label (drug interactions: acetaminophen); INR rises with regular use of more than ~2 g a day (Hylek 1998)
    ("acetaminophen", "warfarin", "주의", "아세트아미노펜을 며칠 이상 매일 많이(하루 2g 이상) 먹으면 INR이 올라 출혈 위험이 생길 수 "
                                       "있습니다 — 가끔 먹는 것은 괜찮습니다"),
    ("grapefruit", "grapefruit_major", "높음", "자몽(주스)이 약의 혈중 농도를 크게 올립니다 — 복용 중 자몽을 피하세요"),
    ("grapefruit", "grapefruit_moderate", "주의", "자몽(주스)이 약의 혈중 농도를 올릴 수 있습니다 — 많이 마시지 마세요"),
    ("dairy", "tetracycline", "주의", "우유·유제품이 항생제 흡수를 막습니다 — 2~3시간 간격을 두세요"),
    ("dairy", "fluoroquinolone", "주의", "우유·유제품이 항생제 흡수를 막습니다 — 2시간 이상 간격을 두세요"),
    ("vitamin_k_food", "warfarin", "주의", "비타민K가 많은 음식은 와파린 효과를 줄입니다 — 끊지 말고 매일 비슷한 양을 드세요"),
    ("vitamin_k_supplement", "warfarin", "주의", "비타민K 영양제는 와파린 효과(INR)를 떨어뜨립니다 — 시작하거나 끊기 전에 의사와 "
                                               "상의하고 INR을 확인하세요"),
    ("high_potassium_food", "k_sparing", "주의", "칼륨이 많은 음식을 많이 먹으면 칼륨이 지나치게 올라갈 수 있습니다(고칼륨혈증) — "
                                              "특히 콩팥 기능이 떨어졌을 때"),
    ("high_potassium_food", "ace_arb", "주의", "칼륨이 많은 음식을 많이 먹으면 칼륨이 지나치게 올라갈 수 있습니다(고칼륨혈증) — "
                                            "특히 콩팥 기능이 떨어졌을 때"),
    ("smoking", "oral_contraceptive", "주의", "흡연자(특히 35세 이상)는 피임약으로 혈전·심혈관 사고 위험이 큽니다 (박스경고)"),
    ("smoking", "smoking_cyp1a2", "주의", "흡연이 약의 분해를 빠르게 해 효과가 떨어질 수 있습니다 — 금연·흡연 시작 시 용량 조절 필요"),
    ("anticoagulant", "nsaid", "주의", "출혈(특히 위장관 출혈) 위험이 커집니다 — 검은 변·멍·잇몸 출혈을 살피세요"),
    ("anticoagulant", "ssri", "주의", "SSRI·SNRI는 혈소판 기능을 떨어뜨려 출혈 위험을 더합니다 — 출혈 징후를 살피세요"),
    ("nsaid", "ssri", "주의", "위장관 출혈 위험이 커집니다 — 속쓰림·검은 변이 생기면 상담하세요"),
    ("bleeding_herb", "anticoagulant", "주의", "은행잎·마늘·강황 같은 성분은 혈액 응고를 늦춰 출혈 위험을 더할 수 있습니다"),
    ("bleeding_herb", "nsaid", "주의", "은행잎·마늘·강황 같은 성분은 혈액 응고를 늦춰 출혈 위험을 더할 수 있습니다"),
    ("ginseng", "warfarin", "주의", "인삼이 와파린 효과(INR)를 떨어뜨릴 수 있습니다"),
    # every diabetes medicine: the MFDS standard caution of 홍삼 products ('의약품(당뇨치료제, 혈액항응고제) 복용 시
    # 섭취에 주의' - 4,012 of 4,313 ginseng products in 건강기능식품정보)
    ("ginseng", "antidiabetic", "주의", "인삼·홍삼이 혈당을 더 낮출 수 있습니다 — 저혈당 증상을 살피세요(식약처 홍삼 제품 "
                                     "표시: '당뇨치료제 복용 시 섭취에 주의')"),
    # common supplements (product labelling and standard interaction references); never above '주의'
    ("omega3", "anticoagulant", "주의", "오메가3를 많이(하루 3g 이상) 먹으면 출혈 경향이 커질 수 있습니다 — 멍·잇몸 출혈을 살피세요"),
    ("coq10", "warfarin", "주의", "코엔자임Q10이 와파린 효과(INR)를 떨어뜨릴 수 있습니다 — 시작하거나 끊을 때 INR을 확인하세요"),
    ("cranberry", "warfarin", "주의", "크랜베리 주스·농축 제품을 많이 먹으면 INR이 올라 출혈 위험이 생길 수 있습니다"),
    ("probiotic", "antibacterial", "주의", "항생제가 유산균을 죽여 효과가 줄어듭니다 — 항생제와 2시간 이상 간격을 두고 드세요"),
    ("st_johns_wort", "immunosuppressant", "높음", "세인트존스워트가 면역억제제 농도를 크게 낮춰 거부반응 위험이 있습니다"),
    ("st_johns_wort", "oral_contraceptive", "주의", "세인트존스워트가 피임약 효과를 떨어뜨립니다"),
    ("st_johns_wort", "anticoagulant", "주의", "세인트존스워트가 항응고 효과를 떨어뜨립니다"),
    ("st_johns_wort", "ssri", "주의", "세로토닌 증후군 위험이 있습니다"),
    ("statin", "statin", "주의", "스타틴 성분이 겹쳐 근육 손상 위험이 커집니다"),
    ("polyvalent_cation", "fluoroquinolone", "주의", "칼슘·철·마그네슘 등이 항생제 흡수를 막습니다 — 2시간 이상 간격을 두세요"),
    ("polyvalent_cation", "tetracycline", "주의", "칼슘·철·마그네슘 등이 항생제 흡수를 막습니다 — 2~3시간 간격을 두세요"),
    ("polyvalent_cation", "levothyroxine", "주의", "칼슘·철 등이 갑상선 호르몬 흡수를 막습니다 — 4시간 간격을 두세요"),
    ("polyvalent_cation", "bisphosphonate", "주의", "골다공증약 흡수를 막습니다 — 골다공증약을 먼저, 30분 이상 뒤에 드세요"),
    ("potassium_supplement", "ace_arb", "주의", "칼륨이 지나치게 올라갈 수 있습니다(고칼륨혈증)"),
    ("potassium_supplement", "k_sparing", "높음", "칼륨이 위험하게 올라갈 수 있습니다(고칼륨혈증)"),
    ("botulinum", "neuromuscular_blocker", "주의", "근육 마비 효과가 강해질 수 있습니다 (허가사항)"),
    ("tnf_inhibitor", "immunosuppressant", "주의", "면역 억제가 겹쳐 감염 위험이 커집니다"),
    # recreational / controlled substances (recreational.py): combinations documented in labelling (oxybate, PDE5
    # inhibitor, amphetamine labels), poison-centre / overdose-death reports and the harm-reduction literature
    ("ghb", "alcohol", "높음", "GHB(물뽕)와 술을 함께 쓰면 의식 소실·호흡 정지로 사망할 수 있습니다 (옥시베이트 허가사항 병용금기)"),
    ("ghb", "sedative", "높음", "GHB와 수면·진정제를 함께 쓰면 호흡 억제·혼수 위험이 큽니다 (옥시베이트 허가사항 병용금기)"),
    ("ghb", "opioid", "높음", "GHB와 오피오이드를 함께 쓰면 호흡 억제·혼수·사망 위험이 큽니다 (옥시베이트 박스경고)"),
    ("ghb", "dissociative", "높음", "GHB와 케타민 계열을 함께 쓰면 의식을 잃은 채 토해 질식하거나 호흡이 억제될 수 있습니다"),
    ("ghb", "gabapentinoid", "주의", "진정·호흡 억제가 커집니다"),
    ("dissociative", "alcohol", "높음", "케타민 계열과 술을 함께 쓰면 의식이 떨어진 채 토해 질식(흡인)하거나 호흡이 억제될 수 있습니다"),
    ("dissociative", "opioid", "주의", "케타민 계열과 오피오이드를 함께 쓰면 호흡 억제가 커집니다 (허가사항)"),
    ("dissociative", "sedative", "주의", "케타민 계열과 수면·진정제를 함께 쓰면 진정·호흡 억제가 커집니다 (허가사항)"),
    ("cocaine", "alcohol", "높음", "코카인과 술을 함께 쓰면 몸속에서 코카에틸렌이 생겨 심장 독성·돌연사 위험이 커집니다"),
    ("cocaine", "opioid", "높음", "코카인+오피오이드(스피드볼)는 흥분 효과가 먼저 끝나면 호흡 억제가 남아 과다복용 사망이 많습니다"),
    ("street_stimulant", "opioid", "주의", "흥분제가 오피오이드의 진정을 가려 더 쓰게 만들고, 흥분 효과가 끝나면 호흡 억제가 남습니다"),
    ("street_stimulant", "alcohol", "주의", "흥분제가 술 취한 느낌을 가려 과음하게 되고 탈수·체온 상승·심장 부담이 커집니다"),
    ("stimulant", "maoi", "높음", "흥분제와 MAO 억제제를 함께 쓰면 고혈압 위기·고열로 위험합니다 (허가사항 병용금기 — 끊은 뒤 14일 간격)"),
    ("mdma_like", "maoi", "높음", "MDMA 계열과 MAO 억제제를 함께 쓰면 세로토닌 증후군·고혈압 위기로 사망한 사례가 있습니다"),
    ("mdma_like", "hiv_booster", "높음", "리토나비르·코비시스타트가 MDMA 분해를 막아 농도가 크게 올라 사망한 사례가 보고되었습니다"),
    ("mdma_like", "serotonergic", "주의", "세로토닌 증후군(고열·근육 경직·혼돈) 위험이 있습니다 — SSRI 계열은 MDMA 효과를 줄여 "
                                          "더 많이 쓰게 만들 수 있습니다"),
    ("psychedelic", "lithium", "높음", "LSD·환각버섯 계열과 리튬을 함께 쓰면 경련(발작)·의식 저하가 여러 차례 보고되었습니다"),
    ("psychedelic", "maoi", "주의", "MAO 억제제가 환각제(특히 DMT 계열)의 작용을 크게 키워 세로토닌 증후군·혈압 상승 위험이 있습니다"),
    ("harmala", "serotonergic", "높음", "아야와스카의 하르민은 MAO 억제제라 항우울제(SSRI) 등 세로토닌 약과 함께 쓰면 세로토닌 증후군 "
                                       "위험이 큽니다 (MAO 억제제 병용금기와 같은 이유)"),
    ("synthetic_cannabinoid", "alcohol", "주의", "술과 함께 쓰면 구토·의식 저하·판단력 저하가 커집니다"),
    ("psychedelic", "cannabinoid", "주의", "대마가 환각 작용을 예측할 수 없게 키워 공황·정신병적 반응이 생길 수 있습니다"),
    ("cannabinoid", "warfarin", "주의", "대마 성분(THC·CBD)이 와파린 분해를 늦춰 INR이 오르고 출혈 위험이 생길 수 있습니다"),
    ("cannabinoid", "sedative", "주의", "졸음·운동 능력 저하가 커집니다 — 운전하지 마세요"),
    ("cannabinoid", "alcohol", "주의", "술과 함께 쓰면 어지러움·구토·판단력 저하가 커집니다 — 운전하지 마세요"),
    ("nitrite", "pde5", "높음", "파퍼스(아질산 알킬)와 발기부전 치료제를 함께 쓰면 혈압이 급격히 떨어져 실신·심근경색·사망 위험이 "
                              "있습니다 (허가사항 병용금기)"),
    ("kratom", "opioid", "주의", "크라톰도 오피오이드 수용체에 작용해 호흡 억제가 더해집니다 — 사망 사례 대부분이 다른 약물과 "
                                 "함께 쓴 경우입니다"),
    ("kratom", "sedative", "주의", "진정·호흡 억제가 커집니다"),
    ("kratom", "alcohol", "주의", "진정·호흡 억제가 커집니다"),
    ("xylazine", "opioid", "높음", "자일라진(트랑크)이 오피오이드의 진정·호흡 억제를 키우며 날록손으로 풀리지 않습니다 (미국 FDA 경고)"),
    ("gaba_depressant", "alcohol", "주의", "진정·호흡 억제가 커집니다"),
    ("gaba_depressant", "sedative", "주의", "진정·호흡 억제가 커집니다"),
    ("gaba_depressant", "opioid", "주의", "진정·호흡 억제가 커집니다"),
]

# Plain-language explanation of a rule for people who are not clinicians (key = the rule's two groups as in PAIR_RULES):
#   short  one-line takeaway (the result headline when this is the only documented interaction)
#   why    how the interaction works and what goes wrong in BOTH directions
#   do     what to do (replaces the generic advice) - never 'stop the medicine' for a drug that must not be stopped
#   signs  when to see a doctor at once (shown once, after the actions of every explained pair)
_WARFARIN_SIGNS = ("이럴 때는 바로 진료를 받으세요: 출혈 신호(쉽게 드는 멍, 잇몸·코 출혈, 검은 변·붉은 소변) 또는 혈전 신호(한쪽 "
                   "다리가 붓고 아픔, 갑작스러운 숨참·가슴 통증, 한쪽 팔다리 마비·말이 어눌해짐) — 와파린은 임의로 끊지 마세요.")
RULE_DETAIL: dict[tuple[str, str], dict] = {
    ("vitamin_k_food", "warfarin"): {
        "short": "함께 먹어도 되지만, 비타민K가 많은 음식은 매일 비슷한 양을 꾸준히 드세요 — 갑자기 늘리거나 줄이는 것이 위험합니다.",
        "why": "와파린은 피를 굳게 하는 응고 인자를 만들 때 필요한 비타민K의 작용을 막아 피가 잘 굳지 않게 하는 약입니다. "
               "비타민K를 평소보다 갑자기 많이 먹으면 와파린 효과가 약해져 혈전(뇌졸중·폐색전증 등) 위험이 커지고, 반대로 갑자기 "
               "크게 줄이면 효과가 너무 강해져 출혈 위험이 커집니다. 피할 필요는 없고, 평소 먹는 양을 일정하게 유지하는 것이 핵심입니다",
        "do": ["비타민K가 많은 음식(시금치·케일·상추·브로콜리·청국장·녹즙 등)을 끊지 말고 매일 비슷한 양을 드세요.",
               "식단을 크게 바꾸거나(다이어트·채식 시작, 입원·여행) 녹즙·비타민K 영양제를 시작하거나 끊을 때는 의사에게 알리고 "
               "INR 검사를 받으세요."],
        "signs": _WARFARIN_SIGNS},
    ("vitamin_k_supplement", "warfarin"): {
        "short": "비타민K 영양제는 와파린 효과를 바꿉니다 — 시작하거나 끊기 전에 의사와 상의하고, 먹는다면 매일 같은 양을 드세요.",
        "why": "와파린은 비타민K의 작용을 막아 피가 잘 굳지 않게 하는 약입니다. 비타민K(K1·K2) 영양제를 새로 먹기 시작하면 와파린 "
               "효과가 약해져 혈전(뇌졸중·폐색전증 등) 위험이 커지고, 먹던 영양제를 갑자기 끊으면 효과가 너무 강해져 출혈 위험이 "
               "커집니다(의사가 INR이 너무 높을 때 비타민K를 처방하는 것은 이 작용을 이용한 치료입니다)",
        "do": ["비타민K가 든 영양제(비타민K2·종합비타민 포함)를 시작하거나 끊기 전에 의사와 상의하세요 — 먹는다면 매일 같은 양을 드세요.",
               "영양제를 시작하거나 끊은 뒤에는 INR 검사를 평소보다 자주 받으세요."],
        "signs": _WARFARIN_SIGNS},
    ("acetaminophen", "warfarin"): {
        "short": "가끔 먹는 것은 괜찮습니다 — 며칠 이상 매일 먹어야 한다면 의사에게 알리고 INR을 확인하세요.",
        "why": "아세트아미노펜(타이레놀)은 와파린을 먹는 사람에게 소염진통제보다 안전한 진통제로 권장됩니다. 다만 며칠 이상 매일 "
               "많이(하루 2g 이상) 먹으면 와파린 효과가 세져 INR이 오르고 출혈 위험이 커질 수 있습니다",
        "do": ["필요할 때 가끔 먹는 것은 괜찮습니다 — 하루 최대량을 넘기지 말고, 감기약 등 다른 제품의 아세트아미노펜도 합쳐서 세세요.",
               "며칠 이상 매일 먹어야 한다면 의사에게 알리고 INR 검사를 받으세요."],
        "signs": _WARFARIN_SIGNS},
    ("alcohol", "warfarin"): {
        "short": "마신다면 적게, 매일 비슷하게 — 폭음은 와파린 효과를 크게 흔듭니다.",
        "why": "한 번에 많이 마시면 와파린 분해가 늦어져 효과가 세지고 출혈 위험이 커지며, 오랫동안 많이 마시면 반대로 효과가 약해질 "
               "수 있습니다. 술 자체도 위 출혈과 넘어짐(머리 출혈) 위험을 키웁니다",
        "do": ["마신다면 적게(하루 1~2잔 이내) 마시고 폭음은 피하세요.",
               "음주 습관이 크게 바뀌면 의사에게 알리고 INR 검사를 받으세요."],
        "signs": _WARFARIN_SIGNS},
    ("anticoagulant", "nsaid"): {
        "short": "출혈 위험이 커지는 조합입니다 — 스스로 사 먹는 소염진통제는 피하고, 처방받은 약은 임의로 끊지 마세요.",
        "why": "항응고·항혈소판제는 피가 굳는 것을 막고, 소염진통제(이부프로펜·아스피린 등)는 혈소판 기능을 떨어뜨리며 위 점막을 "
               "상하게 합니다. 함께 쓰면 두 작용이 겹쳐 출혈, 특히 위장관 출혈 위험이 커집니다",
        "do": ["스스로 사 먹는 소염진통제(이부프로펜·나프록센 등)는 피하고, 진통제가 필요하면 의사·약사와 상의해 아세트아미노펜 같은 "
               "약을 쓰세요.",
               "처방받은 아스피린·소염진통제라면 임의로 끊지 말고, 함께 쓰는 것이 의도된 처방인지 의사에게 확인하세요."],
        "signs": "출혈 신호(검은 변, 피 섞인 구토, 쉽게 드는 멍, 잇몸·코 출혈)가 있으면 바로 진료를 받으세요."},
}

# Pairs that people often ASK about but that do not interact (documented): shown as information when nothing else is
# found - 'vitamin K + apixaban' gets an answer instead of silence
PAIR_NOTES: list[tuple[str, str, str]] = [
    *((vk, "doac", "비타민K는 와파린과 달리 아픽사반·리바록사반·다비가트란·에독사반(새 항응고제)의 효과에는 영향을 주지 "
                   "않습니다 — 이 약을 먹을 때는 비타민K 음식을 따로 조절하지 않아도 됩니다")
      for vk in ("vitamin_k_food", "vitamin_k_supplement")),
    ("dairy", "penicillin", "우유·유제품은 아목시실린 같은 페니실린계 항생제의 흡수에 영향을 주지 않습니다 — 음식과 관계없이 "
                            "먹어도 됩니다 (허가사항; 테트라사이클린·퀴놀론계와 다름)"),
]


# Korean OTC labels (식약처 허가사항, stage "kr-labels"): '이 약을 복용하는 동안 다음의 약을 복용하지 말 것' items ->
# (pattern, groups or ingredient names the other medicine must have, level). A label word names a CLASS loosely
# ('진정제', '다른 감기약'), so these are '주의' - except MAO inhibitors, which every source treats as contraindicated.
# Topical items ('외용 코르티코이드', '바셀린') and explanations are not matched.
KR_AVOID_TERMS: list[tuple[re.Pattern, frozenset[str], str]] = [
    (re.compile(r"MAO\s*억제"), frozenset({"maoi"}), "높음"),
    (re.compile(r"감기약"), frozenset({"decongestant", "antitussive", "sedating_antihistamine"}), "주의"),
    (re.compile(r"해열\s*진통제|비스테로이드성\s*소염진통제"), frozenset({"nsaid", "acetaminophen"}), "주의"),
    (re.compile(r"진정제|수면제|안정제|중추신경\s*억제"), frozenset({"sedative", "gabapentinoid"}), "주의"),
    (re.compile(r"항히스타민|멀미약|알레르기용약"), frozenset({"sedating_antihistamine", "antihistamine2"}), "주의"),
    (re.compile(r"진해|거담|기침"), frozenset({"antitussive"}), "주의"),
    (re.compile(r"테트라사이클린"), frozenset({"tetracycline"}), "주의"),
    (re.compile(r"제산제|칼슘\s*(염|함유)|마그네슘\s*함유"), frozenset({"polyvalent_cation"}), "주의"),
    (re.compile(r"교감신경|충혈\s*제거|암페타민|식욕\s*억제"), frozenset({"decongestant", "stimulant"}), "주의"),
    (re.compile(r"항정신병"), frozenset({"dopamine_antagonist"}), "주의"),
    (re.compile(r"삼환계\s*항우울"), frozenset({"amitriptyline", "imipramine", "nortriptyline", "clomipramine",
                                           "desipramine", "doxepin"}), "주의"),
    (re.compile(r"진경제"), frozenset({"anticholinergic"}), "주의"),
    (re.compile(r"치아지드"), frozenset({"thiazide"}), "주의"),
    (re.compile(r"알코올|음주|(^|[\s(])술($|[\s),])"), frozenset({"alcohol"}), "주의"),  # not 수술 / 기술
    (re.compile(r"레보도파"), frozenset({"levodopa"}), "주의"),
    (re.compile(r"아스피린"), frozenset({"aspirin"}), "주의"),
    (re.compile(r"메토트렉세이트"), frozenset({"methotrexate"}), "주의"),
    (re.compile(r"시클로스포린|사이클로스포린"), frozenset({"cyclosporine"}), "주의"),
    (re.compile(r"타크로리무스"), frozenset({"tacrolimus"}), "주의"),
    (re.compile(r"메칠도파|메틸도파"), frozenset({"methyldopa"}), "주의"),
    (re.compile(r"(?<!외용 )코르티코(스테로이드|이드)"), frozenset({"corticosteroid"}), "주의"),
    # classes the health functional food labels name ('의약품(당뇨치료제, 혈액항응고제) 복용 시 섭취에 주의')
    (re.compile(r"항응고|혈액\s*응고|응고\s*방지|항혈소판|항혈전"), frozenset({"anticoagulant"}), "주의"),
    (re.compile(r"당[뇨료]|혈당\s*강하|항당뇨"), frozenset({"antidiabetic"}), "주의"),  # '당료치료제': a common misspelling
    (re.compile(r"혈압\s*강[하화]|고혈압|혈압약"), frozenset({"antihypertensive"}), "주의"),
    (re.compile(r"면역\s*억제"), frozenset({"immunosuppressant"}), "주의"),
    (re.compile(r"부정맥"), frozenset({"antiarrhythmic"}), "주의"),
    (re.compile(r"고지혈증|콜레스테롤"), frozenset({"statin"}), "주의"),
    (re.compile(r"갑상선\s*호르몬"), frozenset({"levothyroxine"}), "주의"),
    (re.compile(r"항우울"), frozenset({"ssri", "serotonergic"}), "주의"),
    (re.compile(r"수면\s*유도"), frozenset({"sedative"}), "주의"),
]
_KR_AVOID_SKIP = re.compile(r"외용|바셀린|레티노이드|디트라놀|^이 약은|필요하지 않다")

# Indication bias: an event that is what the drug is GIVEN FOR is reported with it because of the disease, not caused by
# it (cetirizine -> angioedema, ursodeoxycholic acid -> liver failure, warfarin -> stroke / pulmonary embolism).
# event (modelled MedDRA term) -> pattern over the drug's ChEMBL indications (MeSH / EFO terms, stage "chembl-dump").
# Events that are a drug's own pharmacology (INR rise on warfarin, hypoglycaemia on antidiabetics, bradycardia on
# beta-blockers, somnolence on hypnotics) are deliberately absent: those ARE the drug's risk.
_LIVER = r"liver|hepat|cirrho|cholang|cholesta|biliar|gallstone|cholelith"
_KIDNEY = r"kidney|renal|nephr"
EVENT_INDICATION: dict[str, re.Pattern] = {k: re.compile(v) for k, v in {
    "Angioedema": r"angioedema|urticaria|hypersensitiv|allerg|anaphyla|pruritus", "Rash": r"urticaria|dermatitis|eczema|"
    r"psoriasis|pruritus|allerg", "Hepatic failure": _LIVER, "Alanine aminotransferase increased": _LIVER,
    "Aspartate aminotransferase increased": _LIVER, "Hepatic enzyme increased": _LIVER, "Jaundice": _LIVER,
    "Liver function test abnormal": _LIVER, "Convulsion": r"epilep|seizure|convuls", "Tremor": r"tremor|parkinson",
    "Atrial fibrillation": r"atrial fibrillation|atrial flutter|arrhythm", "Arrhythmia": r"arrhythm|atrial fibrillation|"
    r"tachycardia", "Tachycardia": r"tachycardia|arrhythm", "Cardiac failure congestive": r"heart failure",
    "Cerebrovascular accident": r"stroke|cerebrovascular|cerebral infarct|brain ischemia",
    "Pulmonary embolism": r"pulmonary embol|venous thrombo|thromboembol|thrombosis",
    "Myocardial infarction": r"myocardial infarction|coronary|angina", "Hypertension": r"hypertension",
    "Hypotension": r"hypotension|shock", "Anaemia": r"anemia|anaemia", "Neutropenia": r"neutropenia",
    "Thrombocytopenia": r"thrombocytopeni|purpura", "Renal failure": _KIDNEY, "Renal failure acute": _KIDNEY,
    "Renal impairment": _KIDNEY, "Blood creatinine increased": _KIDNEY,
    "Pneumonia": r"pneumonia|respiratory tract infection|lung infection|bacterial infection",
    "Dyspnoea": r"asthma|pulmonary disease|copd|bronch|dyspnea", "Respiratory failure": r"asthma|pulmonary disease|copd",
    "Nausea": r"nausea|vomiting|emesis", "Vomiting": r"nausea|vomiting|emesis", "Diarrhoea": r"diarrh",
    "Constipation": r"constipation", "Abdominal pain": r"abdominal pain|irritable bowel|peptic|gastritis|dyspepsia",
    "Headache": r"headache|migraine", "Dizziness": r"vertigo|dizziness|motion sickness",
    "Confusional state": r"delirium|dementia|confusion", "Syncope": r"syncope",
    "Gastrointestinal haemorrhage": r"hemorrhage|haemorrhage|bleeding", "Haemorrhage": r"hemorrhage|haemorrhage|bleeding",
    "Pancreatitis": r"pancreat",
}.items()}

# where a mechanism / rule entry comes from (engine pair rows' "mechanism"[].kind -> source shown and cited)
MECH_SOURCE = {"규칙": "임상 규칙", "CYP-FDA": "FDA CYP 지표표", "식약처허가": "식약처 허가사항(제품 설명서)",
               "식약처표시": "식약처 건강기능식품정보(제품 표시)"}
KR_LABEL_KINDS = ("식약처허가", "식약처표시")  # a Korean product's own text (medicine label / health food label)


def mech_source(kind: str) -> str:
    return MECH_SOURCE.get(kind, "약리 기전(ChEMBL)")


def kr_avoid_targets(item: str) -> tuple[frozenset[str], str] | None:
    """One item of a Korean label's 'do not take with' list -> (groups / ingredient names, level), or None.
    List items are short names ('진정제', '경구용 테트라사이클린계 제제'); a long sentence is an explanation that slipped
    in from the next section ('…제산제와의 상호작용을 나타낸 연구는 없었다') and is not used."""
    core = re.sub(r"\(.*?\)|\[.*?\]", "", item).strip()
    if _KR_AVOID_SKIP.search(item) or len(core) > 20 or re.search(r"없었|없다|않는다|않았|연구|필요하지", item):
        return None
    hits = [(g, lv) for rx, g, lv in KR_AVOID_TERMS if rx.search(item)]
    if not hits:
        return None
    return frozenset().union(*(g for g, _ in hits)), ("높음" if any(lv == "높음" for _, lv in hits) else "주의")


# --------------------------------------------------------------------------- #
# Ingredients without a small-molecule structure model ("lab:" keys): herbal extracts, vitamins and minerals,
# antibodies and other biologics found in product databases or FDA labels. They are matched by name patterns
# to rule groups so their well-documented interactions are still caught.
# --------------------------------------------------------------------------- #
HERB_RE = re.compile(r"root|rhizome|leaf|folium|\bherb|herba\b|bark|fruit|seed|flower|tuber|peel|extract|ginseng|"
                     r"licorice|liquorice|glycyrrhiz|jujube|ginger|poria|ephedra|cinnamon|cnidium|angelica|astragal|"
                     r"atractylod|platycod|schisandra|pueraria|bupleur|scutellaria|rehmannia|massa medicata|"
                     r"ramulus|spike|ssanghwa|tang\b|hwan\b|ginkgo|hypericum|valerian|silymarin|milk thistle|"
                     r"zea mays|magnolia|artemisia|propolis|cordyceps|antler|placenta|garlic|turmeric|palmetto|"
                     r"garcinia|cranberr|green tea", re.I)
VITMIN_RE = re.compile(r"vitamin|tocopher|calciferol|retinol|cobalamin|thiamine|fursultiamine|benfotiamine|riboflavin|"
                       r"pyridox|nicotinamide|niacin|folic|biotin|pantothen|ascorb|calcium|magnesium|\bzinc|\biron\b|"
                       r"ferr|selenium|copper|manganese|chromium|iodine|potassium|taurine|inositol|carnitine|lysine|"
                       r"arginine|glutamine|cysteine|omega|fish oil|lutein|coenzyme|ubidecarenone|glucosamine|"
                       r"chondroitin|hyaluron|collagen|glutathione|"
                       r"비타민|토코페롤|칼시페롤|레티놀|코발라민|티아민|푸르설티아민|벤포티아민|리보플라빈|피리독신|니코틴산|"
                       r"엽산|비오틴|판토텐|아스코르브|칼슘|마그네슘|아연|철|셀레늄|구리|망간|크롬|요오드|칼륨|타우린|이노시톨|"
                       r"카르니틴|리신|아르기닌|글루타민|시스테인|오메가|루테인|코엔자임|글루코사민|콘드로이틴|히알루론|콜라겐|"
                       r"글루타치온", re.I)
BIO_RE = re.compile(r"mab\b|cept\b|kinra\b|toxin|insulin|interferon|epoetin|filgrastim|somatropin|peg|"
                    r"glutide|tide\b|ase\b|globulin|vaccine|factor\b|heparin|parin\b|albumin", re.I)


_LAB_DROP = re.compile(r"\(.*?\)|\d+(\.\d+)?\s*(%|％)|\b(powder|granules?|dried|soft|concentrate|dilute|purified|"
                       r"precipitated|hydrate|monohydrate|dihydrate|anhydrous|usp|bp|ep|jp)\b|\d+(\.\d+)?\s*→\s*\d+",
                       re.I)


def lab_name(raw: str) -> str | None:
    """Normalised name of an ingredient without a structure model (shared by product DBs and the label index)."""
    import unicodedata

    s = " ".join(_LAB_DROP.sub(" ", unicodedata.normalize("NFKC", raw or "")).lower().replace(",", " ").split())
    return s if len(s) >= 2 and not s.isdigit() else None


def structureless_category(name: str) -> str:
    n = name.removeprefix("lab:").removeprefix("bio:").removeprefix("food:")
    if name.startswith("food:") or n in FOODS:
        return "음식·생활"
    if n in BIOLOGICS or BIO_RE.search(n):
        return "생물의약품"
    if VITMIN_RE.search(n):
        return "비타민·미네랄·영양"
    if HERB_RE.search(n):
        return "생약"
    return "기타"


# name pattern -> rule groups (applied to every ingredient name, molecules included)
NAME_GROUPS: list[tuple[re.Pattern, set[str]]] = [
    (re.compile(r"ephedra|마황", re.I), {"decongestant", "ephedra"}),
    (re.compile(r"glycyrrhiz|licorice|liquorice|감초", re.I), {"licorice"}),
    (re.compile(r"ginkgo|garlic|allium sativum|turmeric|curcum", re.I), {"bleeding_herb"}),
    (re.compile(r"omega|fish oil|krill|icosapent|eicosapentaen|docosahexaen", re.I), {"omega3"}),
    (re.compile(r"ubidecarenone|coenzyme q", re.I), {"coq10"}),
    (re.compile(r"cranberr|vaccinium macrocarpon", re.I), {"cranberry"}),
    (re.compile(r"probiotic|lactobacill|bifidobacter|streptococcus faec|enterococcus faec|bacillus subtilis|"
                r"bacillus coagulans|clostridium butyricum", re.I), {"probiotic"}),
    (re.compile(r"panax|ginseng|인삼|홍삼", re.I), {"ginseng"}),
    (re.compile(r"hypericum|st\.? ?john", re.I), {"st_johns_wort"}),
    (re.compile(r"red yeast rice|monascus", re.I), {"statin"}),
    (re.compile(r"\bcalcium\b|\bmagnesium\b|\bferrous\b|\bferric\b|\biron\b|\bzinc\b|alumin|sucralfate|"
                r"\bbismuth\b|sevelamer|lanthanum", re.I), {"polyvalent_cation"}),
    (re.compile(r"\bferrous\b|\bferric\b|\biron\b|polysaccharide-iron", re.I), {"iron_supplement"}),
    (re.compile(r"green tea|black tea|camellia sinensis", re.I), {"tea_tannin"}),
    (re.compile(r"^potassium (chloride|citrate|gluconate|bicarbonate)", re.I), {"potassium_supplement"}),
    (re.compile(r"\bnitrite\b", re.I), {"nitrite"}),  # amyl / isobutyl nitrite (poppers)
    # (not 'vitamin k foods': a food is not a supplement - it has its own rule and advice)
    (re.compile(r"phytonadione|vitamin k\b(?! foods)|vitamin k ?[12]|menaquinone|menatetrenone|phylloquinone", re.I),
     {"vitamin_k_supplement"}),
]


# --------------------------------------------------------------------------- #
# Food, drink and smoking ("food:" keys): checked like ingredients against labels and rules. Label terms avoid
# the bare word "alcohol" (benzyl alcohol / alcohol-free excipient text) and "milk" (breast milk).
# --------------------------------------------------------------------------- #
FOODS: dict[str, dict] = {
    "alcohol": {"ko": "술(알코올)", "aliases": ["술", "알코올", "알콜", "음주", "소주", "맥주", "와인", "막걸리", "양주",
                                           "alcohol", "ethanol", "beer", "wine", "liquor"],
                "groups": {"alcohol"},
                "terms": ["alcoholic beverages", "alcoholic drinks", "alcohol consumption", "alcohol intake",
                          "alcohol use", "drinking alcohol", "consume alcohol", "ethanol", "alcohol abuse",
                          "alcoholic beverage"]},
    "grapefruit": {"ko": "자몽(주스)", "aliases": ["자몽", "자몽주스", "grapefruit", "grapefruit juice"],
                   "groups": {"grapefruit"}, "terms": ["grapefruit"]},
    "dairy": {"ko": "우유·유제품", "aliases": ["우유", "유제품", "치즈", "요구르트", "요거트", "milk", "dairy"],
              "groups": {"polyvalent_cation", "dairy"},
              "terms": ["dairy products", "milk products", "calcium-fortified", "dairy"]},
    "vitamin k foods": {"ko": "녹색 채소(비타민K 식품)", "aliases": ["녹색채소", "녹황색채소", "시금치", "케일", "청국장",
                                                           "낫토", "비타민k식품", "vitamin k foods", "leafy greens"],
                        "groups": {"vitamin_k_food"},
                        "terms": ["vitamin K-rich", "vitamin K rich", "green leafy vegetables", "vitamin K intake",
                                  "foods containing vitamin K", "dietary vitamin K"]},
    "smoking": {"ko": "흡연(담배)", "aliases": ["흡연", "담배", "smoking", "tobacco", "cigarette"],
                "groups": {"smoking"},
                "terms": ["cigarette smoking", "smokers", "tobacco smoking", "smoking", "who smoke", "smoke"]},
    # foods found by name in the MFDS food nutrient DB (stage "food-nutrients") by what they contain
    "potassium-rich foods": {"ko": "칼륨이 많은 음식", "aliases": ["칼륨이많은음식", "고칼륨식품", "칼륨많은음식"],
                             "groups": {"high_potassium_food"},
                             "terms": ["potassium-rich", "high-potassium", "high potassium diet", "salt substitutes",
                                       "potassium-containing salt"]},
    "calcium-rich foods": {"ko": "칼슘이 많은 음식", "aliases": ["칼슘이많은음식", "고칼슘식품"],
                           "groups": {"polyvalent_cation"}, "terms": ["calcium-rich", "calcium-fortified"]},
}
FOOD_ALIAS = {a.replace(" ", "").lower(): k for k, v in FOODS.items() for a in v["aliases"]}


def food_key(text: str) -> str | None:
    return FOOD_ALIAS.get((text or "").replace(" ", "").lower())


CLASS_TERMS = _class_terms()  # built last: needs the DRUG_GROUPS additions above


def class_phrases(name: str, label_of: str | None = None) -> list[str]:
    """Label phrases for every drug class this ingredient belongs to; with label_of, only the classes the label's own
    drug ALSO belongs to (there the sentence must say 'other ...': 'contraindicated with other MAOIs' in a safinamide
    label is about another MAOI, 'Benzodiazepines are contraindicated in glaucoma' in a midazolam label is not)."""
    n = name.removeprefix("ing:").removeprefix("bio:").removeprefix("lab:")
    own = (label_of or "").removeprefix("ing:").removeprefix("bio:").removeprefix("lab:")
    return [p for phrases, members in CLASS_TERMS.values() if n in members and (not own or own in members)
            for p in phrases]


def bio_key(text: str) -> str | None:
    """Biologic ingredient for a typed name / brand / Korean product name ('위고비프리필드펜2.4mg'), or None."""
    t = (text or "").split("(")[0].strip().lower().replace(" ", "")
    if not t:
        return None
    for k, v in BIOLOGICS.items():
        names = [k, v["ko"], *v["brands"], *v.get("label", [])]
        if any(t == n.replace(" ", "") or (len(n) >= 2 and not n.isascii() and t.startswith(n)) for n in names):
            return k
    return None


def normalize_ingredient(name: str, known: set[str] | frozenset[str]) -> str | None:
    """Map a raw ingredient string (e.g. 'WARFARIN SODIUM') to a known drug name, or None."""
    s = " ".join(name.lower().replace(",", " ").split())
    s = DRUG_SYNONYMS.get(s, s)
    if s in known:
        return s
    words = s.split()
    while len(words) > 1 and words[-1] in SALT_WORDS:
        words.pop()
        s = DRUG_SYNONYMS.get(" ".join(words), " ".join(words))
        if s in known:
            return s
    words = [w for w in words if w not in SALT_WORDS]
    s = " ".join(words)
    s = DRUG_SYNONYMS.get(s, s)
    return s if s in known else None
