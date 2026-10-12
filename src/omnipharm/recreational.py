"""Recreational and controlled substances - harm reduction and research.

Some people use substances that are illegal where they live, or legal in their country or city (adult cannabis in
Canada, Uruguay, Germany and many US states; licensed psilocybin services in Oregon and Colorado; psychiatrist-
prescribed MDMA and psilocybin in Australia). Whatever the law, the combinations that kill are well known - GHB or
ketamine with alcohol, cocaine with alcohol, MDMA with MAO inhibitors, poppers with erectile-dysfunction drugs - and
someone about to mix them should be able to find out. This module only makes these substances analysable like any
other ingredient:
  - names and street names (Korean / English) -> the ingredient(s); a curated structure when the drug database has
    none, so the structure models work offline too,
  - the legal status in Korea and the danger signs, shown with every result, and
  - the rule groups knowledge.PAIR_RULES uses for the documented dangerous combinations.
It never gives use or dose guidance (the dose check is skipped for these products), and the audit log does not record
these names (mask()).
"""

from __future__ import annotations

LAW = {
    "대마": "대마(마약류관리법) — 허가된 의약품 외 소지·사용·매매 불법",
    "마약": "마약(마약류관리법) — 의료·연구 목적 외 소지·사용·매매 불법",
    "향정": "향정신성의약품(마약류관리법) — 의료·연구 목적 외 소지·사용·매매 불법",
    "환각물질": "환각물질(화학물질관리법) — 흡입 목적의 사용·판매 불법",
    "임시": "임시마약류(마약류관리법) — 마약류에 준해 소지·사용·매매 금지",
    "임시+": "임시마약류로 지정된 뒤 정식 마약류 지정이 추진된 물질 — 소지·사용·매매 금지",
    "크라톰": "크라톰 성분 7-하이드록시미트라지닌이 향정신성의약품(2021) — 크라톰 제품의 반입·소지도 처벌될 수 있음",
    "규제": "국내 마약류·임시마약류 등 규제 대상일 수 있음 — 최신 지정 여부 확인 필요",
    "오남용": "의약품 오남용 — 처방 외 사용·거래 불법",
    "동물용": "동물용 마취제 — 사람에게 쓰도록 허가되지 않음",
}

# danger signs by kind: what to watch for and when to call 119 (never how to use)
SIGNS = {
    "opioid": "깨워도 반응이 없음·숨이 느리거나 멈춤·입술이 파래짐·동공이 바늘 끝처럼 작아짐 → 바로 119 (날록손이 있으면 사용)",
    "depressant": "깨워도 깨지 않음·숨이 느림·토하면서 의식이 없음 → 옆으로 눕혀 기도를 지키고 바로 119",
    "xylazine": "깊은 진정·느린 호흡 → 119 (날록손으로 자일라진 효과는 풀리지 않음), 주사 부위 피부 괴사·상처",
    "stimulant": "가슴 통증·심한 두근거림·고열(몸이 뜨겁고 땀이 멈춤)·경련·심한 흥분 → 서늘한 곳에서 몸을 식히고 바로 119",
    "mdma": "고열·근육 경직·혼돈(세로토닌 증후군), 물을 지나치게 마신 뒤 두통·구토·혼돈(저나트륨혈증) → 바로 119",
    "psychedelic": "심한 공황·혼돈 → 조용한 곳에서 믿을 만한 사람이 곁에 있게, 경련·고열·의식 저하 → 바로 119",
    "nbome": "경련·고열·심한 흥분·손발이 차고 파래짐(혈관 수축) → 바로 119 — LSD로 속여 파는 경우가 있습니다",
    "cannabis": "심한 불안·공황, 가슴 통증, 멈추지 않는 구토(대마 구토 증후군) → 의료진 상담, 의식 저하·흉통은 119",
    "synthetic_cannabinoid": "경련·심한 흥분·가슴 통증·의식 저하 → 바로 119 — 천연 대마보다 훨씬 강하고 예측하기 어렵습니다",
    "nitrite": "어지러움·실신·심한 두통·입술이 파래짐(메트헤모글로빈혈증) → 바로 중단, 의식 저하는 119 — 마시면 치명적",
    "n2o": "어지러움·실신 → 바로 중단하고 신선한 공기, 반복해 쓰면 비타민B12 결핍으로 손발 저림·걸음 장애",
    "kratom": "깊은 졸음·숨이 느림 → 119, 매일 쓰다 끊으면 금단 증상",
    "ibogaine": "두근거림·어지러움·실신 → 심장 리듬 이상(QT 연장)으로 사망 사례가 있어 바로 119",
}

