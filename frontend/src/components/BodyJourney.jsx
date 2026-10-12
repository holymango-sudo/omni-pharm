import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { Html, Line } from "@react-three/drei";
import * as THREE from "three";
import Bloom from "./journey/Bloom.jsx";
import MolecularView, { molecularPlan } from "./journey/MolecularView.jsx";
import { MechanismChain, PKChart } from "./journey/Panels.jsx";
import {
  ARTERIES, COLON, GUT_LOOPS, STOMACH, V, VEINS, bodyGeometry, organGeometries, skeletonGeometry, vesselGeometry, xray,
} from "./journey/anatomy.js";

/* Animated journey of the drugs through an X-ray style body, driven entirely by `res.pathway`
   (src/omnipharm/journey.py): stomach -> gut -> liver enzymes -> blood -> targets -> problem organs -> excretion.
   The caption panel adds a molecular close-up, a concentration-time sketch or the mechanism chain. */

const now = () => performance.now() / 1000;
export const DRUG_COLORS = ["#00e5ff", "#ff4fd8", "#ffe14d", "#7dff6a", "#b98cff", "#ff9a3d", "#4da3ff", "#ff6b6b"];
const SEV_HEX = { high: "#ff2d55", mid: "#ffb020", low: "#ffd27a" };
export const STAGE_SEC = { intake: 6, absorb: 6.5, liver: 8, blood: 6.5, target: 8, problem: 10, excrete: 7 };

// anchor points of the body sites (person faces the camera: the person's right side is on the screen's left)
const SITE = {
  brain: V(0, 3.13, 0.05), eye: V(0.14, 3.0, 0.38), thyroid: V(0, 2.43, 0.19), lung: V(-0.45, 1.7, 0.1),
  heart: V(0.12, 1.42, 0.2), vessel: V(1.1, 0.9, 0.1), stomach: V(0.38, 0.8, 0.2), liver: V(-0.34, 0.82, 0.2),
  pancreas: V(0.18, 0.55, 0.08), intestine: V(0, -0.1, 0.28), kidney: V(0.4, 0.37, -0.12), bladder: V(0, -0.8, 0.22),
  muscle: V(-0.4, -1.75, 0.24), joint: V(0.42, -2.6, 0.18), skin: V(-1.15, 0.2, 0.18), bone: V(0.42, -1.75, 0.06),
  reproductive: V(0, -0.88, 0.18), infection: V(-0.8, 2.05, 0.44),
};
const W = {
  sky: V(0, 3.9, 1.8), mouth: V(0, 2.82, 0.4), throat: V(0, 2.46, 0.14), esoph: V(0.04, 1.6, -0.02), cardia: STOMACH[0],
  portal: V(-0.16, 0.5, 0.12), ivc: V(-0.04, 1.25, 0.06), bile: V(-0.2, 0.6, 0.3), kidneyL: V(-0.4, 0.31, -0.12),
  injOut: V(-0.95, 0.3, 0.8), inj: V(-0.55, 0.2, 0.44), vein: V(-0.3, 0.6, 0.14), trachea: V(0, 2.18, 0.14),
  lungR: V(0.43, 1.7, 0.1), ureter: V(0.2, -0.3, -0.05), exitU: V(0, -1.2, 0.24), bowelExit: V(0.02, -0.95, 0.06),
};
/** the female frame has narrower shoulders: arm points move inward */
const armFit = (sex) => (v) => (sex === "F" && Math.abs(v.x) > 0.62 && v.y > -1.0 ? V(v.x * 0.9, v.y, v.z) : v);
const AORTA = ARTERIES[0];
const IVC = VEINS[0];
const GUT_PATH = [...STOMACH.slice(2), ...GUT_LOOPS.filter((_, i) => i % 2 === 0), W.portal, SITE.liver];
const LOOP = [...AORTA, ...IVC.slice(1)];
const UPPER = new Set(["brain", "eye", "thyroid"]);

/** heart -> organ along the arteries */
function arteryTo(site) {
  const s = SITE[site] || SITE.vessel;
  const desc = AORTA.slice(0, 5);
  if (site === "heart") return [W.ivc, SITE.heart];
  if (site === "lung" || site === "infection") return [SITE.heart, V(-0.18, 1.62, 0.08), s];
  if (UPPER.has(site)) return [...ARTERIES[3].slice(0, 3), s].map((p, i) => (i === 0 ? SITE.heart : p));
  if (site === "vessel") return [SITE.heart, ...ARTERIES[5].slice(0, 4), s];
  if (site === "skin") return [SITE.heart, ...ARTERIES[6].slice(0, 4), s];
  if (site === "muscle") return [SITE.heart, ...AORTA.slice(1), ...ARTERIES[2].slice(1, 4), s];
  if (site === "joint" || site === "bone") return [SITE.heart, ...AORTA.slice(1), ...ARTERIES[1].slice(1, 4), s];
  if (site === "kidney") return [SITE.heart, ...desc.slice(1), ...ARTERIES[7].slice(1)];
  if (site === "liver") return [SITE.heart, ...desc.slice(1, 4), V(-0.1, 0.75, -0.1), s];
  return [SITE.heart, ...desc.slice(1), V(s.x * 0.5, s.y + 0.15, s.z * 0.4), s];
}

// ------------------------------------------------------------------ journey state (shared by the scene and the HUD)
export function useJourney(pathway) {
  const stages = pathway?.stages || [];
  const [idx, setIdx] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [since, setSince] = useState(now);
  useEffect(() => { setIdx(0); setPlaying(true); }, [pathway]);
  useEffect(() => { setSince(now()); }, [idx, pathway]);
  useEffect(() => {
    if (!playing || !stages.length) return undefined;
    if (idx >= stages.length - 1) { setPlaying(false); return undefined; }
    const t = setTimeout(() => setIdx((i) => Math.min(i + 1, stages.length - 1)), (STAGE_SEC[stages[idx]?.id] || 6) * 1000);
    return () => clearTimeout(t);
  }, [idx, playing, stages.length]); // eslint-disable-line react-hooks/exhaustive-deps
  return {
    stages, idx, stage: stages[idx], playing, since,
    go: (i) => { setIdx(Math.max(0, Math.min(stages.length - 1, i))); setPlaying(false); },
    toggle: () => { if (idx >= stages.length - 1) { setIdx(0); setPlaying(true); } else setPlaying((p) => !p); },
  };
}

// ------------------------------------------------------------------ time after the dose
export const fmtH = (h) => {
  if (h < 1) return `${Math.max(1, Math.round(h * 60))}분`;
  if (h < 48) {
    let hh = Math.floor(h), mm = Math.round((h - hh) * 12) * 5;
    if (mm === 60) { hh += 1; mm = 0; }
    return `${hh}시간${mm && hh < 10 ? ` ${mm}분` : ""}`;
  }
  const d = h / 24;
  return d < 14 ? `${d.toFixed(1).replace(".0", "")}일` : `${Math.round(d / 7)}주`;
};
/** running clock text: '0분' -> '1시간 25분' -> '2일 4시간' */
const fmtClock = (h) => {
  if (h < 1) return `${Math.round(h * 60)}분`;
  if (h < 48) return `${Math.floor(h)}시간 ${String(Math.floor((h % 1) * 60)).padStart(2, "0")}분`;
  return `${Math.floor(h / 24)}일 ${Math.floor(h % 24)}시간`;
};

