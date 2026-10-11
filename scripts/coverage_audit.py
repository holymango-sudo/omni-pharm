"""How many real-world medicines can the engine resolve? Prints the misses per category.

  python scripts/coverage_audit.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("OMNIPHARM_ONLINE_LOOKUP", "0")  # measure the offline databases only

from omnipharm.engine import Engine, ResolveError

CATEGORIES = {
    "수면·진정": ["zolpidem", "eszopiclone", "zopiclone", "zaleplon", "suvorexant", "lemborexant", "daridorexant",
              "ramelteon", "melatonin", "doxepin", "trazodone", "mirtazapine", "quetiapine", "triazolam",
              "temazepam", "flurazepam", "estazolam", "lorazepam", "alprazolam", "clonazepam", "diazepam",
              "etizolam", "brotizolam", "flunitrazepam", "nitrazepam", "doxylamine", "diphenhydramine",
              "hydroxyzine", "gabapentin", "pregabalin", "valerian", "스틸녹스", "졸피뎀", "수면유도제", "벨솜라",
              "데이비고", "큐비빅", "서카딘", "아티반", "자낙스", "할시온", "레스타스", "독시라민", "아론정"],
    "미용·피부": ["minoxidil", "finasteride", "dutasteride", "bimatoprost", "isotretinoin", "tretinoin",
              "adapalene", "tazarotene", "trifarotene", "hydroquinone", "azelaic acid", "tranexamic acid",
              "deoxycholic acid", "botulinum toxin", "hyaluronidase", "glutathione", "ascorbic acid",
              "spironolactone", "clascoterone", "eflornithine", "oxymetazoline", "brimonidine", "ivermectin",
              "metronidazole", "benzoyl peroxide", "clindamycin", "doxycycline", "minocycline", "sarecycline",
              "kojic acid", "niacinamide", "biotin", "collagen", "엘라스틴", "스킨부스터", "리쥬란", "쥬베룩",
              "키블라", "보톡스", "글루타치온", "신데렐라주사", "백옥주사", "태반주사", "라에넥", "멜스몬", "판토가",
              "마이녹실", "로게인", "판시딜", "에피듀오", "디페린", "트레티노인크림", "도란사민", "멜라토닝"],
    "비만·대사": ["semaglutide", "liraglutide", "tirzepatide", "orlistat", "phentermine", "topiramate",
               "naltrexone", "bupropion", "lorcaserin", "phendimetrazine", "diethylpropion", "mazindol",
               "setmelanotide", "위고비", "삭센다", "마운자로", "큐시미아", "콘트라브", "제니칼", "디에타민", "푸링"],
    "성기능·비뇨": ["sildenafil", "tadalafil", "vardenafil", "avanafil", "udenafil", "mirodenafil", "dapoxetine",
                "flibanserin", "bremelanotide", "alprostadil", "testosterone", "비아그라", "시알리스", "팔팔정",
                "센돔", "구구정", "프릴리지", "제피드"],
    "호르몬·피임": ["ethinyl estradiol", "levonorgestrel", "drospirenone", "desogestrel", "norethindrone",
                "ulipristal", "mifepristone", "misoprostol", "estradiol", "progesterone", "dydrogesterone",
                "tibolone", "raloxifene", "clomiphene", "letrozole", "cabergoline", "노레보", "엘라원", "야즈",
                "머시론", "마이보라", "듀파스톤", "프로기노바"],
    "정신·신경": ["sertraline", "escitalopram", "fluoxetine", "paroxetine", "venlafaxine", "desvenlafaxine",
              "duloxetine", "vortioxetine", "agomelatine", "bupropion", "methylphenidate", "atomoxetine",
              "lisdexamfetamine", "modafinil", "armodafinil", "lithium", "lamotrigine", "valproate",
              "aripiprazole", "olanzapine", "risperidone", "paliperidone", "brexpiprazole", "cariprazine",
              "lurasidone", "donepezil", "memantine", "콘서타", "메디키넷", "스트라테라", "렉사프로", "졸로푸트"],
    "통증·감기": ["acetaminophen", "ibuprofen", "naproxen", "dexibuprofen", "loxoprofen", "celecoxib",
              "tramadol", "oxycodone", "tapentadol", "codeine", "pseudoephedrine", "phenylephrine",
              "dextromethorphan", "chlorpheniramine", "cetirizine", "fexofenadine", "타이레놀", "판콜에이",
              "판피린큐", "게보린", "펜잘큐", "이지엔6", "탁센", "애드빌", "베아제", "훼스탈", "겔포스", "개비스콘",
              "까스활명수", "부스코판", "스멕타", "정로환", "광동쌍화탕", "우루사", "인사돌", "센트룸", "아로나민골드",
              "임팩타민", "비맥스", "박카스", "판콜", "테라플루", "타이레놀콜드"],
    "만성질환": ["amlodipine", "losartan", "telmisartan", "valsartan", "olmesartan", "candesartan", "lisinopril",
              "ramipril", "bisoprolol", "carvedilol", "nebivolol", "atorvastatin", "rosuvastatin", "pitavastatin",
              "ezetimibe", "fenofibrate", "metformin", "sitagliptin", "gemigliptin", "evogliptin", "dapagliflozin",
              "empagliflozin", "glimepiride", "pioglitazone", "insulin glargine", "levothyroxine", "allopurinol",
              "febuxostat", "colchicine", "apixaban", "rivaroxaban", "edoxaban", "warfarin", "clopidogrel",
              "aspirin", "ticagrelor", "tamsulosin", "silodosin", "mirabegron", "alendronate", "denosumab",
              "제미글로", "슈가논", "카나브", "듀카브", "트윈스타", "엑스포지", "아토젯", "리바로", "자누메트"],
    "항생·항바이러스": ["amoxicillin", "amoxicillin clavulanate", "cefuroxime", "cefixime", "azithromycin",
                   "clarithromycin", "levofloxacin", "ciprofloxacin", "doxycycline", "metronidazole",
                   "nitrofurantoin", "fosfomycin", "oseltamivir", "baloxavir", "acyclovir", "valacyclovir",
                   "nirmatrelvir", "molnupiravir", "fluconazole", "itraconazole", "terbinafine", "타미플루",
                   "조플루자", "오구멘틴", "팍스로비드", "발트렉스"],
    "기타·주사·생물의약품": ["adalimumab", "etanercept", "infliximab", "ustekinumab", "secukinumab",
                       "dupilumab", "omalizumab", "rituximab", "trastuzumab", "pembrolizumab", "nivolumab",
                       "bevacizumab", "aflibercept", "ranibizumab", "teriparatide", "romosozumab", "evolocumab",
                       "alirocumab", "insulin aspart", "insulin degludec", "dulaglutide", "heparin", "enoxaparin",
                       "erythropoietin", "filgrastim", "interferon", "vitamin b12", "iron sucrose",
                       "hyaluronic acid", "휴미라", "레미케이드", "스텔라라", "듀피젠트", "프롤리아", "키트루다"],
}


def main() -> None:
    eng = Engine()
    total = hit = 0
    for cat, names in CATEGORIES.items():
        miss = []
        for n in names:
            try:
                eng.resolve_full(n)
                hit += 1
            except ResolveError as e:
                miss.append(n + (" (후보 있음)" if e.candidates else ""))
            total += 1
        print(f"[{cat}] {len(names) - len(miss)}/{len(names)}  누락: {', '.join(miss) or '-'}")
    print(f"TOTAL {hit}/{total} ({hit / total:.0%})")


if __name__ == "__main__":
    main()
