import os
os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["DISABLE_GEMINI"] = "1"
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app)

def test_flow():
    c.post("/api/reset"); c.post("/api/seed")
    assert c.post("/api/match").json()["created"] == 1
    st = c.get("/api/staff").json()
    assert len(st["tables"]) == 6 and st["counts"]["active_matches"] == 1
    v = {t["name"]: t for t in st["tables"] if t["name"]}
    assert v["Asha"]["state"] == "focus"
    r = c.post("/api/visits", json={"table_id": 1, "first_name": "Zed", "why": "work", "open_to_meet": True}).json()
    assert r["intent"] == "focus" and r["recs"]
    for page in ("/", "/staff", "/t/1"): assert c.get(page).status_code == 200
