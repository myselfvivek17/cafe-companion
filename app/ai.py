import os, json, logging

MENU = [
    {"id": "filter", "name": "Filter Coffee", "price": 90, "tags": "focus"},
    {"id": "coldbrew", "name": "Cold Brew", "price": 160, "tags": "focus"},
    {"id": "matcha", "name": "Matcha Latte", "price": 190, "tags": "chill"},
    {"id": "masala", "name": "Masala Chai", "price": 80, "tags": "connect"},
    {"id": "nachos", "name": "Loaded Nachos (to share)", "price": 220, "tags": "connect"},
    {"id": "sandwich", "name": "Grilled Sandwich", "price": 180, "tags": "focus"},
    {"id": "brownie", "name": "Walnut Brownie", "price": 120, "tags": "chill"},
    {"id": "lemonade", "name": "Mint Lemonade", "price": 110, "tags": "chill"},
]
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
_client = None

def _gen(prompt: str):
    """Return parsed JSON from Gemini, or None on any failure."""
    global _client
    if os.getenv("DISABLE_GEMINI") or not os.getenv("GOOGLE_CLOUD_PROJECT"):
        return None
    try:
        from google import genai
        from google.genai import types
        if _client is None:
            _client = genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"],
                                   location=os.getenv("GOOGLE_CLOUD_LOCATION", "global"))
        r = _client.models.generate_content(model=MODEL, contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4,
                                               thinking_config=types.ThinkingConfig(thinking_budget=0)))
        return json.loads(r.text)
    except Exception as e:
        logging.warning("Gemini failed, using fallback: %s", type(e).__name__)
        return None

def _words(s): return {w.strip().lower() for w in (s or "").replace(";", ",").split(",") if w.strip()}

def _rule_intent(a):
    why = (a.get("why") or "").lower()
    if any(k in why for k in ("work", "study", "focus", "read", "write", "code")): return "focus"
    if a.get("open_to_meet") and any(k in why for k in ("meet", "social", "friends", "people", "chat")): return "connect"
    if a.get("open_to_meet"): return "connect"
    return "chill"

def _with_usual(recs, history):
    """Returning guest: put their most-ordered item first."""
    usual = (history or {}).get("usual_ids") or []
    if not usual: return recs
    recs = [r for r in recs if r["id"] != usual[0]]
    return ([{"id": usual[0], "reason": "⭐ Your usual. Welcome back!"}] + recs)[:3]

def classify(a: dict, history=None):
    """a: why, stay_minutes, open_to_meet, interests -> {intent, recs:[{id,reason}]}"""
    ids = [m["id"] for m in MENU]
    out = _gen(f"""You are a café host. Customer answers: {json.dumps(a)}.
Past visits of this guest at this café (null = first visit): {json.dumps(history)}.
Pick intent: "focus" (wants quiet to work/study; never connect), "connect" (open to meeting people and said so), or "chill".
Only choose connect if open_to_meet is true. Recommend 2-3 items from this menu that fit why they came today
(and their past orders if any): {json.dumps(MENU)}.
Return JSON: {{"intent": "...", "recs": [{{"id": "<menu id>", "reason": "<one short personal line>"}}]}}""")
    try:
        intent = out["intent"]
        recs = [r for r in out["recs"] if r["id"] in ids][:3]
        if intent not in ("focus", "connect", "chill") or not recs: raise ValueError
        if intent == "connect" and not a.get("open_to_meet"): intent = "chill"
        return {"intent": intent, "recs": _with_usual(recs, history)}
    except Exception:
        intent = _rule_intent(a)
        items = [m for m in MENU if m["tags"] == intent][:3]
        why = {"focus": "Steady fuel for a long session.", "connect": "Easy to share and chat over.", "chill": "A nice treat to unwind."}[intent]
        return {"intent": intent, "recs": _with_usual([{"id": m["id"], "reason": why} for m in items], history)}

# --- conversational host agent: one Gemini call per guest message ---
FALLBACK_Q = [
    ("Hi! Welcome in. What's your first name?", []),
    ("What brings you here today?", ["Getting work done", "Catching up with a friend", "Here to meet new people", "Just unwinding"]),
    ("How long are you planning to stay?", ["Under 30 min", "About an hour", "2+ hours"]),
    ("Would you be open to meeting people with similar interests today?", ["Yes, happy to", "Not today"]),
    ("What are you into? A few interests help me recommend and match.", ["Startups", "Books", "Music", "Photography", "Games"]),
]