# key -> substance. Exactly one of: db (name(s) the drug database resolves), smiles (curated structure, checked against
# the molecular formula in the tests) or parts (other keys). ko = name on the result card, ing_ko = the ingredient's
# display name where that differs (a narcolepsy patient's oxybate is not '물뽕'). The key is an alias too, so keys of
# medicines with ordinary medical use are not their English names ('speed', not 'amphetamine'). Street names that are
# also ordinary words ('coke' = cola, 'ice', 'k2' = vitamin K2, 'salvia' = sage, 'spice', '메스' = scalpel, '러쉬' = a
# cosmetics brand) are deliberately not aliases.
SUBSTANCES: dict[str, dict] = {
    # cannabis
    "cannabis": {"ko": "대마·마리화나", "parts": ["thc", "cbd"], "law": "대마", "kind": "cannabis",
                 "abroad": "캐나다·우루과이·독일·미국 여러 주 등은 성인 사용 합법 — 나라·지역마다 다름",
                 "aliases": ["대마", "대마초", "마리화나", "마리화나초", "마리후아나", "카나비스", "칸나비스", "대마잎", "떨",
                             "위드", "해시시", "해쉬", "cannabis", "marijuana", "marihuana", "weed", "ganja", "hashish"]},
    "thc": {"ko": "THC·대마 주성분", "ing_ko": "THC·드로나비놀", "db": "dronabinol", "law": "대마", "kind": "cannabis",
            "aliases": ["티에이치씨", "테트라히드로칸나비놀", "테트라하이드로칸나비놀", "tetrahydrocannabinol",
                        "delta-9-thc", "대마오일", "thc오일", "액상대마", "대마액상", "대마젤리", "대마쿠키", "대마초콜릿"]},
    "cbd": {"ko": "CBD·칸나비디올", "db": "cannabidiol", "law": "대마", "kind": "cannabis",
            "abroad": "여러 나라에서 THC가 거의 없는 CBD 제품은 합법",
            "aliases": ["씨비디", "칸나비디올", "cbd오일", "cbd oil"]},
    "jwh-018": {"ko": "합성대마·JWH-018", "law": "향정", "kind": "synthetic_cannabinoid",
                "smiles": "CCCCCn1cc(C(=O)c2cccc3ccccc23)c2ccccc21",
                "aliases": ["합성대마", "합성카나비노이드", "jwh018", "synthetic cannabinoid"]},
    # stimulants
    "cocaine": {"ko": "코카인", "db": "cocaine", "law": "마약", "kind": "stimulant",
                "aliases": ["코카인", "크랙", "크랙코카인", "crack cocaine"]},
    "methamphetamine": {"ko": "필로폰·메스암페타민", "ing_ko": "메스암페타민", "db": "methamphetamine", "law": "향정",
                        "kind": "stimulant", "aliases": ["필로폰", "히로뽕", "메스암페타민", "메트암페타민", "크리스탈메스",
                                                         "샤부", "meth", "crystal meth"]},
    "speed": {"ko": "암페타민·스피드", "ing_ko": "암페타민", "db": "amphetamine", "law": "향정", "kind": "stimulant",
              "aliases": ["암페타민", "스피드"]},
    "mdma": {"ko": "엑스터시·MDMA", "ing_ko": "MDMA·엑스터시", "db": "midomafetamine", "law": "향정", "kind": "mdma",
             "abroad": "호주는 PTSD 치료에 전문의 처방 허용(2023~)",
             "aliases": ["엑스터시", "엑스타시", "엑스터시정", "엠디엠에이", "몰리", "도리도리", "mdma", "ecstasy", "molly",
                         "xtc"]},
    "mda": {"ko": "MDA", "law": "향정", "kind": "mdma", "smiles": "CC(N)Cc1ccc2OCOc2c1",
            "aliases": ["mda", "tenamfetamine", "methylenedioxyamphetamine"]},
    "mephedrone": {"ko": "메페드론·4-MMC", "law": "향정", "kind": "stimulant", "smiles": "CNC(C)C(=O)c1ccc(C)cc1",
                   "aliases": ["메페드론", "mephedrone", "4-mmc", "4mmc", "meow meow"]},
    "cathinone": {"ko": "카티논·카트 잎", "law": "향정", "kind": "stimulant", "smiles": "CC(N)C(=O)c1ccccc1",
                  "aliases": ["카티논", "카트잎", "cathinone", "khat", "qat"]},
    "alpha-pvp": {"ko": "알파-PVP·플라카", "law": "향정", "kind": "stimulant", "smiles": "CCCC(N1CCCC1)C(=O)c1ccccc1",
                  "aliases": ["알파pvp", "알파-pvp", "플라카", "alpha-pvp", "a-pvp", "α-pvp", "flakka"]},
    # depressants and dissociatives
    "ghb": {"ko": "물뽕·GHB", "ing_ko": "GHB·옥시베이트", "db": "sodium oxybate", "law": "향정", "kind": "depressant",
            "abroad": "기면증 치료제(옥시베이트)로 처방되기도 함",
            "aliases": ["물뽕", "지에이치비", "감마하이드록시뷰티르산", "감마히드록시부티르산", "리퀴드엑스터시", "ghb",
                        "gamma-hydroxybutyrate", "gamma hydroxybutyrate", "liquid ecstasy"]},
    "gamma-butyrolactone": {"ko": "GBL·감마부티로락톤", "law": "규제", "kind": "depressant", "smiles": "O=C1CCCO1",
                            "aliases": ["gbl", "감마부티로락톤", "감마뷰티로락톤", "gamma-butyrolactone"]},
    "1,4-butanediol": {"ko": "1,4-부탄디올", "law": "규제", "kind": "depressant", "smiles": "OCCCCO",
                       "aliases": ["1,4-bd", "14bd", "1,4-butanediol", "1,4-부탄디올", "부탄디올"]},
    "ketamine": {"ko": "케타민", "db": "ketamine", "law": "향정", "kind": "depressant",
                 "abroad": "의료용 마취제·우울증 치료제(에스케타민)로 쓰임",
                 "aliases": ["케타민", "스페셜k", "ketamine", "special k"]},
    "phencyclidine": {"ko": "펜사이클리딘·PCP", "law": "향정", "kind": "depressant",
                      "smiles": "C1CCC(CC1)(c1ccccc1)N1CCCCC1",
                      "aliases": ["pcp", "펜사이클리딘", "엔젤더스트", "phencyclidine", "angel dust"]},
    "methoxetamine": {"ko": "메톡세타민·MXE", "law": "임시+", "kind": "depressant",
                      "smiles": "CCNC1(CCCCC1=O)c1cccc(OC)c1", "aliases": ["mxe", "메톡세타민", "methoxetamine"]},
    "rohypnol": {"ko": "로히프놀·플루니트라제팜", "ing_ko": "플루니트라제팜", "db": "flunitrazepam", "law": "향정",
                 "kind": "depressant", "aliases": ["로히프놀", "roofies"]},
    "phenibut": {"ko": "페니부트", "law": "규제", "kind": "depressant", "smiles": "NCC(CC(=O)O)c1ccccc1",
                 "aliases": ["페니부트", "phenibut"]},
    "tranq": {"ko": "트랑크·자일라진", "ing_ko": "자일라진", "db": "xylazine", "law": "동물용", "kind": "xylazine",
              "aliases": ["트랑크", "트랭크"]},
    "lean": {"ko": "린(코데인 기침약+프로메타진)", "db": ["codeine", "promethazine"], "law": "오남용", "kind": "opioid",
             "aliases": ["퍼플드랭크", "purple drank", "sizzurp"]},
    # inhalants
    "laughing gas": {"ko": "웃음가스·아산화질소", "ing_ko": "아산화질소", "db": "nitrous oxide", "law": "환각물질",
                     "kind": "n2o", "abroad": "의료용 마취 가스·휘핑크림 충전 가스, 영국은 2023년 오락용 소지 불법화",
                     "aliases": ["웃음가스", "해피벌룬", "마약풍선", "휘핑가스", "whippets", "whippet", "happy balloon"]},
    "poppers": {"ko": "파퍼스·아질산 아밀", "ing_ko": "아질산 아밀", "db": "amyl nitrite", "law": "임시", "kind": "nitrite",
                "aliases": ["파퍼스", "포퍼스", "팝퍼스", "아질산아밀", "아밀나이트라이트", "알킬나이트라이트", "alkyl nitrite"]},
    "isobutyl nitrite": {"ko": "아질산 이소부틸·파퍼스", "law": "임시", "kind": "nitrite", "smiles": "CC(C)CON=O",
                         "aliases": ["아질산이소부틸", "이소부틸나이트라이트", "isobutyl nitrite"]},
    # opioids
    "heroin": {"ko": "헤로인", "db": "heroin", "law": "마약", "kind": "opioid",
               "aliases": ["헤로인", "다이아모르핀", "디아세틸모르핀", "heroin", "diamorphine"]},
    "carfentanil": {"ko": "카펜타닐", "law": "마약", "kind": "opioid",
                    "smiles": "CCC(=O)N(c1ccccc1)C1(C(=O)OC)CCN(CCc2ccccc2)CC1",
                    "aliases": ["카펜타닐", "carfentanil", "carfentanyl"]},
    "isotonitazene": {"ko": "니타젠·이소토니타젠", "law": "임시+", "kind": "opioid",
                      "smiles": "CCN(CC)CCn1c(Cc2ccc(OC(C)C)cc2)nc2cc([N+](=O)[O-])ccc21",
                      "aliases": ["니타젠", "이소토니타젠", "nitazene", "nitazenes", "isotonitazene"]},
    "kratom": {"ko": "크라톰·미트라기닌", "ing_ko": "크라톰 성분 미트라기닌", "db": "mitragynine", "law": "크라톰",
               "kind": "kratom",
               "abroad": "태국·미국 여러 주 등은 합법, 일부 나라·주는 금지",
               "aliases": ["크라톰", "미트라기닌", "kratom", "ketum", "mitragynine"]},
    # psychedelics
    "lysergide": {"ko": "LSD", "law": "향정", "kind": "psychedelic",
                  "smiles": "CCN(CC)C(=O)[C@H]1CN(C)[C@@H]2Cc3c[nH]c4cccc(c34)C2=C1",
                  "aliases": ["lsd", "엘에스디", "리세르그산디에틸아미드", "lysergide"]},
    "psilocybin": {"ko": "환각버섯·실로시빈", "law": "향정", "kind": "psychedelic",
                   "smiles": "CN(C)CCc1c[nH]c2cccc(OP(=O)(O)O)c12",
                   "abroad": "미국 오리건·콜로라도 주는 허가 시설에서 허용, 호주는 전문의 처방 허용(2023~)",
                   "aliases": ["환각버섯", "마법의버섯", "매직머쉬룸", "매직머시룸", "슈룸", "실로시빈", "실로사이빈",
                               "magic mushroom", "magic mushrooms", "shrooms", "psilocybin"]},
    "psilocin": {"ko": "실로신", "law": "향정", "kind": "psychedelic", "smiles": "CN(C)CCc1c[nH]c2cccc(O)c12",
                 "aliases": ["실로신", "psilocin"]},
    "dimethyltryptamine": {"ko": "DMT", "law": "향정", "kind": "psychedelic", "smiles": "CN(C)CCc1c[nH]c2ccccc12",
                           "aliases": ["dmt", "디엠티", "디메틸트립타민", "dimethyltryptamine", "n,n-dmt"]},
    "5-meo-dmt": {"ko": "5-MeO-DMT", "law": "규제", "kind": "psychedelic", "smiles": "COc1ccc2[nH]cc(CCN(C)C)c2c1",
                  "aliases": ["5-meo-dmt", "5meo-dmt", "5-meo"]},
    "harmine": {"ko": "하르민·MAO 억제 성분", "law": "규제", "kind": "psychedelic",
                "smiles": "COc1ccc2c(c1)[nH]c1c(C)nccc12", "aliases": ["하르민", "하르말라", "harmine", "harmala"]},
    "ayahuasca": {"ko": "아야와스카(DMT+하르민)", "parts": ["dimethyltryptamine", "harmine"], "law": "향정",
                  "kind": "psychedelic", "abroad": "브라질 등 일부 나라는 종교 의식 사용 허용",
                  "aliases": ["아야와스카", "ayahuasca", "yage"]},
    "mescaline": {"ko": "메스칼린·페요테", "law": "향정", "kind": "psychedelic", "smiles": "COc1cc(CCN)cc(OC)c1OC",
                  "aliases": ["메스칼린", "페요테", "산페드로", "mescaline", "peyote", "san pedro"]},
    "2c-b": {"ko": "2C-B", "law": "향정", "kind": "psychedelic", "smiles": "COc1cc(CCN)c(OC)cc1Br",
             "aliases": ["2c-b", "2cb", "투씨비"]},
    "25i-nbome": {"ko": "25I-NBOMe", "law": "규제", "kind": "nbome", "smiles": "COc1ccccc1CNCCc1cc(OC)c(I)cc1OC",
                  "aliases": ["25i-nbome", "25i", "nbome", "n-bomb", "엔밤"]},
    "ibogaine": {"ko": "이보가인", "law": "규제", "kind": "ibogaine",
                 "smiles": "CCC1CC2CC3C1N(C2)CCc1c3[nH]c2ccc(OC)cc12",
                 "aliases": ["이보가인", "이보가", "ibogaine", "iboga"]},
    "salvinorin a": {"ko": "살비노린 A·샐비아 디비노럼", "law": "규제", "kind": "psychedelic",
                     "smiles": "CC(=O)OC1CC(C(=O)OC)C2(C)CCC3C(=O)OC(c4ccoc4)CC3(C)C2C1=O",
                     "aliases": ["살비노린", "샐비아디비노럼", "살비아디비노럼", "salvia divinorum", "salvinorin a",
                                 "salvinorin"]},
    "muscimol": {"ko": "무시몰·광대버섯", "law": "규제", "kind": "depressant", "smiles": "NCc1cc(=O)[nH]o1",
                 "aliases": ["무시몰", "광대버섯", "muscimol", "amanita muscaria", "fly agaric"]},
}


