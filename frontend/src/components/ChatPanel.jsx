import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import Markdown from "./Markdown.jsx";

/* AI 복약 상담: questions about the current analysis, answered by the local LLM from the analysis facts only.
   Conversations live in this browser tab (nothing is stored on the server). */

function suggestions(res) {
  const out = ["이 약들 같이 먹어도 되나요?", "언제, 어떻게 먹는 게 좋아요?"];
  const names = (res?.products || []).map((p) => p.input);
  if (res?.summary?.verdict !== "비교적 안전") out.push("왜 위험(주의)이라고 나왔나요?");
  out.push("술이나 커피를 마셔도 되나요?", "어떤 증상이 생기면 병원에 가야 하나요?");
  if (names.length >= 2) out.push(`${names[0]}과 ${names[1]}은 몇 시간 간격을 둬야 하나요?`);
  if ((res?.pathway?.drugs || []).some((d) => d.release === "ER")) out.push("서방형은 왜 쪼개면 안 되나요?");
  out.push("약을 깜빡하고 안 먹었으면 어떻게 하나요?");
  return out.slice(0, 7);
}

export default function ChatPanel({ res, open, onClose }) {
  const [msgs, setMsgs] = useState([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const stop = useRef(null);
  const end = useRef(null);
  useEffect(() => { setMsgs([]); stop.current?.(); setBusy(false); }, [res?.report_id]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);
  if (!open) return null;

  const ask = (q) => {
    const question = (q ?? text).trim();
    if (!question || busy || !res?.report_id) return;
    setText("");
    const history = msgs.map(({ role, content }) => ({ role, content }));
    setMsgs((m) => [...m, { role: "user", content: question }, { role: "assistant", content: "" }]);
    setBusy(true);
    stop.current = api.chat({ report_id: res.report_id, question, history },
      (t) => setMsgs((m) => { const c = [...m]; c[c.length - 1] = { ...c[c.length - 1], content: c[c.length - 1].content + t }; return c; }),
      () => setBusy(false));
  };

  return (
    <aside className="chat-panel" role="dialog" aria-label="AI 복약 상담">
      <div className="chat-head">
        <div>
          <b>💬 AI 복약 상담</b>
          <small>방금 분석한 약에 대해 물어보세요 · 로컬 AI가 분석 결과만 근거로 답합니다</small>
        </div>
        <button className="btn mini" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      <div className="chat-body">
        {!res && <div className="chat-empty">먼저 약을 넣고 <b>▶ 예측 시작</b>을 누르세요. 그 결과를 바탕으로 답합니다.</div>}
        {res && msgs.length === 0 && (
          <div className="chat-empty">
            <p><b>{(res.products || []).map((p) => p.input).join(" + ")}</b> 분석 결과(판정: <b>{res.summary?.verdict}</b>)를 바탕으로 답합니다.</p>
            <div className="chat-sugs">
              {suggestions(res).map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}
            </div>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={i} className={`chat-msg ${m.role}`}>
            {m.role === "assistant"
              ? <Markdown text={m.content || "…"} cursor={busy && i === msgs.length - 1} />
              : <p>{m.content}</p>}
          </div>
        ))}
        <div ref={end} />
      </div>
      {res && msgs.length > 0 && !busy && (
        <div className="chat-sugs small">
          {suggestions(res).filter((s) => !msgs.some((m) => m.content === s)).slice(0, 3)
            .map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}
        </div>
      )}
      <form className="chat-input" onSubmit={(e) => { e.preventDefault(); ask(); }}>
        <input className="search" value={text} maxLength={600} disabled={!res}
          placeholder={res ? "예: 이 약 먹고 운전해도 되나요?" : "예측 후 질문할 수 있습니다"} onChange={(e) => setText(e.target.value)} />
        {busy
          ? <button type="button" className="btn" onClick={() => { stop.current?.(); setBusy(false); }}>중지</button>
          : <button type="submit" className="btn" disabled={!text.trim() || !res}>보내기</button>}
      </form>
      <div className="chat-foot">AI 답변은 참고용이며 의사·약사를 대신하지 않습니다. 숨참·의식 저하·심한 알레르기 등 응급 증상은 즉시 119.</div>
    </aside>
  );
}
