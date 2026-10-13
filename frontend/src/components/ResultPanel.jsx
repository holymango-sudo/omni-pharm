import React, { useState } from "react";
import BodyMap from "./BodyMap.jsx";
import Markdown from "./Markdown.jsx";
import { fx, pct } from "../api.js";

const LV = { 위험: "high", 주의: "caution", "비교적 안전": "normal" };
const WORD_CLASS = { "매우 높음": "hi", 높음: "hi", "약간 높음": "mid", "약 자체 위험": "mid", 보통: "", "치료 중인 질환": "" };

function Verdict({ res, expert }) {
  const s = res.summary;
  const lv = LV[s.verdict];
  const h = res.headline;
  return (
    <div className={`siren lv-${lv}`}>
      <div className="siren-light" />
      <div className="siren-body">
        <div className="siren-k">종합 판정</div>
        <div className="siren-level">{s.verdict}</div>
        <div className="siren-head">{s.headline}</div>
        {expert && h && (
          <div className="siren-sub mono">{h.title}: 환자 맞춤 {pct(h.prob)} · 일반 보고 대비 ×{fx(h.lift)} · 표준 환자 대비 ×{fx(h.patient_factor)}</div>
        )}
      </div>
    </div>
  );
}

function Reasons({ s }) {
  return (
    <div className="reasons">
      {s.reasons.length > 0 && <h5>왜 그런가요?</h5>}
      {s.reasons.map((r) => (
        <div key={r.text} className={`reason ${r.sev}`}><span className="ri">{r.icon}</span><span>{r.text}</span></div>
      ))}
      <h5>어떻게 해야 하나요?</h5>
      {s.advice.map((a) => <div key={a} className="advice">✔ {a}</div>)}
    </div>
  );
}

function Symptoms({ events, expert }) {
  // the illness a medicine is given for (warfarin -> stroke) is not a side effect to watch: expert view only
  const top = events.filter((e) => e.prob >= 0.003 && e.lift >= 1.3 && (expert || !e.indication_any))
    .slice(0, expert ? 8 : 5);
  if (!top.length) return <div className="muted small">특별히 늘어나는 부작용이 예측되지 않았습니다.</div>;
  const max = Math.max(...top.map((e) => Math.min(e.lift, 6)));
  return (
    <div className="events">
      <h5>특히 조심할 부작용 <small className="muted">(높음 = 함께 먹거나 이 환자라서 늘어나는 위험)</small></h5>
      {top.map((e) => (
        <div key={e.id} className={`ev ${expert ? "" : "simple"}`}>
          <div className="ev-name">{e.ko}{e.serious && <span className="sev">중대</span>}</div>
          <div className="ev-bar"><i style={{ width: `${(Math.min(e.lift, 6) / max) * 100}%` }} className={WORD_CLASS[e.level_word]} /></div>
          {expert
            ? <><div className="ev-val mono">{pct(e.prob)}</div><div className="ev-lift mono">×{fx(e.lift)}</div></>
            : <div className={`ev-word ${WORD_CLASS[e.level_word]}`}>{e.level_word}</div>}
        </div>
      ))}
    </div>
  );
}

const mg = (x) => (x >= 10 ? Math.round(x).toLocaleString() : +x.toPrecision(3)) + "mg";
const STATUS_CLASS = { 초과: "hi", 주의: "mid", 적정: "ok", "기준 없음": "" };

function DoseCheck({ dosing }) {
  if (!dosing || (!dosing.ingredients.length && !dosing.missing.length)) return null;
  return (
    <div className="dosecheck">
      <h5>하루 복용량 점검 <small className="muted">(여러 제품의 같은 성분은 합산)</small></h5>
      {dosing.ingredients.map((r) => (
        <div key={r.key} className="dc-row">
          <div className="dc-name">{r.display}</div>
          <div className="dc-bar">
            {r.max_mg ? <i className={STATUS_CLASS[r.status]} style={{ width: `${Math.min(100, (r.daily_mg / r.max_mg) * 100)}%` }} /> : null}
          </div>
          <div className="dc-val mono">{mg(r.daily_mg)}{r.max_mg ? ` / ${mg(r.max_mg)}` : ""}</div>
          <div className={`dc-st ${STATUS_CLASS[r.status]}`}>{r.status}</div>
          {r.why?.length > 0 && <div className="dc-why">최대량 조정: {r.why.join(", ")}</div>}
        </div>
      ))}
      {dosing.missing.length > 0 && (
        <div className="small muted">용량 계산 불가(함량 정보 없음): {dosing.missing.join(", ")} — 정확한 제품명이나 mg 단위로 입력하세요.</div>
      )}
    </div>
  );
}

