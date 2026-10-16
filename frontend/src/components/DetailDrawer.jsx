import React, { useEffect, useState } from "react";
import { api, fx, pct } from "../api.js";

const ORGAN_KO = { brain: "뇌·신경", heart: "심장", lung: "폐", liver: "간", gi: "위장관", kidney: "신장", blood: "혈액", systemic: "전신" };
const COND_KO = { renal: "신장질환", hepatic: "간질환", cardiac: "심장질환", diabetes: "당뇨", hypertension: "고혈압", age_80_vs_30: "80세 vs 30세" };

function Pairs({ res }) {
  return (
    <div className="pairs">
      {res.pairs.map((p) => (
        <div key={p.a_key + p.b_key} className={`pair lv-${p.level === "높음" ? "high" : p.level === "주의" ? "caution" : "normal"}`}>
          <div className="pair-head"><b>[{p.level}]</b> {p.a} ＋ {p.b} {p.novel && <span className="novel">신규 구조</span>}{p.biologic && <span className="novel">구조 AI 대상 아님</span>}</div>
          <div className="pair-cols">
            <div>
              {p.dur && (<>
                <h5>⛔ 식약처 DUR 병용금기</h5>
                <p><b>병용금기 성분 조합</b>{p.dur.content && <> — {p.dur.content}</>}
                  {p.dur.date && <span className="muted small"> (고시 {p.dur.date})</span>}</p>
              </>)}
              <h5>① FDA 허가 라벨 (문서화된 사실)</h5>
              {p.label_evidence.length ? p.label_evidence.map((e, i) => (
                <div key={i} className="ev-quote"><b>{e.label_of}</b> 라벨 [{e.section}]에 <b>{e.via_class ? `“${e.via_class}”(${e.mentions} 포함 계열)` : e.mentions}</b> 기재
                  {e.n_labels > 1 && <span className="muted"> · 라벨 {e.n_labels}건</span>}
                  {e.context === "no_interaction" && <span className="ok-tag"> ✅ 상호작용 없음으로 기재</span>}
                  {e.context === "co_therapy" && <span className="muted"> · 병용 치료 안내(상호작용 아님)</span>}
                  <p>{e.excerpt}</p></div>
              )) : <p className="muted">라벨에서 상대 약물 언급 없음{p.novel ? " (신규 구조는 라벨 없음)" : ""}</p>}
            </div>
            <div>
              {p.mechanism?.length > 0 && (<>
                <h5>임상 규칙 · 약리 기전 (허가사항 · FDA 대사효소 지표표 · ChEMBL)</h5>
                {p.mechanism.map((m) => <p key={m.text}>{m.kind === "규칙" ? "📋" : m.kind.startsWith("식약처") ? "🇰🇷" : "🧬"} {m.text}
                  {m.source && <span className="muted small"> — 출처: {m.source}{m.review === "미검토" ? " · 약사 검토 전" : m.review ? ` · 약사 검토: ${m.review}` : ""}</span>}</p>)}
              </>)}
              <h5>② 실제 병용 보고 신호 (TWOSIDES)</h5>
              {p.observed.length ? <p>{p.observed.map((e) => e.ko).join(", ")}</p> : <p className="muted">유의한 신호 없음 / 데이터 부족</p>}
              <p className="muted small">PRR ≥ 2, 보고 3건 이상 (관찰 연관성, 인과 아님)</p>
            </div>
            <div>
              <h5>③ AI 구조 예측 ({p.model_kind || "GNN"})</h5>
              {p.biologic ? <p className="muted">생물의약품·생약·비타민 등 분자 구조가 없는 성분은 구조 AI 대상이 아닙니다</p> : p.model.length ? (
                <table className="mini-t"><tbody>
                  {p.model.slice(0, 8).map((m) => (
                    <tr key={m.ko}><td>{m.ko}{m.serious && <span className="sev">중대</span>}</td><td className="mono">{pct(m.prob, 0)}</td><td className="mono muted">기준 {pct(m.threshold, 0)}</td></tr>
                  ))}
                </tbody></table>
              ) : <p className="muted">기준치를 넘는 예측 없음</p>}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function Events({ res }) {
  return (
    <table className="tbl">
      <thead><tr><th>이상반응</th><th>장기</th><th>환자 맞춤 확률</th><th>일반 기준선</th><th>기준 대비</th><th>표준 환자 대비</th><th>조합 초과분</th><th>주요 기여 약물</th></tr></thead>
      <tbody>
        {res.events.map((e) => (
          <tr key={e.id} className={e.lift >= 3 ? "hi" : e.lift >= 1.5 ? "mid" : ""}>
            <td>{e.ko}{e.serious && <span className="sev">중대</span>}</td><td>{ORGAN_KO[e.organ]}</td>
            <td className="mono">{pct(e.prob)}</td><td className="mono muted">{pct(e.baseline)}</td>
            <td className="mono">×{fx(e.lift)}</td><td className="mono">×{fx(e.patient_factor)}</td>
            <td className="mono">{e.combo_excess > 0 ? "+" : ""}{pct(e.combo_excess)}</td><td>{e.driver || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Contrib({ res }) {
  if (!res.drug_contrib.length) return <p className="muted">약물이 2개 이상일 때 계산됩니다.</p>;
  return (
    <div>
      <p className="muted">각 약물을 하나씩 뺐을 때 예측 확률이 얼마나 떨어지는지 (leave-one-out). 값이 클수록 그 약물이 해당 위험을 주도합니다.</p>
      <div className="contrib">
        {res.drug_contrib.map((d) => (
          <div key={d.drug} className="contrib-card">
            <b>{d.drug}</b>
            {d.top.map((t) => <div key={t.ko}>{t.ko} <span className="mono">{t.delta >= 0 ? "+" : ""}{pct(t.delta)}</span></div>)}
          </div>
        ))}
      </div>
    </div>
  );
}

function Metric({ label, m, chance }) {
  if (!m) return null;
  return (
    <tr><td>{label}</td><td className="mono">{m.n?.toLocaleString()}</td><td className="mono">{m.macro_auroc.toFixed(3)}</td>
      <td className="mono">{m.macro_auprc.toFixed(3)}</td><td className="mono muted">{(chance ?? m.mean_prevalence).toFixed(3)}</td>
      <td className="mono">{m.ece.toFixed(3)}</td></tr>
  );
}

function Validation() {
  const [m, setM] = useState(null);
  useEffect(() => { api.metrics().then(setM).catch(() => setM({})); }, []);
  if (!m) return <p className="muted">불러오는 중…</p>;
  const { pair, patient, ablation_no_patient: ab } = m;
  return (
    <div className="valid">
      <p className="muted">AUROC 0.5 = 무작위, 1.0 = 완벽 · AUPRC는 무작위 기준(양성 비율)과 비교 · ECE = 확률 보정 오차(낮을수록 확률이 실제 빈도와 일치)</p>
      {patient && (
        <>
          <h5>A. 환자 맞춤 다약제 모델 (FAERS {patient.n_reports?.toLocaleString()}건, 학습 {patient.trained_at})</h5>
          <table className="tbl"><thead><tr><th>평가 세트</th><th>N</th><th>macro AUROC</th><th>macro AUPRC</th><th>무작위</th><th>ECE</th></tr></thead>
            <tbody>
              <Metric label="미래 시점 보고 (최근 4분기)" m={patient.test_time} />
              <Metric label="  └ 2개 이상 약물 보고" m={patient.test_time_multi_drug} />
              <Metric label="처음 보는 약물 포함 보고" m={patient.test_unseen} />
              <Metric label="다른 나라 외부 검증 (캐나다, 학습 제외)" m={patient.test_canada} />
              {ab && <Metric label="(비교) 환자 정보 제외 모델 — 미래 시점" m={ab.test_time} />}
            </tbody></table>
          {patient.patient_sensitivity && (
            <>
              <h5>환자 요인 민감도 — 같은 약물에서 환자 요인만 바꿨을 때 장기별 예측 위험 배수</h5>
              <table className="tbl"><thead><tr><th>요인</th>{Object.keys(ORGAN_KO).map((o) => <th key={o}>{ORGAN_KO[o]}</th>)}</tr></thead>
                <tbody>{Object.entries(patient.patient_sensitivity).map(([k, v]) => (
                  <tr key={k}><td>{COND_KO[k] || k}</td>{Object.keys(ORGAN_KO).map((o) => (
                    <td key={o} className={`mono ${v[o] >= 1.5 ? "hi" : v[o] >= 1.15 ? "mid" : ""}`}>×{v[o]?.toFixed(2)}</td>))}</tr>
                ))}</tbody></table>
            </>
          )}
        </>
      )}
      {pair && (
        <>
          <h5>B. 약물쌍 구조 모델 (TWOSIDES {pair.n_pairs?.toLocaleString()}쌍, 약물 {pair.n_drugs}종)</h5>
          <table className="tbl"><thead><tr><th>평가 세트</th><th>N</th><th>macro AUROC</th><th>macro AUPRC</th><th>무작위</th><th>ECE</th></tr></thead>
            <tbody>
              <Metric label="★ 앙상블 · 기존 약물 조합 (배포)" m={pair.test_random_split} />
              <Metric label="★ GNN · 신규 약물 1개 포함 (배포)" m={pair.gnn_only?.test_unseen_one_drug} />
              <Metric label="★ GNN · 두 약물 모두 신규 (배포)" m={pair.gnn_only?.test_unseen_both_drugs} />
              <Metric label="(비교) Morgan 지문 · 신규 약물 1개" m={pair.fingerprint_only?.test_unseen_one_drug} />
              <Metric label="(비교) 앙상블 · 신규 약물 1개" m={pair.test_unseen_one_drug} />
            </tbody></table>
          <p className="muted small">★ 배포 규칙: 학습에 포함된 약물끼리는 앙상블, 학습에 없던 분자(신규 SMILES·최신 약물)가 끼면 새 구조 일반화가 가장 좋은 GNN 단독을 사용합니다.</p>
        </>
      )}
      <Stats s={m.stats} />
      {!pair && !patient && <p className="muted">학습된 모델 지표가 없습니다.</p>}
    </div>
  );
}

// 95% confidence intervals, significance tests and baselines (artifacts/v2/stats_metrics.json, stage "stats")
const TEST_KO = { test_time: "미래 시점", test_unseen: "처음 보는 약물 포함", test_canada: "캐나다 외부" };
const MODEL_KO = {
  full: "★ 환자 맞춤 모델 (구조 GNN + 환자 정보)", no_patient: "같은 모델 · 환자 정보 제외",
  logistic: "기준선: 로지스틱 회귀 (약 원-핫 + 환자 정보)", drug_frequency: "기준선: 약물별 이상반응 빈도",
};
const f3 = (x) => (x == null ? "-" : x.toFixed(3));
const ciText = (c) => (c ? `${f3(c[0])}–${f3(c[1])}` : "");
const pText = (d) => (d.p_is_upper_bound ? `p < ${(1 / d.n_boot).toFixed(3)}` : `p = ${d.p_value.toFixed(3)}`);
const ciPct = (c) => `${Math.round(c[0] * 100)}–${Math.round(c[1] * 100)}%`;

function Stats({ s }) {
  if (!s?.patient) return null;
  const tests = Object.keys(TEST_KO).filter((t) => s.patient[t]);
  const diff = (d) => d && <>{d.difference >= 0 ? "+" : ""}{f3(d.difference)} <small>[{ciText(d.ci95)}] {pText(d)}</small></>;
  const pu = s.pair?.test_unseen_one_drug;
  const au = s.clinical?.audit;
  const dur = s.clinical?.dur_external;
  return (
    <>
      <h5>C. 통계 검증 — macro AUROC와 95% 신뢰구간 (평가 자료를 1,000회 다시 뽑는 부트스트랩)</h5>
      <table className="tbl"><thead><tr><th>모델</th>{tests.map((t) => (
        <th key={t}>{TEST_KO[t]} <small>({s.patient[t].n.toLocaleString()}건
          {s.patient[t].n_clusters ? ` · 처음 보는 약 ${s.patient[t].n_clusters}개 단위로 재표본` : ""})</small></th>))}</tr></thead>
        <tbody>
          {Object.keys(MODEL_KO).map((k) => (
            <tr key={k}><td>{MODEL_KO[k]}</td>{tests.map((t) => {
              const m = s.patient[t].models[k];
              return <td key={t} className="mono">{f3(m?.macro_auroc)} <small>[{ciText(m?.macro_auroc_ci95)}]</small></td>;
            })}</tr>
          ))}
          <tr><td>환자 정보의 효과 (차이)</td>{tests.map((t) => <td key={t} className="mono">{diff(s.patient[t].full_vs_no_patient)}</td>)}</tr>
          <tr><td>└ 이상반응별 DeLong 검정 (Holm 보정 p&lt;0.05)</td>{tests.map((t) => {
            const d = s.patient[t].delong_full_vs_no_patient;
            return <td key={t}>{d ? `${d.events}개 중 향상 ${d.better} · 저하 ${d.worse}` : <small className="muted">해당 없음(약물 단위 자료)</small>}</td>;
          })}</tr>
          <tr><td>기준선(로지스틱) 대비 차이</td>{tests.map((t) => <td key={t} className="mono">{diff(s.patient[t].full_vs_logistic)}</td>)}</tr>
        </tbody></table>
      {pu && (
        <p className="small">약물쌍 모델, 신규 약물 1개 포함 ({pu.n.toLocaleString()}쌍, 신규 약물 {pu.n_clusters}개 단위 재표본): GNN {f3(pu.models.gnn.macro_auroc)}
          <small> [{ciText(pu.models.gnn.macro_auroc_ci95)}]</small> vs Morgan 지문 {f3(pu.models.fingerprint.macro_auroc)}
          <small> [{ciText(pu.models.fingerprint.macro_auroc_ci95)}]</small> — 차이 {diff(pu.gnn_vs_fingerprint)}</p>
      )}
      {au && (
        <p className="small">임상 감사(규칙 판정, Wilson 95% 신뢰구간): 처음 보는 세트 첫 측정 {au.holdout_first_measurement.k}/{au.holdout_first_measurement.n}
          ({ciPct(au.holdout_first_measurement.ci95)}) · 중대 조합 {au.holdout_major_first.k}/{au.holdout_major_first.n} ({ciPct(au.holdout_major_first.ci95)})
          {dur && <> · 식약처 DUR 병용금기 {dur.n.toLocaleString()}쌍(DUR 끔) '위험' {Math.round(dur["위험"].rate * 100)}% ({ciPct(dur["위험"].ci95)})</>}</p>
      )}
      <p className="muted small">차이의 95% 신뢰구간이 0을 포함하지 않으면 우연이 아닌 차이로 봅니다. 처음 보는 약물 평가는 보고·쌍이 소수의 약물을
        공유하므로 보고가 아니라 약물을 다시 뽑아 신뢰구간을 구했습니다(더 넓고 보수적). 기준선은 분자 구조를 쓰지 않아 처음 보는 약물에 대해
        아무것도 배우지 못한 상태입니다. 개발에 쓴 감사 세트(109개)는 독립 평가가 아니라 회귀 검사용입니다.</p>
    </>
  );
}

function Limits() {
  return (
    <div className="limits">
      <h5>데이터 출처</h5>
      <ul>
        <li><b>FAERS</b> (미 FDA 이상사례 보고 시스템) 분기별 원자료 — 환자 나이·성별·체중·적응증과 복용 약물, 보고된 이상반응</li>
        <li><b>Canada Vigilance</b> (캐나다 보건부 부작용 DB) — 학습 추가 및 다른 나라 외부 검증</li>
        <li><b>TWOSIDES</b> (Tatonetti et al., Sci Transl Med 2012) — FAERS 기반 약물쌍 이상반응 통계 신호</li>
        <li><b>식약처 DUR</b> (의약품안전사용서비스) — 병용금기·특정연령대금기·임부금기·용량주의·투여기간주의·노인주의·효능군중복 공식 목록</li>
        <li><b>openFDA Drug Label</b> — 허가 라벨 전체(약 6.8만 건, 일반약 Drug Facts 포함)의 상호작용·금기·박스경고 원문 전문 검색</li>
        <li><b>ChEMBL</b> — 대사효소(CYP)·hERG 시험관 활성, 작용 기전 · <b>FDA 약물상호작용 지표 약물표</b></li>
        <li><b>PubChem</b> — 약물 이름 → 실제 화학 구조(SMILES), RDKit으로 표준화·3D 좌표 생성</li>
      </ul>
      <h5>해석 시 주의</h5>
      <ul>
        <li>“환자 맞춤 확률”은 <b>이상사례가 보고된 환자들 중</b> 해당 이상반응이 보고될 비율의 추정치입니다. 전체 복용자 중 발생률이 아닙니다.</li>
        <li>FAERS/TWOSIDES는 자발적 보고 자료로 보고 편향이 있으며 인과관계를 증명하지 않습니다.</li>
        <li>간·신장 수치는 FAERS에 없으므로 CKD-EPI 2021·ULN 기준으로 ‘신장/간 질환’ 입력으로 변환해 모델에 반영합니다.</li>
        <li>생물의약품·펩타이드(GLP-1 비만약·인슐린·보툴리눔톡신·항체 등 19종)는 분자 구조 AI 대상이 아니어서 허가 문서 전문 검색과 임상 규칙(저혈당·신경근 차단·질환 금기 등)으로만 평가합니다.</li>
        <li>의료기기 인허가를 받지 않은 연구·교육용 의사결정 보조 시스템이며, 의사·약사의 판단을 대체하지 않습니다.</li>
      </ul>
    </div>
  );
}

const TABS = [["pairs", "약물 조합 근거"], ["events", "이상반응 전체"], ["contrib", "약물별 기여도"], ["valid", "모델 검증"], ["limits", "데이터·한계"]];

export default function DetailDrawer({ res, onClose, initial = "pairs" }) {
  const [tab, setTab] = useState(res ? initial : "valid");
  return (
    <div className="drawer-bg" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head">
          <div className="tabs">
            {TABS.map(([k, l]) => (
              <button key={k} className={tab === k ? "on" : ""} disabled={!res && !["valid", "limits"].includes(k)} onClick={() => setTab(k)}>{l}</button>
            ))}
          </div>
          <button className="btn mini" onClick={onClose}>닫기 ✕</button>
        </div>
        <div className="drawer-body">
          {tab === "pairs" && res && <Pairs res={res} />}
          {tab === "events" && res && <Events res={res} />}
          {tab === "contrib" && res && <Contrib res={res} />}
          {tab === "valid" && <Validation />}
          {tab === "limits" && <Limits />}
        </div>
      </div>
    </div>
  );
}