def _fallback_chat(messages):
    answers = [m["text"] for m in messages if m["role"] == "guest"]
    if len(answers) < len(FALLBACK_Q):
        q, chips = FALLBACK_Q[len(answers)]
        return {"done": False, "reply": q, "chips": chips}
    name, why, stay, meet, interests = answers[:5]
    return {"done": True, "reply": "Thanks! Setting up your table…",
            "profile": {"first_name": name, "why": why,
                        "stay_minutes": {"Under 30 min": 25, "2+ hours": 150}.get(stay, 60),
                        "open_to_meet": meet.lower().startswith("yes"), "interests": interests}}

def chat(table: int, messages: list, history):
    guest_turns = sum(m["role"] == "guest" for m in messages)
    out = _gen(f"""You are "Bean", the warm AI host of an Indian café, chatting on a guest's phone after they scanned the QR on table {table}.
The café is called "Café Companion"; you are Bean. For a first-time guest, your FIRST message must greet them and ask only their first name (no chips).
Your goal, in as few natural turns as possible (max 5 questions): learn their first name (skip if known), why they came today,
roughly how long they'll stay, whether they're open to meeting other guests with similar interests, and a few interests.
Rules:
- Ask ONE short question at a time (max 25 words), friendly and adaptive. React briefly to what they said.
- If they're here to work/study or with a friend, don't push meeting people: confirm you'll keep things quiet / leave them be.
- Never ask age, gender, appearance, phone number, social handles or photos. This is not a dating app.
- Guest history at this café (null = first visit): {json.dumps(history)}.
  If they're returning, greet them by name, mention their usual order and ask if they'd like it again.
- Offer 2-4 short tap-reply chips that fit your question (or [] for names/open questions).
Conversation so far (host/guest): {json.dumps(messages)}
Guest has replied {guest_turns} times. Finish when you know enough, and always by the 5th guest reply.
Return JSON, either:
{{"done": false, "reply": "...", "chips": ["..."]}}
or {{"done": true, "reply": "<one warm closing line>", "profile": {{"first_name": "...", "why": "<their reason in a few words>", "stay_minutes": <int>, "open_to_meet": <true|false>, "interests": "<comma separated>"}}}}""")
    try:
        if out.get("done"):
            p = out["profile"]
            prof = {"first_name": str(p.get("first_name") or (history or {}).get("name") or "Guest")[:40],
                    "why": str(p.get("why", "")), "stay_minutes": int(p.get("stay_minutes") or 60),
                    "open_to_meet": bool(p.get("open_to_meet")), "interests": str(p.get("interests", ""))}
            return {"done": True, "reply": str(out.get("reply", "")), "profile": prof}
        if guest_turns >= 6: raise ValueError("too long")
        return {"done": False, "reply": str(out["reply"]), "chips": [str(c) for c in out.get("chips", [])][:4]}
    except Exception:
        return _fallback_chat(messages)

def group(people: list[dict]):
    """people: [{id, name, interests}] -> [{ids:[..], reason}]"""
    out = _gen(f"""Group these café visitors into groups of 2-4 who share multiple interests (skip people with no good match).
People: {json.dumps(people)}
Return JSON: {{"groups": [{{"ids": [<visitor ids>], "reason": "<one friendly sentence naming shared interests>"}}]}}""")
    valid = {p["id"] for p in people}
    try:
        gs, seen = [], set()
        for g in out["groups"]:
            ids = [i for i in g["ids"] if i in valid and i not in seen]
            if 2 <= len(ids) <= 4:
                seen.update(ids); gs.append({"ids": ids, "reason": g["reason"]})
        if gs: return gs
    except Exception:
        pass
    # fallback: greedy interest overlap
    gs, left = [], list(people)
    while left:
        base = left.pop(0)
        mates = [p for p in left if _words(base["interests"]) & _words(p["interests"])][:3]
        if mates:
            for m in mates: left.remove(m)
            shared = set.intersection(*[_words(p["interests"]) for p in [base] + mates]) or _words(base["interests"]) & _words(mates[0]["interests"])
            gs.append({"ids": [base["id"]] + [m["id"] for m in mates],
                       "reason": f"You all like {', '.join(sorted(shared))}."})
    return gs

def tips(facts: list[str]):
    """facts: rule-based alert strings -> short staff tips (same count preferred)."""
    if not facts: return []
    out = _gen(f"""Rewrite each fact as a short, actionable tip for café staff (max 12 words each).
Facts: {json.dumps(facts)}
Return JSON: {{"tips": ["..."]}}""")
    try:
        t = [str(x) for x in out["tips"]]
        if t: return t
    except Exception:
        pass
    return facts