/** hours after the dose shown right now: runs through the current stage's time window during the stage */
export function useElapsed(j) {
  const [, tick] = useState(0);
  useEffect(() => {
    const t = setInterval(() => tick((x) => x + 1), 100);
    return () => clearInterval(t);
  }, []);
  const w = j?.stage?.time;
  if (!w) return null;
  const p = Math.min(1, (now() - j.since) / ((STAGE_SEC[j.stage.id] || 6) * 0.85));
  const e = 1 - (1 - p) ** 2;
  const a = Math.log(w.start_h + 0.05), b = Math.log(w.end_h + 0.05); // log time: minutes and days both read
  return Math.max(0, Math.exp(a + (b - a) * e) - 0.05);
}

export function JourneyClock({ pathway, j, compact }) {
  const h = useElapsed(j);
  const c = pathway?.clock;
  if (!c || h == null || !j.stage?.time) return null;
  const changed = Math.abs((c.fold || 1) - 1) > 0.15;
  return (
    <div className={`bj-clock ${compact ? "compact" : ""} sev-${j.stage.sev}`}>
      <div className="bj-clock-k">⏱ {c.dose_word || "복용"} 후 경과 시간</div>
      <div className="bj-clock-v">{fmtClock(h)}</div>
      <div className="bj-clock-s">{j.stage.time.label}</div>
      <div className="bj-clock-f">
        기준: <b>{c.name}</b> · 최고농도 {fmtH(c.tmax_h)} · 반감기 {fmtH(c.t_half_h)}
        {changed && <> → <b className={c.fold > 1 ? "up" : "down"}>{fmtH(c.t_half_eff_h)}</b></>}
        <i> ({c.source})</i>
      </div>
    </div>
  );
}

/** log time axis 0 ... weeks with every stage's window, Tmax, 97% elimination and the running playhead */
function TimeRuler({ pathway, j }) {
  const h = useElapsed(j);
  const c = pathway.clock;
  if (!c) return null;
  const cap = Math.min(24 * 28, Math.max(24, ...pathway.stages.map((s) => s.time?.end_h || 0)));
  const x = (v) => (Math.log1p(Math.min(v, cap) * 4) / Math.log1p(cap * 4)) * 100;
  const ticks = [[0, "0"], [0.5, "30분"], [1, "1시간"], [3, "3시간"], [6, "6시간"], [12, "12시간"], [24, "1일"], [72, "3일"],
    [168, "1주"], [336, "2주"], [672, "4주"]].filter(([v]) => v <= cap);
  const out = 5 * c.t_half_eff_h;
  return (
    <div className="bj-ruler" title="복용 후 시간(로그 눈금)">
      <div className="bj-rtrack">
        {pathway.stages.map((s, i) => s.time && (
          <span key={s.id} className={`seg ${i === j.idx ? "on" : ""} sev-${s.sev}`}
            style={{ left: `${x(s.time.start_h)}%`, width: `${Math.max(0.8, x(s.time.end_h) - x(s.time.start_h))}%` }} />
        ))}
        <em className="mk" style={{ left: `${x(c.tmax_h)}%` }}>최고농도</em>
        {out <= cap && <em className="mk out" style={{ left: `${x(out)}%` }}>97% 배출</em>}
        {h != null && <b className="head" style={{ left: `${x(h)}%` }} />}
      </div>
      <div className="bj-rticks">{ticks.map(([v, l]) => (
        <i key={v} className={[0.5, 6, 12, 72, 336].includes(v) ? "minor" : ""} style={{ left: `${x(v)}%` }}>{l}</i>))}</div>
    </div>
  );
}

// ------------------------------------------------------------------ particles
function dotTexture() {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grd = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grd.addColorStop(0, "rgba(255,255,255,1)");
  grd.addColorStop(0.25, "rgba(255,255,255,0.85)");
  grd.addColorStop(0.6, "rgba(255,255,255,0.18)");
  grd.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grd;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}
const DOT = typeof document !== "undefined" ? dotTexture() : null;

