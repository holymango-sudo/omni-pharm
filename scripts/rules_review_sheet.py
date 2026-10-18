"""Pharmacist review of the clinical rules (src/omnipharm/rules/*.json).

    python scripts/rules_review_sheet.py export                       -> docs/validation/규칙_검토표.xlsx
    python scripts/rules_review_sheet.py import <xlsx> --reviewer 이름 [--date YYYY-MM-DD]

export: one sheet per rule file - the rule, which medicines its groups contain, its source and evidence type, and two
empty columns for the reviewer (판정: 동의 / 수정 필요 / 삭제 필요, 의견).
import: writes each decision into the rule's review record (status, reviewer, date, note). '수정 필요' / '삭제 필요'
make tests/test_rules.py fail until a developer changes the rule and sets the status back (docs/CONTRIBUTING.md).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from omnipharm import knowledge as K  # noqa: E402
from omnipharm import recreational as R  # noqa: E402
from omnipharm import rules  # noqa: E402

OUT = ROOT / "docs" / "validation" / "규칙_검토표.xlsx"
DECISIONS = ("동의", "수정 필요", "삭제 필요")
SHEETS = {  # file -> (sheet title, key fields, shown fields)
    "pair_rules": ("상호작용 규칙", ("a", "b"), ("level", "reason", "source", "evidence", "since")),
    "drug_disease_rules": ("질환 규칙", ("disease", "group"), ("level", "reason", "source", "evidence", "since")),
    "rule_details": ("쉬운 설명", ("a", "b"), ("short", "why", "do", "signs", "since")),
    "pair_notes": ("상호작용 없음 메모", ("a", "b"), ("text", "source", "evidence", "since")),
    "intended_combinations": ("의도된 병용", ("a", "b"), ("why", "source", "since")),
    "conditional_rules": ("조건부 규칙", ("a", "b", "when"), ("level", "reason", "source", "evidence", "since")),
}
NOT_DRUGS = ("disease", "when")  # key fields that name a patient group, not medicines
HEAD_KO = {"a": "A", "b": "B", "disease": "질환", "group": "약 그룹", "level": "등급", "reason": "화면 이유", "source": "출처",
           "evidence": "근거 종류", "since": "추가 버전", "short": "한 줄 요약", "why": "왜", "do": "할 일", "signs": "바로 진료할 신호",
           "text": "화면 문구", "when": "환자 상태"}


def members(name: str, limit: int = 12) -> str:
    """What a rule name covers, in words a pharmacist can check."""
    if name in K.DRUG_GROUPS:
        m = sorted(K.DRUG_GROUPS[name])
    elif name in K.DRUG_CLASSES:
        m = sorted(K.DRUG_CLASSES[name][1])
    else:
        m = sorted(k for t in (K.BIOLOGICS, K.FOODS, R.SUBSTANCES) for k, v in t.items() if name in v.get("groups", ()))
        m += [f"이름 패턴 /{rx.pattern[:60]}/" for rx, g in K.NAME_GROUPS if name in g]
    if not m:
        return "(성분 하나)"
    return ", ".join(m[:limit]) + (f" 외 {len(m) - limit}개" if len(m) > limit else "")


def _text(v) -> str:
    if isinstance(v, list):
        return "\n".join(f"· {x}" for x in v)
    if isinstance(v, str) and v.startswith("$"):
        return rules.load("rule_details")["shared"].get(v[1:], v)
    return "" if v is None else str(v)


def export() -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "안내"
    n_unknown = sum(r.get("source") == rules.UNKNOWN_SOURCE for f in rules.FILES for r in rules.rules(f))
    for row in [["OMNI-PHARM 임상 규칙 검토표"], [],
                ["목적", "프로그램이 판정에 쓰는 규칙(문서로 확인된 상호작용·질환 금기 등)을 약사·의사가 확인합니다."],
                ["방법", "각 시트의 '판정' 칸에 동의 / 수정 필요 / 삭제 필요 중 하나를 고르고, 수정·삭제는 '의견'에 이유와 바른 내용을 적어 "
                         "주세요. 확실하지 않으면 비워 두세요(미검토로 남음)."],
                ["중요", "'등급 높음'은 결과가 '위험'(함께 쓰지 않음)으로 나오는 규칙입니다. 이 규칙들을 먼저 봐 주세요."],
                ["출처", f"출처는 개발 측이 적은 것으로 원문 대조 전입니다. '출처 확인 필요' {n_unknown}개는 근거를 찾아 주시면 좋습니다."],
                ["돌려받기", "저장한 파일을 개발자에게 주면 `python scripts/rules_review_sheet.py import 파일 --reviewer 이름`으로 "
                             "규칙 파일에 검토 기록(검토자·날짜·판정·의견)이 남습니다."],
                ["생성", dt.date.today().isoformat()]]:
        ws.append(row)
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width = 12
    ws.column_dimensions["B"].width = 120
    for r in ws.iter_rows(min_row=3):
        r[0].font = Font(bold=True)
        if len(r) > 1:
            r[1].alignment = Alignment(wrap_text=True, vertical="top")

    head_fill = PatternFill("solid", fgColor="DDE7F0")
    danger_fill = PatternFill("solid", fgColor="FBE3E3")
    for fname, (title, keys, shown) in SHEETS.items():
        ws = wb.create_sheet(title)
        cols = ["키", *(HEAD_KO[k] for k in keys), *(f"{HEAD_KO[k]} 포함 약" for k in keys if k not in NOT_DRUGS),
                *(HEAD_KO[k] for k in shown), "현재 검토 상태", "판정", "의견"]
        ws.append(cols)
        for c in ws[1]:
            c.font, c.fill = Font(bold=True), head_fill
        for r in rules.rules(fname):
            kv = [r[k] for k in keys]
            row = ["|".join(kv), *kv, *(members(r[k]) for k in keys if k not in NOT_DRUGS), *(_text(r.get(k)) for k in shown),
                   r["review"]["status"] + (f" ({r['review']['reviewer']}, {r['review']['date']})" if r["review"]["reviewer"] else ""),
                   "", ""]
            ws.append(row)
            if r.get("level") in ("높음", "금기"):
                for c in ws[ws.max_row]:
                    c.fill = danger_fill
        dv = DataValidation(type="list", formula1='"' + ",".join(DECISIONS) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{_col(len(cols) - 1)}2:{_col(len(cols) - 1)}{ws.max_row}")
        widths = {"키": 10, "A": 16, "B": 16, "질환": 14, "약 그룹": 18, "화면 이유": 50, "출처": 40, "왜": 60, "할 일": 50,
                  "바로 진료할 신호": 40, "한 줄 요약": 40, "화면 문구": 50, "의견": 40}
        for i, c in enumerate(cols, 1):
            ws.column_dimensions[_col(i)].width = widths.get(c, 30 if "포함 약" in c else 12)
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions["A"].hidden = True  # the key: used by import, not by the reviewer
        ws.freeze_panes = "B2"
    ws = wb.create_sheet("약물 그룹(참고)")
    ws.append(["그룹", "포함 약"])
    for g in sorted(K.DRUG_GROUPS):
        ws.append([g, ", ".join(sorted(K.DRUG_GROUPS[g]))])
    for g, (ko, m) in sorted(K.DRUG_CLASSES.items()):
        ws.append([f"{g} ({ko})", ", ".join(sorted(m))])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 28, 140
    OUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUT)
    return OUT


def _col(i: int) -> str:
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def import_(path: Path, reviewer: str, date: str) -> dict[str, int]:
    wb = load_workbook(path, read_only=True)
    counts: dict[str, int] = {}
    for fname, (title, keys, _) in SHEETS.items():
        if title not in wb.sheetnames:
            continue
        rows = list(wb[title].iter_rows(values_only=True))
        head = list(rows[0])
        ki, di, ni = head.index("키"), head.index("판정"), head.index("의견")
        decided = {r[ki]: (r[di], r[ni]) for r in rows[1:] if r[ki] and r[di]}
        if not decided:
            continue
        p = rules.DIR / f"{fname}.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        for r in doc["rules"]:
            d = decided.get("|".join(r[k] for k in keys))
            if d is None:
                continue
            if d[0] not in DECISIONS:
                raise SystemExit(f"{title}: unknown decision {d[0]!r} (use {', '.join(DECISIONS)})")
            r["review"] = {"status": d[0], "reviewer": reviewer, "date": date, "note": d[1] or None}
            counts[d[0]] = counts.get(d[0], 0) + 1
        p.write_bytes((json.dumps(doc, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    rules.load.cache_clear()
    return counts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["export", "import"])
    ap.add_argument("path", nargs="?")
    ap.add_argument("--reviewer")
    ap.add_argument("--date", default=dt.date.today().isoformat())
    a = ap.parse_args()
    if a.mode == "export":
        print(f"wrote {export()}")
        return
    if not a.path or not a.reviewer:
        ap.error("import needs the workbook path and --reviewer")
    print(import_(Path(a.path), a.reviewer, a.date))


if __name__ == "__main__":
    main()
