import React, { useEffect, useState } from "react";
import { api } from "../api.js";

/* '증상으로 약 찾기': pick symptoms -> over-the-counter options per symptom, already screened for this patient and the
   medicines entered (적합 / 주의 / 피하세요 with the reason) -> one click adds a product to the list. */

const STATUS = { ok: ["적합", "ok"], caution: ["주의", "mid"], avoid: ["피하세요", "high"] };

export default function SymptomFinder({ patient, drugs, onAdd, onClose }) {
  const [catalog, setCatalog] = useState([]);
  const [picked, setPicked] = useState([]);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  useEffect(() => { api.symptoms().then(setCatalog).catch(() => setCatalog([])); }, []);
  const toggle = (id) => { setRes(null); setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id])); };
  const run = async () => {
    setBusy(true);
    setErr(null);
    try { setRes(await api.recommend(picked, drugs, patient())); } catch (e) { setErr(e.message); }
    setBusy(false);
  };
  const groups = [...new Set(catalog.map((c) => c.group))];
  const added = new Set(drugs);
  return (
    <div className="sym-panel">
      <div className="sym-head">
        <b>🤒 증상으로 약 찾기</b>
        <small>환자 정보·지금 넣은 약과 맞는지 미리 확인해서 보여 드립니다</small>
        <button className="btn mini" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      {groups.map((g) => (
        <div key={g} className="sym-group">
          <em>{g}</em>
          {catalog.filter((c) => c.group === g).map((c) => (
            <button key={c.id} className={`chip ${picked.includes(c.id) ? "on" : ""}`} onClick={() => toggle(c.id)}>{c.icon} {c.ko}</button>
          ))}
        </div>
      ))}
      <button className="btn sym-go" disabled={!picked.length || busy} onClick={run}>
        {busy ? "확인 중…" : picked.length ? `${picked.length}개 증상에 맞는 약 찾기` : "증상을 골라 주세요"}
      </button>
      {err && <div className="error">⚠ {err}</div>}
      {res && (
        <div className="sym-results">
          {res.symptoms.map((s) => (
            <div key={s.id} className="sym-card">
              <div className="sym-title">{s.icon} {s.ko}</div>
              {s.options.map((o) => (
                <div key={o.key} className={`sym-opt st-${o.status}`}>
                  <div className="sym-line">
                    <span className={`sym-badge ${STATUS[o.status][1]}`}>{STATUS[o.status][0]}</span>
                    <b>{o.name}</b>{o.rank === 1 && <small className="first">1차 선택</small>}
                    <span className="sym-does">{o.does}</span>
                  </div>
                  {o.reasons.map((r, i) => <div key={i} className={`sym-why ${r.sev}`}>{r.sev === "avoid" ? "⛔" : "⚠"} {r.text}</div>)}
                  {o.status !== "avoid" && o.products.length > 0 && (
                    <div className="sym-prods">
                      {o.products.map((p) => (
                        <button key={p.full} className="chip drug" disabled={added.has(p.full)} onClick={() => onAdd(p.full)}
                          title={`${p.full}${p.otc ? " · 일반의약품" : " · 전문의약품(처방 필요)"}${p.combo ? " · 복합제" : ""}`}>
                          ＋ {p.name}{!p.otc && <small> 처방</small>}{p.combo && <small> 복합</small>}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
              {s.doctor && <div className="sym-doctor">🏥 병원에 가야 할 때: {s.doctor}</div>}
            </div>
          ))}
          {res.notes.map((n) => <div key={n} className="sym-note">💡 {n}</div>)}
          <div className="sym-disc">{res.disclaimer} 추가한 뒤 <b>▶ 예측 시작</b>을 누르면 전체 조합을 다시 확인합니다.</div>
        </div>
      )}
    </div>
  );
}
