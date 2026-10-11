import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import DoseEditor, { doseLabel } from "./DoseEditor.jsx";

export const SCENARIOS = [
  { name: "타이레놀 + 와파린", drugs: ["타이레놀", "와파린"] },
  { name: "와파린 + 코다론 + 부루펜", drugs: ["쿠마딘", "코다론", "부루펜"] },
  { name: "감기약 2종 + 진통제", drugs: ["콜대원코프에스시럽", "판콜에이내복액", "타이레놀"] },
  { name: "팍스로비드 + 심바스타틴", drugs: ["팍스로비드", "심바스타틴"] },
  { name: "해외약: Doliprane + Advil + 와파린", drugs: ["Doliprane", "Advil", "와파린"] },
  { name: "일본약: ロキソニン + ワーファリン", drugs: ["ロキソニン", "ワーファリン"] },
  { name: "SSRI + 트라마돌", drugs: ["프로작", "트라마돌"] },
];

// food, drink and smoking: checked against labels and rules like any medicine
const LIFESTYLE = [["🍺", "술"], ["🍊", "자몽주스"], ["🥛", "우유"], ["☕", "커피"], ["🥬", "녹색채소"], ["🚬", "흡연"]];
const isFood = (d) => d.molecules?.length > 0 && d.molecules.every((m) => m.category === "음식·생활");

export default function DrugBar({ drugs, onAdd, onRemove, onReplace, onScenario, onPredict, onDose, busy }) {
  const [q, setQ] = useState("");
  const [editing, setEditing] = useState(null);
  const [hits, setHits] = useState([]);
  const [focus, setFocus] = useState(false);
  const [sel, setSel] = useState(0);
  const input = useRef();

  // server-side search: Korean products (식약처 허가 4만여 품목), ingredients, brands
  useEffect(() => {
    const s = q.trim();
    if (!s) { setHits([]); return; }
    let live = true;
    const t = setTimeout(() => api.search(s).then((r) => live && setHits(r)).catch(() => {}), 120);
    return () => { live = false; clearTimeout(t); };
  }, [q]);

  const add = (name) => {
    const n = name.trim();
    if (!n) return;
    onAdd(n);
    setQ("");
    setSel(0);
    input.current?.focus();
  };

  return (
    <div className="drugbar">
      <div className="panel-title"><span className="idx">02</span> 3D 약물 합성 — 약물 검색</div>
      <div className="search-wrap">
        <input ref={input} className="search" value={q} placeholder="국내·해외 제품명(콜대원, NyQuil, Doliprane, ロキソニン…), 성분명(와파린, warfarin), 또는 SMILES"
          onChange={(e) => { setQ(e.target.value); setSel(0); }}
          onFocus={() => setFocus(true)} onBlur={() => setTimeout(() => setFocus(false), 150)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") { setSel((s) => Math.min(s + 1, hits.length - 1)); e.preventDefault(); }
            if (e.key === "ArrowUp") { setSel((s) => Math.max(s - 1, 0)); e.preventDefault(); }
            if (e.key === "Enter") add(hits[sel] && !q.includes("(") ? hits[sel].name : q);
          }} />
        <button className="btn ghost" onClick={() => add(q)}>추가</button>
        {focus && hits.length > 0 && (
          <div className="dropdown">
            {hits.map((h, i) => (
              <div key={h.name} className={`opt ${i === sel ? "sel" : ""}`} onMouseDown={() => add(h.name)}>
                <span className="opt-main">{h.name}{h.sub && <small>{h.sub}</small>}</span>
                <span className={`tag ${h.type === "일반약" || h.type.endsWith("OTC") ? "otc" : ""}`}>{h.type}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      <div className="chips">
        {drugs.map((d) => (
          <span key={d.name} className={`chip drug ${d.status}`}
            title={d.error || [d.product, ...(d.molecules || []).map((m) => m.display)].filter(Boolean).join("\n")}>
            {d.status === "loading" && <i className="spin" />}
            {d.name}
            {d.molecules?.length > 1 && <small> ({d.molecules.length}성분)</small>}
            {d.molecules?.some((m) => !m.known) && <small className="novel"> NEW</small>}
            {d.molecules?.some((m) => m.biologic) && <small className="novel" title="분자 구조가 없는 성분: 허가 문서·임상 규칙으로 평가">
              {" "}{[...new Set(d.molecules.filter((m) => m.biologic).map((m) => (m.category === "생물의약품" ? "바이오" : m.category?.split("·")[0])))].join("·")}</small>}
            {d.unresolved?.length > 0 && <small className="warnc"> +{d.unresolved.length} 분석불가</small>}
            {d.status === "ok" && !isFood(d) && (
              <button className={`dose-btn ${d.dose ? "set" : ""}`} onClick={() => setEditing(editing === d.name ? null : d.name)}
                title="복용량·횟수·기간 입력">{d.dose ? doseLabel(d.dose, d.dose_info) : "＋용량"}</button>
            )}
            <b onClick={() => onRemove(d.name)}>×</b>
          </span>
        ))}
        {!drugs.length && <span className="muted small">약물을 2개 이상 추가하세요. 데모 시나리오:</span>}
        {!drugs.length && SCENARIOS.map((s) => (
          <button key={s.name} className="chip ghost" onClick={() => onScenario(s.drugs)}>{s.name}</button>
        ))}
      </div>
      {drugs.length > 0 && (
        <div className="chips lifestyle">
          <span className="muted small">함께 확인:</span>
          {LIFESTYLE.filter(([, n]) => !drugs.some((d) => d.name === n)).map(([icon, n]) => (
            <button key={n} className="chip ghost" title={`${n}과(와) 약의 상호작용도 확인합니다`} onClick={() => onAdd(n)}>
              {icon} {n}</button>
          ))}
        </div>
      )}
      {editing && drugs.find((d) => d.name === editing) && (
        <DoseEditor key={editing} drug={drugs.find((d) => d.name === editing)} onClose={() => setEditing(null)}
          onSave={(dose) => { onDose(editing, dose); setEditing(null); }} />
      )}
      {drugs.length > 0 && !drugs.some((d) => d.dose) && (
        <div className="small muted">💡 약 이름 옆 <b>＋용량</b>을 누르면 복용량·기간까지 반영해 1일 최대용량 초과도 확인합니다.</div>
      )}
      {drugs.filter((d) => d.candidates?.length).map((d) => (
        <div key={d.name} className="cands">
          <div className="small">“{d.name}” — 어떤 제품인가요?</div>
          {d.candidates.map((c) => (
            <button key={c.name} className="cand" onClick={() => onReplace(d.name, c.name)}>
              <b>{c.name}</b> <span className="tag">{c.kind}</span><small>{c.ingredients}</small>
            </button>
          ))}
        </div>
      ))}
      <button className={`btn predict ${busy ? "busy" : ""}`} disabled={busy || drugs.filter((d) => d.status === "ok").length < 1
        || drugs.filter((d) => d.status === "ok").flatMap((d) => d.molecules || []).length < 2}
        onClick={onPredict}>
        {busy ? "SIMULATING…" : "▶ 예측 시작"}
      </button>
    </div>
  );
}
