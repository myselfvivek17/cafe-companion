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
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4))
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

def classify(a: dict):
    """a: why, stay_minutes, open_to_meet, interests -> {intent, recs:[{id,reason}]}"""
    ids = [m["id"] for m in MENU]
    out = _gen(f"""You are a café host. Customer answers: {json.dumps(a)}.
Pick intent: "focus" (wants quiet to work/study; never connect), "connect" (open to meeting people and said so), or "chill".
Only choose connect if open_to_meet is true. Recommend 2-3 items from this menu: {json.dumps(MENU)}.
Return JSON: {{"intent": "...", "recs": [{{"id": "<menu id>", "reason": "<one short line>"}}]}}""")
    try:
        intent = out["intent"]
        recs = [r for r in out["recs"] if r["id"] in ids][:3]
        if intent not in ("focus", "connect", "chill") or not recs: raise ValueError
        if intent == "connect" and not a.get("open_to_meet"): intent = "chill"
        return {"intent": intent, "recs": recs}
    except Exception:
        intent = _rule_intent(a)
        items = [m for m in MENU if m["tags"] == intent][:3]
        why = {"focus": "Steady fuel for a long session.", "connect": "Easy to share and chat over.", "chill": "A nice treat to unwind."}[intent]
        return {"intent": intent, "recs": [{"id": m["id"], "reason": why} for m in items]}

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
