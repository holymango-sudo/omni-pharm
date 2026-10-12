import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { api, handleLoginRequired, handlePinRequired, setPin, setToken } from "./api.js";
import { HelpModal, LoginModal, PinModal } from "./components/Help.jsx";
import MoleculeScene from "./components/MoleculeScene.jsx";
import BodyScene, { JourneyClock, JourneyHud, useJourney } from "./components/BodyJourney.jsx";
import PatientPanel, { PRESETS } from "./components/PatientPanel.jsx";
import DrugBar from "./components/DrugBar.jsx";
import ResultPanel from "./components/ResultPanel.jsx";
import DetailDrawer from "./components/DetailDrawer.jsx";
import ChatPanel from "./components/ChatPanel.jsx";
import { printReport } from "./print.js";

const now = () => performance.now() / 1000;
const LAYOUT = { centerFrac: 0.445, targetFrac: 0.8, focusY: 0.47 }; // must match the CSS grid columns
// stable objects: the molecule stage must not re-render every second with the header clock (that made molecules twitch)
const MOL_FULL = { full: true, ...LAYOUT };
const MOL_INLINE = { full: false };

function siren(on) {
  if (!on) return;
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const o = ctx.createOscillator();
    const g = ctx.createGain();
    o.type = "sawtooth";
    o.connect(g).connect(ctx.destination);
    const t = ctx.currentTime;
    for (let k = 0; k < 3; k++) {
      o.frequency.setValueAtTime(620, t + k * 0.7);
      o.frequency.linearRampToValueAtTime(1180, t + k * 0.7 + 0.35);
      o.frequency.linearRampToValueAtTime(620, t + k * 0.7 + 0.7);
    }
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.07, t + 0.05);
    g.gain.setValueAtTime(0.07, t + 1.9);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 2.1);
    o.start(t);
    o.stop(t + 2.15);
  } catch { /* audio unavailable */ }
}

function useWide() {
  const q = "(min-width: 1180px)";
  const [w, setW] = useState(() => window.matchMedia(q).matches);
  useEffect(() => {
    const m = window.matchMedia(q);
    const f = () => setW(m.matches);
    m.addEventListener("change", f);
    return () => m.removeEventListener("change", f);
  }, []);
  return w;
}