const MAXP = 3200;
const MAXH = 48;
/** streams: [{color, points, mode: flow|bind|stuck|pile|swarm, count, speed, radius}] */
function Streams({ streams, size }) {
  const ref = useRef();
  const headRef = useRef();
  const lineRefs = useRef([]);
  const buf = useMemo(() => ({
    pos: new Float32Array(MAXP * 3).fill(9999), col: new Float32Array(MAXP * 3),
    hpos: new Float32Array(MAXH * 3).fill(9999), hcol: new Float32Array(MAXH * 3),
    seed: Float32Array.from({ length: MAXP }, Math.random),
    jit: Float32Array.from({ length: MAXP * 3 }, () => Math.random() * 2 - 1),
  }), []);
  const plan = useMemo(() => {
    let i = 0;
    const list = [];
    for (const s of streams) {
      const n = Math.min(Math.round(s.count), MAXP - i);
      if (n <= 0 || s.points.length < 2) continue;
      const curve = new THREE.CatmullRomCurve3(s.points, false, "centripetal");
      list.push({ ...s, curve, start: i, n, end: s.points[s.points.length - 1].clone() });
      const c = new THREE.Color(s.color);
      for (let k = i; k < i + n; k++) { buf.col[k * 3] = c.r; buf.col[k * 3 + 1] = c.g; buf.col[k * 3 + 2] = c.b; }
      i += n;
    }
    for (let k = i; k < MAXP; k++) buf.pos[k * 3] = 9999;
    // a bright bolus leads each stream (two on circulating routes): the movement reads at any screen size
    const heads = [];
    for (const L of list) for (let h = 0; h < (L.mode === "flow" ? 2 : 1) && heads.length < MAXH; h++) heads.push({ L, h });
    heads.forEach(({ L }, k) => {
      const c = new THREE.Color(L.color).lerp(new THREE.Color("#ffffff"), 0.4);
      buf.hcol[k * 3] = c.r; buf.hcol[k * 3 + 1] = c.g; buf.hcol[k * 3 + 2] = c.b;
    });
    for (let k = heads.length; k < MAXH; k++) buf.hpos[k * 3] = 9999;
    // dashed route guide per distinct path of this stage
    const seen = new Set();
    const routes = [];
    for (const L of list) {
      const id = `${L.color}|${L.points.map((v) => v.toArray().map((x) => x.toFixed(2)).join(",")).join(";")}`;
      if (seen.has(id) || routes.length >= 14) continue;
      seen.add(id);
      routes.push({ color: L.color, pts: L.curve.getPoints(80) });
    }
    return { list, heads, routes, t0: now() };
  }, [streams, buf]);
  useEffect(() => {
    if (ref.current) ref.current.geometry.attributes.color.needsUpdate = true;
    if (headRef.current) headRef.current.geometry.attributes.color.needsUpdate = true;
  }, [plan]);
  const p = useMemo(() => new THREE.Vector3(), []);
  useFrame((_, dt) => {
    const t = now() - plan.t0;
    const { pos, seed, jit, hpos } = buf;
    for (const L of plan.list) {
      const sp = L.speed;
      for (let k = 0; k < L.n; k++) {
        const i = L.start + k;
        const s = seed[i], jx = jit[i * 3], jy = jit[i * 3 + 1], jz = jit[i * 3 + 2];
        if (L.mode === "flow") {
          const raw = t * sp - s;
          if (raw < 0) { pos[i * 3] = 9999; continue; }
          L.curve.getPointAt(raw % 1, p);
          const w = L.radius * 0.45;
          p.x += jx * w; p.y += jy * w; p.z += jz * w;
        } else {
          const u = Math.min(1, Math.max(0, t * sp * 1.25 - s * 1.3));
          if (u <= 0) { pos[i * 3] = 9999; continue; }
          if (u < 1) {
            L.curve.getPointAt(1 - (1 - u) * (1 - u), p);
            p.x += jx * 0.025; p.y += jy * 0.025; p.z += jz * 0.025;
          } else {
            let R = L.radius * (0.5 + 0.5 * Math.abs(jx));
            let w = 1.4;
            if (L.mode === "stuck") { R = L.radius * (0.25 + 0.3 * Math.abs(jx)); w = 0.25; }
            if (L.mode === "pile") R = L.radius * (0.45 + Math.min(1, Math.max(0, t - 1.2) / 4) * 1.1 * Math.abs(jy));
            if (L.mode === "swarm") w = 3.4;
            const a = s * 6.283 + t * w * (jz > 0 ? 1 : -1);
            p.copy(L.end);
            p.x += Math.cos(a) * R; p.y += Math.sin(a) * R * 0.85; p.z += Math.sin(a * 1.7 + jy * 3) * R * 0.6;
            if (L.mode === "swarm") { p.x += (Math.random() - 0.5) * 0.03; p.y += (Math.random() - 0.5) * 0.03; }
          }
        }
        pos[i * 3] = p.x; pos[i * 3 + 1] = p.y; pos[i * 3 + 2] = p.z;
      }
    }
    plan.heads.forEach(({ L, h }, k) => {
      const u = L.mode === "flow" ? (t * L.speed + h * 0.5) % 1 : Math.min(1, t * L.speed * 1.25);
      L.curve.getPointAt(u, p);
      hpos[k * 3] = p.x; hpos[k * 3 + 1] = p.y; hpos[k * 3 + 2] = p.z;
    });
    if (ref.current) ref.current.geometry.attributes.position.needsUpdate = true;
    if (headRef.current) {
      headRef.current.geometry.attributes.position.needsUpdate = true;
      headRef.current.material.size = size * 3.2 * (1 + 0.15 * Math.sin(t * 7));
    }
    lineRefs.current.forEach((l) => { if (l?.material) l.material.dashOffset -= dt * 0.35; });
  });
  return (
    <group>
      {plan.routes.map((r, i) => (
        <Line key={`${plan.t0}${i}`} ref={(el) => { lineRefs.current[i] = el; }} points={r.pts} color={r.color} lineWidth={1.1}
          dashed dashSize={0.06} gapSize={0.05} transparent opacity={0.4} depthWrite={false} />
      ))}
      <points ref={ref} frustumCulled={false} renderOrder={6}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={MAXP} array={buf.pos} itemSize={3} />
          <bufferAttribute attach="attributes-color" count={MAXP} array={buf.col} itemSize={3} />
        </bufferGeometry>
        <pointsMaterial size={size} map={DOT} vertexColors transparent opacity={0.95} depthWrite={false}
          blending={THREE.AdditiveBlending} sizeAttenuation />
      </points>
      <points ref={headRef} frustumCulled={false} renderOrder={7}>
        <bufferGeometry>
          <bufferAttribute attach="attributes-position" count={MAXH} array={buf.hpos} itemSize={3} />
          <bufferAttribute attach="attributes-color" count={MAXH} array={buf.hcol} itemSize={3} />
        </bufferGeometry>
        <pointsMaterial size={size * 3.2} map={DOT} vertexColors transparent depthWrite={false}
          blending={THREE.AdditiveBlending} sizeAttenuation />
      </points>
    </group>
  );
}

/** red (arterial) and blue (venous) blood always circulating in the vessels */
function BloodFlow({ size, sex }) {
  const ref = useRef();
  const data = useMemo(() => {
    const fit = armFit(sex);
    const curves = [...ARTERIES.map((p) => ({ c: new THREE.CatmullRomCurve3(p.map(fit)), art: true })),
      ...VEINS.map((p) => ({ c: new THREE.CatmullRomCurve3(p.map(fit)), art: false }))];
    const n = 520;
    const pos = new Float32Array(n * 3), col = new Float32Array(n * 3);
    const items = Array.from({ length: n }, (_, i) => {
      const cv = curves[i % curves.length];
      const c = new THREE.Color(cv.art ? "#ff3b5c" : "#3b6bff");
      col.set([c.r * 0.5, c.g * 0.5, c.b * 0.5], i * 3);
      return { cv, s: Math.random(), sp: 0.05 + Math.random() * 0.05 };
    });
    return { pos, col, items };
  }, [sex]);
  const p = useMemo(() => new THREE.Vector3(), []);
  useFrame(() => {
    const t = now();
    data.items.forEach((it, i) => {
      const u = (it.s + t * it.sp) % 1;
      it.cv.c.getPointAt(it.cv.art ? u : 1 - u, p);
      data.pos[i * 3] = p.x; data.pos[i * 3 + 1] = p.y; data.pos[i * 3 + 2] = p.z;
    });
    if (ref.current) ref.current.geometry.attributes.position.needsUpdate = true;
  });
  return (
    <points ref={ref} frustumCulled={false} renderOrder={4}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" count={data.pos.length / 3} array={data.pos} itemSize={3} />
        <bufferAttribute attach="attributes-color" count={data.col.length / 3} array={data.col} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial size={size * 0.55} map={DOT} vertexColors transparent opacity={0.8} depthWrite={false}
        blending={THREE.AdditiveBlending} sizeAttenuation />
    </points>
  );
}

// ------------------------------------------------------------------ anatomy
const ORGAN_COL = {
  brain: "#b9a6ff", eye: "#c7f2ff", thyroid: "#a5f3fc", lung: "#62f0d8", heart: "#ff8fb4", stomach: "#7ee7ff",
  liver: "#4fb3ff", pancreas: "#b5f5ff", intestine: "#3fe0c5", kidney: "#8f9bff", bladder: "#9cc9ff",
  muscle: "#9ad1ff", joint: "#c4b5fd", skin: "#99f6e4", bone: "#e8f4ff", reproductive: "#f0abfc", infection: "#bef264",
};
const MINOR = new Set(["eye", "thyroid", "muscle", "joint", "skin", "bone", "reproductive", "infection", "pancreas"]);
const SEV_COLOR = { high: new THREE.Color("#ff2d55"), mid: new THREE.Color("#ffb020") };

