import { mdToHtml } from "./components/Markdown.jsx";
import { fx, pct } from "./api.js";

const esc = (s) => String(s ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;");

function open(html) {
  const w = window.open("", "_blank");
  if (!w) { alert("팝업이 차단되었습니다. 브라우저에서 이 사이트의 팝업을 허용해 주세요."); return; }
  w.document.write(html);
  w.document.close();
}

/** Large-print, number-free handout for the patient / family. */
function patientHandout(res, patient) {
  const s = res.summary;
  const warn = s.reasons.filter((r) => r.sev !== "low");  // the reasons behind the verdict
  const info = s.reasons.filter((r) => r.sev === "low");  // a drug's own side effects, reassurance, rare signals
  const color = { 위험: "#c00", 주의: "#c70", "비교적 안전": "#077" }[s.verdict];
  const drugs = res.products.map((p) => `<li><b>${esc(p.input)}</b> <span class="sub">(${p.ingredients.map(esc).join(", ")})</span></li>`).join("");
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>복약 안전 안내</title><style>
    body{font-family:'Malgun Gothic','Noto Sans KR',sans-serif;color:#111;margin:32px;font-size:17px;line-height:1.7}
    h1{font-size:26px;margin:0 0 6px}.v{font-size:34px;font-weight:800;color:${color};border:3px solid ${color};
    display:inline-block;padding:4px 18px;border-radius:10px;margin:8px 0}h2{font-size:20px;margin:22px 0 6px;border-bottom:2px solid #ddd}
    li{margin:6px 0}.sub{color:#666;font-size:14px}.box{background:#f5f7f8;border-radius:8px;padding:10px 16px}
    .foot{margin-top:28px;font-size:13px;color:#666}
  </style></head><body>
  <h1>복용하시는 약의 안전 안내</h1><div>${new Date().toLocaleDateString("ko-KR")} · ${patient.age}세 ${patient.sex === "F" ? "여성" : "남성"}</div>
  <div class="v">${esc(s.verdict)}</div><div><b>${esc(s.headline)}</b></div>
  <h2>함께 확인한 약</h2><ul>${drugs}</ul>
  ${warn.length ? `<h2>${s.verdict === "위험" ? "왜 위험한가요?" : "확인할 점"}</h2><ul>${warn.map((r) => `<li>${esc(r.text)}</li>`).join("")}</ul>`
    : `<h2>확인 결과</h2><p>함께 드셔서 문제가 되는 조합은 확인되지 않았습니다.</p>`}
  ${info.length ? `<h2>참고</h2><ul class="sub">${info.map((r) => `<li>${esc(r.text)}</li>`).join("")}</ul>` : ""}
  ${(res.controlled || []).length ? `<h2>규제 물질 안내</h2><ul>${res.controlled.map((c) =>
    `<li><b>${esc(c.input)}</b> — 한국: ${esc(c.law)}<br><span class="sub">위험 신호: ${esc(c.signs)}</span></li>`).join("")}
    <li>혼자 쓰지 말고 여러 물질을 섞지 마세요. 응급 시 <b>119</b>, 마약류 24시간 익명 상담 <b>1342</b></li></ul>` : ""}
  <h2>이렇게 하세요</h2><div class="box"><ul>${s.advice.map((a) => `<li>${esc(a)}</li>`).join("")}
    <li>숨이 차거나, 검은 변·피가 섞인 변, 심한 어지러움, 가슴 두근거림, 피부 발진이 생기면 즉시 복용을 멈추고 병원에 연락하세요.</li></ul></div>
  <div class="foot">이 안내문은 컴퓨터 분석 결과를 쉽게 정리한 참고 자료입니다. 약을 끊거나 바꾸기 전에 반드시 의사·약사와 상의하세요.</div>
  <script>setTimeout(()=>print(),300)</script></body></html>`;
}

/** Opens a print-ready report (browser "Save as PDF"). mode = "patient" (plain handout) | "pro" (clinical). */
export function printReport(res, patient, report, mode = "pro") {
  if (mode === "patient") { open(patientHandout(res, patient)); return; }
  const k = res.patient.kidney, l = res.patient.liver;
  const rows = res.events.filter((e) => e.lift >= 1.2 && e.prob >= 0.005).slice(0, 12).map((e) =>
    `<tr><td>${esc(e.ko)}${e.serious ? " <b>(중대)</b>" : ""}</td><td>${pct(e.prob)}</td><td>${pct(e.baseline)}</td><td>×${fx(e.lift)}</td><td>×${fx(e.patient_factor)}</td><td>${esc(e.driver || "")}</td></tr>`).join("");
  const tag = (e) => (e.context === "no_interaction" ? " <b>[상호작용 없음으로 기재]</b>"
    : e.context === "co_therapy" ? " <i>[병용 치료 안내 — 상호작용 아님]</i>" : e.combo ? " <i>[복합제 라벨]</i>" : "");
  const pairs = res.pairs.map((p) => `<div class="pair"><b>[${p.level}] ${esc(p.a)} + ${esc(p.b)}</b>
    ${p.dur ? `<div><b>식약처 DUR 병용금기</b>${p.dur.content ? `: ${esc(p.dur.content)}` : ""}${p.dur.date ? ` (고시 ${esc(p.dur.date)})` : ""}</div>` : ""}
    ${(p.mechanism || []).map((m) => `<div>${m.kind === "규칙" ? "임상 규칙" : "약리 기전"}: ${esc(m.text)}</div>`).join("")}
    ${p.label_evidence.map((e) => `<div>FDA 라벨(${esc(e.label_of)}, ${esc(e.section)}${e.via_class ? `, 계열 문구 “${esc(e.via_class)}”` : ""}${e.n_labels > 1 ? `, 라벨 ${e.n_labels}건` : ""})${tag(e)}: ${esc(e.excerpt)}</div>`).join("")}
    ${p.observed.length ? `<div>실제 병용 보고 신호(참고): ${p.observed.map((e) => esc(e.ko)).join(", ")}</div>` : ""}
    ${p.model.length ? `<div>AI 구조 예측(참고, 신호 점수): ${p.model.slice(0, 6).map((m) => `${esc(m.ko)} ${pct(m.prob, 0)}`).join(", ")}</div>` : ""}</div>`).join("");
  const dups = (res.class_duplicates || []).map((d) => `<div>[같은 계열] ${esc(d.class)}: ${d.ingredients.map(esc).join(", ")} (${d.products.map(esc).join(" / ")})</div>`).join("");
  const html = `<!doctype html><html lang="ko"><head><meta charset="utf-8"><title>OMNI-PHARM 리포트</title><style>
    body{font-family:'Malgun Gothic','Noto Sans KR',sans-serif;color:#111;margin:28px;font-size:12px;line-height:1.55}
    h1{font-size:20px;margin:0}h2{font-size:14px;border-bottom:2px solid #0a6;padding-bottom:3px;margin-top:18px}
    h4{margin:10px 0 4px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:3px 6px;text-align:left}
    .lv{font-size:18px;font-weight:800;color:${res.overall_level === "높음" ? "#c00" : res.overall_level === "주의" ? "#c70" : "#068"}}
    .pair{margin:6px 0;padding:6px;border-left:3px solid #999}.disc{color:#666;font-size:11px;margin-top:20px}
  </style></head><body>
  <h1>OMNI-PHARM 다약제 상호작용 분석 리포트</h1><div>${new Date().toLocaleString("ko-KR")}</div>
  <h2>환자</h2><div>${patient.age}세 · ${patient.sex === "F" ? "여성" : "남성"} · ${patient.weight_kg}kg
  ${k ? ` · eGFR ${k.egfr} (${k.stage} ${k.desc})` : ""}${l ? ` · 간기능: ${l.desc}` : ""}
  ${patient.diagnosis ? ` · 진단: ${esc(patient.diagnosis)}` : ""}</div>
  <div>복용 약물: ${res.products.map((p) => `${esc(p.input)} [${p.ingredients.map(esc).join(", ")}]`).join(" / ")}</div>
  <h2>종합 판정</h2><div class="lv">${esc(res.summary?.verdict || res.overall_level)}</div>
  <div>${esc(res.summary?.headline || "")}</div>
  ${(res.summary?.reasons || []).map((r) => `<div>• ${esc(r.text)}</div>`).join("")}
  <h4>권고</h4>${(res.summary?.advice || []).map((a) => `<div>✔ ${esc(a)}</div>`).join("")}
  ${(res.disease_alerts || []).length ? `<h4>질환별로 더 위험한 약</h4>` + res.disease_alerts.map((d) =>
    `<div>[${esc(d.severity)}] ${esc(d.disease)} + ${esc(d.drug)}: ${esc(d.reason)} <i>(${esc(d.source)})</i></div>`).join("") : ""}
  ${dups ? `<h4>같은 계열 약 중복</h4>${dups}` : ""}
  ${(res.controlled || []).length ? `<h4>규제 물질 안내 (위해 감소 정보 · 용량은 안내하지 않음)</h4>` + res.controlled.map((c) =>
    `<div>${esc(c.input)} (${esc(c.ko)}) — 한국: ${esc(c.law)}${c.abroad ? ` · 해외: ${esc(c.abroad)}` : ""}<br>위험 신호: ${esc(c.signs)}</div>`).join("")
    + `<div>응급 시 119 · 마약류 24시간 익명 상담 1342</div>` : ""}
  ${(res.dosing?.ingredients || []).length ? `<h4>하루 복용량 점검</h4><table><tr><th>성분</th><th>하루 총량</th><th>1일 최대</th><th>기준</th><th>판정</th></tr>` +
    res.dosing.ingredients.map((r) => `<tr><td>${esc(r.display)}</td><td>${r.daily_mg}mg</td><td>${r.max_mg ?? "-"}${r.max_mg ? "mg" : ""}${r.why?.length ? ` (${esc(r.why.join(", "))})` : ""}</td><td>${esc(r.max_mg ? r.source || "" : "")}</td><td>${esc(r.status)}</td></tr>`).join("") + "</table>" : ""}
  ${(res.unsupported || []).length ? `<h4>분석하지 못한 입력</h4>${res.unsupported.map((u) => `<div>${esc(u.input)}: ${esc(u.reason)}</div>`).join("")}` : ""}
  ${(res.notes || []).length ? `<h4>참고</h4>${res.notes.map((n) => `<div>※ ${esc(n)}</div>`).join("")}` : ""}
  <h2>세부 등급</h2><div>${res.overall_level}</div>
  ${res.headline ? `<div>${esc(res.headline.title)} — 환자 맞춤 확률 ${pct(res.headline.prob)} (일반 보고 대비 ×${fx(res.headline.lift)})</div>` : ""}
  ${res.alerts.map((a) => `<div>• ${esc(a)}</div>`).join("")}
  <h2>주요 이상반응 (환자 맞춤 모델)</h2><table><tr><th>이상반응</th><th>예측</th><th>기준선</th><th>기준 대비</th><th>표준환자 대비</th><th>주요 기여</th></tr>${rows}</table>
  <h2>약물 조합별 근거</h2>${pairs}
  <h2>AI 임상 소견</h2>${mdToHtml(report || "")}
  <div class="disc">※ 연구·교육용 의사결정 보조 자료입니다(의료기기 인허가 전). 근거: 식약처 DUR·의약품 허가정보, 미국 FDA 허가 라벨·FAERS, Health Canada,
  ChEMBL, TWOSIDES. 자발적 보고 자료는 인과관계를 증명하지 않으며, 의사·약사의 임상 판단을 대체하지 않습니다. OMNI-PHARM v${esc(res.version || "")}</div>
  <script>setTimeout(()=>print(),300)</script></body></html>`;
  open(html);
}
