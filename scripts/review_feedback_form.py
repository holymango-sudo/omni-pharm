"""The reviewer's kit that goes with a copied OMNI-PHARM (scripts/make_review_package.ps1):

    python scripts/review_feedback_form.py   -> docs/review_package/0_먼저_읽어_주세요.txt
                                                docs/review_package/프로그램_검수_의견서.xlsx

The guide says how to start the program on another PC; the form walks the reviewer through scenarios (each checked to
behave as described) and asks for an overall rating. Unlike the pharmacist case review it is not blinded - it is a
review of the whole program (accuracy, explanations, safety wording, ease of use).
"""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "review_package"

GUIDE = """OMNI-PHARM 검수 안내
==================================================

■ 이것은 무엇인가요
  여러 약(일반약·전문약·건강기능식품·음식)을 함께 쓸 때의 상호작용과, 이 환자(나이·신장·간·임신·질환)에게 맞지 않는
  약을 판정하고 근거와 할 일을 보여 주는 연구·교육용 프로그램입니다(졸업 연구). 의료기기가 아니며 진료에 쓰지 않습니다.

■ 필요한 것
  - Windows 10 또는 11 (64비트), 메모리 8GB 이상(16GB 권장), 빈 공간 약 7GB(AI 상담 포함판은 약 17GB)
  - 인터넷·설치·관리자 권한 필요 없음. 그래픽카드가 없어도 됩니다(분석이 조금 느릴 수 있음).

■ 시작하기
  1. 이 폴더 전체를 PC로 복사하세요(바탕 화면 등 아무 곳, 한글 경로 가능). USB에서 바로 실행해도 되지만 느립니다.
  2. 폴더 안의 'OMNI-PHARM 실행.bat'을 더블클릭하세요. 처음에는 20~40초 걸리고, 브라우저가 http://localhost:8000 으로 열립니다.
     - 'Windows의 PC 보호' 창이 나오면 '추가 정보' → '실행'을 누르세요(서명되지 않은 학생 프로그램이라 나오는 경고입니다).
     - 브라우저가 안 열리면 직접 http://localhost:8000 을 여세요.
  3. 처음 화면에서 '의료인(약사·의사·학생)으로 시작'을 고르면 근거 원문·확률까지 보입니다(위쪽 '전문가 보기'로 언제든 전환).
  4. 끝낼 때는 'OMNI-PHARM 종료.bat'을 더블클릭하세요.

■ 검수 방법
  '프로그램_검수_의견서.xlsx'를 열어
  - '시나리오' 시트: 12가지를 순서대로 해 보고 결과와 의견을 적어 주세요(프로그램이 보여 준 판정도 함께).
  - '전체 평가' 시트: 항목별 점수(1~5)와 좋았던 점·고칠 점을 적어 주세요.
  - '안내' 시트에 이름을 적고, 저장한 파일을 그대로 보내 주세요. 시간은 30분~1시간 정도입니다.
  틀렸다고 생각되는 판정은 '어떤 환자 + 어떤 약'인지와 바른 판단을 적어 주시면 가장 큰 도움이 됩니다.

■ AI 복약 상담·처방전 사진
  이 복사본에 AI 모델(runtime\\ollama-models 폴더)이 없으면 이 두 기능은 AI 대신 정해진 문장으로 답합니다.
  판정·근거·몸속 화면·인쇄 등 나머지 기능은 모두 같습니다.

■ 더 읽을거리 (폴더 안 docs)
  - docs\\INTENDED_USE.md : 사용 목적, 쓰면 안 되는 경우, 한계, 규제 검토
  - docs\\MODEL_CARD.md   : 판정 기준, 검증 결과(약사 검수 1·2차 포함), AI의 역할
  - docs\\USER_GUIDE.md   : 화면 사용법
  - docs\\validation\\     : 검증 자료(약사 검수 결과, 외부 기준 검증)
  (.md 파일은 메모장으로 열 수 있습니다)

■ 문제가 생기면
  - 검은 창에 'Server did not start'가 나오거나 바로 닫히면: 폴더의 logs\\api.log 파일을 보내 주세요.
  - 'VCRUNTIME140.dll' 같은 DLL 오류: Microsoft 'Visual C++ 재배포 가능 패키지(x64)'를 설치한 뒤 다시 실행하세요.
  - 이미 다른 프로그램이 8000번 포트를 쓰고 있으면 실행되지 않습니다 — 그 프로그램을 끄고 다시 실행하세요.

■ 개인정보
  입력한 환자 정보는 이 PC 밖으로 나가지 않고 저장되지 않습니다. 의견서에는 이름 외의 개인정보를 적지 않습니다.
"""