function Anatomy({ sites, sex, pregnant }) {
  const geo = useMemo(() => {
    const organs = organGeometries(sex, pregnant);
    organs.heart.computeBoundingBox();
    const hc = organs.heart.boundingBox.getCenter(new THREE.Vector3());
    organs.heart.translate(-hc.x, -hc.y, -hc.z);
    const fit = armFit(sex);
    return { body: bodyGeometry(sex), skel: skeletonGeometry(sex), organs, heartAt: hc,
      art: vesselGeometry(ARTERIES.map((p) => p.map(fit)), 0.018), vein: vesselGeometry(VEINS.map((p) => p.map(fit)), 0.02) };
  }, [sex, pregnant]);
  // deliberately dim: the body is the backdrop, the drugs and the affected organs are the subject
  const mats = useMemo(() => ({
    body: xray({ color: "#2b9fd0", opacity: 0.4, power: 3.0, fill: 0.004, fade: -4.2 }),
    skel: xray({ color: "#bfe3f5", opacity: 0.17, power: 1.5, fill: 0.03, fade: -4.2, lines: 0.2 }),
    art: xray({ color: "#e0445f", opacity: 0.3, power: 1.2, fill: 0.2, fade: -4.2, lines: 0 }),
    vein: xray({ color: "#4268d8", opacity: 0.26, power: 1.2, fill: 0.2, fade: -4.2, lines: 0 }),
    organs: Object.fromEntries(Object.keys(ORGAN_COL).map((k) => [k, xray({ color: ORGAN_COL[k], opacity: MINOR.has(k) ? 0.12 : 0.3,
      power: 1.9, fill: 0.03, lines: 0.35 })])),
  }), []);
  const base = useMemo(() => Object.fromEntries(Object.entries(ORGAN_COL).map(([k, c]) => [k, new THREE.Color(c)])), []);
  const heart = useRef();
  useFrame(() => {
    const t = now();
    const scan = -4.2 + ((t * 0.3) % 1) * 8.2;
    mats.body.uniforms.uScan.value = scan;
    mats.skel.uniforms.uScan.value = scan;
    for (const [k, m] of Object.entries(mats.organs)) {
      const st = sites[k];
      const sevC = st?.sev === "high" || st?.sev === "mid" ? SEV_COLOR[st.sev] : null;
      m.uniforms.uColor.value.lerp(sevC || base[k], 0.08);
      const op = sevC ? 0.95 : st?.active ? 0.75 : k === "infection" ? 0.04 : MINOR.has(k) ? 0.1 : 0.28;
      m.uniforms.uOpacity.value += (op - m.uniforms.uOpacity.value) * 0.08;
      m.uniforms.uFill.value = sevC ? 0.12 : st?.active ? 0.07 : 0.025;
      m.uniforms.uPulse.value = st?.sev === "high" ? 0.55 + 0.55 * Math.sin(t * 6.5)
        : st?.sev === "mid" ? 0.3 + 0.3 * Math.sin(t * 3.5) : st?.active ? 0.12 + 0.1 * Math.sin(t * 2.2) : 0;
    }
    const vs = sites.vessel;
    for (const m of [mats.art, mats.vein]) {
      m.uniforms.uPulse.value = vs?.sev === "high" ? 0.5 + 0.5 * Math.sin(t * 6.5) : vs?.sev === "mid" ? 0.3 + 0.3 * Math.sin(t * 3.5)
        : vs?.active ? 0.15 : 0;
      m.uniforms.uOpacity.value = vs?.sev ? 0.8 : vs?.active ? 0.55 : 0.28;
    }
    if (vs?.sev) mats.art.uniforms.uColor.value.lerp(SEV_COLOR[vs.sev] || SEV_COLOR.mid, 0.05);
    if (heart.current) heart.current.scale.setScalar(1 + 0.08 * Math.max(0, Math.sin(t * 7.2)) ** 6);
  });
  return (
    <group>
      <mesh geometry={geo.body} material={mats.body} renderOrder={1} />
      <mesh geometry={geo.skel} material={mats.skel} renderOrder={1} />
      <mesh geometry={geo.art} material={mats.art} renderOrder={2} />
      <mesh geometry={geo.vein} material={mats.vein} renderOrder={2} />
      {Object.entries(geo.organs).map(([k, g]) => (k === "heart"
        ? <mesh key={k} ref={heart} geometry={g} material={mats.organs[k]} position={geo.heartAt} renderOrder={3} />
        : <mesh key={k} geometry={g} material={mats.organs[k]} renderOrder={3} />))}
    </group>
  );
}

function Backdrop() {
  return (
    <group position={[0, -0.2, -3.2]}>
      <gridHelper args={[26, 52, "#082733", "#04141b"]} rotation={[Math.PI / 2, 0, 0]} />
      <mesh position={[0, -3.9, 3.2]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[0.95, 0.975, 96]} />
        <meshBasicMaterial color="#1d8fb0" transparent opacity={0.16} side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[0, -3.9, 3.2]} rotation={[-Math.PI / 2, 0, 0]}>
        <ringGeometry args={[1.32, 1.33, 96]} />
        <meshBasicMaterial color="#1d8fb0" transparent opacity={0.09} side={THREE.DoubleSide} />
      </mesh>
    </group>
  );
}

function Ripple({ pos, color, speed = 1 }) {
  const rs = [useRef(), useRef(), useRef()];
  useFrame(() => {
    const t = now() * speed;
    rs.forEach((r, k) => {
      if (!r.current) return;
      const f = (t + k / 3) % 1;
      r.current.scale.setScalar(0.12 + f * 0.62);
      r.current.material.opacity = (1 - f) * 0.9;
    });
  });
  return (
    <group position={pos}>
      {rs.map((r, k) => (
        <mesh key={k} ref={r} renderOrder={8}>
          <ringGeometry args={[0.94, 1, 64]} />
          <meshBasicMaterial color={color} transparent depthWrite={false} side={THREE.DoubleSide} blending={THREE.AdditiveBlending} />
        </mesh>
      ))}
    </group>
  );
}

function Tag({ pos, side, children, cls = "" }) {
  const elbow = pos.clone().add(V(side * 0.32, 0.18, 0));
  const at = elbow.clone().add(V(side * 0.32, 0, 0));
  return (
    <>
      <Line points={[pos, elbow, at]} color="#7fe9ff" lineWidth={1} transparent opacity={0.7} />
      <mesh position={pos} renderOrder={9}>
        <ringGeometry args={[0.028, 0.04, 24]} />
        <meshBasicMaterial color="#e6fbff" side={THREE.DoubleSide} />
      </mesh>
      <Html position={at} zIndexRange={[20, 10]} style={{ pointerEvents: "none" }}>
        <div className={`bj-tag ${side < 0 ? "left" : ""} ${cls}`}>{children}</div>
      </Html>
    </>
  );
}

