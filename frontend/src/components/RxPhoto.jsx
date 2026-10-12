import React, { useEffect, useRef, useState } from "react";
import { api } from "../api.js";

/* 처방전·약봉투 사진으로 약 입력: the photo is shrunk in the browser, read by the local vision AI on this PC (never sent to
   the internet, never stored), each medicine line is matched to a product, and the person confirms what is added
   (with the dose written on the paper: 1회량 · 하루 횟수 · 일수). */

const MAX_SIDE = 1600;
const STATUS = { ok: ["확인됨", "ok"], check: ["확인 필요", "mid"], none: ["못 찾음", "high"] };

async function shrink(file) {
  // createImageBitmap honours the EXIF orientation of phone photos
  const bmp = await createImageBitmap(file, { imageOrientation: "from-image" }).catch(() => null);
  const src = bmp || await new Promise((ok, bad) => {
    const im = new Image();
    im.onload = () => ok(im);
    im.onerror = () => bad(new Error("사진을 열 수 없습니다"));
    im.src = URL.createObjectURL(file);
  });
  const w = src.width, h = src.height, k = Math.min(1, MAX_SIDE / Math.max(w, h));
  const c = document.createElement("canvas");
  c.width = Math.round(w * k);
  c.height = Math.round(h * k);
  c.getContext("2d").drawImage(src, 0, 0, c.width, c.height);
  return c.toDataURL("image/jpeg", 0.88);
}

export default function RxPhoto({ existing, onAdd, onClose }) {
  const file = useRef();
  const [img, setImg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const [res, setRes] = useState(null);
  const [rows, setRows] = useState([]);
  useEffect(() => { file.current?.click(); }, []);

  const pick = async (f) => {
    if (!f) return;
    setErr(null);
    setRes(null);
    setRows([]);
    setBusy(true);
    try {
      const data = await shrink(f);
      setImg(data);
      const r = await api.rxPhoto(data);
      setRes(r);
      setRows(r.items.map((it) => ({ ...it, use: it.status !== "none" && !existing.includes(it.name), choice: it.name })));
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  };
  const set = (i, patch) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const chosen = rows.filter((r) => r.use && r.choice);
  const addAll = () => {
    chosen.forEach((r) => onAdd(r.choice, r.dose));
    onClose();
  };

  return (
    <div className="sym-panel rx-panel">
      <div className="sym-head">
        <b>📷 처방전·약봉투 사진으로 입력</b>
        <small>사진은 인터넷으로 보내지 않고 이 PC의 AI만 읽습니다 · 저장하지 않음</small>
        <button className="btn mini" onClick={onClose} aria-label="닫기">✕</button>
      </div>
      <input ref={file} type="file" accept="image/*" hidden onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
      <div className="rx-top">
        {img && <img className="rx-thumb" src={img} alt="올린 사진" />}
        <div className="rx-help">
          <button className="btn" disabled={busy} onClick={() => file.current?.click()}>{img ? "다른 사진 고르기" : "사진 고르기 / 찍기"}</button>
          <div className="sym-disc">처방전의 <b>약 이름 표</b>나 약봉투의 <b>약 목록</b>이 잘 보이게, 밝은 곳에서 반듯하게 찍어 주세요.
            휴대폰에서는 카메라로 바로 찍을 수 있습니다.</div>
        </div>
      </div>
      {busy && <div className="rx-busy"><i className="spin" /> 로컬 AI가 사진 속 약 이름을 읽는 중… (보통 3~15초)</div>}
      {err && <div className="error">⚠ {err}</div>}
      {res && !rows.length && <div className="sym-note">사진에서 약 이름을 찾지 못했습니다 — 약 목록 부분이 크게 보이게 다시 찍어 주세요.</div>}
      {rows.length > 0 && (
        <div className="rx-list">
          <div className="sym-disc">{res.document && <b>{res.document} · </b>}{rows.length}개 줄을 읽었습니다
            ({(res.elapsed_ms / 1000).toFixed(1)}초). <b>실제 종이와 꼭 비교해</b> 맞는 것만 체크하세요.</div>
          {rows.map((r, i) => (
            <label key={r.raw} className={`rx-row st-${r.status}`}>
              <input type="checkbox" checked={r.use} disabled={!r.choice} onChange={(e) => set(i, { use: e.target.checked })} />
              <div className="rx-main">
                <div className="sym-line">
                  <span className={`sym-badge ${STATUS[r.status][1]}`}>{STATUS[r.status][0]}</span>
                  {r.status === "ok" && <b>{r.name}</b>}
                  {r.status !== "ok" && (
                    <select value={r.choice || ""} onChange={(e) => set(i, { choice: e.target.value, use: !!e.target.value })}>
                      <option value="">— 제품 선택 —</option>
                      {r.name && !r.candidates.some((c) => c.name === r.name) && <option value={r.name}>{r.name}</option>}
                      {r.candidates.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                    </select>
                  )}
                  {existing.includes(r.choice) && <small className="first">이미 추가됨</small>}
                </div>
                <div className="rx-sub">사진: “{r.raw}”{r.ingredients?.length > 0 && <> → {r.ingredients.join(", ")}</>}
                  {r.by_ingredient && " (제품을 못 찾아 성분으로 인식)"}</div>
                {r.dose_text && <div className="rx-dose">💊 {r.dose_text}{r.dose && " — 용량으로 함께 입력"}</div>}
              </div>
            </label>
          ))}
          <button className="btn sym-go" disabled={!chosen.length} onClick={addAll}>
            {chosen.length ? `선택한 ${chosen.length}개 약 추가` : "추가할 약을 체크하세요"}</button>
          <div className="sym-disc">사진 속 글씨를 AI가 읽은 결과라 틀릴 수 있습니다. 추가한 뒤 <b>▶ 예측 시작</b>을 누르면 함께 먹어도 되는지 확인합니다.</div>
        </div>
      )}
    </div>
  );
}
