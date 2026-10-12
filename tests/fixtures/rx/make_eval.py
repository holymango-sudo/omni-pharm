"""Build the 처방전·약봉투 photo sets: fake documents listing REAL Korean products -> phone-like photos.

    python tests/fixtures/rx/make_eval.py              -> tests/fixtures/rx/eval/ (seed 7: the reported evaluation)
    python tests/fixtures/rx/make_eval.py dev 11 12    -> tests/fixtures/rx/dev/  (seed 11: for tuning the matching)
    python tests/fixtures/rx/make_eval.py eval_small 7 12 1 / dev_small 11 12 1   the same pages photographed from far away

Matching rules are tuned on 'dev' only, so the 'eval' numbers are not fitted to their own photos.
Three layouts (hospital prescription form, pharmacy medicine bag, pharmacy dosing sheet), 3-6 medicines each, drawn at
random from the MFDS product list: oral tablets / capsules / syrups that are on the market. Patients are invented
('홍길동' ...) so the scorer can check that no identifier comes back. Rendered at 2x (text about 26 px high, like a
phone photo of a printed page); every fourth photo is taken badly (stronger tilt, blur, low JPEG quality).
Rendering uses the Edge browser in headless mode (Windows).
"""

from __future__ import annotations

import html
import json
import random
import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
from make_photo import phone_photo  # noqa: E402

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PATIENTS = [("홍길동", "800101-1"), ("김철수", "751231-1"), ("이영희", "920315-2"), ("박민수", "680707-1"),
            ("최지은", "010203-4"), ("정우성", "550505-1")]
TIMING = ["식후 30분", "아침·저녁 식후", "취침 전", "식전 30분", "아침 식후", "필요시"]
CSS = """body{margin:0;background:#fff;font-family:"Malgun Gothic",sans-serif;color:#111}
.page{width:760px;padding:24px 28px}h1{text-align:center;font-size:26px;letter-spacing:12px;margin:4px 0 14px}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border:1px solid #333;padding:5px 6px}
th{background:#f1f1f1;font-weight:600}.sm{font-size:11px;color:#444}.c{text-align:center}
.bag{width:620px;padding:20px 24px;border:2px solid #2b6cb0;border-radius:10px;margin:12px}
.top{display:flex;justify-content:space-between;align-items:baseline}.top b{font-size:24px;color:#2b6cb0}
.who{margin:10px 0;font-size:15px}.med{display:flex;gap:12px;align-items:center;border-top:1px dashed #999;padding:9px 0}
.pill{width:54px;height:30px;border-radius:16px;background:#e8eef7;border:1px solid #9ab;flex:none}
.nm{font-weight:700;font-size:16px}.ds{font-size:13px;color:#333}"""


def products(n: int, rng: random.Random) -> list[dict]:
    P = pd.read_parquet(ROOT / "data" / "processed" / "kr_products.parquet")
    ok = (P["status"].eq("정상") & P["name"].str.contains(r"정|캡슐|시럽")
          & ~P["name"].str.contains(r"주사|주$|점안|외용|크림|연고|패취|패치|좌제|가글|수출명|\(|/|,")
          & P["name"].str.len().between(4, 22) & P["ingredients"].map(len).between(1, 3)
          & P["unresolved"].map(len).eq(0))
    pool = P[ok].drop_duplicates("base")
    return pool.sample(n, random_state=rng.randrange(10**6))[["name", "kind"]].to_dict("records")


def dose(name: str, rng: random.Random) -> dict:
    if "시럽" in name:
        a = rng.choice([5, 10, 15])
        return {"amount": float(a), "unit": "mL", "per_dose": f"{a}mL", "bag": f"{a}mL"}
    a = rng.choice([1, 1, 1, 2, 0.5])
    shown = "0.5" if a == 0.5 else str(int(a))
    return {"amount": float(a), "unit": "unit", "per_dose": shown, "bag": shown + ("캡슐" if "캡슐" in name else "정")}