function EnzymeRing({ pos, color, label, lock, spin }) {
  const a = useRef(), b = useRef();
  useFrame((_, dt) => {
    if (a.current) a.current.rotation.z += dt * (spin ? 3.2 : lock ? 0.15 : 0.9);
    if (b.current) b.current.rotation.z -= dt * (spin ? 2 : 0.5);
  });
  return (
    <group position={pos}>
      <mesh ref={a} renderOrder={6}>
        <torusGeometry args={[0.12, 0.012, 8, 48, Math.PI * 1.6]} />
        <meshBasicMaterial color={color} transparent opacity={0.95} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      <mesh ref={b} renderOrder={6}>
        <torusGeometry args={[0.16, 0.005, 6, 48, Math.PI * 1.2]} />
        <meshBasicMaterial color={color} transparent opacity={0.6} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
      <Html position={[-0.22, 0, 0]} zIndexRange={[20, 10]} style={{ pointerEvents: "none" }}>
        <div className={`bj-enz ${lock ? "lock" : ""}`} style={{ borderColor: color, color }}>
          {label}{lock ? " · 억제" : spin ? " · 유도" : ""}
        </div>
      </Html>
    </group>
  );
}

function BindMarker({ pos, color }) {
  const r = useRef();
  useFrame(() => {
    if (!r.current) return;
    const f = (now() * 0.8) % 1;
    r.current.scale.setScalar(0.6 + f * 0.6);
    r.current.material.opacity = 1 - f;
  });
  return (
    <group position={pos}>
      <mesh renderOrder={7}><sphereGeometry args={[0.03, 12, 8]} /><meshBasicMaterial color={color} /></mesh>
      <mesh ref={r} renderOrder={7}>
        <ringGeometry args={[0.09, 0.105, 40]} />
        <meshBasicMaterial color={color} transparent side={THREE.DoubleSide} blending={THREE.AdditiveBlending} depthWrite={false} />
      </mesh>
    </group>
  );
}

// ------------------------------------------------------------------ what happens in each stage
function enzymeSlots(stage) {
  const list = (stage?.enzymes || []).slice(0, 4);
  return Object.fromEntries(list.map((e, k) => [e.name, { ...e, pos: SITE.liver.clone().add(V(-0.72, 0.45 - k * 0.32, 0.32)) }]));
}

function plan(pathway, stage, colorOf, fit = (v) => v) {
  const out = planRaw(pathway, stage, colorOf);
  out.streams.forEach((s) => { s.points = s.points.map(fit); });
  out.markers.forEach((m) => { m.pos = fit(m.pos); });
  out.ripples.forEach((r) => { r.pos = fit(r.pos); });
  out.rings.forEach((r) => { r.pos = fit(r.pos); });
  return out;
}

