// File name of a recorded server answer. Shared by the demo site (replay.js) and the recorder (scripts/record-demo.mjs),
// so the same request always maps to the same file. Order that does not change the answer (drugs, symptoms) is ignored.

const ORDER_FREE = { predict: ["drugs"], recommend: ["symptoms", "drugs"], chat: ["drugs"] };

function canonical(v) {
  if (Array.isArray(v)) return `[${v.map(canonical).join(",")}]`;
  if (v && typeof v === "object") {
    return `{${Object.keys(v).sort().filter((k) => v[k] !== undefined).map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(",")}}`;
  }
  return JSON.stringify(v ?? null);
}

// cyrb53: 53-bit string hash (public domain, bryc)
function hash(s) {
  let h1 = 0xdeadbeef;
  let h2 = 0x41c6ce57;
  for (let i = 0; i < s.length; i++) {
    const c = s.charCodeAt(i);
    h1 = Math.imul(h1 ^ c, 2654435761);
    h2 = Math.imul(h2 ^ c, 1597334677);
  }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(36);
}

/**
 * @param {string} path  API path without the /api/v1/ prefix, query string included ("status", "predict")
 * @param {object|null} body  JSON body of a POST, or null
 * @returns {string} e.g. "predict-1x2k9f0"
 */
export function demoKey(path, body = null) {
  const name = path.split("?")[0].replace(/\//g, "_");
  let b = body;
  if (b && typeof b === "object") {
    b = { ...b };
    // a chat answer is recorded for the first question; later turns replay it whatever was said before
    if (name === "chat") delete b.history;
    for (const k of ORDER_FREE[name] || []) {
      if (Array.isArray(b[k])) b[k] = b[k].map(canonical).sort().map((s) => JSON.parse(s));
    }
  }
  return `${name}-${hash(`${path}|${canonical(b)}`)}`;
}
