"""Release form of a product from its name: extended / sustained / controlled release (ER), delayed release /
enteric coated (DR) or ordinary immediate release (IR).

Extended-release tablets put more drug in one tablet, reach their peak later and are taken less often; their
daily maximum, dosing interval and handling ("swallow whole") differ from the ordinary tablet.
"""

from __future__ import annotations

import re

# Korean product names: 서방정 / 서방캡슐 / 서방성 / 이알·엑스알·에스알·씨알(ER/XR/SR/CR spelled in Hangul) / 지속성
_KR_ER = re.compile(r"서방|지속성|(?<![가-힣])(이알|엑스알|에스알|씨알|엘에이|엑스엘)(?![가-힣]*엠\()|이알서방")
_KR_DR = re.compile(r"장용")
# English / French names. Short abbreviations only in capitals and as separate words
# ("CRÈME", "La Roche", "Dr." and "CRM197" are not release forms)
_EN_ER = re.compile(r"extended[- ]?release|sustained[- ]?release|controlled[- ]?release|prolonged[- ]?release|"
                    r"modified[- ]?release|lib[ée]ration prolong[ée]e|retard\b|\b24[- ]?h(ou)?r\b|\bOROS\b|contin\b|"
                    r"concerta", re.I)
_EN_ER_ABBR = re.compile(r"(?<![A-Za-z])(ER|XR|XL|SR|CR|LA|CD|MR|LP|ZOK)(?![A-Za-zÀ-ÿ\d.]|\s?ROCHE)")
_EN_DR = re.compile(r"delayed[- ]?release|enteric[- ]?coated|gastro[- ]?r[ée]sistant", re.I)
_EN_DR_ABBR = re.compile(r"(?<![A-Za-z])(DR|EC)(?=\s*\d|\s*$|\s*\()")  # 'Omeprazole DR 20 mg', not 'DR HOLST'


def release_of(name: str | None) -> str:
    """-> 'ER' | 'DR' | 'IR'"""
    n = name or ""
    if not n:
        return "IR"
    if _KR_ER.search(n) or _EN_ER.search(n) or _EN_ER_ABBR.search(n):
        return "ER"
    if _KR_DR.search(n) or _EN_DR.search(n) or _EN_DR_ABBR.search(n):
        return "DR"
    return "IR"


RELEASE_KO = {"ER": "서방형", "DR": "장용형", "IR": "일반형"}