function planRaw(pathway, stage, colorOf) {
  const drugs = pathway.drugs;
  const id = stage.id;
  const streams = [];
  const add = (key, points, mode, count, speed = 0.3, radius = 0.14) =>
    streams.push({ color: colorOf(key), points, mode, count, speed, radius });
  const st = (sid) => pathway.stages.find((s) => s.id === sid) || {};
  const chel = (st("intake").items || []).filter((i) => i.kind === "chelation");
  const chelVictim = new Set(chel.map((i) => i.drugs[1]));
  const chelAgent = new Set(chel.map((i) => i.drugs[0]));
  const gut = st("absorb").items || [];
  const boosted = new Set(gut.map((i) => i.drugs[1])); // grapefruit / P-gp victims: more drug gets through
  const levels = Object.fromEntries((st("blood").levels || []).map((l) => [l.drug, l]));
  const n = Math.max(1, drugs.length);
  const per = Math.min(240, Math.floor(2200 / n));
  const sites = {}, tags = [], rings = [], markers = [], ripples = [];

  if (id === "intake") {
    for (const d of drugs) {
      if (d.route === "injection") add(d.key, [W.injOut, W.inj], "bind", per * 0.7, 0.35, 0.09);
      else if (d.route === "inhaled") {
        add(d.key, [W.mouth, W.throat, W.trachea, SITE.lung], "bind", per * 0.5, 0.35, 0.22);
        add(d.key, [W.mouth, W.throat, W.trachea, W.lungR], "bind", per * 0.5, 0.35, 0.22);
      } else {
        const stuck = chelVictim.has(d.key) || chelAgent.has(d.key);
        add(d.key, [W.sky, W.mouth, W.throat, W.esoph, W.cardia, STOMACH[2], SITE.stomach], stuck ? "stuck" : "bind", per, 0.28,
          stuck ? 0.08 : 0.15);
      }
    }
    sites.stomach = { active: true, sev: stage.sev };
    if (drugs.some((d) => d.route === "inhaled")) sites.lung = { active: true };
    tags.push({ site: "stomach", side: 1, head: "STOMACH · 위", text: stage.items?.length ? stage.items[0].text : "붕해·용해 — 약 성분이 풀려나옴", sev: stage.sev });
  } else if (id === "absorb") {
    for (const d of drugs) {
      if (d.route === "injection") { add(d.key, [W.inj, W.vein, W.ivc, SITE.heart], "flow", per * 0.6, 0.22, 0.05); continue; }
      if (d.route === "inhaled") { add(d.key, [SITE.lung, V(-0.15, 1.55, 0.1), SITE.heart], "flow", per * 0.6, 0.25, 0.06); continue; }
      if ((d.enzymes || []).some((e) => e.strength === "gut")) { // grapefruit binds the gut-wall enzyme
        add(d.key, GUT_PATH.slice(0, 6), "stuck", per * 0.8, 0.35, 0.14);
        continue;
      }
      if (chelVictim.has(d.key)) {
        add(d.key, [W.cardia, SITE.stomach], "stuck", per * 0.75, 0.4, 0.09);
        add(d.key, GUT_PATH, "flow", per * 0.25, 0.1, 0.04);
      } else if (d.release === "ER") { // the tablet matrix travels down the gut and lets the drug out slowly
        add(d.key, GUT_PATH.slice(0, 8), "stuck", per * 0.55, 0.12, 0.07);
        add(d.key, GUT_PATH, "flow", per * 0.45, 0.05, 0.04);
      } else add(d.key, GUT_PATH, "flow", per * (boosted.has(d.key) ? 1.7 : 1), 0.1, 0.045);
    }
    sites.intestine = { active: true, sev: stage.sev };
    if (chel.length) sites.stomach = { sev: "mid" };
    const it = stage.items?.[0];
    tags.push({ site: "intestine", side: -1, head: "SMALL INTESTINE · 소장", text: it ? it.text : "흡수 → 문맥 → 간", sev: stage.sev });
  } else if (id === "liver") {
    const slots = enzymeSlots(stage);
    const clashes = stage.clashes || [];
    for (const d of drugs) {
      if (d.route !== "oral") { add(d.key, [W.vein, W.ivc, SITE.heart], "flow", per * 0.4, 0.25, 0.05); continue; }
      const mine = (d.enzymes || []).filter((e) => slots[e.enzyme] && e.strength !== "gut");
      const asPerp = clashes.find((c) => c.perpetrator === d.key && slots[c.enzyme]);
      const asVictim = clashes.find((c) => c.victim === d.key && slots[c.enzyme]);
      const from = [W.portal, SITE.liver];
      if (asPerp) add(d.key, [...from, slots[asPerp.enzyme].pos], "stuck", per * 0.8, 0.35, 0.15);
      else if (asVictim) add(d.key, [...from, slots[asVictim.enzyme].pos], asVictim.dir === "up" ? "pile" : "bind", per * 1.2, 0.3, 0.28);
      else if (mine.length) add(d.key, [...from, slots[mine[0].enzyme].pos], "bind", per * 0.8, 0.3, 0.17);
      else add(d.key, [W.portal, SITE.liver, W.ivc, SITE.heart], "flow", per * 0.6, 0.18, 0.05);
    }
    for (const e of Object.values(slots)) {
      const hit = clashes.filter((c) => c.enzyme === e.name);
      const sev = hit.reduce((a, c) => (["high", "mid", "low"].indexOf(c.sev) < ["high", "mid", "low"].indexOf(a) ? c.sev : a), "none");
      rings.push({ pos: e.pos, label: e.name, color: SEV_HEX[sev] || "#00e5ff",
        lock: hit.some((c) => c.action === "inhibitor"), spin: hit.some((c) => c.action === "inducer") });
    }
    sites.liver = { active: true, sev: stage.sev === "low" ? undefined : stage.sev };
    if (!rings.length) tags.push({ site: "liver", side: -1, head: "LIVER · 간", text: "확인된 효소 충돌 없음" });
  } else if (id === "blood") {
    for (const d of drugs) {
      const lv = levels[d.key];
      const k = lv?.dir === "up" ? 1.8 : lv?.dir === "down" ? 0.4 : 1;
      add(d.key, LOOP, "flow", per * k, lv?.dir === "up" ? 0.06 : 0.09, lv?.dir === "up" ? 0.1 : 0.05);
    }
    sites.heart = { active: true, sev: stage.sev === "low" ? undefined : stage.sev };
    sites.vessel = { active: true };
  } else if (id === "target") {
    for (const d of drugs) {
      const ts = (stage.binds || []).filter((b) => b.drug === d.key).slice(0, 2);
      if (!ts.length) { add(d.key, LOOP, "flow", per * 0.3, 0.08, 0.05); continue; }
      ts.forEach((b, k) => {
        add(d.key, arteryTo(b.site), "bind", (per * 0.9) / ts.length, 0.26, 0.13 + k * 0.03);
        markers.push({ pos: SITE[b.site].clone().add(V((markers.length % 3) * 0.08 - 0.08, 0.05, 0.12)), color: colorOf(d.key) });
      });
    }
    const bySite = {};
    for (const b of stage.binds || []) (bySite[b.site] ||= []).push(b);
    for (const [s, bs] of Object.entries(bySite)) {
      const over = (stage.overlaps || []).some((o) => o.site === s);
      sites[s] = { active: true, sev: over && stage.sev === "mid" ? "mid" : undefined };
      tags.push({ site: s, side: SITE[s].x <= 0 ? -1 : 1, binds: bs.slice(0, 3) });
    }
  } else if (id === "problem") {
    const probs = (stage.problems || []).slice(0, 6);
    const placed = new Set();
    for (const p of probs) {
      const s = p.sites[0];
      for (const k of p.drugs) add(k, arteryTo(s), "swarm", (per * 0.7) / Math.max(1, p.drugs.length) + 30, 0.35, 0.18);
      for (const s2 of p.sites) {
        const cur = sites[s2];
        if (!cur || (cur.sev !== "high" && p.sev === "high")) sites[s2] = { active: true, sev: p.sev };
        if (!placed.has(s2)) ripples.push({ pos: SITE[s2], color: SEV_HEX[p.sev], speed: p.sev === "high" ? 1.3 : 0.8 });
        if (!placed.has(s2) && tags.length < 5) {
          const outcome = p.chain?.find((c) => c.k === "결과")?.v;
          tags.push({ site: s2, side: SITE[s2].x <= 0 ? -1 : 1, head: outcome || pathway.sites[s2], text: p.title, sev: p.sev });
        }
        placed.add(s2);
      }
    }
    if (!probs.length) for (const d of drugs) add(d.key, LOOP, "flow", per * 0.5, 0.08, 0.05);
  } else if (id === "excrete") {
    const renal = new Set(stage.renal || []);
    const slow = stage.sev === "high" || stage.sev === "mid";
    const toK = [SITE.heart, ...AORTA.slice(1, 5), ...ARTERIES[7].slice(1)];
    for (const d of drugs) {
      if (renal.has(d.key) && slow) {
        add(d.key, toK, "pile", per * 1.1, 0.3, 0.2);
        add(d.key, [SITE.heart, ...AORTA.slice(1, 5), ...ARTERIES[8].slice(1)], "pile", per * 0.5, 0.3, 0.2);
        add(d.key, [SITE.kidney, W.ureter, SITE.bladder, W.exitU], "flow", per * 0.15, 0.08, 0.04);
      } else if (renal.has(d.key) || !(d.enzymes || []).length) {
        add(d.key, [...toK, W.ureter, SITE.bladder, W.exitU], "flow", per * 0.8, 0.12, 0.04);
      } else {
        add(d.key, [W.portal, SITE.liver, W.bile, ...COLON.slice(4, 9), W.bowelExit], "flow", per * 0.5, 0.1, 0.04);
        add(d.key, [...toK, W.ureter, SITE.bladder, W.exitU], "flow", per * 0.35, 0.12, 0.04);
      }
    }
    sites.kidney = { active: true, sev: slow ? stage.sev : undefined };
    sites.bladder = { active: true };
    if (slow) ripples.push({ pos: SITE.kidney, color: SEV_HEX[stage.sev], speed: 0.8 });
    tags.push({ site: "kidney", side: 1, head: "KIDNEY · 콩팥",
      text: stage.kidney ? `여과 기능 정상의 약 ${stage.kidney.pct_normal}%` : "사구체 여과 → 소변 배출",
      sub: slow ? `${(stage.renal || []).map((k) => drugs.find((d) => d.key === k)?.name).join(", ")} 축적` : null, sev: slow ? stage.sev : undefined });
    for (const [s, v] of Object.entries(pathway.site_state || {})) if (!sites[s]) sites[s] = { sev: v.sev };
  }
  return { streams, sites, tags, rings, markers, ripples };
}

const FOCUS = {
  intake: [V(0.1, 1.7, 0), 0.72], absorb: [V(0, 0.3, 0), 0.62], liver: [V(-0.72, 0.75, 0), 0.52],
  blood: [V(0.1, 0.7, 0), 0.82], target: [V(0, -0.1, 0), 1.0], problem: [V(0, -0.1, 0), 1.0], excrete: [V(0.15, -0.1, 0), 0.66],
};
const BODY_UNITS = 8.2; // head top to shins plus a margin
const TAN = 2 * Math.tan((45 / 2) * (Math.PI / 180)); // visible height per unit of distance (fov 45)

