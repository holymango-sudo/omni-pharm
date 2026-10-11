import React, { useEffect, useState } from "react";

/* Side panels of the journey caption: a concentration-time sketch (blood / excretion) and the mechanism chain of a
   finding (problem stage). Both are drawn from the analysis data; the curve is a concept sketch scaled by the
   evidence category (e.g. FDA 'strong inhibitor x sensitive substrate' = AUC >= 5x), not a measured concentration. */

const KA = 1.4; // absorption rate (1/h)
const KE = 0.18; // elimination rate (1/h), t1/2 ~ 3.9 h
const T_MAX = 24;

function curve(kind, fold) {
  let amp = 1, ke = KE;
  if (kind === "amount") amp = fold; // absorption / duplicated dose: same shape, more or less drug
  else ke = KE / fold; // clearance blocked (fold > 1) or induced (fold < 1)
  const raw = (t) => (Math.exp(-ke * t) - Math.exp(-KA * t));
  const base = (t) => (Math.exp(-KE * t) - Math.exp(-KA * t));
  let bmax = 0;
  for (let t = 0; t <= T_MAX; t += 0.1) bmax = Math.max(bmax, base(t));
  // same dose: AUC scales with 1/ke, so normalise both to the baseline's (KE) dose constant
  const k = (KA - KE) / (KA - ke);
  return (t) => (amp * k * raw(t)) / bmax;
}

const kindOf = (why = "") => (/흡수|중복|최대량/.test(why) ? "amount" : "clearance");

export function PKChart({ levels, drugs, colorOf, title, mode }) {
  const W = 300, H = 168, L = 34, B = 22, T = 14, R = 8;
  const rows = (levels || []).filter((l) => l.fold && Math.abs(Math.log(l.fold)) > 0.05).slice(0, 2);
  const shown = rows.length ? rows : (drugs || []).filter((d) => d.kind === "drug").slice(0, 1).map((d) => ({ drug: d.key, fold: 1 }));
  const curves = shown.map((l) => ({ l, f: curve(kindOf(l.why), l.fold), b: curve("amount", 1) }));
  let ymax = 1.9;
  for (const c of curves) for (let t = 0; t <= T_MAX; t += 0.25) ymax = Math.max(ymax, c.f(t) * 1.08);
  ymax = Math.min(ymax, 7);
  const x = (t) => L + ((W - L - R) * t) / T_MAX;
  const y = (v) => T + (H - T - B) * (1 - Math.min(v, ymax) / ymax);
  const path = (f) => Array.from({ length: 97 }, (_, i) => (i * T_MAX) / 96).map((t, i) => `${i ? "L" : "M"}${x(t).toFixed(1)},${y(f(t)).toFixed(1)}`).join("");
  const MEC = 0.25, MTC = 1.3; // therapeutic window relative to the usual peak (concept)
  const nameOf = (k) => drugs.find((d) => d.key === k)?.name || k;
  return (
    <div className="bj-panel">
      <div className="bj-ptitle">{title}<span>개념도</span></div>
      <svg viewBox={`0 0 ${W} ${H}`} className="bj-pk">
        <defs>
          <linearGradient id="tox" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ff2d55" stopOpacity="0.28" /><stop offset="1" stopColor="#ff2d55" stopOpacity="0.06" /></linearGradient>
        </defs>
        <rect x={L} y={T} width={W - L - R} height={y(MTC) - T} fill="url(#tox)" />
        <rect x={L} y={y(MTC)} width={W - L - R} height={y(MEC) - y(MTC)} fill="rgba(0,229,160,0.08)" />
        <line x1={L} x2={W - R} y1={y(MTC)} y2={y(MTC)} className="pk-lim tox" />
        <line x1={L} x2={W - R} y1={y(MEC)} y2={y(MEC)} className="pk-lim" />
        <text x={W - R - 2} y={y(MTC) - 3} className="pk-note tox" textAnchor="end">독성 범위</text>
        <text x={W - R - 2} y={(y(MTC) + y(MEC)) / 2 + 3} className="pk-note ok" textAnchor="end">치료 범위</text>
        {[0, 6, 12, 18, 24].map((t) => (
          <g key={t}><line x1={x(t)} x2={x(t)} y1={H - B} y2={H - B + 3} className="pk-ax" />
            <text x={x(t)} y={H - B + 13} className="pk-tick" textAnchor="middle">{t}h</text></g>
        ))}
        <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="pk-ax" />
        <line x1={L} x2={L} y1={T} y2={H - B} className="pk-ax" />
        <text x={10} y={(H - B + T) / 2} className="pk-tick" transform={`rotate(-90 10 ${(H - B + T) / 2})`} textAnchor="middle">혈중 농도</text>
        {curves.map(({ l, f, b }) => (
          <g key={l.drug}>
            <path d={path(b)} className="pk-base" style={{ stroke: colorOf(l.drug) }} />
            <path d={path(f)} className="pk-line" style={{ stroke: colorOf(l.drug) }} />
          </g>
        ))}
      </svg>
      <div className="bj-plegend">
        {curves.map(({ l }) => (
          <span key={l.drug}><i style={{ background: colorOf(l.drug) }} />{nameOf(l.drug)}
            <b className={l.dir === "up" ? "up" : l.dir === "down" ? "down" : ""}>{l.fold_label || (mode === "excrete" ? "정상 배출" : "변화 없음")}</b></span>
        ))}
        <span className="muted">점선 = 단독 복용 · 실선 = 이 조합·이 환자</span>
      </div>
    </div>
  );
}

export function MechanismChain({ problems }) {
  const list = (problems || []).filter((p) => p.chain).slice(0, 4);
  const [i, setI] = useState(0);
  useEffect(() => {
    setI(0);
    if (list.length < 2) return undefined;
    const t = setInterval(() => setI((k) => (k + 1) % list.length), 4500);
    return () => clearInterval(t);
  }, [problems]); // eslint-disable-line react-hooks/exhaustive-deps
  const p = list[i];
  if (!p) return <div className="bj-panel"><div className="bj-ptitle">기전 경로</div><div className="bj-empty">확인된 위험 경로가 없습니다.</div></div>;
  return (
    <div className={`bj-panel chain sev-${p.sev}`}>
      <div className="bj-ptitle">기전 경로 (Mechanism)<span>{i + 1}/{list.length}</span></div>
      <ol className="bj-chain">
        {p.chain.map((c, k) => (
          <li key={k} className={`st-${k}`} style={{ animationDelay: `${k * 0.35}s` }}>
            <em>{c.k}</em><span>{c.v}</span>
          </li>
        ))}
      </ol>
      {list.length > 1 && (
        <div className="bj-cdots">{list.map((_, k) => <button key={k} className={k === i ? "on" : ""} onClick={() => setI(k)} aria-label={`경로 ${k + 1}`} />)}</div>
      )}
    </div>
  );
}
