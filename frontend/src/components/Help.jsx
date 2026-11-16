import React, { useState } from "react";
import { DEMO } from "../api.js";

/** 공개 데모 사이트에서 되는 것 / 설치형 프로그램에서만 되는 것 (src/demo/replay.js가 녹화에서 찾는 범위와 같아야 함) */
function DemoGuide() {
  return (
    <div className="demo-guide">
      <p className="demo-lead">
        이 사이트는 <b>공개 데모</b>입니다. 서버 없이, 실제 프로그램(로컬 AI 켜짐)으로 <b>미리 분석해 둔 결과</b>를 그대로 보여 줍니다.
      </p>
      <ol className="steps">
        <li>왼쪽 <b>환자 예시 4명</b>(간경변 고령 · 녹내장·전립선 노인 · 만성콩팥병 · 건강한 성인) 중 한 명을 누르세요.
          값을 직접 바꾸면 분석되지 않습니다.</li>
        <li>가운데 <b>데모 시나리오</b> 버튼 7개 중 하나를 누르세요. ‘건강한 성인’이면 술·자몽주스 같은 <b>함께 확인</b> 1가지를 더할 수 있습니다.</li>
        <li><b>▶ 예측 시작</b> → 판정·근거·3D 인체 여정·AI 전문가 소견이 나옵니다. <b>💬 AI 복약 상담</b>에서는 추천 질문 버튼을 눌러 보세요.</li>
      </ol>
      <div className="demo-cols">
        <div className="demo-col ok">
          <b>✅ 데모에서 되는 것</b>
          <ul>
            <li>데모 시나리오 7개 × 환자 예시 4명 분석 (판정·근거·3D 분자·인체 여정·AI 소견·인쇄)</li>
            <li>건강한 성인 + 시나리오 + 음식·술 1가지 (술·자몽주스·우유·커피·녹색채소·흡연)</li>
            <li>AI 복약 상담의 <b>추천 질문 버튼</b> (분석 결과 상담, 분석 전 일반 상담)</li>
            <li>증상·질환으로 약 찾기: 약을 넣기 전, 증상 51개 중 <b>1개</b></li>
            <li>전문가 보기, 모델 검증 화면</li>
          </ul>
        </div>
        <div className="demo-col no">
          <b>🖥 설치형 프로그램에서만 되는 것</b>
          <ul>
            <li>그 밖의 약 검색·추가 (국내외 제품 16.9만 개, 성분, SMILES)</li>
            <li>복용량 입력, 나이·검사값·질환 직접 수정</li>
            <li>AI에게 직접 쓴 질문, 증상 여러 개·자유 검색</li>
            <li>처방전·약봉투 사진 읽기</li>
          </ul>
        </div>
      </div>
    </div>
  );
}

export function HelpModal({ onClose }) {
  return (
    <div className="modal-bg" onClick={() => onClose()}>
      <div className="modal help" onClick={(e) => e.stopPropagation()}>
        <h3>OMNI-PHARM {DEMO ? "공개 데모 안내" : "사용법"}</h3>
        {DEMO && <DemoGuide />}
        {!DEMO && <ol className="steps">
          <li><b>환자 정보</b> (왼쪽) — 나이·키·몸무게·성별을 맞추고, 검사값(크레아티닌, 간수치)이 있으면 입력하세요.
            앓고 있는 병을 모두 눌러 주세요. 모르는 값은 기본값 그대로 두어도 됩니다.</li>
          <li><b>약 추가</b> (가운데 아래) — 약 이름을 입력하고 목록에서 고르세요. 한국 제품(콜대원), 해외 제품(Tylenol, Doliprane),
            일본 약(ロキソニン), 성분명(와파린), 화학 구조식(SMILES) 모두 됩니다. 2개 이상 넣으세요.</li>
          <li><b>예측 시작</b>을 누르면 오른쪽에 결과가 나옵니다.</li>
        </ol>}
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
          <b>사용 목적</b>: 연구·교육용 의사결정 보조 도구입니다. 의료기기 인허가를 받지 않았으며, 진단·처방 결정을 이것만으로 하면
          안 됩니다. 응급 상황이면 바로 119에 연락하세요. 약을 끊거나 바꾸기 전에는 반드시 의사·약사와 상의하세요.
          입력한 환자 정보는 이 컴퓨터 밖으로 전송·저장되지 않습니다.
        </p>
        <h4>누가 사용하시나요?</h4>
        <div className="usertype">
          <button className="btn predict" onClick={() => onClose("public")}>일반인(환자·보호자)으로 시작
            <small>쉬운 말과 할 일 중심, 숫자는 숨김</small></button>
          <button className="btn ghost" onClick={() => onClose("pro")}>의료인(약사·의사·학생)으로 시작
            <small>전문가 보기: 확률·배수·근거 원문까지</small></button>
        </div>
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