function CameraRig({ stageId, layout }) {
  const { camera, size } = useThree();
  // full-screen canvas: frame the body into the free part of the center column (measured by the App);
  // inline canvas: the whole canvas is free
  const r = layout.full ? (layout.region || { x: layout.centerFrac, top: 0.08, bottom: layout.focusY * 2 - 0.08, width: 0.4 }) : null;
  const free = r ? Math.max(0.25, r.bottom - r.top) : 1;
  const fitH = BODY_UNITS / (TAN * free);
  const fitW = r ? 4.6 / (TAN * (size.width / size.height) * Math.max(0.2, r.width)) : 4.0 / (TAN * (size.width / size.height));
  const D = Math.max(fitH, fitW, 8);
  useLayoutEffect(() => {
    if (r) {
      camera.setViewOffset(size.width, size.height, (0.5 - r.x) * size.width,
        (0.5 - (r.top + r.bottom) / 2) * size.height, size.width, size.height);
    } else camera.clearViewOffset();
    camera.updateProjectionMatrix();
    return () => { camera.clearViewOffset(); camera.updateProjectionMatrix(); };
  }, [camera, size, r?.x, r?.top, r?.bottom]); // eslint-disable-line react-hooks/exhaustive-deps
  const look = useRef(V(0, 0.3, 0));
  const tgt = useMemo(() => new THREE.Vector3(), []);
  useFrame((state, dt) => {
    const [f, z] = FOCUS[stageId] || [V(0, 0.2, 0), 1];
    const k = 1 - Math.exp(-dt * 1.6);
    look.current.lerp(f, k);
    const yaw = Math.sin(state.clock.elapsedTime * 0.2) * 0.2;
    const dist = D * z;
    tgt.set(look.current.x + Math.sin(yaw) * dist, look.current.y + dist * 0.05, Math.cos(yaw) * dist);
    camera.position.lerp(tgt, k);
    camera.lookAt(look.current);
  });
  return null;
}

function Scene({ pathway, stage, layout }) {
  const colorOf = useMemo(() => {
    const m = Object.fromEntries(pathway.drugs.map((d, i) => [d.key, DRUG_COLORS[i % DRUG_COLORS.length]]));
    return (k) => m[k] || "#ffffff";
  }, [pathway]);
  const sex = pathway.subject?.sex === "F" ? "F" : "M";
  const pregnant = sex === "F" && !!pathway.subject?.pregnant;
  const fit = useMemo(() => armFit(sex), [sex]);
  const p = useMemo(() => plan(pathway, stage, colorOf, fit), [pathway, stage, colorOf, fit]);
  const nameOf = (k) => pathway.drugs.find((d) => d.key === k)?.name || k;
  const size = layout.full ? 0.1 : 0.085;
  return (
    <>
      <CameraRig stageId={stage.id} layout={layout} />
      <ambientLight intensity={0.4} />
      <Backdrop />
      <Anatomy sites={p.sites} sex={sex} pregnant={pregnant} />
      <BloodFlow size={size} sex={sex} />
      <Streams streams={p.streams} size={size} />
      {p.rings.map((r) => <EnzymeRing key={r.label} {...r} />)}
      {p.markers.map((r, i) => <BindMarker key={i} {...r} />)}
      {p.ripples.map((r, i) => <Ripple key={`${stage.id}${i}`} {...r} />)}
      {p.tags.map((t, i) => (
        <Tag key={`${stage.id}${i}`} pos={fit(SITE[t.site])} side={t.side} cls={t.sev ? `sev-${t.sev}` : ""}>
          {!layout.full ? ( // phone: the caption below the canvas has the text, the body only names the organ
            <b>{t.binds ? pathway.sites[t.site] : (() => {
              const x = stage.id === "problem" && t.head ? t.head : t.text;
              return x?.length > 24 ? `${x.slice(0, 23)}…` : x;
            })()}</b>
          ) : t.binds ? (
            <>
              <em>TARGET · {pathway.sites[t.site]}</em>
              {t.binds.map((b, k) => <span key={k} className="bj-bind"><i style={{ background: colorOf(b.drug) }} />{nameOf(b.drug)} → {b.label}</span>)}
            </>
          ) : (
            <>
              {t.head && <em>{t.head}</em>}
              <b>{t.text?.length > 60 ? `${t.text.slice(0, 58)}…` : t.text}</b>
              {t.sub && <small>{t.sub.length > 70 ? `${t.sub.slice(0, 68)}…` : t.sub}</small>}
            </>
          )}
        </Tag>
      ))}
      {stage.id === "blood" && (
        <Html position={SITE.heart.clone().add(V(0.75, 0.35, 0))} zIndexRange={[20, 10]} style={{ pointerEvents: "none" }}>
          <div className="bj-gauge">
            <b>PLASMA LEVEL · 혈중 농도</b>
            {pathway.drugs.map((d) => {
              const lv = (stage.levels || []).find((l) => l.drug === d.key);
              const w = lv?.dir === "up" ? (lv.sev === "high" ? 96 : 78) : lv?.dir === "down" ? 22 : 50;
              return (
                <div key={d.key} className="bj-lv">
                  <span><i style={{ background: colorOf(d.key) }} />{d.name}</span>
                  <em><u className={lv ? `sev-${lv.sev}` : ""} style={{ width: `${w}%` }} /></em>
                  <strong className={lv ? `sev-${lv.sev}` : ""}>{lv ? (lv.dir === "up" ? "▲" : "▼") : "─"}</strong>
                </div>
              );
            })}
          </div>
        </Html>
      )}
      <Bloom strength={layout.full ? 0.55 : 0.5} radius={0.5} threshold={0.32} />
    </>
  );
}

// memo: the 3D scene re-renders only when its own inputs change, not every second with the header clock
export default React.memo(BodyScene);

function BodyScene({ pathway, stage, layout }) {
  if (!pathway || !stage) return null;
  return (
    <Canvas camera={{ position: [0, 0.6, 16], fov: 45 }} dpr={[1, 2]} gl={{ antialias: true }}>
      <color attach="background" args={["#02070b"]} />
      <fog attach="fog" args={["#02070b", 24, 60]} />
      <Scene pathway={pathway} stage={stage} layout={layout} />
    </Canvas>
  );
}

