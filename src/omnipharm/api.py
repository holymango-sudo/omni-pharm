"""FastAPI service: inference, 3D molecules, streamed LLM report, and the built React UI.

Run:  python -m uvicorn omnipharm.api:app --app-dir src --port 8000

Privacy: patient data is processed in memory only. It is never written to disk or to the logs
(request bodies are not logged); LLM fact sheets live in a bounded in-memory cache for streaming.
The audit log (security.py) records who analysed which drug names and the verdict - no patient values.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import re
import time
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from omnipharm import assistant, chat_checks, fhir, recommend, recreational, review, rx_photo, security
from omnipharm.clinical import Patient, assess
from omnipharm.config import VERSION, settings
from omnipharm.dosing import Dose
from omnipharm.engine import Engine, ResolveError
from omnipharm.knowledge import DISEASES
from omnipharm.models import llm
from omnipharm.release import release_of

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("omnipharm.api")
logging.getLogger("httpx").setLevel(logging.WARNING)  # request URLs may contain API keys
E: dict = {}
REPORTS: OrderedDict[str, dict] = OrderedDict()  # report_id -> fact sheet (bounded LRU)
CHATS: OrderedDict[str, dict] = OrderedDict()  # report_id -> what the AI 상담 may rely on (memory only, never saved)
FIELD_KO = {"age": "나이", "weight_kg": "몸무게", "height_cm": "키", "scr": "크레아티닌", "egfr": "eGFR",
            "ast": "AST", "alt": "ALT", "total_bilirubin": "빌리루빈", "albumin": "알부민", "inr": "INR",
            "ascites": "복수", "encephalopathy": "간성뇌증", "child_pugh": "Child-Pugh 등급",
            "drugs": "약물 목록", "name": "약물명"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    t0 = time.time()
    E["engine"] = await asyncio.to_thread(Engine)
    E["started"] = time.time()
    log.info(f"engine ready in {time.time() - t0:.1f}s (access PIN {'ON' if settings.access_pin else 'off'})")
    E["warmup"] = asyncio.create_task(llm.warmup())  # keep a reference so the task is not GC'd
    yield


app = FastAPI(title="OMNI-PHARM API", version=VERSION, lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                   allow_methods=["*"], allow_headers=["*"])


OPEN_PATHS = {"/api/v1/health", "/api/v1/login", "/api/v1/auth-mode"}
MAX_BODY = 2_000_000  # bytes; a FHIR bundle of a real patient is far smaller
MAX_PHOTO = 15_000_000  # /rx-photo: a phone photo as base64 (the browser shrinks it first; this is the ceiling)
# EventSource cannot send headers, so only the streamed report may carry the login token / PIN in the URL
QUERY_AUTH_PREFIX = "/api/v1/report/"


def _client(request: Request) -> str:
    return request.client.host if request.client else "-"


# Middleware order: the one defined LAST runs first (outermost). access_control is defined first so that its own
# 401 answers also pass through limits_and_headers and get the security headers.
@app.middleware("http")
async def access_control(request: Request, call_next):
    """User accounts (config/users.json, see scripts/users.py) or, without accounts, the optional shared PIN
    (OMNIPHARM_ACCESS_PIN). The UI shell, health check and login stay open."""
    path = request.url.path
    request.state.user = None
    if path.startswith("/api/") and path not in OPEN_PATHS:
        in_url = path.startswith(QUERY_AUTH_PREFIX)  # secrets in URLs end up in histories and proxies: SSE only
        if security.accounts_enabled():
            auth = request.headers.get("authorization", "")
            token = (auth[7:] if auth.lower().startswith("bearer ") else
                     request.query_params.get("token") if in_url else None)
            s = security.session(token)
            if not s:
                return JSONResponse({"detail": {"message": "로그인이 필요합니다.", "login_required": True}}, status_code=401)
            request.state.user = s
        elif settings.access_pin:
            given = request.headers.get("x-access-pin") or (request.query_params.get("pin") if in_url else None) or ""
            if not hmac.compare_digest(given.encode(), settings.access_pin.encode()):
                return JSONResponse({"detail": {"message": "접근 PIN이 필요합니다.", "pin_required": True}},
                                    status_code=401)
    return await call_next(request)


def _secure(resp, path: str):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")  # no embedding in other sites (clickjacking)
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")  # results may contain clinical data
    return resp


@app.middleware("http")
async def limits_and_headers(request: Request, call_next):
    """Reject oversized bodies (memory exhaustion) and add basic browser security headers to every answer."""
    path = request.url.path
    size = request.headers.get("content-length")
    limit = MAX_PHOTO if path == "/api/v1/rx-photo" else MAX_BODY
    if size and size.isdigit() and int(size) > limit:
        return _secure(JSONResponse({"detail": {"message": "요청이 너무 큽니다."}}, status_code=413), path)
    if size is None and "chunked" in request.headers.get("transfer-encoding", "").lower():
        # a chunked body has no length to check up front and would be read whole into memory before validation;
        # browsers and FHIR clients send Content-Length for these small JSON bodies (no body = no header: fine)
        return _secure(JSONResponse({"detail": {"message": "요청 길이(Content-Length)가 필요합니다."}}, status_code=411), path)
    return _secure(await call_next(request), path)


def _who(request: Request) -> str | None:
    return (request.state.user or {}).get("user")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=256)


@app.get("/api/v1/auth-mode")
def auth_mode():
    return {"accounts": security.accounts_enabled(), "pin": bool(settings.access_pin) and not security.accounts_enabled()}


@app.post("/api/v1/login")
def login(body: LoginRequest, request: Request):
    try:
        s = security.login(body.username, body.password, _client(request))
    except PermissionError as e:
        raise HTTPException(429, {"message": str(e)}) from None
    security.audit("login" if s else "login_failed", body.username, _client(request))
    if not s:
        raise HTTPException(401, {"message": "아이디 또는 비밀번호가 맞지 않습니다.", "login_required": True})
    return s


@app.post("/api/v1/logout")
def logout(request: Request):
    auth = request.headers.get("authorization", "")
    security.logout(auth[7:] if auth.lower().startswith("bearer ") else None)
    return {"ok": True}


@app.get("/api/v1/me")
def me(request: Request):
    u = request.state.user
    return {"user": u["user"], "role": u["role"]} if u else {"user": None, "role": None}


@app.get("/api/v1/audit")
def audit_log(request: Request, month: str = "", limit: int = 500):
    """Audit log of one month (admin accounts only; with no accounts configured: local access only)."""
    u = request.state.user
    if (u and u["role"] != "admin") or (not u and _client(request) not in ("127.0.0.1", "::1")):
        raise HTTPException(403, {"message": "관리자만 볼 수 있습니다."})
    month = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else time.strftime("%Y-%m")
    return {"month": month, "events": security.read_audit(month, min(max(limit, 1), 5000))}


@app.exception_handler(RequestValidationError)
async def validation_error(_req: Request, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        field = next((FIELD_KO.get(str(x), str(x)) for x in reversed(e.get("loc", ())) if not isinstance(x, int)), "")
        msgs.append(f"{field}: 허용 범위를 벗어났거나 형식이 잘못되었습니다" if field else "입력 형식 오류")
    return JSONResponse({"detail": {"message": " / ".join(dict.fromkeys(msgs))}}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(_req: Request, exc: Exception):
    log.exception("unhandled error")  # stack trace only; request bodies (patient data) are never logged
    return JSONResponse({"detail": {"message": "서버 내부 오류가 발생했습니다. 잠시 후 다시 시도하세요."}}, status_code=500)


class DrugInput(BaseModel):
    name: str = Field(..., min_length=1, max_length=2000, description="제품명/성분명(한글·영문·일본어), '성분+성분', 또는 SMILES")
    dose: Dose | None = Field(None, description="복용량·횟수·기간 (없으면 용량 점검 생략)")


class PredictRequest(BaseModel):
    drugs: list[DrugInput] = Field(..., min_length=1, max_length=15)
    patient: Patient = Field(default_factory=Patient)


async def _resolve(name: str):
    """-> (ingredients, info, error). error = {"message", "candidates"} when the name cannot be resolved."""
    eng: Engine = E["engine"]
    try:
        ings, info = await asyncio.to_thread(eng.resolve_full, name)  # may hit PubChem for unknown names
        return ings, info, None
    except ResolveError as e:
        return None, None, {"message": str(e), "candidates": e.candidates}


@app.get("/api/v1/health")
def health():
    return {"ok": "engine" in E, "version": VERSION, "uptime_s": round(time.time() - E.get("started", time.time()))}


@app.get("/api/v1/status")
async def status():
    return {**E["engine"].status(), "version": VERSION, "llm_model": settings.llm_model,
            "llm_available": await llm.llm_available(), "pin": bool(settings.access_pin)}


@app.get("/api/v1/catalog")
def catalog():
    return E["engine"].catalog()


@app.get("/api/v1/metrics")
def metrics():
    eng: Engine = E["engine"]
    return {"pair": eng.pair_metrics, "patient": eng.patient_metrics, "ablation_no_patient": eng.ablation,
            "stats": eng.stats}  # 95% CIs, significance tests and baselines (pipeline stage "stats")


@app.post("/api/v1/assess")
def assess_patient(p: Patient):
    """Live body / kidney / liver / capacity summary for the patient panel (no drugs needed)."""
    return assess(p)


@app.get("/api/v1/diseases")
def diseases():
    return [{"id": k, "ko": v["ko"]} for k, v in DISEASES.items() if not v.get("derived")]  # (age / sex groups)


@app.get("/api/v1/search")
def search(q: str = "", limit: int = 15):
    return E["engine"].search(q[:100], min(max(limit, 1), 40))


@app.post("/api/v1/molecule")
async def molecule(d: DrugInput):
    ings, info, err = await _resolve(d.name)
    if err:
        raise HTTPException(422, err)
    eng: Engine = E["engine"]
    mols = await asyncio.to_thread(lambda: [eng.molecule(g) for g in ings])
    st = info.get("strength")
    dose_info = {"unit": st["unit"], "per": st["per"],
                 "per_unit": [{"name": eng.ko(g), "mg": st["mg"][g.key]} for g in ings if g.key in st["mg"]]} if st else None
    return {"input": d.name, "molecules": mols, "product": info["product"], "country": info["country"],
            "release": info.get("release") or release_of(info["product"] or d.name),
            "unresolved": info["unresolved"], "dose_info": dose_info, "single": len(ings) == 1,
            "controlled": info.get("controlled"), "nutrients": info.get("nutrients")}


def _logged(name: str) -> str:
    """Drug name as the audit log records it: controlled substances (대마, 엑스터시 ...) are not recorded by name."""
    return recreational.mask(name)[:80]


@app.post("/api/v1/predict")
async def predict(req: PredictRequest, request: Request):
    res = await _predict(req)
    security.audit("analysis", _who(request), _client(request), drugs=[_logged(d.name) for d in req.drugs],
                   verdict=res["summary"]["verdict"], n_ingredients=len(res["ingredients"]))
    return res


@app.post("/api/v1/fhir/assess")
async def fhir_assess(bundle: dict, request: Request):
    """EMR / OCS integration: FHIR R4 Bundle (Patient, Observation, Condition, MedicationRequest/Statement) ->
    OMNI-PHARM result + FHIR DetectedIssue list. Identifiers and names in the bundle are ignored."""
    try:
        drugs, patient, warn = fhir.parse(bundle)
        req = PredictRequest(drugs=[DrugInput(name=d["name"], dose=Dose(**d["dose"]) if d["dose"] else None)
                                    for d in drugs][:15], patient=Patient(**patient))
    except Exception as e:
        raise HTTPException(422, {"message": f"FHIR 번들을 해석할 수 없습니다: {type(e).__name__}"}) from None
    res = await _predict(req)
    res["fhir"] = {"warnings": warn, "parsed_patient": {k: v for k, v in patient.items() if k != "diagnosis"},
                   "parsed_drugs": [d["name"] for d in drugs], "detected_issues": fhir.detected_issues(res)}
    security.audit("fhir_analysis", _who(request), _client(request), drugs=[_logged(d["name"]) for d in drugs],
                   verdict=res["summary"]["verdict"], n_ingredients=len(res["ingredients"]))
    return res


async def _predict(req: PredictRequest) -> dict:
    eng: Engine = E["engine"]
    products, unsupported, controlled = [], [], []
    for d in req.drugs:
        ings, info, err = await _resolve(d.name)
        if err:
            unsupported.append({"input": d.name, "reason": err["message"], "candidates": err["candidates"]})
            continue
        ctl = info.get("controlled")
        if ctl:  # harm reduction: dangerous combinations yes, amounts never (no dose check for these)
            controlled.append({**ctl, "input": d.name, "keys": [g.key for g in ings]})
        products.append({"input": d.name, "ingredients": ings, "product": info["product"],
                         "dose": None if ctl else d.dose, "controlled": ctl,
                         "strength": info.get("strength"), "release": info.get("release"),
                         "label_ko": info.get("label_ko"), "nutrients": info.get("nutrients")})
        if info["unresolved"]:
            unsupported.append({"input": d.name, "reason": "분석 불가 성분: " + ", ".join(info["unresolved"]),
                                "candidates": []})
    n_ing = len({g.key for p in products for g in p["ingredients"]})
    if n_ing < 2 and not (len(products) >= 2 and n_ing == 1):
        why = "; ".join(f"{u['input']}: {u['reason']}" for u in unsupported)
        raise HTTPException(422, {"message": "분석 가능한 성분이 2개 이상 필요합니다." + (f" ({why})" if why else ""),
                                  "unsupported": unsupported})
    t0 = time.time()
    res = await asyncio.to_thread(eng.predict, products, req.patient)
    res["unsupported"] = unsupported
    res["controlled"] = controlled
    res["products"] = [{"input": p["input"], "product": p["product"],
                        "ingredients": [eng.ko(g) for g in p["ingredients"]],
                        "usage": (p.get("label_ko") or {}).get("usage"), "kind": (p.get("label_ko") or {}).get("kind"),
                        "nutrients": p.get("nutrients")}
                       for p in products]
    res["elapsed_ms"] = round((time.time() - t0) * 1000)
    res["version"] = VERSION
    rid = uuid.uuid4().hex[:12]
    REPORTS[rid] = llm.fact_sheet(res, req.patient.model_dump())
    # the chat re-checks medicines named in a question together with these products, for this patient
    CHATS[rid] = {"ctx": llm.chat_context(res, req.patient.model_dump()), "products": products,
                  "patient": req.patient, "verdict": res["summary"]["verdict"]}
    while len(REPORTS) > 200:
        REPORTS.popitem(last=False)
    while len(CHATS) > 200:
        CHATS.popitem(last=False)
    res["report_id"] = rid
    return res


@app.get("/api/v1/report/{rid}")
async def report(rid: str, request: Request):
    facts = REPORTS.get(rid)
    if facts is None:
        raise HTTPException(404, {"message": "소견서를 찾을 수 없습니다 (서버가 재시작되었을 수 있습니다). 다시 분석하세요."})
    security.audit("report", _who(request), _client(request), report_id=rid, llm=settings.llm_model)

    async def gen():
        async for tok in llm.stream_report(facts):
            yield f"data: {json.dumps({'t': tok}, ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class ChatTurn(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., max_length=4000)


class ChatRequest(BaseModel):
    report_id: str | None = Field(None, min_length=6, max_length=40, description="분석 결과 (없으면 일반 상담)")
    question: str = Field(..., min_length=1, max_length=600)
    history: list[ChatTurn] = Field(default_factory=list, max_length=20)
    drugs: list[str] = Field(default_factory=list, max_length=15, description="분석 전: 입력해 둔 약 이름")
    patient: Patient = Field(default_factory=Patient, description="분석 전: 환자 정보")


@app.post("/api/v1/chat")
async def chat(req: ChatRequest, request: Request):
    """AI 복약 상담 (streamed). With a report_id the answer rests on that analysis; medicines / foods named in the
    question are checked together with it by the engine first (assistant.lookups). Without one: general consultation
    for this patient and the medicines typed so far.
    The question is not logged (it may contain health details) - only that a question was asked."""
    c = CHATS.get(req.report_id) if req.report_id else None
    if req.report_id and c is None:
        raise HTTPException(404, {"message": "분석 결과를 찾을 수 없습니다 (서버가 재시작되었을 수 있습니다). 다시 예측하세요."})
    eng: Engine = E["engine"]
    if c:
        ctx, base, patient, verdict = c["ctx"], c["products"], c["patient"], c["verdict"]
    else:
        base, patient, verdict = [], req.patient, None
        for name in req.drugs:
            ings, info, err = await _resolve(name[:200])
            if not err:
                base.append({"input": name, "ingredients": ings, "product": info["product"],
                             "strength": info.get("strength"), "release": info.get("release"),
                             "label_ko": info.get("label_ko"), "controlled": info.get("controlled")})
        ctx = llm.general_context(assess(patient), patient.model_dump())
        if base:
            ctx["입력한 약(아직 조합 분석 전)"] = [f"{p['input']} = {', '.join(eng.ko(g) for g in p['ingredients'])}"
                                         for p in base]
    found = await asyncio.to_thread(assistant.lookups, eng, req.question, base, patient, verdict)
    security.audit("chat", _who(request), _client(request), report_id=req.report_id, chars=len(req.question),
                   checked=[_logged(f["확인한 제품"].split(" = ")[0])[:60] for f in found])

    async def gen():
        if found:  # tell the UI which products were checked (shown as chips above the answer)
            chips = [{"name": f["확인한 제품"].split(" = ")[0], "verdict": f["함께 먹을 때 판정"]} for f in found]
            yield f"event: checked\ndata: {json.dumps(chips, ensure_ascii=False)}\n\n"
        parts = []
        async for tok in llm.stream_chat(ctx, [h.model_dump() for h in req.history], req.question, found):
            parts.append(tok)
            yield f"data: {json.dumps({'t': tok}, ensure_ascii=False)}\n\n"
        # v2.18: clinical claims about these medicines that nothing given to the model supports are pointed out under
        # the answer (the model's memory must not look like a checked result)
        claims = chat_checks.unsupported_claims("".join(parts), [ctx, found], req.question,
                                                assistant.conversation_names(eng, base, found))
        if claims:
            yield f"event: review\ndata: {json.dumps([s for _, s in claims], ensure_ascii=False)}\n\n"
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class RxPhoto(BaseModel):
    image: str = Field(..., min_length=100, max_length=MAX_PHOTO, description="사진 (base64 또는 data URL)")


@app.post("/api/v1/rx-photo")
async def rx_photo_read(req: RxPhoto, request: Request):
    """처방전·약봉투 사진 -> medicine lines read by the local vision LLM, each matched to a product.
    The photo stays in memory (never written, never logged); only the number of lines goes to the audit log."""
    meta: dict = {}
    try:
        img = await asyncio.to_thread(rx_photo.prepare_image, req.image, meta)
    except ValueError as e:
        raise HTTPException(422, {"message": str(e)}) from None
    t0 = time.time()
    data = await llm.vision_json(rx_photo.PROMPT, rx_photo.SCHEMA, img)
    if data is None:
        raise HTTPException(503, {"message": "로컬 AI가 실행 중이 아니거나 사진을 읽을 수 없는 모델입니다. 프로그램을 다시 켠 뒤 시도하세요."})
    items = await asyncio.to_thread(rx_photo.match_all, E["engine"], data.get("medicines") or [])
    security.audit("rx_photo", _who(request), _client(request), lines=len(items),
                   matched=sum(i["status"] != "none" for i in items))
    return {"document": data.get("document") or "", "items": items, "elapsed_ms": round((time.time() - t0) * 1000),
            "model": settings.llm_model, "small_photo": bool(meta.get("enlarged"))}


@app.get("/api/v1/symptoms")
def symptoms():
    return recommend.catalog()


@app.get("/api/v1/symptoms/search")
def symptoms_search(q: str = Query("", max_length=60)):
    """Everyday words ('허리디스크', '무릎 십자인대 파열', '감기') -> matching symptoms / conditions (emergencies first)."""
    return recommend.search(q)


class RecommendRequest(BaseModel):
    symptoms: list[str] = Field(default_factory=list, max_length=8)
    query: str | None = Field(None, max_length=60, description="free text that matched no condition")
    drugs: list[str] = Field(default_factory=list, max_length=15)
    patient: Patient = Field(default_factory=Patient)
    expert: bool = Field(False, description="also list prescription medicines (reference, expert view)")


@app.post("/api/v1/recommend")
async def recommend_drugs(req: RecommendRequest):
    """'증상·질환으로 약 찾기': self-care options per symptom / condition (tablets, creams / patches, OTC products whose
    approved indication names it), screened against this patient and the medicines already entered."""
    if not req.symptoms and not (req.query or "").strip():
        raise HTTPException(422, {"message": "증상을 고르거나 검색어를 입력하세요."})
    eng: Engine = E["engine"]
    current = []
    for name in req.drugs:
        ings, _info, err = await _resolve(name)
        if not err:
            current.append({"input": name, "ingredients": ings})
    return await asyncio.to_thread(recommend.recommend, eng, req.symptoms, current, req.patient, req.query, req.expert)


# --------------------------------------------------------------------------- blinded pharmacist review (v2.18)
class ReviewAnswer(BaseModel):
    reviewer: str = Field("", max_length=40)  # ignored when logged in (the account is the reviewer)
    case: int = Field(..., ge=1, le=100000)
    verdict: str = Field(..., max_length=4)
    memo: str = Field("", max_length=500)


@app.get("/api/v1/review/cases")
async def review_cases(round_no: int | None = Query(None, alias="round")):
    """The review cases (patient + medicines) of one round (default: the latest) - never the program's verdict
    (the review is blinded)."""
    rounds = review.rounds()
    if not rounds:
        raise HTTPException(404, {"message": "검수표가 없습니다 (python scripts/review_sheet.py)."})
    rno = round_no if round_no in rounds else rounds[-1]
    key = f"review_cases_{rno}"
    if key not in E:
        E[key] = await asyncio.to_thread(review.cases, E["engine"], rno)
    return {"cases": E[key], "round": rno, "verdicts": list(review.VERDICTS)}


def _reviewer(request: Request, name: str) -> str:
    """With user accounts the reviewer IS the logged-in account: a typed name could file answers under someone else's
    name (or read their answers). Without accounts (PIN / open) the typed name is all there is."""
    return _who(request) or name


@app.get("/api/v1/review/answers")
async def review_answers(request: Request, reviewer: str = ""):
    try:
        return {"answers": review.answers(_reviewer(request, reviewer)), "reviewer": _who(request)}
    except ValueError as e:
        raise HTTPException(422, {"message": str(e)}) from None


@app.post("/api/v1/review/answer")
async def review_answer(a: ReviewAnswer, request: Request):
    try:
        res = review.save_answer(_reviewer(request, a.reviewer), a.case, a.verdict, a.memo)
    except ValueError as e:
        raise HTTPException(422, {"message": str(e)}) from None
    security.audit("review", _who(request), _client(request), reviewer=res["reviewer"], case=a.case)
    return res


@app.get("/review", include_in_schema=False)
def review_page():
    return FileResponse(Path(__file__).parent / "static" / "review.html", headers={"Cache-Control": "no-cache"})


# --------------------------------------------------------------------------- built React UI
dist = settings.frontend_dist
if dist.exists():
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/"):  # unknown API route: a JSON 404, not the web page (integrations rely on it)
            return JSONResponse({"detail": {"message": "없는 API 경로입니다."}}, status_code=404)
        f = (dist / path).resolve()
        ok = path and f.is_file() and f.is_relative_to(dist.resolve())
        if ok:
            return FileResponse(f)
        # hashed assets may be cached forever, but index.html must be re-checked so a rebuild is picked up
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
