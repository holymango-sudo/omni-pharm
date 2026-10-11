const PIN_KEY = "omnipharm.pin";
const getPin = () => { try { return localStorage.getItem(PIN_KEY) || ""; } catch { return ""; } };
export const setPin = (p) => { try { localStorage.setItem(PIN_KEY, p); } catch { /* storage blocked */ } };
const TOKEN_KEY = "omnipharm.token";  // login session (user accounts mode); kept for this browser tab only
const getToken = () => { try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch { return ""; } };
export const setToken = (t) => { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch { /* blocked */ } };
let onPinRequired = () => {};
export const handlePinRequired = (fn) => { onPinRequired = fn; };
let onLoginRequired = () => {};
export const handleLoginRequired = (fn) => { onLoginRequired = fn; };

async function req(path, opts = {}) {
  let r;
  const token = getToken();
  try {
    r = await fetch(`/api/v1/${path}`, {
      ...opts,
      headers: { "Content-Type": "application/json", "X-Access-PIN": getPin(),
        ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(opts.headers || {}) },
    });
  } catch {
    const err = new Error("서버에 연결할 수 없습니다. 프로그램(start_servers)이 실행 중인지 확인하세요.");
    err.offline = true;
    throw err;
  }
  const body = await r.json().catch(() => ({}));
  if (!r.ok) {
    const d = body.detail;
    if (r.status === 401 && d?.pin_required) onPinRequired();
    if (r.status === 401 && d?.login_required && path !== "login") onLoginRequired();
    const msg = typeof d === "string" ? d : d?.message || `요청 실패 (HTTP ${r.status})`;
    const err = new Error(msg);
    err.detail = d;
    err.status = r.status;
    throw err;
  }
  return body;
}

export const api = {
  status: () => req("status"),
  login: (username, password) => req("login", { method: "POST", body: JSON.stringify({ username, password }) }),
  logout: () => req("logout", { method: "POST" }),
  me: () => req("me"),
  metrics: () => req("metrics"),
  search: (q) => req(`search?q=${encodeURIComponent(q)}&limit=15`),
  diseases: () => req("diseases"),
  assess: (patient) => req("assess", { method: "POST", body: JSON.stringify(patient) }),
  molecule: (name) => req("molecule", { method: "POST", body: JSON.stringify({ name }) }),
  // drugs: [{name, dose?}] - dose = {amount, unit, per_day, days, prn, weekly}
  predict: (drugs, patient) => req("predict", { method: "POST", body: JSON.stringify({ drugs, patient }) }),
  streamReport(id, onToken, onDone) {
    const pin = getPin();
    const token = getToken();
    const q = token ? `?token=${encodeURIComponent(token)}` : pin ? `?pin=${encodeURIComponent(pin)}` : "";
    const es = new EventSource(`/api/v1/report/${id}${q}`);
    es.onmessage = (e) => onToken(JSON.parse(e.data).t);
    es.addEventListener("done", () => {
      es.close();
      onDone();
    });
    es.onerror = () => {
      es.close();
      onDone();
    };
    return () => es.close();
  },
};

export const pct = (x, d = 1) => `${(x * 100).toFixed(d)}%`;
// ratios against a tiny base rate explode (0.2% -> 20% = x100); above x50 the exact value is noise
export const fx = (x) => (x >= 50 ? "50+" : x >= 10 ? x.toFixed(0) : x.toFixed(1));
