import React from "react";

export const PRESETS = {
  "간경변 고령": { age: 74, sex: "M", weight_kg: 61, height_cm: 168, labMode: "scr", scr: 1.1, egfr: 70, ast: 128, alt: 96,
    total_bilirubin: 3.4, conditions: ["hepatic", "arrhythmia"], diagnosis: "", symptoms: "무릎 관절통" },
  "녹내장·전립선 노인": { age: 78, sex: "M", weight_kg: 58, height_cm: 163, labMode: "scr", scr: 1.3, egfr: 55, ast: 24,
    alt: 20, total_bilirubin: 0.8, conditions: ["glaucoma", "bph", "hypertension"], diagnosis: "", symptoms: "감기·콧물" },
  "만성콩팥병": { age: 68, sex: "F", weight_kg: 52, height_cm: 155, labMode: "scr", scr: 2.3, egfr: 30, ast: 24, alt: 20,
    total_bilirubin: 0.7, conditions: ["renal", "hypertension", "diabetes"], diagnosis: "", symptoms: "부종" },
  "건강한 성인": { age: 35, sex: "M", weight_kg: 72, height_cm: 176, labMode: "scr", scr: 0.9, egfr: 100, ast: 22, alt: 19,
    total_bilirubin: 0.6, conditions: [], diagnosis: "", symptoms: "두통" },
};

const LV_COLOR = { 좋음: "#00e5a0", 보통: "#00e5ff", 저하: "#ffb020", "매우 저하": "#ff2d55" };

function Slider({ label, unit, value, min, max, step, onChange, color }) {
  const p = ((value - min) / (max - min)) * 100;
  return (
    <label className="slider">
      <div className="slider-head">
        <span>{label}</span>
        <span className="mono val" style={{ color }}>{value}{unit && <small> {unit}</small>}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        style={{ "--p": `${p}%`, "--c": color || "#00e5ff" }} />
    </label>
  );
}

function Meter({ title, value, ratio, color, sub, expert }) {
  const segs = 20;
  const on = Math.round(Math.max(0, Math.min(1, ratio)) * segs);
  return (
    <div className="gauge">
      <div className="gauge-head"><span>{title}</span><b style={{ color }}>{value}</b></div>
      <div className="gauge-bar" style={{ gridTemplateColumns: `repeat(${segs}, 1fr)` }}>
        {Array.from({ length: segs }, (_, i) => (
          <i key={i} style={{ background: i < on ? color : undefined, boxShadow: i < on ? `0 0 6px ${color}` : undefined }} />
        ))}
      </div>
      {sub && <div className="gauge-sub">{sub}</div>}
      {expert && <div className="gauge-sub mono muted">{expert}</div>}
    </div>
  );
}

