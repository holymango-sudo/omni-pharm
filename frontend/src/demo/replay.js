// Demo site (GitHub Pages, no server): every server call is answered from answers recorded from the real program
// (scripts/record-demo.mjs). Requests nobody recorded get a message pointing to the installed program.
import { demoKey } from "./key.js";

const BASE = `${import.meta.env.BASE_URL}demo/`;
const TICK_MS = 24;     // replay speed of streamed text (report, chat)
const CHUNK = 7;        // characters per tick

const MISS = {
  predict: "데모 사이트에서는 미리 분석해 둔 조합만 볼 수 있습니다. ‘데모 시나리오’ 버튼으로 약을 넣고 왼쪽 환자 예시 4개 중 하나를 "
    + "그대로 쓰세요(술·커피 같은 ‘함께 확인’ 1가지 추가는 ‘건강한 성인’에서). 용량 입력, 환자 값 수정, 다른 약은 설치형 프로그램에서 분석됩니다.",
  molecule: "데모 사이트에 녹화되지 않은 약입니다. 데모 시나리오의 약과 ‘함께 확인’ 음식만 쓸 수 있습니다.",
  recommend: "데모 사이트에서는 약을 넣기 전에, 환자 예시 그대로, 증상 1가지만 고른 경우를 볼 수 있습니다.",
  "rx-photo": "처방전 사진 읽기는 PC의 AI가 사진을 읽어야 해서 데모 사이트에서는 쓸 수 없습니다.",
  login: "데모 사이트에는 로그인이 없습니다.",
};
const CHAT_MISS = "데모 사이트에서는 데모 시나리오 분석 결과(또는 분석 전 일반 상담)의 **추천 질문 버튼**에만 녹화된 실제 AI 답변을 "
  + "보여 드립니다. 직접 쓴 질문이나 녹화되지 않은 조합은 설치형 프로그램에서 PC의 AI가 답합니다.";

const cache = new Map();
async function load(name) {
  if (!cache.has(name)) {
    cache.set(name, fetch(`${BASE}${name}.json`).then((r) => (r.ok ? r.json() : null)).catch(() => null));
  }
  return cache.get(name);
}

function missError(name) {
  const err = new Error(MISS[name] || "데모 사이트에서는 쓸 수 없는 기능입니다.");
  err.demo = true;
  return err;
}

const norm = (s) => (s || "").toLowerCase().replace(/\s+/g, "");

async function searchDrugs(q) {
  const names = (await load("names")) || [];
  const n = norm(q);
  return names.filter((h) => norm(h.name).includes(n) || norm(h.sub).includes(n)).slice(0, 15);
}

async function searchSymptoms(path, q) {
  const recorded = await load(demoKey(path));
  if (recorded) return recorded;
  const catalog = (await load(demoKey("symptoms"))) || [];
  const n = norm(q);
  const matches = catalog.filter((c) => norm(c.ko).includes(n) || norm(c.group).includes(n))
    .slice(0, 6).map(({ id, ko, icon, care }) => ({ id, ko, icon, care }));
  return { matches, free: false };
}

/** Same contract as api.js req(): resolves with the recorded body or throws an Error with a message. */
export async function demoReq(path, opts = {}) {
  const body = opts.body ? JSON.parse(opts.body) : null;
  const name = path.split("?")[0];
  const q = new URLSearchParams(path.split("?")[1] || "").get("q") || "";
  if (name === "me") return { user: null };
  if (name === "logout") return {};
  if (name === "search") return searchDrugs(q);
  if (name === "symptoms/search") return searchSymptoms(path, q);
  const data = await load(demoKey(path, body));
  if (data) return data;
  if (name === "assess") return null;  // a patient changed by hand: no body summary rather than a stale one
  throw missError(name);
}

function play(text, onToken, onDone) {
  let i = 0;
  const t = setInterval(() => {
    onToken(text.slice(i, i + CHUNK));
    i += CHUNK;
    if (i >= text.length) {
      clearInterval(t);
      onDone();
    }
  }, TICK_MS);
  return () => clearInterval(t);
}

/** api.streamReport(): the expert opinion recorded with that prediction. */
export function demoReport(id, onToken, onDone) {
  let stop = () => {};
  let live = true;
  load(`report-${id}`).then((r) => {
    if (!live) return;
    stop = play(r?.text || "_(데모: 이 조합의 AI 전문가 소견은 녹화되지 않았습니다.)_", onToken, onDone);
  });
  return () => { live = false; stop(); };
}

/** api.chat(): a recorded answer to a suggested question. */
export function demoChat(body, onToken, onDone, onChecked, onReview) {
  let stop = () => {};
  let live = true;
  load(demoKey("chat", body)).then((r) => {
    if (!live) return;
    if (r?.checked?.length) onChecked?.(r.checked);
    stop = play(r?.text || CHAT_MISS, onToken, () => {
      if (r?.review?.length) onReview?.(r.review);
      onDone();
    });
  });
  return () => { live = false; stop(); onDone(); };
}