// ------------------------------------------------------------------ caption / controls (DOM)
function Items({ stage, nameOf, colorOf }) {
  const id = stage.id;
  const rows = [];
  const dot = (k) => <i className="bj-dot" style={{ background: colorOf(k) }} />;
  if (id === "intake" || id === "absorb") for (const i of stage.items || []) rows.push({ sev: i.sev, el: i.text });
  if (id === "liver") {
    for (const c of stage.clashes || []) rows.push({ sev: c.sev, el: <>{c.text} <small>· {c.source}</small></> });
    if (!rows.length) {
      for (const e of (stage.enzymes || []).slice(0, 3)) {
        rows.push({ sev: "normal", el: <>{e.name}: {[...e.substrates, ...e.activates].map(nameOf).join(", ") || "-"} 분해</> });
      }
    }
  }
  if (id === "blood") {
    for (const l of stage.levels || []) {
      rows.push({ sev: l.sev, el: <>{dot(l.drug)}{nameOf(l.drug)} {l.dir === "up" ? "▲" : "▼"} <b>{l.fold_label}</b> — {l.why}</> });
    }
  }
  if (id === "target") {
    const seen = new Set();
    for (const b of stage.binds || []) {
      if (seen.has(b.drug)) continue;
      seen.add(b.drug);
      rows.push({ sev: "normal", el: <>{dot(b.drug)}<b>{nameOf(b.drug)}</b> → {b.label}: {b.action}</> });
    }
    for (const o of stage.overlaps || []) rows.push({ sev: "mid", el: o.text });
  }
  if (id === "problem") for (const p of stage.problems || []) rows.push({ sev: p.sev, el: <><b>{p.title}</b> — {p.text} <small>· {p.source}</small></> });
  if (id === "excrete" && stage.renal?.length) rows.push({ sev: stage.sev, el: <>콩팥으로 주로 배출: {stage.renal.map(nameOf).join(", ")}</> });
  if (!rows.length) return null;
  return <ul className="bj-items">{rows.slice(0, 5).map((r, k) => <li key={k} className={`sev-${r.sev}`}>{r.el}</li>)}</ul>;
}

function SidePanel({ pathway, stage, molecules, colorOf }) {
  const molByKey = useMemo(() => Object.fromEntries((molecules || []).map((m) => [m.key, m])), [molecules]);
  const mp = useMemo(() => (["intake", "absorb", "liver", "target"].includes(stage.id)
    ? molecularPlan(stage, pathway, molByKey, colorOf) : null), [stage, pathway, molByKey]); // eslint-disable-line react-hooks/exhaustive-deps
  const blood = pathway.stages.find((s) => s.id === "blood");
  const [probView, setProbView] = useState("chain");
  if (stage.id === "problem") {
    // the chain explains WHY; the repeated-dosing curve shows HOW it builds up over days (cumulative harms)
    const tabs = (
      <span className="bj-ptabs side">
        <button className={probView === "chain" ? "on" : ""} onClick={() => setProbView("chain")}>기전 경로</button>
        <button className={probView === "pk" ? "on" : ""} onClick={() => setProbView("pk")}>반복 복용 농도</button>
      </span>
    );
    return (
      <div className="bj-side">
        {tabs}
        {probView === "chain" ? <MechanismChain problems={stage.problems} />
          : <PKChart levels={blood?.levels} drugs={pathway.drugs} colorOf={colorOf} title="반복 복용 시 농도 (PK)" mode="problem"
            defaultView="repeat" />}
      </div>
    );
  }
  if (mp) {
    return (
      <div className={`bj-panel mol sev-${mp.sev}`}>
        <div className="bj-ptitle">MOLECULAR VIEW<span>분자 수준 · 도식</span></div>
        <div className="bj-mol"><MolecularView key={stage.id} plan={mp} /></div>
        <div className="bj-mcap"><b>{mp.caption}</b><small>{mp.sub}</small></div>
      </div>
    );
  }
  if (stage.id === "excrete") {
    return <PKChart levels={blood?.levels} drugs={pathway.drugs} colorOf={colorOf} title="복용 중단 후 배출 (PK)" mode="excrete"
      defaultView="repeat" />;
  }
  return <PKChart levels={blood?.levels} drugs={pathway.drugs} colorOf={colorOf} title="혈중농도-시간 곡선 (PK)" mode="blood" />;
}

/** whose body this is: the shape follows the sex entered in the patient panel */
function Subject({ s }) {
  if (!s) return null;
  const parts = [s.age != null && `${Math.round(s.age)}세`, s.height_cm && `${Math.round(s.height_cm)}cm`,
    s.weight_kg && `${Math.round(s.weight_kg)}kg`, s.kidney_pct != null && `신기능 ${s.kidney_pct}%`].filter(Boolean);
  return (
    <span className="bj-subj" title="환자 정보에 입력한 성별·체형으로 인체를 그립니다">
      <b>{s.sex === "F" ? "♀ 여성" : s.sex === "M" ? "♂ 남성" : "성별 미입력"}{s.pregnant ? " · 임신" : ""}</b>{parts.join(" · ")}
    </span>
  );
}

export function JourneyHud({ pathway, j, onMolecules, compact, boxRef, molecules }) {
  if (!pathway || !j.stage) return null;
  const colorOf = (k) => DRUG_COLORS[Math.max(0, pathway.drugs.findIndex((d) => d.key === k)) % DRUG_COLORS.length];
  const nameOf = (k) => pathway.drugs.find((d) => d.key === k)?.name || k;
  const s = j.stage;
  return (
    <div className={`journey-hud ${compact ? "compact" : ""}`} ref={boxRef}>
      <div className="bj-main">
        <div className="bj-top">
          <span className="bj-k">BODY JOURNEY · {j.idx + 1}/{j.stages.length}</span>
          <Subject s={pathway.subject} />
          <span className="bj-ctl">
            <button className="btn mini" onClick={() => j.go(j.idx - 1)} title="이전 단계">⏮</button>
            <button className="btn mini" onClick={j.toggle} title="재생/정지">{j.playing ? "⏸" : "▶"}</button>
            <button className="btn mini" onClick={() => j.go(j.idx + 1)} title="다음 단계">⏭</button>
            {onMolecules && <button className="btn mini" onClick={onMolecules} title="3D 분자 구조 보기">분자 보기</button>}
          </span>
        </div>
        <div className="bj-dots">
          {j.stages.map((x, i) => (
            <button key={x.id} className={`bj-step ${i === j.idx ? "on" : ""} sev-${x.sev}`} onClick={() => j.go(i)} title={x.title}>
              {x.title.replace(/^[①-⑦]\s*/, "")}
            </button>
          ))}
        </div>
        <TimeRuler pathway={pathway} j={j} />
        <div className={`bj-title sev-${s.sev}`}>{s.title}
          {s.time && <span className="bj-ttime">{s.time.label}</span>}</div>
        <div className="bj-text">{s.text}</div>
        <Items stage={s} nameOf={nameOf} colorOf={colorOf} />
        <div className="bj-legend">
          {pathway.drugs.map((d) => (
            <span key={d.key}><i className="bj-dot" style={{ background: colorOf(d.key) }} />{d.name}
              {d.route !== "oral" && <small> ({d.route === "injection" ? "주사" : "흡입"})</small>}
              {d.release === "ER" && <small className="rel-tag">서방형</small>}{d.release === "DR" && <small className="rel-tag dr">장용형</small>}</span>
          ))}
        </div>
      </div>
      <SidePanel pathway={pathway} stage={s} molecules={molecules} colorOf={colorOf} />
    </div>
  );
}
