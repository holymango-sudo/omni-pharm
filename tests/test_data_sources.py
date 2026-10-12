"""v2.16 data sources: the parallel resumable downloader, Korean label parsing (식약처 허가사항), the 'do not take with'
mapping, the OFFSIDES own-risk filter and the indication-bias filter."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from omnipharm.config import settings
from omnipharm.data import download
from omnipharm.knowledge import EVENT_INDICATION, kr_avoid_targets
from omnipharm.pipeline.dur import min_age
from omnipharm.pipeline.kr_labels import avoid_with


def test_parallel_download_resumes_dropped_connections(tmp_path, monkeypatch):
    data = bytes(range(256)) * 4000  # 1 MB
    monkeypatch.setattr(download, "PIECE", 100_000)
    monkeypatch.setattr(download.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "HEAD":
            return httpx.Response(200, headers={"Content-Length": str(len(data))})
        lo, hi = (int(x) for x in req.headers["Range"].removeprefix("bytes=").split("-"))
        calls["n"] += 1
        body = data[lo:hi + 1]
        if calls["n"] % 3 == 0:  # every third request is cut off half-way (as EBI does with long connections)
            body = body[: len(body) // 2]
        return httpx.Response(206, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = download.download_file_parallel("https://example.test/f.bin", tmp_path / "f.bin", c, connections=4)
    assert out.read_bytes() == data and not (tmp_path / "f.bin.part.json").exists()
    sums = f"{hashlib.sha256(data).hexdigest()}\tf.bin\n"
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=sums))) as c:
        assert download.verify_checksum(out, "https://example.test/sums", c) == hashlib.sha256(data).hexdigest()


CAUTIONS = """1. 다음과 같은 사람은 이 약을 복용하지 말 것.
1) 이 약에 과민반응이 있는 환자
2) 15세 미만의 소아
2. 이 약을 복용하는 동안 다음의 약을 복용하지 말 것.
다른 감기약, 해열진통제, 진정제, 항히스타민제를 함유하는 내복약(비염용 경구제, 멀미약, 알레르기용약)
3.다음과 같은 사람(경우)은 이 약을 복용하기 전에 의사, 치과의사, 약사와 상의할 것.
1) 6세 미만의 유아"""


def test_korean_label_parsing():
    assert avoid_with(CAUTIONS) == ["다른 감기약", "해열진통제", "진정제",
                                    "항히스타민제를 함유하는 내복약(비염용 경구제, 멀미약, 알레르기용약)"]
    assert min_age("성인 1회 1정", CAUTIONS) == 15  # '6세 미만' is under 'ask first', not a ban
    assert min_age("만 12세 이하의 소아: 1회 권장용량 …", "1. 이 약에 과민반응이 있는 환자는 복용하지 말 것.") is None


def test_do_not_take_with_mapping():
    assert kr_avoid_targets("진정제") == (frozenset({"sedative", "gabapentinoid"}), "주의")
    assert kr_avoid_targets("MAO 억제제")[1] == "높음"
    assert "tetracycline" in kr_avoid_targets("경구(먹는)용 테트라사이클린계 제제")[0]
    for not_an_item in ("외용 코르티코이드", "바셀린", "수술 전후", "경구용 혈당강하제 또는 제산제와의 상호작용을 나타낸 연구는 없었다"):
        assert kr_avoid_targets(not_an_item) is None, not_an_item


def test_indication_patterns():
    assert EVENT_INDICATION["Angioedema"].search("urticaria | rhinitis, allergic")
    assert EVENT_INDICATION["Cerebrovascular accident"].search("stroke | venous thrombosis")
    assert "International normalised ratio increased" not in EVENT_INDICATION  # warfarin's INR rise is its own risk
    assert "Hypoglycaemia" not in EVENT_INDICATION


needs_models = pytest.mark.skipif(not (settings.artifacts_dir / "v2" / "pair_metrics.json").exists(),
                                  reason="v2 artifacts not built")


@pytest.fixture(scope="module")
def eng():
    from omnipharm.engine import Engine

    return Engine()


def _predict(eng, names, age=40):
    from omnipharm.clinical import Patient

    prods = []
    for n in names:
        ings, info = eng.resolve_full(n)
        prods.append({"input": n, "ingredients": ings, "product": info.get("product"), "label_ko": info.get("label_ko")})
    return eng.predict(prods, Patient(age=age))


@needs_models
@pytest.mark.slow
def test_korean_label_evidence_in_the_result(eng):
    if not eng.label_ko or not any(k.startswith("D:") for k in eng.label_ko):
        pytest.skip("Korean labels not built (stage kr-labels)")
    r = _predict(eng, ["판콜에이내복액", "졸피뎀"])
    lines = [x["text"] for x in r["summary"]["reasons"] if "식약처 허가사항" in x["text"]]
    assert len(lines) == 1 and "진정제" in lines[0] and "졸피뎀" in lines[0]  # once per product, naming both sides
    child = _predict(eng, ["게보린정", "타이레놀정500밀리그람"], age=10)
    assert child["summary"]["verdict"] == "위험"
    assert any("15세 미만" in d["disease"] and d.get("key") == "age_label" for d in child["disease_alerts"])


@needs_models
@pytest.mark.slow
def test_dur_age_rule_only_for_its_dosage_form(eng):
    """MFDS DUR '아세트아미노펜 12세 미만' covers extended-release tablets only - not a children's syrup."""
    def alerts(names, age):
        return [(d["severity"], d["disease"]) for d in _predict(eng, names, age)["disease_alerts"]
                if "12세 미만" in d["disease"]]
    assert alerts(["어린이타이레놀현탁액", "세티리진"], 6) == []
    assert alerts(["타이레놀8시간이알서방정", "세티리진"], 10) == [("금기", "연령(12세 미만)")]
    assert alerts(["아세트아미노펜", "세티리진"], 6) == [("주의", "연령(12세 미만)")]  # form unknown: a note


@needs_models
@pytest.mark.slow
def test_own_risk_lines_need_offsides(eng):
    if not eng.offsides:
        pytest.skip("OFFSIDES not built (stage offsides)")
    r = _predict(eng, ["와파린", "타이레놀"])
    own = [x["text"] for x in r["summary"]["reasons"] if "약 자체에 따르는 위험" in x["text"]]
    assert own and all("OFFSIDES" in t for t in own)
    for e in r["events"]:
        if e.get("own_signal"):
            assert not e.get("indication")
    if eng.indications:  # stage chembl-dump: what a drug is GIVEN for is not its own risk (indication bias)
        ev = {e["condition"]: e for e in r["events"] if e.get("driver", "").startswith("와파린")}
        assert ev.get("International normalised ratio increased", {}).get("own_signal")  # its real risk stays
        for c in ("Cerebrovascular accident", "Pulmonary embolism"):
            assert not ev.get(c, {}).get("own_signal"), c
        cet = _predict(eng, ["세티리진", "타이레놀"])
        assert not any("혈관부종" in x["text"] and "세티리진" in x["text"] for x in cet["summary"]["reasons"])
