import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import Markdown from "./Markdown.jsx";
import { suggestions } from "../chatSuggestions.js";

/* AI 복약 상담: a free conversation with the local LLM. After an analysis its answers rest on that result; medicines
   or foods named in a question ("박카스 먹어도 돼요?") are first checked together with the analysed medicines by the
   engine (shown as chips above the answer). Before an analysis it is a general consultation for this patient.
   Conversations live in this browser tab (nothing is stored on the server). */

// the small local model sometimes writes LaTeX arrows ('$\rightarrow$') that plain markdown cannot show
const LATEX = { rightarrow: "→", to: "→", leftarrow: "←", times: "×", geq: "≥", leq: "≤", approx: "≈" };
const clean = (t) => t.replace(/\$\s*\\([a-z]+)\s*\$/g, (m, w) => LATEX[w] || m);

const TONE ={ "위험": "bad", "주의": "warn", "비교적 안전": "ok" };
const tone = (v) => TONE[Object.keys(TONE).find((k) => (v || "").startsWith(k))] || "";

export default function ChatPanel({ res, open, onClose, patientPayload, drugNames = [] }) {
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const stop = useRef(null);
  const end = useRef(null);
  useEffect(() => { setMsgs([]); stop.current?.(); setBusy(false); }, [res?.report_id]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);
  if (!open) return null;

  const patch = (f) => setMsgs((m) => { const c = [...m]; c[c.length - 1] = f(c[c.length - 1]); return c; });
  const ask = (q) => {
    const question = (q ?? text).trim();
    if (!question || busy) return;
    setText("");
    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: question }, { role: "assistant", content: "" }]);
    setBusy(true);
    const body = res?.report_id
      ? { report_id: res.report_id, question, history }
      : { question, history, drugs: drugNames.slice(0, 15), patient: patientPayload?.() || {} };
    stop.current = api.chat(body,
      (t) => patch((x) => ({ ...x, content: x.content + t })),
      () => setBusy(false),
      (checked) => patch((x) => ({ ...x, checked })),
      (review) => patch((x) => ({ ...x, review })));
  };

  return (
    <aside className="chat-panel" role="dialog" aria-label="AI 복약 상담">
      <div className="chat-head">
        <div>
          <b>💬 AI 복약 상담</b>
          <small>{res ? "분석 결과를 바탕으로, 질문에 나온 약·음식은 바로 함께 검사해서 답합니다"
            : "약·건강에 대해 무엇이든 물어보세요 · 약을 넣고 예측하면 그 결과로 더 정확히 답합니다"}</small>
        </div>
        <button className="btn mini" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      <div className="chat-body">
        {msgs.length === 0 && (
          <div className="chat-empty">
            {res
              ? <p><b>{(res.products || []).map((p) => p.input).join(" + ")}</b> 분석 결과(판정: <b>{res.summary?.verdict}</b>)를 바탕으로 답합니다.
                  다른 약이나 음식을 물어보면 지금 약과 함께 바로 검사합니다.</p>
              : <p>아직 분석한 약 조합이 없어 <b>일반 상담</b>으로 답합니다{drugNames.length ? <> (입력한 약: <b>{drugNames.join(", ")}</b>)</> : null}.
                  질문에 약 이름이 나오면 그 약을 바로 확인해서 답합니다.</p>}
            <div className="chat-sugs">
              {suggestions(res).map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            {m.checked?.length > 0 && (
              <div className="chat-checked">
                <span>🔎 함께 검사함</span>
                {m.checked.map((c) => (
                  <span key={c.name} className={`chat-check ${tone(c.verdict)}`} title={c.verdict}>
                    {c.name} · {c.verdict.split(" (")[0]}
                  </span>
                ))}
              </div>
            )}
            {m.role === "assistant"
              ? <Markdown text={clean(m.content) || "…"} cursor={busy && i === msgs.length - 1} />
              : <p>{m.content}</p>}
            {m.review?.length > 0 && (
              <div className="chat-review">
                ⚠ 아래 내용은 이 분석·검사 결과에는 근거가 없는 AI의 일반 지식입니다 — 약사·의사에게 확인하세요.
                <ul>{m.review.map((s) => <li key={s}>{s}</li>)}</ul>
              </div>
            )}
          </div>
        ))}
        <div ref={end} />
      </div>
      {msgs.length > 0 && !busy && (
        <div className="chat-sugs small">
          {suggestions(res).filter((s) => !msgs.some((m) => m.content === s)).slice(0, 3)
            .map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}
        </div>
      )}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <input className="search" value={text} maxLength={600}
          placeholder={res ? "예: 박카스 같이 마셔도 돼요? / 이 약 먹고 운전해도 되나요?" : "예: 두통약 먹고 커피 마셔도 되나요?"}
          onChange={(e) => setText(e.target.value)} />
        {busy
          ? <button type="button" className="btn" onClick={() => { stop.current?.(); setBusy(false); }}>중지</button>
          : <button type="submit" className="btn" disabled={!text.trim()}>보내기</button>}
      </form>
      <div className="chat-foot">AI 답변은 참고용이며 의사·약사를 대신하지 않습니다. 숨참·의식 저하·심한 알레르기 등 응급 증상은 즉시 119.</div>
    </aside>
  );
}