def prescription(p, meds, i) -> str:
    rows = "".join(f"<tr><td>{html.escape(m['name'])}</td><td class=c>{m['per_dose']}</td><td class=c>{m['times']}</td>"
                   f"<td class=c>{m['days']}</td><td>{m['timing']}</td></tr>" for m in meds)
    return (f"<div class=page><div class=sm>〈시험용 가짜 처방전 — 실제 환자 아님〉</div><h1>처 방 전</h1><table>"
            f"<tr><th style=width:15%>교부번호</th><td>2026년 10월 12일 - 제 {i:05d} 호</td><th style=width:15%>의료기관</th>"
            f"<td>시험의원</td></tr><tr><th>환자 성명</th><td>{p[0]}</td><th>주민등록번호</th><td>{p[1]}******</td></tr>"
            "</table><br><table><tr><th>처방 의약품의 명칭</th><th style=width:13%>1회 투약량</th><th style=width:13%>1일 투여횟수</th>"
            f"<th style=width:13%>총 투약일수</th><th style=width:16%>용법</th></tr>{rows}</table></div>")


def bag(p, meds, i) -> str:
    rows = "".join(f"<div class=med><div class=pill></div><div><div class=nm>{html.escape(m['name'])}</div><div class=ds>"
                   f"1회 {m['bag']} · 1일 {m['times']}회 ({m['timing']}) · {m['days']}일</div></div></div>" for m in meds)
    return (f"<div class=bag><div class=top><b>시험약국 복약안내</b><span>조제일 2026.10.12</span></div>"
            f"<div class=who>{p[0]} 님 | {max(m['days'] for m in meds)}일분 | 〈시험용 가짜 약봉투〉</div>{rows}</div>")


def sheet(p, meds, i) -> str:
    rows = "".join(f"<tr><td class=c>{k + 1}</td><td>{html.escape(m['name'])}</td><td class=c>{m['per_dose']}</td>"
                   f"<td class=c>{m['times']}</td><td class=c>{m['days']}</td></tr>" for k, m in enumerate(meds))
    return (f"<div class=page><h1 style=letter-spacing:4px>복약 안내문</h1><div class=sm>환자: {p[0]} · 시험용 가짜 문서</div><br>"
            f"<table><tr><th style=width:6%>No</th><th>약품명</th><th>투약량</th><th>횟수</th><th>일수</th></tr>{rows}</table></div>")


def render(body: str, path: Path, scale: int) -> None:
    src = path.with_suffix(".html")
    src.write_text(f"<!doctype html><html lang=ko><head><meta charset=utf-8><style>{CSS}</style></head><body>{body}</body>"
                   "</html>", encoding="utf-8")
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--force-device-scale-factor={scale}",
                    f"--screenshot={path}", "--window-size=820,620", src.as_uri()], check=True, capture_output=True,
                   timeout=60)


def main(name: str = "eval", seed: int = 7, n_docs: int = 12, scale: int = 2) -> None:
    """scale 2 = a normal phone photo of the page; scale 1 = taken from far away (text about 13 px high)."""
    rng = random.Random(seed)
    out = HERE / name
    out.mkdir(exist_ok=True)
    truth = []
    for i in range(n_docs):
        layout = ("prescription", "bag", "sheet")[i % 3]
        p = PATIENTS[i % len(PATIENTS)]
        meds = []
        for pr in products(rng.randint(3, 6), rng):
            d = dose(pr["name"], rng)
            meds.append({"name": pr["name"], **d, "times": rng.choice([1, 2, 3, 3]), "days": rng.choice([3, 5, 7, 14, 30]),
                         "timing": rng.choice(TIMING)})
        body = {"prescription": prescription, "bag": bag, "sheet": sheet}[layout](p, meds, i + 1)
        png = out / f"doc{i + 1:02d}_{layout}.png"
        render(body, png, scale)
        jpg = png.with_name(png.stem + "_photo.jpg")
        hard = i % 4 == 3  # every fourth photo is taken badly
        phone_photo(str(png), str(jpg), angle=rng.uniform(-6, 6) if hard else rng.uniform(-3, 3),
                    tilt=rng.uniform(0.02, 0.06), shade=rng.uniform(0.4, 0.8), blur=1.4 if hard else rng.uniform(0.6, 1.0),
                    quality=60 if hard else rng.randint(70, 85))
        png.unlink()
        png.with_suffix(".html").unlink()
        truth.append({"photo": jpg.name, "layout": layout, "hard": hard, "patient": p[0], "rrn": p[1],
                      "medicines": [{k: m[k] for k in ("name", "amount", "unit", "times", "days")} for m in meds]})
        print(jpg.name, len(meds), "hard" if hard else "")
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0] if a else "eval", int(a[1]) if len(a) > 1 else 7, int(a[2]) if len(a) > 2 else 12,
         int(a[3]) if len(a) > 3 else 2)
