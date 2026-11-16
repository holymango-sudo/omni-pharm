// Records real answers of the running program (start_servers / uvicorn on :8000) for the server-less demo site.
//   npm run record:demo                  everything still missing
//   npm run record:demo -- static|predict|chat|recommend   one part
//   npm run record:demo -- all --force   re-record everything
// Output: demo-public/demo/<key>.json (key = src/demo/key.js, the same function the demo site uses to look answers up).
import { existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { demoKey } from "../src/demo/key.js";
import { DEFAULT_PRESET, PRESETS, patientPayload } from "../src/patient.js";
import { LIFESTYLE, SCENARIOS } from "../src/scenarios.js";
import { suggestions } from "../src/chatSuggestions.js";

const API = process.env.OMNIPHARM_API || "http://127.0.0.1:8000/api/v1/";
const OUT = join(dirname(fileURLToPath(import.meta.url)), "..", "demo-public", "demo");
const args = process.argv.slice(2);
const FORCE = args.includes("--force");
const PART = args.find((a) => !a.startsWith("--")) || "all";
const SYMPTOM_QUERIES = ["허리디스크", "무릎 십자인대 파열", "감기", "발목을 삐었어요", "무좀", "소변 볼 때 아파요", "두통", "배탈",
  "생리통", "불면", "콧물", "기침", "열", "속쓰림", "변비", "설사", "알레르기", "치통", "근육통", "멀미"];

mkdirSync(OUT, { recursive: true });
const file = (name) => join(OUT, `${name}.json`);
const have = (name) => !FORCE && existsSync(file(name));
const save = (name, obj) => writeFileSync(file(name), JSON.stringify(obj));
const log = (...a) => console.log(new Date().toTimeString().slice(0, 8), ...a);

async function call(path, body) {
  const r = await fetch(API + path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const text = await r.text();
  if (!r.ok) throw new Error(`${path} -> HTTP ${r.status}: ${text.slice(0, 200)}`);
  return text;
}
const json = async (path, body) => JSON.parse(await call(path, body));

/** server-sent events -> { text, checked, review } (same parsing as api.js) */
function parseSse(raw) {
  const out = { text: "", checked: null, review: null };
  for (const evt of raw.split("\n\n")) {
    const data = evt.split("\n").find((l) => l.startsWith("data: "));
    if (!data || evt.startsWith("event: done")) continue;
    const v = JSON.parse(data.slice(6));
    if (evt.startsWith("event: checked")) out.checked = v;
    else if (evt.startsWith("event: review")) out.review = v;
    else out.text += v.t || "";
  }
  return out;
}

async function record(path, body) {
  const key = demoKey(path, body ?? null);
  if (have(key)) return JSON.parse(readFileSync(file(key), "utf8"));
  const v = await json(path, body);
  save(key, v);
  return v;
}

async function recordStatic() {
  for (const p of ["status", "diseases", "metrics", "symptoms"]) await record(p);
  const names = [...new Set([...SCENARIOS.flatMap((s) => s.drugs), ...LIFESTYLE.map(([, n]) => n)])];
  const list = [];
  for (const n of names) {
    await record("molecule", { name: n });
    const hits = await json(`search?q=${encodeURIComponent(n)}&limit=15`);
    const h = hits.find((x) => x.name === n) || hits[0];
    list.push({ name: n, sub: h?.name === n ? h.sub || "" : h?.name || "", type: h?.type || "약" });
  }
  save("names", list);
  for (const p of Object.values(PRESETS)) await record("assess", patientPayload(p));
  for (const q of SYMPTOM_QUERIES) await record(`symptoms/search?q=${encodeURIComponent(q)}`);
  log(`static: ${names.length} drug names, ${Object.keys(PRESETS).length} patients`);
}

/** one analysis: prediction + its streamed expert opinion (+ chat answers to its suggested questions) */
async function recordPrediction(drugNames, patient, withChat) {
  const body = { drugs: drugNames.map((name) => ({ name, dose: null })), patient };
  const key = demoKey("predict", body);
  const chatKeys = (res) => suggestions(res).map((q) => [q, demoKey("chat", { report_id: res.report_id, question: q, history: [] })]);
  if (have(key)) {
    const old = JSON.parse(readFileSync(file(key), "utf8"));
    const complete = have(`report-${old.report_id}`) && (!withChat || chatKeys(old).every(([, k]) => have(k)));
    if (complete) return false;
  }
  const res = await json("predict", body);  // a fresh report_id: the server only keeps recent ones
  save(key, res);
  save(`report-${res.report_id}`, { text: parseSse(await call(`report/${res.report_id}`)).text });
  if (withChat) {
    for (const [question, k] of chatKeys(res)) {
      save(k, parseSse(await call("chat", { report_id: res.report_id, question, history: [] })));
    }
  }
  return true;
}

async function recordPredictions(withChat) {
  let n = 0;
  for (const [pname, preset] of Object.entries(PRESETS)) {
    const patient = patientPayload(preset);
    for (const s of SCENARIOS) {
      if (await recordPrediction(s.drugs, patient, withChat)) n++;
      if (pname !== DEFAULT_PRESET) continue;  // food/drink chips: default patient only (keeps recording ~1 h)
      for (const [, food] of LIFESTYLE) if (await recordPrediction([...s.drugs, food], patient, withChat)) n++;
    }
    log(`${withChat ? "chat" : "predict"}: ${pname} done (${n} new)`);
  }
}

async function recordGeneralChat() {
  for (const preset of Object.values(PRESETS)) {
    const patient = patientPayload(preset);
    for (const question of suggestions(null)) {
      const body = { question, history: [], drugs: [], patient };
      const k = demoKey("chat", body);
      if (!have(k)) save(k, parseSse(await call("chat", body)));
    }
  }
  log("chat: general questions done");
}

async function recordRecommend() {
  const catalog = await record("symptoms");
  for (const preset of Object.values(PRESETS)) {
    const patient = patientPayload(preset);
    for (const c of catalog) {
      for (const expert of [false, true]) await record("recommend", { symptoms: [c.id], drugs: [], patient, query: null, expert });
    }
  }
  log(`recommend: ${catalog.length} symptoms x ${Object.keys(PRESETS).length} patients x 2 views`);
}

/** expert opinions of predictions that were re-recorded since */
function pruneReports() {
  const used = new Set(readdirSync(OUT).filter((f) => f.startsWith("predict-"))
    .map((f) => `report-${JSON.parse(readFileSync(join(OUT, f), "utf8")).report_id}.json`));
  const stale = readdirSync(OUT).filter((f) => f.startsWith("report-") && !used.has(f));
  for (const f of stale) rmSync(join(OUT, f));
  if (stale.length) log(`removed ${stale.length} unused reports`);
}

const t0 = Date.now();
if (!(await json("status")).llm_available) {
  console.error("Local AI (Ollama) is offline: answers would be rule summaries, not AI. Start Ollama first.");
  process.exit(1);
}
if (PART === "all" || PART === "static") await recordStatic();
if (PART === "all" || PART === "predict") await recordPredictions(false);
if (PART === "all" || PART === "recommend") await recordRecommend();
if (PART === "all" || PART === "chat") { await recordPredictions(true); await recordGeneralChat(); }
pruneReports();
log(`finished in ${Math.round((Date.now() - t0) / 1000)}s -> ${readdirSync(OUT).length} files`);