export default function PatientPanel({ p, set, assessment: a, diseases, expert }) {
  const up = (patch) => set({ ...p, ...patch });
  const toggle = (c) => up({ conditions: p.conditions.includes(c) ? p.conditions.filter((x) => x !== c) : [...p.conditions, c] });
  const cap = a?.capacity;
  const k = a?.kidney;
  const l = a?.liver;
  const b = a?.body;
  const kColor = !k ? "#666" : k.pct_normal >= 90 ? "#00e5a0" : k.pct_normal >= 60 ? "#00e5ff" : k.pct_normal >= 30 ? "#ffb020" : "#ff2d55";
  const lColor = ["#00e5a0", "#ffb020", "#ff8a00", "#ff2d55"][l?.severity ?? 0];

  return (
    <div className="panel patient">
      <div className="panel-title"><span className="idx">01</span> 환자 정보</div>
      <div className="presets">
        {Object.keys(PRESETS).map((n) => <button key={n} className="chip" onClick={() => set({ ...PRESETS[n] })}>{n}</button>)}
      </div>

      {cap && (
        <div className="capacity" style={{ "--c": LV_COLOR[cap.level] }}>
          <div className="cap-head"><span>약물 처리 능력</span><b>{cap.level}</b></div>
          <div className="cap-bar"><i style={{ width: `${cap.score}%` }} /></div>
          <div className="cap-text">{cap.text}</div>
          {cap.factors.length > 0 && <div className="cap-factors">{cap.factors.map((f) => <span key={f}>{f}</span>)}</div>}
        </div>
      )}

      <div className="row3">
        <Slider label="나이" unit="세" value={p.age} min={1} max={100} step={1} onChange={(v) => up({ age: v })} />
        <Slider label="키" unit="cm" value={p.height_cm} min={100} max={200} step={1} onChange={(v) => up({ height_cm: v })} />
        <Slider label="몸무게" unit="kg" value={p.weight_kg} min={10} max={150} step={1} onChange={(v) => up({ weight_kg: v })} />
      </div>
      <div className="row-inline">
        <div className="seg">
          {[["F", "여성"], ["M", "남성"]].map(([v, lab]) => (
            <button key={v} className={p.sex === v ? "on" : ""} onClick={() => up({ sex: v })}>{lab}</button>
          ))}
        </div>
        {b?.bmi && <span className={`bmi ${b.bmi_cat === "정상" ? "" : "warnc"}`}>BMI {b.bmi} · {b.bmi_cat}</span>}
      </div>

      <div className="subhead">콩팥(신장) 검사
        <div className="seg mini">
          <button className={p.labMode === "scr" ? "on" : ""} onClick={() => up({ labMode: "scr" })}>크레아티닌</button>
          <button className={p.labMode === "egfr" ? "on" : ""} onClick={() => up({ labMode: "egfr" })}>eGFR 직접</button>
        </div>
      </div>
      {p.labMode === "scr"
        ? <Slider label="혈청 크레아티닌" unit="mg/dL" value={p.scr} min={0.3} max={8} step={0.1} color={kColor} onChange={(v) => up({ scr: v })} />
        : <Slider label="eGFR" unit="mL/min/1.73㎡" value={p.egfr} min={5} max={130} step={1} color={kColor} onChange={(v) => up({ egfr: v })} />}
      <Meter title="콩팥이 약을 걸러내는 능력" value={k ? `정상의 ${k.pct_normal}%` : "—"} ratio={(k?.pct_normal || 0) / 120}
        color={kColor} sub={k ? `${k.desc} · 몸무게와 키를 반영해 계산` : "검사값을 입력하세요"}
        expert={expert && k ? `${k.clearance_kind} ${k.clearance} mL/min · eGFR ${k.egfr} (KDIGO ${k.stage})` +
          (b?.dosing_weight ? ` · 계산 체중 ${b.dosing_weight}kg(${b.dosing_weight_kind})` : "") : null} />

      <div className="subhead">간 검사</div>
      <div className="row3">
        <Slider label="AST" value={p.ast} min={5} max={600} step={1} color={p.ast >= 120 ? "#ff2d55" : undefined} onChange={(v) => up({ ast: v })} />
        <Slider label="ALT" value={p.alt} min={5} max={600} step={1} color={p.alt >= 120 ? "#ff2d55" : undefined} onChange={(v) => up({ alt: v })} />
        <Slider label="빌리루빈" value={p.total_bilirubin} min={0.1} max={12} step={0.1} color={p.total_bilirubin >= 1.8 ? "#ffb020" : undefined} onChange={(v) => up({ total_bilirubin: v })} />
      </div>
      <Meter title="간 상태" value={l ? l.desc : "—"} ratio={((l?.severity ?? 0) + 0.3) / 3.3} color={lColor}
        expert={expert && l ? `정상 상한 대비: ${Object.entries(l.uln_multiples).map(([x, v]) => `${x} ×${v}`).join(", ")}` : null} />

      <div className="subhead">앓고 있는 병 <small className="muted">(해당하는 것 모두 선택)</small></div>
      <div className="conds">
        {(diseases || []).map((d) => (
          <button key={d.id} className={`chip ${p.conditions.includes(d.id) ? "on" : ""}`} onClick={() => toggle(d.id)}>{d.ko}</button>
        ))}
      </div>
      <input className="txt" placeholder="그 밖의 진단명을 자유롭게 입력 (예: 녹내장, 위궤양)" value={p.diagnosis} onChange={(e) => up({ diagnosis: e.target.value })} />
      {a?.diseases?.length > 0 && (
        <div className="small muted">인식된 질환: {a.diseases.map((id) => (diseases || []).find((d) => d.id === id)?.ko || id).join(", ")}</div>
      )}
      <input className="txt" placeholder="복용 이유 / 증상 (예: 감기, 관절통)" value={p.symptoms} onChange={(e) => up({ symptoms: e.target.value })} />
    </div>
  );
}
