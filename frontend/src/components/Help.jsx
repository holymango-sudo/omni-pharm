import React, { useState } from "react";

export function HelpModal({ onClose }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal help" onClick={(e) => e.stopPropagation()}>
        <h3>OMNI-PHARM 사용법</h3>
        <ol className="steps">
          <li><b>환자 정보</b> (왼쪽) — 나이·키·몸무게·성별을 맞추고, 검사값(크레아티닌, 간수치)이 있으면 입력하세요.
            앓고 있는 병을 모두 눌러 주세요. 모르는 값은 기본값 그대로 두어도 됩니다.</li>
          <li><b>약 추가</b> (가운데 아래) — 약 이름을 입력하고 목록에서 고르세요. 한국 제품(콜대원), 해외 제품(Tylenol, Doliprane),
            일본 약(ロキソニン), 성분명(와파린), 화학 구조식(SMILES) 모두 됩니다. 2개 이상 넣으세요.</li>
          <li><b>예측 시작</b>을 누르면 오른쪽에 결과가 나옵니다.</li>
        </ol>
        <h4>결과 읽는 법</h4>
        <div className="legend3">
          <span className="lg high">위험</span> 함께 먹지 않는 것이 좋거나, 이 환자에게 특히 해로운 조합
          <span className="lg caution">주의</span> 복용은 가능하나 용량·증상 관찰이 필요한 조합
          <span className="lg normal">비교적 안전</span> 뚜렷한 위험 신호가 없음
        </div>
        <ul className="tips">
          <li>"왜 그런가요?"에 이유가, "어떻게 해야 하나요?"에 할 일이 쉬운 말로 적혀 있습니다.</li>
          <li>근거는 ① 허가 문서(FDA·식약처) ② 실제 부작용 보고 ③ AI 예측 순으로 신뢰합니다. <b>상세 근거</b>에서 원문을 볼 수 있습니다.</li>
          <li>의사·약사는 상단 <b>전문가 보기</b>를 켜면 확률·배수·공식 값을 함께 볼 수 있습니다.</li>
          <li><b>인쇄/PDF</b>로 환자용 안내문 또는 전문가용 리포트를 출력할 수 있습니다.</li>
        </ul>
        <p className="disclaimer">
          이 프로그램은 의사·약사의 판단을 돕는 <b>참고용 도구</b>이며 의료기기 인허가를 받지 않았습니다.
          약을 끊거나 바꾸기 전에는 반드시 의사·약사와 상의하세요. 입력한 환자 정보는 이 컴퓨터 밖으로 전송·저장되지 않습니다.
        </p>
        <button className="btn predict" onClick={onClose}>시작하기</button>
      </div>
    </div>
  );
}

export function LoginModal({ onLogin }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try { await onLogin(u, p); } catch (x) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <div className="modal-bg">
      <form className="modal pin" onSubmit={submit}>
        <h3>로그인</h3>
        <p className="muted small">이 OMNI-PHARM 서버는 사용자 계정으로 보호되어 있습니다. 사용 기록은 감사 로그에 남습니다(환자 정보 제외).</p>
        <input className="search" placeholder="아이디" autoFocus value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" />
        <input className="search" type="password" placeholder="비밀번호" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        {err && <div className="error">{err}</div>}
        <button className="btn predict" type="submit" disabled={busy || !u || !p}>{busy ? "확인 중…" : "로그인"}</button>
      </form>
    </div>
  );
}

export function PinModal({ onSubmit }) {
  const [v, setV] = useState("");
  return (
    <div className="modal-bg">
      <form className="modal pin" onSubmit={(e) => { e.preventDefault(); onSubmit(v); }}>
        <h3>접근 PIN 입력</h3>
        <p className="muted small">이 OMNI-PHARM 서버는 PIN으로 보호되어 있습니다. 관리자에게 받은 PIN을 입력하세요.</p>
        <input className="search" type="password" autoFocus value={v} onChange={(e) => setV(e.target.value)} />
        <button className="btn predict" type="submit">확인</button>
      </form>
    </div>
  );
}
