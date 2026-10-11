import json, time, pathlib, io, os
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from . import ai
from .db import Session, Visit, Match, Order, Table, init_db, now

app = FastAPI(title="Café Companion")
STATIC = pathlib.Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")
init_db()

def page(name): return FileResponse(STATIC / name)

@app.get("/")
def landing(): return page("index.html")
@app.get("/staff")
def staff(): return page("staff.html")
@app.get("/t/{table}")
def customer(table: int): return page("customer.html")

@app.get("/qr/{table}.svg")
def qr(table: int, request: Request):
    """QR code pointing at this deployment's own /t/{table} URL (works on Cloud Run and locally)."""
    import qrcode, qrcode.image.svg
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = request.headers.get("host", request.url.netloc)
    url = os.environ.get("BASE_URL", f"{proto}://{host}").rstrip("/") + f"/t/{table}"
    buf = io.BytesIO()
    qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, border=3).save(buf)
    return Response(buf.getvalue(), media_type="image/svg+xml")

@app.get("/api/menu")
def menu(): return ai.MENU

class VisitIn(BaseModel):
    table_id: int
    first_name: str
    why: str = ""
    stay_minutes: int = 60
    open_to_meet: bool = False
    interests: str = ""
    guest_id: str = ""

import datetime as _dt
IST = _dt.timedelta(hours=5, minutes=30)

def slot_of(t):
    """UTC datetime -> café time-of-day slot (IST)."""
    h = (t + IST).hour
    return "morning" if 5 <= h < 12 else "afternoon" if 12 <= h < 17 else "evening"

def guest_history(s, gid):
    """Past visits + most-ordered items (overall and per time of day) for a returning guest."""
    if not gid: return None
    vs = s.query(Visit).filter_by(guest_id=gid).order_by(Visit.id).all()
    if not vs: return None
    from collections import Counter
    c, by_slot = Counter(), {}
    for o in s.query(Order).filter(Order.visit_id.in_([v.id for v in vs])):
        names = [n for n in o.items.split(", ") if n]
        c.update(names); by_slot.setdefault(slot_of(o.created_at), Counter()).update(names)
    by_name = {m["name"]: m["id"] for m in ai.MENU}
    usual = [n for n, _ in c.most_common(2)]
    usual_by_slot = {k: [n for n, _ in v.most_common(2)] for k, v in by_slot.items()}
    return {"name": vs[-1].first_name, "past_visits": len(vs), "usual_orders": usual,
            "slot": slot_of(now()), "usual_by_slot": usual_by_slot,
            "usual_ids": [by_name[n] for n in usual if n in by_name],
            "usual_ids_by_slot": {k: [by_name[n] for n in v if n in by_name] for k, v in usual_by_slot.items()},
            "past_reasons": list(dict.fromkeys(v.intent for v in vs))}

def new_visit(s, table_id, name, why, stay, open_to_meet, interests, intent=None, guest_id=""):
    history = guest_history(s, guest_id)
    # one active visit per table / per guest: close the previous one
    for old in s.query(Visit).filter_by(table_id=table_id, active=True): old.active = False
    if guest_id:
        for old in s.query(Visit).filter_by(guest_id=guest_id, active=True): old.active = False
    rec = {"intent": intent, "recs": []} if intent else ai.classify(
        dict(why=why, stay_minutes=stay, open_to_meet=open_to_meet, interests=interests), history)
    v = Visit(table_id=table_id, guest_id=guest_id or None, first_name=name.strip()[:40] or "Guest",
              intent=rec["intent"], interests=interests, stay_minutes=stay)
    s.add(v); s.commit()
    return v, rec["recs"]

@app.post("/api/visits")
def create_visit(b: VisitIn):
    with Session() as s:
        if not s.get(Table, b.table_id): raise HTTPException(404, "unknown table")
        v, recs = new_visit(s, b.table_id, b.first_name, b.why, b.stay_minutes, b.open_to_meet, b.interests,
                            guest_id=b.guest_id[:64])
        names = {m["id"]: m for m in ai.MENU}
        return {"visit_id": v.id, "intent": v.intent,
                "recs": [{**names[r["id"]], "reason": r["reason"]} for r in recs]}

class ChatIn(BaseModel):
    table_id: int
    guest_id: str = ""
    messages: list[dict] = []