function DiseaseAlerts({ list }) {
  if (!list?.length) return null;
  return (
    <div className="dz">
      <h5>이 환자의 병 때문에 더 위험한 약</h5>
      {list.slice(0, 6).map((d) => (
        <div key={d.disease + d.drug} className={`dz-row ${d.severity === "금기" ? "high" : "mid"}`}>
          <b>{d.severity === "금기" ? "피해야 함" : "주의"}</b>
          <span><u>{d.disease}</u> + {d.drug} — {d.reason}</span>
          {d.evidence && <details><summary>근거 보기 ({d.source})</summary><p>{d.evidence}</p></details>}
        </div>
      ))}
    </div>
  );
}

// recreational / controlled substances: legal status and danger signs - harm reduction, never amounts
function Controlled({ list }) {
  if (!list?.length) return null;
  return (
    <div className="ctl">
      <h5>⚖️ 규제 물질 안내 <small className="muted">위험을 줄이기 위한 정보 · 사용을 권하지 않으며 용량은 안내하지 않습니다</small></h5>
      {list.map((c) => (
        <div key={c.input} className="ctl-row">
          <b>{c.input}</b> <span className="muted">({c.ko})</span>
          <div>🇰🇷 한국: {c.law}</div>
          {c.abroad && <div>🌐 해외: {c.abroad}</div>}
          <div>🚨 위험 신호: {c.signs}</div>
        </div>
      ))}
      <div className="ctl-foot">혼자 쓰지 말고 여러 물질을 섞지 마세요 · 응급 시 <b>119</b> · 마약류 24시간 익명 상담 <b>1342</b></div>
    </div>
  );
}

export default function ResultPanel({ res, report, streaming, onDetail, onPrint, showReport, expert }) {
  const [showLLM, setShowLLM] = useState(true);
  if (!res) {
    return (
      <div className="panel result empty">
        <div className="panel-title"><span className="idx">03</span> 결과</div>
        <BodyMap organs={null} />
        <div className="idle-msg">
          <div className="mono">AWAITING SIMULATION</div>
          ① 왼쪽에 환자 정보를 넣고 ② 가운데에 약을 추가한 뒤 ③ <b>예측 시작</b>을 누르세요.
        </div>
      </div>
    );
  }
  return (
    <div className="panel result">
      <Verdict res={res} expert={expert} />
      <Reasons s={res.summary} />
      <DoseCheck dosing={res.dosing} />
      <DiseaseAlerts list={res.disease_alerts} />
      <Controlled list={res.controlled} />
      <div className="split">
        <BodyMap organs={res.organs} expert={expert} />
        <Symptoms events={res.events} expert={expert} />
      </div>
      {res.notes?.map((n) => <div key={n} className="muted small">ℹ {n}</div>)}
      {res.unsupported?.length > 0 && (
        <div className="unsupported">분석하지 못한 항목: {res.unsupported.map((u) => `${u.input} (${u.reason})`).join(" · ")}</div>
      )}
      {expert && res.alerts.length > 0 && (
        <div className="alerts">{res.alerts.map((a) => <div key={a} className="alert">🩺 {a}</div>)}</div>
      )}
      {showReport && (
        <div className="report">
          <div className="report-head">
            <span onClick={() => setShowLLM(!showLLM)} style={{ cursor: "pointer" }}>
              {showLLM ? "▾" : "▸"} AI 전문가 소견 <small className="muted">로컬 LLM · 근거 기반</small></span>
            <span className="actions">
              <button className="btn mini" onClick={onDetail}>상세 근거</button>
              <button className="btn mini" onClick={() => onPrint("patient")} disabled={streaming}>환자용 안내문</button>
              <button className="btn mini" onClick={() => onPrint("pro")} disabled={streaming}>전문가 리포트</button>
            </span>
          </div>
          {showLLM && <div className="report-body"><Markdown text={report || ""} cursor={streaming} /></div>}
        </div>
      )}
    </div>
  );
}
