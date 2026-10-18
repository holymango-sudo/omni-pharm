import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

/* '증상·질환으로 약 찾기' (v2.21): type what hurts or the diagnosis ('허리디스크', '무릎 십자인대 파열', '감기') or pick a
   common symptom -> self-care options per condition, already screened for this patient and the medicines entered
   (적합 / 주의 / 피하세요 with the reason): tablets, creams / patches, and the OTC products whose MFDS-approved indication
   names the complaint. A condition that needs a doctor first says so; an emergency gets no medicine at all.
   Expert view: prescription medicines doctors typically use, as a reference. One click adds a product to the list. */

const STATUS = { ok: ["적합", "ok"], caution: ["주의", "mid"], avoid: ["피하세요", "high"] };
const CARE = {
  emergency: ["🚑 응급 — 약을 찾지 말고 지금 바로 119 또는 응급실", "care-emergency"],
  doctor_first: ["🏥 진료가 먼저입니다 — 아래 약은 진료 전까지 증상을 덜어 주는 용도", "care-doctor"],
};

function Why({ reasons }) {
  return reasons.map((r, i) => <div key={i} className={`sym-why ${r.sev}`}>{r.sev === "avoid" ? "⛔" : "⚠"} {r.text}</div>);
}

function Option({ o, added, onAdd, rx }) {
  return (
    <div className={`sym-opt st-${o.status}`}>
      <div className="sym-line">
        <span className={`sym-badge ${STATUS[o.status][1]}`}>{STATUS[o.status][0]}</span>
        <b>{o.name}</b>{o.rank === 1 && <small className="first">1차 선택</small>}{rx && <small className="rx">처방 필요</small>}
        <span className="sym-does">{o.does}</span>
      </div>
      <Why reasons={o.reasons} />
      {o.status !== "avoid" && o.products.length > 0 && (
        <div className="sym-prods">
          {o.products.map((p) => (
            <button key={p.full} className="chip drug" disabled={added.has(p.full)} onClick={() => onAdd(p.full)}
              title={`${p.full}${p.otc ? " · 일반의약품" : " · 전문의약품(처방 필요)"}${p.combo ? " · 복합제" : ""}`}>
              ＋ {p.name}{!p.otc && <small> {rx ? "검토용" : "처방"}</small>}{p.combo && <small> 복합</small>}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function LabelProducts({ items, added, onAdd }) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, 3);
  return (
    <div className="sym-sec">
      <em>📄 식약처 허가 효능·효과로 찾은 일반의약품</em>
      {shown.map((p) => (
        <div key={p.full} className={`sym-opt st-${p.status}`}>
          <div className="sym-line">
            <span className={`sym-badge ${STATUS[p.status][1]}`}>{STATUS[p.status][0]}</span>
            <b>{p.name}</b>{p.topical && <small className="first">바르는·붙이는</small>}
            <span className="sym-does">{p.ingredients_ko.join(" · ")}</span>
            {p.status !== "avoid" && (
              <button className="chip drug" disabled={added.has(p.full)} onClick={() => onAdd(p.full)} title={p.full}>＋ 추가</button>
            )}
          </div>
          <div className="sym-eff">효능·효과: {p.snippet}</div>
          <Why reasons={p.reasons} />
        </div>
      ))}
      {items.length > 3 && <button className="btn mini" onClick={() => setAll(!all)}>{all ? "접기" : `${items.length - 3}개 더 보기`}</button>}
    </div>
  );
}

export default function SymptomFinder({ patient, drugs, onAdd, onClose, expert }) {
  const [catalog, setCatalog] = useState([]);
  const [picked, setPicked] = useState([]);  // condition ids
  const [free, setFree] = useState(null);    // free text that matched no condition
  const [q, setQ] = useState("");
  const [hits, setHits] = useState({ matches: [], free: false });
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const input = useRef();
  useEffect(() => { api.symptoms().then(setCatalog).catch(() => setCatalog([])); }, []);
  useEffect(() => {
    const s = q.trim();
    if (s.length < 2) { setHits({ matches: [], free: false }); return; }
    let live = true;
    const t = setTimeout(() => api.symptomSearch(s).then((r) => live && setHits(r)).catch(() => {}), 150);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  const name = (id) => catalog.find((c) => c.id === id);
  const pick = (id) => { setRes(null); setPicked((p) => (p.includes(id) ? p : [...p, id])); setQ(""); };
  const unpick = (id) => { setRes(null); setPicked((p) => p.filter((x) => x !== id)); };
  const submit = () => {  // Enter: the best match, or search the words themselves in the approved indications
    const s = q.trim();
    if (s.length < 2) return;
    if (hits.matches.length) pick(hits.matches[0].id);
    else { setRes(null); setFree(s); setQ(""); }
  };
  const results = useRef();
  const run = async () => {
    setBusy(true);
    setErr(null);
    try {
      setRes(await api.recommend(picked, drugs, patient(), picked.length ? null : free, !!expert));
      setTimeout(() => results.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 50);
    } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const added = new Set(drugs);
  const popular = catalog.filter((c) => c.popular);
  const ready = picked.length > 0 || !!free;

  return (
    <div className="sym-panel">
      <div className="sym-head">
        <b>🤒 증상·질환으로 약 찾기</b>
        <small>아픈 곳이나 병명을 입력하면 이 환자·지금 넣은 약과 맞는지 미리 확인해서 보여 드립니다</small>
        <button className="btn mini" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      <div className="sym-search">
        <input ref={input} className="search" value={q} autoFocus
          placeholder="예: 허리디스크, 무릎 십자인대 파열, 감기, 발목을 삐었어요, 무좀, 소변 볼 때 아파요"
          onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") submit(); }} />
        {q.trim().length >= 2 && (
          <div className="sym-suggest">
            {hits.matches.map((m) => (
              <button key={m.id} className={`sym-sugg ${m.care}`} onClick={() => pick(m.id)}>
                {m.icon} {m.ko}{m.care === "doctor_first" && <small> 진료 우선</small>}{m.care === "emergency" && <small> 응급</small>}
              </button>
            ))}
            {hits.free && (
              <button className="sym-sugg" onClick={submit}>🔎 '{q.trim()}'(으)로 허가 효능·효과에서 일반의약품 찾기</button>
            )}
          </div>
        )}
      </div>
      {!res && (  // once there are results the buttons fold away so the results are what you see
        <div className="sym-group">
          <em>자주 찾는 증상</em>
          {popular.map((c) => (
            <button key={c.id} className={`chip ${picked.includes(c.id) ? "on" : ""}`}
              onClick={() => (picked.includes(c.id) ? unpick(c.id) : pick(c.id))}>{c.icon} {c.ko}</button>
          ))}
        </div>
      )}
      {(picked.length > 0 || free) && (
        <div className="sym-picked">
          <em>고른 것</em>
          {picked.map((id) => (
            <button key={id} className="chip on" onClick={() => unpick(id)} title="빼기">{name(id)?.icon} {name(id)?.ko} ✕</button>
          ))}
          {free && !picked.length && <button className="chip on" onClick={() => { setFree(null); setRes(null); }}>🔎 '{free}' ✕</button>}
        </div>
      )}
      <button className="btn sym-go" disabled={!ready || busy} onClick={run}>
        {busy ? "확인 중…" : ready ? "이 환자에게 맞는 약 찾기" : "증상·질환을 검색하거나 골라 주세요"}
      </button>
      {err && <div className="error">⚠ {err}</div>}
      {res && (
        <div className="sym-results" ref={results}>
          {res.notes.map((n) => <div key={n} className={`sym-note ${n.startsWith("🚑") ? "emergency" : ""}`}>{n.startsWith("🚑") ? "" : "💡 "}{n}</div>)}
          {res.symptoms.map((s) => (
            <div key={s.id} className={`sym-card ${s.parent ? "sub" : ""} ${CARE[s.care]?.[1] || ""}`}>
              <div className="sym-title">{s.icon} {s.ko}{s.parent && <small> · {name(s.parent)?.ko}</small>}</div>
              {CARE[s.care] && <div className={`sym-care ${CARE[s.care][1]}`}>{CARE[s.care][0]}</div>}
              {s.care === "emergency" && <div className="sym-doctor">{s.doctor}</div>}
              {s.tips && <div className="sym-tips">🧊 약 없이 할 수 있는 것: {s.tips}</div>}
              {s.options.length > 0 && <div className="sym-sec"><em>💊 먹는 약</em>
                {s.options.map((o) => <Option key={o.key} o={o} added={added} onAdd={onAdd} />)}</div>}
              {s.topical.length > 0 && <div className="sym-sec"><em>🩹 바르는·붙이는 약</em>
                {s.topical.map((o) => <Option key={o.key} o={o} added={added} onAdd={onAdd} />)}</div>}
              <LabelProducts items={s.label_products} added={added} onAdd={onAdd} />
              {expert && s.rx.length > 0 && (
                <div className="sym-sec rx">
                  <em>🩺 의사가 흔히 쓰는 처방약 (참고 — 처방은 의사의 판단, 이 환자에게 맞는지 미리 확인)</em>
                  {s.rx.map((o) => <Option key={o.key} o={o} added={added} onAdd={onAdd} rx />)}
                </div>
              )}
              {s.care !== "emergency" && s.doctor && <div className="sym-doctor">🏥 병원에 가야 할 때: {s.doctor}</div>}
              {s.id === "free" && !s.label_products.length && (
                <div className="sym-doctor">허가 효능·효과에서 맞는 일반의약품을 찾지 못했습니다 — 다른 말(예: 증상 이름)로 검색하거나 약사와 상의하세요.</div>
              )}
            </div>
          ))}
          <div className="sym-disc">{res.disclaimer} 추가한 뒤 <b>▶ 예측 시작</b>을 누르면 전체 조합을 다시 확인합니다.</div>
        </div>
      )}
    </div>
  );
}
