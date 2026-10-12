import React, { useEffect, useState } from "react";

/* Side panels of the journey caption: a concentration-time sketch (blood / excretion) and the mechanism chain of a
   finding (problem stage). Both are drawn from the analysis data; the curve is a concept sketch scaled by the
   evidence category (e.g. FDA 'strong inhibitor x sensitive substrate' = AUC >= 5x), not a measured concentration. */

/* One-compartment oral model per drug: elimination from its label half-life, absorption solved so the peak falls at
   its label Tmax (extended-release products: their own, later Tmax -> flatter curve). Repeated dosing = the same dose
   curve added every dosing interval. 'This combination' changes clearance (enzyme block, kidneys: ke / fold) or the
   amount absorbed (chelation, duplicated product: x fold). Concentrations are relative to the usual peak. */

/** absorption rate giving the peak at tmax for elimination rate ke (ka < ke = absorption-limited, typical of ER) */
function solveKa(tmax, ke) {
  const g = (ka) => Math.log(ka / ke) / (ka - ke);
  if (Math.abs(tmax - 1 / ke) < 1e-6) return ke * 1.0001;
  let lo, hi;
  if (tmax < 1 / ke) { lo = ke * 1.0001; hi = 200; } else { lo = 1e-5; hi = ke * 0.9999; }
  for (let i = 0; i < 80; i++) { const m = (lo + hi) / 2; if (g(m) > tmax) lo = m; else hi = m; }
  return (lo + hi) / 2;
}
const one = (t, ka, ke) => (t <= 0 ? 0 : (ka / (ka - ke)) * (Math.exp(-ke * t) - Math.exp(-ka * t)));

function drugModel(d, lv) {
  const T = Math.max(0.1, d?.pk?.tmax_h || 1.5), H = Math.max(0.2, d?.pk?.t_half_h || 6);
  const ke = Math.LN2 / H;
  const ka = solveKa(T, ke);
  let amp = 1, keX = ke;
  if (lv?.fold) {
    if (/흡수|중복|최대량/.test(lv.why || "")) amp = lv.fold;
    else keX = ke / lv.fold;
  }
  const r = d?.regimen || {};
  const tau = r.weekly ? 168 : 24 / Math.max(1, r.per_day || 1);
  return { T, H, ke, ka, keX, amp, tau, days: r.days, regimen: r };
}

/** concentration at time t (h) for doses every tau until stop (h); keFor/ampFor pick baseline or this combination */
function conc(m, t, repeat, stop, mine) {
  const ke = mine ? m.keX : m.ke, a = mine ? m.amp : 1;
  if (!repeat) return a * one(t, m.ka, ke);
  let c = 0;
  for (let td = 0; td <= Math.min(t, stop - 1e-9); td += m.tau) c += one(t - td, m.ka, ke);
  return a * c;
}

const fmtT = (h) => (h < 48 ? `${Math.round(h)}h` : `${Math.round(h / 24)}일`);

