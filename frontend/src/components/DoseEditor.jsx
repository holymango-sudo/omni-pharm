import React, { useState } from "react";

const UNIT_KO = { tablet: "정", capsule: "캡슐", "comprimé/gélule": "정", mL: "mL", g: "g" };
const fmt = (x) => (x >= 10 ? Math.round(x).toLocaleString() : +x.toPrecision(3)) + "mg";

/** Per-product dose entry: amount per dose, doses/day, days, regular vs as-needed. */
export default function DoseEditor({ drug, onSave, onClose }) {
  const info = drug.dose_info; // {unit, per, per_unit: [{name, mg}]} or null
  const options = [];
  if (info) {
    if (info.per === "mL") options.push(["mL", "mL"]);
    else if (info.per === "g") options.push(["g", "g"]);
    else options.push(["unit", UNIT_KO[info.unit] || info.unit]);
  }
  if (drug.single) options.push(["mg", "mg (성분량)"]);
  const d0 = drug.dose || {};
  const [amount, setAmount] = useState(d0.amount ?? 1);
  const [unit, setUnit] = useState(d0.unit ?? options[0]?.[0] ?? "mg");
  const [perDay, setPerDay] = useState(d0.per_day ?? 3);
  const [days, setDays] = useState(d0.days ?? 3);
  const [prn, setPrn] = useState(d0.prn ?? false);
  const [weekly, setWeekly] = useState(d0.weekly ?? false);

  if (!options.length) {
    return (
      <div className="dose-ed">
        <div className="small">“{drug.name}”은(는) 1정당 성분 함량 정보가 없어 용량 점검을 할 수 없습니다.
          정확한 제품명(예: 타이레놀정500밀리그람)으로 다시 추가하면 함량이 자동으로 들어갑니다.</div>
        <button className="btn mini" onClick={onClose}>닫기</button>
      </div>
    );
  }
  const perDose = (row) => (unit === "mg" ? amount : row.mg * amount);
  const preview = unit === "mg" ? [{ name: drug.molecules?.[0]?.display || drug.name, mg: 1 }] : info?.per_unit || [];
  const daily = (row) => perDose(row) * (weekly ? perDay / 7 : perDay);

  return (
    <div className="dose-ed">
      <div className="dose-head"><b>{drug.name}</b> 복용 방법</div>
      <div className="dose-row">
        <label>1회 <input type="number" min="0.25" step="0.25" value={amount} onChange={(e) => setAmount(+e.target.value)} /></label>
        <select value={unit} onChange={(e) => setUnit(e.target.value)}>
          {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </select>
        <label>{weekly ? "주" : "하루"} <input type="number" min="1" max="24" value={perDay} onChange={(e) => setPerDay(+e.target.value)} />회</label>
        <label><input type="number" min="1" max="3650" value={days} onChange={(e) => setDays(+e.target.value)} />일 동안</label>
      </div>
      <div className="dose-row">
        <div className="seg mini">
          <button className={!prn ? "on" : ""} onClick={() => setPrn(false)}>매일 규칙적으로</button>
          <button className={prn ? "on" : ""} onClick={() => setPrn(true)}>필요할 때만</button>
        </div>
        <label className="small"><input type="checkbox" checked={weekly} onChange={(e) => setWeekly(e.target.checked)} /> 주 단위로 먹는 약</label>
      </div>
      {info && unit !== "mg" && (
        <div className="small muted">1{unit === "unit" ? (UNIT_KO[info.unit] || info.unit) : unit}당: {info.per_unit.map((r) => `${r.name} ${fmt(r.mg)}`).join(", ")}</div>
      )}
      <div className="dose-sum">하루 {prn ? "최대 " : ""}성분량: {preview.map((r) => `${r.name} ${fmt(daily(r))}`).join(" · ")}</div>
      <div className="dose-row end">
        {drug.dose && <button className="btn mini" onClick={() => onSave(null)}>용량 지우기</button>}
        <button className="btn mini" onClick={onClose}>취소</button>
        <button className="btn mini on" onClick={() => onSave({ amount, unit, per_day: perDay, days, prn, weekly })}>적용</button>
      </div>
    </div>
  );
}

export function doseLabel(d, info) {
  if (!d) return null;
  const u = d.unit === "unit" ? (UNIT_KO[info?.unit] || info?.unit || "개") : d.unit;
  return `${d.amount}${u}×${d.per_day}${d.weekly ? "/주" : ""}${d.prn ? "(필요시)" : ""}·${d.days}일`;
}