@app.post("/api/chat")
def host_chat(b: ChatIn):
    """AI host agent: Gemini decides the next question (or finishes with a profile)."""
    msgs = [{"role": "guest" if m.get("role") == "guest" else "host", "text": str(m.get("text", ""))[:300]}
            for m in b.messages][-12:]
    with Session() as s:
        history = guest_history(s, b.guest_id[:64])
    if history: history = {k: v for k, v in history.items() if not k.startswith("usual_ids")}
    return {**ai.chat(b.table_id, msgs, history), "returning": bool(history)}

class OrderIn(BaseModel):
    visit_id: int
    items: list[str]

def place_order(s, visit_id, item_ids):
    """Create an order with a wait-time estimate from the live queue."""
    names = {m["id"]: m["name"] for m in ai.MENU}
    items = [names[i] for i in item_ids if i in names]
    if not items: raise HTTPException(400, "no valid items")
    ahead = s.query(Order).filter_by(status="new").count()
    eta = 4 + 3 * ahead + max(0, len(items) - 1)  # base prep + 3 min per order ahead + 1 per extra item
    o = Order(visit_id=visit_id, items=", ".join(items), eta_min=eta); s.add(o); s.commit()
    return {"order_id": o.id, "eta_min": eta, "ahead": ahead, "items": items}

@app.post("/api/orders")
def create_order(b: OrderIn):
    with Session() as s:
        return place_order(s, b.visit_id, b.items)

class AskIn(BaseModel):
    visit_id: int
    text: str

@app.post("/api/ask")
def ask_bean(b: AskIn):
    """After setup the guest can keep chatting with Bean: order by chat or ask about the menu."""
    with Session() as s:
        v = s.get(Visit, b.visit_id)
        if not v: raise HTTPException(404)
        r = ai.assistant(b.text[:300], v.intent)
        out = {"reply": r["reply"]}
        if r["items"]: out["order"] = place_order(s, v.id, r["items"])
        return out

@app.post("/api/orders/{oid}/ready")
def order_ready(oid: int):
    with Session() as s:
        o = s.get(Order, oid)
        if not o: raise HTTPException(404)
        o.status = "ready"; s.commit()
        return {"ok": True}

def _ids(t): return [int(x) for x in (t or "").split(",") if x]

@app.post("/api/match")
def run_match():
    with Session() as s:
        busy = set()
        for m in s.query(Match).filter(Match.status != "declined"): busy.update(_ids(m.visit_ids))
        vs = [v for v in s.query(Visit).filter_by(active=True, intent="connect") if v.id not in busy]
        groups = ai.group([{"id": v.id, "name": v.first_name, "interests": v.interests} for v in vs]) if len(vs) >= 2 else []
        for g in groups: s.add(Match(visit_ids=",".join(map(str, g["ids"])), reason=g["reason"]))
        s.commit()
        return {"created": len(groups)}

@app.get("/api/visits/{vid}/match")
def my_match(vid: int):
    """Match card for a visitor. Names/tables revealed only once everyone accepted."""
    with Session() as s:
        v = s.get(Visit, vid)
        if not v: raise HTTPException(404)
        if v.intent != "connect": return {"intent": v.intent, "match": None}
        for m in s.query(Match).filter(Match.status.in_(["pending", "matched"])).order_by(Match.id.desc()):
            ids = _ids(m.visit_ids)
            if vid in ids:
                out = {"id": m.id, "reason": m.reason, "status": m.status, "size": len(ids),
                       "accepted": vid in _ids(m.accepts)}
                if m.status == "matched":
                    out["people"] = [{"first_name": p.first_name, "table": p.table_id}
                                     for p in (s.get(Visit, i) for i in ids) if p and p.id != vid]
                    out["icebreaker"] = m.icebreaker
                return {"intent": v.intent, "match": out}
        return {"intent": v.intent, "match": None}

class Respond(BaseModel):
    visit_id: int
    accept: bool