export function PKChart({ levels, drugs, colorOf, title, mode, defaultView = "single" }) {
  const [view, setView] = useState(defaultView);
  useEffect(() => setView(defaultView), [defaultView, title]);
  const W = 300, H = 168, L = 34, B = 22, T = 14, R = 8;
  const rows = (levels || []).filter((l) => l.fold && Math.abs(Math.log(l.fold)) > 0.05).slice(0, 2);
  const shown = rows.length ? rows : (drugs || []).filter((d) => d.kind === "drug").slice(0, 1).map((d) => ({ drug: d.key, fold: 1 }));
  const byKey = Object.fromEntries((drugs || []).map((d) => [d.key, d]));
  // a recreational / controlled substance: one use only - no repeated-dosing schedule (harm reduction, not a regimen)
  const ctl = shown.some((l) => byKey[l.drug]?.controlled);
  const repeat = view === "repeat" && !ctl;
  const models = shown.map((l) => ({ l, m: drugModel(byKey[l.drug], l) }));
  // time window: one dose -> until ~97% gone; repeated -> to steady state (and the wash-out after the last dose)
  const effH = Math.max(...models.map(({ m }) => m.H * (m.ke / m.keX)), 1);
  const baseH = Math.max(...models.map(({ m }) => m.H), 1);
  let tEnd, stop = Infinity;
  if (!repeat) tEnd = Math.min(24 * 14, Math.max(24, Math.max(...models.map(({ m }) => m.T)) + 5 * effH));
  else {
    const given = Math.max(0, ...models.map(({ m }) => m.days || 0));
    const dosing = given || Math.min(14, Math.max(3, Math.ceil((5 * effH) / 24) + 1));
    const washout = mode === "excrete" || given ? Math.min(24 * 10, 5 * effH) : 0;
    stop = mode === "excrete" || given ? dosing * 24 : Infinity;
    tEnd = Math.min(24 * 21, dosing * 24 + washout);
  }
  const N = 240;
  const ts = Array.from({ length: N + 1 }, (_, i) => (i * tEnd) / N);
  // relative scale: the usual peak (single dose) / the usual steady-state peak (repeated) = 1
  const norm = Math.max(1e-9, ...models.map(({ m }) => Math.max(...ts.map((t) => conc(m, t, repeat, stop, false)))));
  const curves = models.map(({ l, m }) => ({ l, m, mine: ts.map((t) => conc(m, t, repeat, stop, true) / norm),
    base: ts.map((t) => conc(m, t, repeat, stop, false) / norm) }));
  let ymax = 1.9;
  for (const c of curves) ymax = Math.max(ymax, ...c.mine.map((v) => v * 1.08));
  ymax = Math.min(ymax, 8);
  const x = (t) => L + ((W - L - R) * t) / tEnd;
  const y = (v) => T + (H - T - B) * (1 - Math.min(v, ymax) / ymax);
  const path = (vals) => vals.map((v, i) => `${i ? "L" : "M"}${x(ts[i]).toFixed(1)},${y(v).toFixed(1)}`).join("");
  const MEC = 0.25, MTC = 1.3; // therapeutic window relative to the usual peak (concept)
  const step = tEnd <= 30 ? 6 : tEnd <= 72 ? 12 : tEnd <= 24 * 7 ? 24 : 72;
  const ticks = Array.from({ length: Math.floor(tEnd / step) + 1 }, (_, i) => i * step);
  const ss = 5 * effH;
  const nameOf = (k) => drugs.find((d) => d.key === k)?.name || k;
  const regText = (m) => (m.regimen.weekly ? "주 1회" : `하루 ${m.regimen.per_day || 1}회`)
    + (m.regimen.source === "입력값" ? "" : "(추정)");
  return (
    <div className="bj-panel">
      <div className="bj-ptitle">{title}
        <span className="bj-ptabs">
          <button className={!repeat ? "on" : ""} onClick={() => setView("single")}>1회 복용</button>
          {!ctl && <button className={repeat ? "on" : ""} onClick={() => setView("repeat")}>반복 복용</button>}
        </span>
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="bj-pk" key={`${view}${title}`}>
        <defs>
          <linearGradient id="tox" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stopColor="#ff2d55" stopOpacity="0.28" /><stop offset="1" stopColor="#ff2d55" stopOpacity="0.06" /></linearGradient>
        </defs>
        <rect x={L} y={T} width={W - L - R} height={y(MTC) - T} fill="url(#tox)" />
        <rect x={L} y={y(MTC)} width={W - L - R} height={y(MEC) - y(MTC)} fill="rgba(0,229,160,0.08)" />
        <line x1={L} x2={W - R} y1={y(MTC)} y2={y(MTC)} className="pk-lim tox" />
        <line x1={L} x2={W - R} y1={y(MEC)} y2={y(MEC)} className="pk-lim" />
        <text x={W - R - 2} y={y(MTC) - 3} className="pk-note tox" textAnchor="end">독성 범위</text>
        <text x={W - R - 2} y={(y(MTC) + y(MEC)) / 2 + 3} className="pk-note ok" textAnchor="end">{ctl ? "보통 범위" : "치료 범위"}</text>
        {ticks.map((t) => (
          <g key={t}><line x1={x(t)} x2={x(t)} y1={H - B} y2={H - B + 3} className="pk-ax" />
            <text x={x(t)} y={H - B + 13} className="pk-tick" textAnchor="middle">{fmtT(t)}</text></g>
        ))}
        <line x1={L} x2={W - R} y1={H - B} y2={H - B} className="pk-ax" />
        <line x1={L} x2={L} y1={T} y2={H - B} className="pk-ax" />
        <text x={10} y={(H - B + T) / 2} className="pk-tick" transform={`rotate(-90 10 ${(H - B + T) / 2})`} textAnchor="middle">혈중 농도</text>
        {repeat && ss < (stop === Infinity ? tEnd : stop) && (
          <g><line x1={x(ss)} x2={x(ss)} y1={T} y2={H - B} className="pk-mark" />
            <text x={x(ss) + 2} y={T + 9} className="pk-note">정상상태 ≈{fmtT(ss)}</text></g>
        )}
        {repeat && stop < tEnd && (
          <g><line x1={x(stop)} x2={x(stop)} y1={T} y2={H - B} className="pk-mark stop" />
            <text x={x(stop) + 2} y={T + 19} className="pk-note stop">복용 중단</text></g>
        )}
        {curves.map(({ l, base, mine }) => (
          <g key={l.drug}>
            <path d={path(base)} className="pk-base" style={{ stroke: colorOf(l.drug) }} />
            <path d={path(mine)} className="pk-line" style={{ stroke: colorOf(l.drug) }} />
          </g>
        ))}
      </svg>
      <div className="bj-plegend">
        {curves.map(({ l, m }) => {
          const d = byKey[l.drug];
          return (
            <span key={l.drug}><i style={{ background: colorOf(l.drug) }} />{nameOf(l.drug)}
              {d?.release === "ER" && <em className="rel">서방형</em>}{d?.release === "DR" && <em className="rel">장용형</em>}
              <b className={l.dir === "up" ? "up" : l.dir === "down" ? "down" : ""}>{l.fold_label || (mode === "excrete" ? "정상 배출" : "변화 없음")}</b>
              <small>Tmax {fmtT(m.T)} · t½ {m.H < 48 ? `${m.H.toFixed(m.H < 10 ? 1 : 0)}h` : fmtT(m.H)}{repeat ? ` · ${regText(m)}` : ""}</small></span>
          );
        })}
        <span className="muted">점선 = 단독 복용 · 실선 = 이 조합·이 환자 · {repeat ? "평소 정상상태 최고치 = 1" : "평소 최고치 = 1"}
          {byKey[shown[0]?.drug]?.pk?.source ? ` · 수치 출처: ${byKey[shown[0].drug].pk.source}` : ""}</span>
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