export default function App() {
  const [status, setStatus] = useState(null);
  const [patient, setPatient] = useState({ ...PRESETS["건강한 성인"] });
  const [drugs, setDrugs] = useState([]);
  const [res, setRes] = useState(null);
  const [report, setReport] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sim, setSim] = useState(false);  // 예측 시작 -> collision animation playing
  const stageRef = useRef(null);
  const [error, setError] = useState(null);
  const [drawer, setDrawer] = useState(null);
  const [sound, setSound] = useState(() => { try { return localStorage.getItem("omnipharm.sound") === "1"; } catch { return false; } });
  const [help, setHelp] = useState(() => { try { return !localStorage.getItem("omnipharm.seenHelp"); } catch { return true; } });
  const [needPin, setNeedPin] = useState(false);
  const [clock, setClock] = useState(new Date());
  const [expert, setExpert] = useState(false);
  const [diseases, setDiseases] = useState([]);
  const [assessment, setAssessment] = useState(null);
  const anim = useRef({ phase: "idle", t0: now() });
  const stopStream = useRef(null);
  const wide = useWide();
  // after a prediction the center stage switches from the molecules to the drugs' journey through the body
  const [view, setView] = useState("mol");
  const [chatOpen, setChatOpen] = useState(false);
  const bodyTimer = useRef(null);
  const journey = useJourney(view === "body" ? res?.pathway : null);
  const showBody = view === "body" && !!res?.pathway;
  // free screen area between the header and the journey caption: the body is framed into it
  const centerRef = useRef(null);
  const hudRef = useRef(null);
  const [region, setRegion] = useState(null);
  const bodyLayout = useMemo(() => ({ full: true, ...LAYOUT, region }), [region]);
  useLayoutEffect(() => {
    if (!showBody || !wide) return undefined;
    const measure = () => {
      const c = centerRef.current?.getBoundingClientRect();
      const h = hudRef.current?.getBoundingClientRect();
      if (!c || !h) return;
      setRegion({ x: (c.left + c.width / 2) / window.innerWidth, top: (c.top + 6) / window.innerHeight,
        bottom: (h.top - 6) / window.innerHeight, width: c.width / window.innerWidth });
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [showBody, wide]);

  const [needLogin, setNeedLogin] = useState(false);
  const [user, setUser] = useState(null);
  const loadAll = () => {
    api.status().then(setStatus).catch((e) => setStatus(e.status === 401 ? null : false));
    api.diseases().then(setDiseases).catch(() => {});
    api.me().then((m) => setUser(m.user ? m : null)).catch(() => {});
  };
  const doLogin = async (u, p) => {
    const s = await api.login(u, p);
    setToken(s.token);
    setNeedLogin(false);
    loadAll();
  };
  const doLogout = () => { api.logout().catch(() => {}); setToken(""); setUser(null); setNeedLogin(true); };
  useEffect(() => {
    handlePinRequired(() => setNeedPin(true));
    handleLoginRequired(() => setNeedLogin(true));
    loadAll();
    const t = setInterval(() => setClock(new Date()), 1000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { try { localStorage.setItem("omnipharm.sound", sound ? "1" : "0"); } catch { /* ignore */ } }, [sound]);
  const closeHelp = () => { setHelp(false); try { localStorage.setItem("omnipharm.seenHelp", "1"); } catch { /* ignore */ } };

  // live body / kidney / liver / capacity summary computed by the server (single source of truth)
  useEffect(() => {
    let live = true;
    const t = setTimeout(() => api.assess(payload()).then((a) => live && setAssessment(a)).catch(() => {}), 150);
    return () => { live = false; clearTimeout(t); };
  }, [patient]); // eslint-disable-line react-hooks/exhaustive-deps

  const resetResult = () => {
    stopStream.current?.();
    clearTimeout(bodyTimer.current);
    setSim(false);
    setView("mol");
    setRes(null);
    setReport("");
    setStreaming(false);
    anim.current = { phase: "idle", t0: now() };
  };

  const names = useRef(new Set());
  const addDrug = (name) => {
    if (names.current.has(name)) return;
    names.current.add(name);
    resetResult();
    setDrugs((ds) => [...ds, { name, status: "loading" }]);
    api.molecule(name)
      .then((r) => setDrugs((ds) => ds.map((d) => (d.name === name
        ? { ...d, status: "ok", molecules: r.molecules, product: r.product, unresolved: r.unresolved, release: r.release,
          dose_info: r.dose_info, single: r.single, controlled: r.controlled } : d))))
      .catch((e) => setDrugs((ds) => ds.map((d) => (d.name === name
        ? { ...d, status: "error", error: e.message, candidates: e.detail?.candidates } : d))));
  };
  const replaceDrug = (oldName, newName) => {
    names.current.delete(oldName);
    setDrugs((ds) => ds.filter((d) => d.name !== oldName));
    setTimeout(() => addDrug(newName), 0);
  };
  const removeDrug = (name) => {
    names.current.delete(name);
    resetResult();
    setDrugs((ds) => ds.filter((d) => d.name !== name));
  };
  const scenario = (list) => {
    names.current = new Set();
    resetResult();
    setDrugs([]);
    list.forEach((n, i) => setTimeout(() => addDrug(n), 100 + i * 350));
  };

  const molecules = useMemo(() => {
    const seen = new Set();
    return drugs.flatMap((d) => d.molecules || []).filter((m) => (seen.has(m.key) ? false : seen.add(m.key)));
  }, [drugs]);

  const pairLevelByKey = useMemo(() => {
    const rank = { 높음: 2, 주의: 1, 낮음: 0 };
    const out = {};
    for (const p of res?.pairs || []) {
      for (const k of [p.a_key, p.b_key]) if (!out[k] || rank[p.level] > rank[out[k]]) out[k] = p.level;
    }
    return out;
  }, [res]);

  const payload = () => {
    const p = { age: patient.age, sex: patient.sex, weight_kg: patient.weight_kg, height_cm: patient.height_cm,
      ast: patient.ast, alt: patient.alt,
      total_bilirubin: patient.total_bilirubin, conditions: patient.conditions, diagnosis: patient.diagnosis || null,
      symptoms: patient.symptoms || null };
    if (patient.labMode === "scr") p.scr = patient.scr; else p.egfr = patient.egfr;
    return p;
  };

  const predict = async () => {
    resetResult();
    setError(null);
    setBusy(true);
    setSim(true);  // the drug bar folds into one line so the collision on the 3D stage is not hidden behind it
    if (!wide) stageRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    anim.current = { phase: "colliding", t0: now() };
    const minDelay = new Promise((r) => setTimeout(r, 1700));
    try {
      const [r] = await Promise.all([api.predict(drugs.filter((d) => d.status === "ok").map((d) => ({ name: d.name, dose: d.dose || null })), payload()), minDelay]);
      setRes(r);
      anim.current = { phase: "done", t0: now() };
      // let the collision flash and the data stream play, then follow the drugs into the body
      bodyTimer.current = setTimeout(() => { if (r.pathway) setView("body"); setSim(false); }, 2300);
      siren(sound && r.overall_level === "높음");
      setStreaming(true);
      setTimeout(() => {
        stopStream.current = api.streamReport(r.report_id, (t) => setReport((x) => x + t), () => setStreaming(false));
      }, 700);
    } catch (e) {
      setError(e.message);
      setSim(false);
      anim.current = { phase: "idle", t0: now() };
    } finally {
      setBusy(false);
    }
  };

  const level = res?.overall_level;
  return (
    <div className={`app ${wide ? "wide" : "narrow"} ${level === "높음" ? "alarm" : ""}`}>
      {wide && (
        <div className={`stage-bg ${showBody ? "body-on" : ""}`}>
          {showBody
            ? <BodyScene pathway={res.pathway} stage={journey.stage} layout={bodyLayout} />
            : <MoleculeScene molecules={molecules} anim={anim} layout={MOL_FULL} level={level} pairLevelByKey={pairLevelByKey} />}
        </div>
      )}
      <header className="hud">
        <div className="brand">
          <div className="logo">OMNI<span>-</span>PHARM</div>
          <div className="tag">초정밀 개인화 다중 약물 상호작용 예측 시스템</div>
        </div>
        <div className="sys">
          {status === false && <span className="pill bad">서버 연결 실패 — start_servers 실행 확인</span>}
          <button className="pill btnlike" onClick={() => setHelp(true)}>❔ 사용법</button>
          {status && <>
            <span className={`pill ${status.device.startsWith("cuda") ? "ok" : ""}`}>{status.device.startsWith("cuda") ? "GPU · CUDA" : "CPU"}</span>
            <span className="pill">약물 {status.n_drugs.toLocaleString()}종</span>
            {status.n_products && Object.keys(status.n_products).length > 0 && (
              <span className="pill" title={Object.entries(status.n_products).map(([c, n]) => `${c} ${n.toLocaleString()}`).join(" · ")}>
                시판제품 {(Object.values(status.n_products).reduce((a, b) => a + b, 0) / 1e4).toFixed(1)}만 · {Object.keys(status.n_products).join("/")}
              </span>
            )}
            {status.n_reports && <span className="pill">FAERS {(status.n_reports / 1e6).toFixed(1)}M건</span>}
            <span className={`pill ${status.patient_model && status.pair_model ? "ok" : "warn"}`}>
              GNN {status.pair_model ? "●" : "○"} 환자모델 {status.patient_model ? "●" : "○"}</span>
            <span className={`pill ${status.llm_available ? "ok" : "warn"}`}>LLM {status.llm_available ? status.llm_model : "오프라인(규칙 요약)"}</span>
            {status.n_dur_pairs > 0 && <span className="pill ok" title="식약처 의약품안전사용서비스(DUR) 공식 목록 적용">식약처 DUR</span>}
            {status.commercial_safe && <span className="pill" title="연구용 라이선스 데이터(TWOSIDES·KEGG) 사용 안 함">상업용 모드</span>}
            {user && <span className="pill" title={`권한: ${user.role === "admin" ? "관리자" : "의료진"}`}>👤 {user.user}
              <button className="btn mini" style={{ marginLeft: 6 }} onClick={doLogout}>로그아웃</button></span>}
          </>}
          <button className={`pill btnlike ${expert ? "ok" : ""}`} onClick={() => setExpert(!expert)}
            title="수치(확률·배수)와 전문 용어를 함께 표시">{expert ? "전문가 보기 ON" : "전문가 보기"}</button>
          <button className="pill btnlike" onClick={() => setDrawer("valid")}>모델 검증</button>
          <button className="pill btnlike" onClick={() => setSound(!sound)}>{sound ? "🔊" : "🔇"}</button>
          <span className="clock" title={clock.toLocaleString("ko-KR", { dateStyle: "full", timeStyle: "medium" })}>
            <small>현재 시각</small>
            {`${clock.getFullYear()}.${String(clock.getMonth() + 1).padStart(2, "0")}.${String(clock.getDate()).padStart(2, "0")} `}
            ({"일월화수목금토"[clock.getDay()]}) <b className="mono">{clock.toTimeString().slice(0, 8)}</b>
          </span>
        </div>
      </header>

      <main className="grid">
        <PatientPanel p={patient} set={(p) => { setPatient(p); }} assessment={assessment} diseases={diseases} expert={expert} />
        <section className="center" ref={centerRef}>
          {!wide && (
            <div ref={stageRef} className={`stage-inline ${showBody ? "body-on" : ""}`}>
              {showBody
                ? <BodyScene pathway={res.pathway} stage={journey.stage} layout={MOL_INLINE} />
                : <MoleculeScene molecules={molecules} anim={anim} layout={MOL_INLINE} level={level} pairLevelByKey={pairLevelByKey} />}
              {showBody && <JourneyClock pathway={res.pathway} j={journey} compact />}
            </div>
          )}
          {showBody && wide && <JourneyClock pathway={res.pathway} j={journey} />}
          {showBody && <JourneyHud pathway={res.pathway} j={journey} compact={!wide} boxRef={hudRef} molecules={molecules}
            onMolecules={() => setView("mol")} />}
          {!showBody && res?.pathway && (
            <button className="btn journey-btn" onClick={() => setView("body")}>🧍 몸속 이동 보기 — 약이 어디서 결합하고 어디에 문제가 생기는지</button>
          )}
          {wide && molecules.length === 0 && (
            <div className="stage-hint"><div className="mono">3D MOLECULAR STAGE</div>약물을 검색하면 실제 분자 구조가 이 공간에 생성됩니다</div>
          )}
          {error && <div className="error">⚠ {error}</div>}
          {drugs.filter((d) => d.status === "error" && !d.candidates?.length).map((d) => <div key={d.name} className="error">“{d.name}”: {d.error}</div>)}
          {(showBody && wide) || sim ? (
            // the body (and the collision before it) needs the room: the drug bar folds into one line meanwhile;
            // open panels (증상으로 약 찾기, 처방전 사진) close with it
            <div className={`drugstrip ${sim ? "sim" : ""}`}>
              {sim && <span className="sim-tag"><i className="spin" /> 분석 중</span>}
              {drugs.filter((d) => d.status === "ok").map((d) => <span key={d.name} className="chip drug ok">{d.name}</span>)}
              <span className="grow" />
              {!sim && <button className="btn mini" onClick={() => setView("mol")}>✎ 약 추가·수정</button>}
            </div>
          ) : (
            <DrugBar drugs={drugs} onAdd={addDrug} onRemove={removeDrug} onReplace={replaceDrug} onScenario={scenario}
              onDose={(name, dose) => { resetResult(); setDrugs((ds) => ds.map((d) => (d.name === name ? { ...d, dose } : d))); }}
              onPredict={predict} busy={busy} patientPayload={payload} />
          )}
        </section>
        <ResultPanel res={res} report={report} streaming={streaming} showReport={!!res} expert={expert}
          onDetail={() => setDrawer("pairs")} onPrint={(mode) => printReport(res, patient, report, mode)} />
      </main>
      <footer className="foot">
        연구·교육용 의사결정 보조 시스템 · 결과는 FDA 라벨 / 실제 보고(FAERS·TWOSIDES) / AI 예측으로 구분 표기 · 의사·약사의 임상 판단을 대체하지 않습니다
        {res && <span className="mono"> · 추론 {res.elapsed_ms}ms</span>}
      </footer>
      {drawer && <DetailDrawer res={res} initial={drawer} onClose={() => setDrawer(null)} />}
      {!chatOpen && (
        <button className={`chat-fab ${res ? "ready" : ""}`} onClick={() => setChatOpen(true)}
          title={res ? "방금 분석한 약에 대해 물어보세요" : "약·건강에 대해 무엇이든 물어보세요"}>
          💬 AI 복약 상담<small>{res ? "분석 결과로 상담" : "무엇이든 질문"}</small>
        </button>
      )}
      <ChatPanel res={res} open={chatOpen} onClose={() => setChatOpen(false)} patientPayload={payload}
        drugNames={drugs.filter((d) => d.status === "ok").map((d) => d.name)} />
      {help && !needPin && !needLogin && <HelpModal onClose={closeHelp} />}
      {needLogin && <LoginModal onLogin={doLogin} />}
      {needPin && <PinModal onSubmit={(p) => { setPin(p); setNeedPin(false); loadAll(); }} />}
    </div>
  );
}