@app.post("/api/matches/{mid}/respond")
def respond(mid: int, b: Respond):
    with Session() as s:
        m = s.get(Match, mid)
        if not m or b.visit_id not in _ids(m.visit_ids): raise HTTPException(404)
        if m.status == "pending":
            if not b.accept: m.status = "declined"
            else:
                acc = set(_ids(m.accepts)) | {b.visit_id}
                m.accepts = ",".join(map(str, sorted(acc)))
                if acc >= set(_ids(m.visit_ids)):
                    m.status = "matched"
                    ppl = [{"name": p.first_name, "interests": p.interests}
                           for p in (s.get(Visit, i) for i in _ids(m.visit_ids)) if p]
                    m.icebreaker = ai.icebreaker(ppl, m.reason or "")
            s.commit()
        return {"status": m.status}

@app.post("/api/seed")
def seed():
    demo = [("Asha", "focus", "coding, startups", 2), ("Ravi", "connect", "hiking, photography, startups", 3),
            ("Meera", "connect", "photography, hiking, books", 5), ("Kabir", "chill", "music", 6)]
    with Session() as s:
        taken = {v.table_id for v in s.query(Visit).filter_by(active=True)}
        free = [t.id for t in s.query(Table).order_by(Table.id) if t.id not in taken]
        n = 0
        for (name, intent, interests, _), t in zip(demo, free):  # only use free tables, never bump a real guest
            new_visit(s, t, name, "", 60, intent == "connect", interests, intent=intent); n += 1
    return {"seeded": n}

@app.post("/api/reset")
def reset():
    with Session() as s:
        for M in (Visit, Match, Order): s.query(M).delete()
        s.commit()
    return {"ok": True}

_tip_cache = {"key": None, "t": 0, "tips": []}

@app.get("/api/staff")
def staff_state():
    with Session() as s:
        visits = s.query(Visit).filter_by(active=True).all()
        matches = s.query(Match).filter(Match.status.in_(["pending", "matched"])).all()
        orders = s.query(Order).order_by(Order.id).all()
        matched = {i for m in matches if m.status == "matched" for i in _ids(m.visit_ids)}
        by_table = {v.table_id: v for v in visits}
        from collections import Counter
        gids = [v.guest_id for v in visits if v.guest_id]
        nvis = Counter(g for (g,) in s.query(Visit.guest_id).filter(Visit.guest_id.in_(gids))) if gids else {}
        tables = []
        for t in s.query(Table).order_by(Table.id):
            v = by_table.get(t.id)
            state = "free" if not v else "matched" if v.id in matched else v.intent
            tables.append({"id": t.id, "zone": t.zone, "state": state, "name": v.first_name if v else None,
                           "regular": bool(v and v.guest_id and nvis.get(v.guest_id, 0) > 1)})
        counts = {"working": sum(t["state"] == "focus" for t in tables),
                  "open": sum(t["state"] in ("connect", "chill") for t in tables),
                  "active_matches": len(matches),
                  "pending_matches": sum(m.status == "pending" for m in matches),
                  "accepted_matches": sum(m.status == "matched" for m in matches), "free": sum(t["state"] == "free" for t in tables)}
        vt = {v.id: v.table_id for v in visits}
        queue = [{"id": o.id, "table": vt.get(o.visit_id), "items": o.items,
                  "minutes": int((now() - o.created_at).total_seconds() // 60), "eta": o.eta_min}
                 for o in orders if o.status == "new"]
        for q in queue: q["late"] = bool(q["eta"] and q["minutes"] > q["eta"])
        facts = []
        focus = [t["id"] for t in tables if t["state"] == "focus"]
        if focus: facts.append(f"{'Tables' if len(focus) > 1 else 'Table'} {', '.join(map(str, focus))} {'are' if len(focus) > 1 else 'is'} in focus mode: keep noise low, seat groups elsewhere.")
        pend = [m for m in matches if m.status == "pending"]
        if pend: facts.append(f"{len(pend)} match suggestion(s) awaiting responses.")
        for q in queue:
            if q["late"] or q["minutes"] >= 10:
                facts.append(f"Table {q['table']} order is late: waiting {q['minutes']} min (promised {q['eta']}).")
        if counts["free"] == 0: facts.append("No free tables: expect a wait for walk-ins.")
        key = json.dumps(facts)
        if key != _tip_cache["key"] or time.time() - _tip_cache["t"] > 120:
            _tip_cache.update(key=key, t=time.time(), tips=ai.tips(facts))
        return {"tables": tables, "counts": counts, "alerts": _tip_cache["tips"], "orders": [q for q in queue if q["table"]]}