def _norm(s: str) -> str:
    return "".join((s or "").split()).lower()


ALIAS: dict[str, str] = {}
for _k, _v in SUBSTANCES.items():
    for _a in [_k, *_v["aliases"]]:
        ALIAS.setdefault(_norm(_a), _k)


def key_of(text: str) -> str | None:
    """Typed name / street name -> substance key, or None. Whole-name match only ('대마' yes, '대마도' no)."""
    return ALIAS.get(_norm(text))


def info(key: str) -> dict:
    """What the result shows with this substance: Korean legal status, the law abroad and the danger signs."""
    s = SUBSTANCES[key]
    return {"key": key, "ko": s["ko"], "law": LAW[s["law"]], "abroad": s.get("abroad"), "signs": SIGNS[s["kind"]]}


def ingredient_names() -> dict[str, str]:
    """Ingredient name (database name or curated key) -> Korean display name."""
    out = {}
    for k, s in SUBSTANCES.items():
        if "parts" in s or isinstance(s.get("db"), list):  # mixtures: their parts are named by their own entries
            continue
        out[s.get("db") or k] = s.get("ing_ko") or s["ko"]
    return out


def search(q: str) -> list[dict]:
    """Autocomplete: substances one of whose names starts with q (one row per substance)."""
    qn = _norm(q)
    if not qn:
        return []
    out, seen = [], set()
    for a, k in ALIAS.items():
        if a.startswith(qn) and k not in seen and (len(qn) >= 2 or a == qn):
            seen.add(k)
            name = next((x for x in [k, *SUBSTANCES[k]["aliases"]] if _norm(x) == a), a)
            out.append({"name": name, "type": "규제물질", "sub": SUBSTANCES[k]["ko"]})
    return out


def mask(name: str) -> str:
    """Audit-log form of a typed name: controlled substances are not recorded by name."""
    return "[규제물질]" if key_of(name) else name