# (what, patient settings on the left panel, medicines / foods to add, what to look at) - each checked on v2.20
SCENARIOS = [
    ("기본 사용", "70세 여성", "와파린, 이부프로펜",
     "판정·조치 수준·'왜 그런가요?'·'어떻게 해야 하나요?'가 이해되나요? 이 판정이 임상적으로 맞나요?"),
    ("한국 제품명·성분 중복", "30세 남성", "콜대원코프에스시럽, 타이레놀", "두 제품에 같은 성분이 들어 있다는 것을 알려 주나요?"),
    ("신장 기능 저하", "82세 여성, 몸무게 48kg, 키 150cm, 크레아티닌 1.6", "리튬, 스피로노락톤",
     "검사값만 넣어도 신기능 저하를 판단해 금기를 알려 주나요?"),
    ("간경변", "63세 남성, '간질환' 선택, AST 110, ALT 80, 총빌리루빈 2.8", "심바스타틴, 메트포르민", "간경변에서 피해야 할 약을 알려 주나요?"),
    ("임신", "31세 여성, '임신' 선택", "이부프로펜, 글리메피리드", "임신 중 피해야 할 약과 대신 쓸 수 있는 약을 안내하나요?"),
    ("소아", "9세, 몸무게 30kg", "트라마돌 (1개만 넣어도 됨)", "나이 금기를 알려 주나요?"),
    ("약물 상호작용", "50세 남성", "클래리트로마이신, 심바스타틴", "위험한 상호작용과 그 이유(근거)를 보여 주나요?"),
    ("음식", "65세 여성", "와파린, 시금치 / 따로: 자몽, 심바스타틴", "음식과의 상호작용을 쉬운 말로 설명하나요? 할 일이 현실적인가요?"),
    ("건강기능식품", "60세 남성, '당뇨' 선택", "메트포르민, 홍삼", "건강기능식품의 주의 표시를 근거로 알려 주나요?"),
    ("전문가 보기·근거", "아무 환자", "위 조합 중 하나", "위쪽 '전문가 보기'를 켜고 '상세 근거'에서 허가 문서 원문과 규칙 출처가 확인되나요?"),
    ("몸속 이동 화면·인쇄", "아무 환자", "위 조합 중 하나", "가운데 몸속 화면이 이해에 도움이 되나요? '인쇄/PDF'의 환자용 안내문은 쓸 만한가요?"),
    ("AI 복약 상담 (AI 포함판만)", "분석이 끝난 뒤", "오른쪽 아래 💬 → '박카스 같이 마셔도 되나요?' 등 자유 질문",
     "답이 분석 근거와 맞나요? 끝까지 나오나요? 위험한 조언(약을 끊으라는 등)은 없나요?"),
    ("자유 시나리오", "평소 자주 보는 환자", "자주 함께 처방되거나 문의받는 조합", "판정과 설명이 기대와 맞나요?"),
]
RATINGS = ["판정이 임상적으로 타당한가", "이유(근거) 설명이 정확한가", "할 일 안내가 적절하고 안전한가",
           "일반인이 이해하기 쉬운가", "의료인에게 근거가 충분한가", "사용하기 편한가", "속도가 적당한가",
           "실제로(교육·상담 보조로) 쓰고 싶은가"]


