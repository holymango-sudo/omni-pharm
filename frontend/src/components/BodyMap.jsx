import React, { useState } from "react";
import { fx, pct } from "../api.js";

const BODY =
  "M108,86 L108,100 C90,104 70,106 58,116 C48,124 46,140 44,160 L36,250 C34,270 32,290 30,305 L40,307 " +
  "C46,290 52,268 56,250 L66,170 L70,175 C70,215 72,245 74,270 C72,320 74,370 78,440 L100,442 C104,380 110,330 " +
  "116,290 L124,290 C130,330 136,380 140,442 L162,440 C166,370 168,320 166,270 C168,245 170,215 170,175 L174,170 " +
  "L184,250 C188,268 194,290 200,307 L210,305 C208,290 206,270 204,250 L196,160 C194,140 192,124 182,116 " +
  "C170,106 150,104 132,100 L132,86 Z";

const ORGAN_SHAPES = {
  brain: [<ellipse key="b" cx="120" cy="44" rx="22" ry="24" />,
    <path key="b2" d="M104,36 C112,30 118,40 120,32 C124,40 130,30 136,36 M102,50 C110,46 116,56 120,48 C126,56 132,46 138,50" fill="none" />],
  lung: [<path key="l1" d="M115,122 C100,120 88,140 88,165 C88,180 95,188 112,186 Z" />,
    <path key="l2" d="M125,122 C140,120 152,140 152,165 C152,180 145,188 128,186 Z" />],
  heart: [<path key="h" d="M120,150 C128,140 143,146 141,159 C139,171 129,179 123,186 C118,176 109,170 111,159 C112,150 117,148 120,150 Z" />],
  liver: [<path key="v" d="M82,190 C100,185 128,187 137,196 C131,211 112,215 95,215 C86,213 82,204 82,190 Z" />],
  gi: [<path key="s" d="M138,197 C153,195 158,211 150,223 C142,231 130,227 130,214 Z" />,
    <path key="i" d="M93,229 C110,223 132,223 150,231 C154,251 150,263 120,267 C92,265 86,251 93,229 Z" />,
    <path key="i2" d="M100,240 C112,236 128,246 142,240 M100,252 C114,258 126,248 142,254" fill="none" />],
  kidney: [<ellipse key="k1" cx="99" cy="223" rx="7" ry="12" />, <ellipse key="k2" cx="141" cy="223" rx="7" ry="12" />],
  blood: [<path key="a" d="M120,186 L120,270 M114,270 L100,432 M126,270 L140,432 M64,150 L42,296 M176,150 L198,296 M108,96 L72,130 M132,96 L168,130" fill="none" />],
};

// where the organ legend leader lines start (svg coords)
const ANCHOR = { brain: [120, 44], heart: [128, 163], lung: [96, 160], liver: [100, 202], gi: [132, 248],
  kidney: [141, 223], blood: [44, 250], systemic: [196, 200] };

// organ colours come from the AI models (statistical signals), so the words say "signal", not "danger"
const WORD = { high: "강한 신호", caution: "약한 신호", normal: "정상" };

export default function BodyMap({ organs, expert }) {
  const [hover, setHover] = useState(null);
  const lv = (k) => organs?.[k]?.level || "normal";
  const sys = lv("systemic");
  const h = hover && organs?.[hover];
  return (
    <div className="bodymap">
      <svg viewBox="0 0 240 460" className="body-svg">
        <defs>
          <filter id="glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="3.2" result="b" />
            <feMerge><feMergeNode in="b" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <pattern id="scan" width="4" height="4" patternUnits="userSpaceOnUse">
            <path d="M0,0 L4,0" stroke="#00e5ff" strokeOpacity="0.07" />
          </pattern>
        </defs>
        <ellipse cx="120" cy="48" rx="30" ry="36" className={`body-outline lv-${sys}`} />
        <path d={BODY} className={`body-outline lv-${sys}`} />
        <path d={BODY} fill="url(#scan)" />
        {Object.entries(ORGAN_SHAPES).map(([k, shapes]) => (
          <g key={k} className={`organ lv-${lv(k)} ${hover === k ? "hover" : ""}`}
            filter={lv(k) !== "normal" ? "url(#glow)" : undefined}
            onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
            {shapes}
          </g>
        ))}
        {organs && Object.entries(ANCHOR).map(([k, [x, y]]) =>
          lv(k) !== "normal" ? <circle key={k} cx={x} cy={y} r="3" className={`ping lv-${lv(k)}`} /> : null)}
      </svg>
      {organs && <div className="muted small">장기별 AI 예측 신호 (마우스를 올리면 상세)</div>}
      <div className="organ-legend">
        {organs ? Object.entries(organs).map(([k, o]) => (
          <div key={k} className={`organ-row lv-${o.level}`} onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
            <span className="dot" />
            <span className="oname">{o.name}</span>
            <span className="oval">{expert ? (o.max_lift >= 1 ? `×${fx(o.max_lift)}` : "—") : WORD[o.level]}</span>
          </div>
        )) : <div className="muted small">분석 후 장기별 위험이 표시됩니다</div>}
        {h && (
          <div className="organ-tip">
            <b>{h.name}</b>
            {h.events.length ? h.events.map((e) => (
              <div key={e.ko}>{e.ko} {expert && <span className="mono">{pct(e.prob)} · ×{fx(e.lift)}</span>}</div>
            )) : <div className="muted">특별히 늘어나는 위험 없음</div>}
            {h.pair_signals.length > 0 && <div className="muted">구조/보고 신호: {h.pair_signals.join(", ")}</div>}
          </div>
        )}
      </div>
    </div>
  );
}