def build() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "0_먼저_읽어_주세요.txt").write_bytes(GUIDE.replace("\n", "\r\n").encode("utf-8-sig"))  # Notepad-friendly

    bold, head = Font(bold=True), PatternFill("solid", fgColor="DDE7F0")
    fill_in = PatternFill("solid", fgColor="FFF2CC")
    wrap = Alignment(wrap_text=True, vertical="top")
    thin = Border(*(Side(style="thin", color="BFBFBF"),) * 4)
    wb = Workbook()
    ws = wb.active
    ws.title = "안내"
    for r in [("OMNI-PHARM 프로그램 검수 의견서", ""), ("", ""), ("검수자 이름", ""), ("직업·전공(선택)", ""), ("작성일", ""),
              ("", ""),
              ("하는 법", "'시나리오' 시트의 순서대로 프로그램을 써 보고 결과·판정·의견을 적은 뒤, '전체 평가'에 점수를 매겨 주세요. "
                         "노란 칸이 적는 곳입니다. 실행 방법은 폴더의 '0_먼저_읽어_주세요.txt'에 있습니다."),
              ("가장 도움이 되는 의견", "틀렸다고 생각되는 판정(어떤 환자 + 어떤 약, 바른 판단과 근거), 오해를 부를 수 있는 문장, 위험한 안내")]:
        ws.append(list(r))
    ws["A1"].font = Font(bold=True, size=14)
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 22, 100
    for row in ws.iter_rows(min_row=3):
        row[0].font = bold
        row[1].alignment = wrap
    for c in ("B3", "B4", "B5"):
        ws[c].fill, ws[c].border = fill_in, thin

    ws = wb.create_sheet("시나리오")
    cols = ["번호", "무엇을 보나", "환자 설정(왼쪽)", "넣을 약·음식", "확인할 점", "결과", "프로그램 판정", "의견(틀린 점·고칠 점)"]
    ws.append(cols)
    for i, s in enumerate(SCENARIOS, 1):
        ws.append([i, *s, None, None, None])
    for c in ws[1]:
        c.font, c.fill, c.alignment = bold, head, wrap
    for col, w in zip("ABCDEFGH", (5, 18, 30, 30, 44, 12, 12, 46), strict=True):
        ws.column_dimensions[col].width = w
    n = len(SCENARIOS) + 1
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment, c.border = wrap, thin
        for c in row[5:8]:
            c.fill = fill_in
        ws.row_dimensions[row[0].row].height = 48
    for rng, opts in ((f"F2:F{n}", "잘 됨,일부 문제,안 됨,해 보지 못함"), (f"G2:G{n}", "위험,주의,비교적 안전")):
        dv = DataValidation(type="list", formula1=f'"{opts}"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(rng)
    ws.freeze_panes = "C2"

    ws = wb.create_sheet("전체 평가")
    ws.append(["항목", "점수 (1 매우 아님 ~ 5 매우 그렇다)", "의견"])
    for r in RATINGS:
        ws.append([r, None, None])
    ws.append([])
    for q in ("가장 좋았던 점", "가장 먼저 고쳐야 할 점", "위험하거나 틀린 판정을 보았다면 (환자 + 약 + 바른 판단)", "그 밖의 의견"):
        ws.append([q, None, None])
    for c in ws[1]:
        c.font, c.fill, c.alignment = bold, head, wrap
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 44, 18, 70
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = wrap
        if row[0].value:
            row[0].font = bold
            (row[1] if row[0].row <= len(RATINGS) + 1 else row[2]).fill = fill_in
            row[2].fill = fill_in
    dv = DataValidation(type="list", formula1='"1,2,3,4,5"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"B2:B{len(RATINGS) + 1}")
    for s in wb.worksheets:
        s.page_setup.orientation, s.page_setup.paperSize = "landscape", s.PAPERSIZE_A4
        s.page_setup.fitToWidth, s.page_setup.fitToHeight = 1, 0
        s.sheet_properties.pageSetUpPr.fitToPage = True
    wb.save(OUT / "프로그램_검수_의견서.xlsx")
    print(f"-> {OUT}")


if __name__ == "__main__":
    build()
