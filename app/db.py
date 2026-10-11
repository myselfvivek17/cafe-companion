import os, datetime as dt
from sqlalchemy import create_engine, Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

url = os.getenv("DATABASE_URL") or "sqlite:///./cafe.db"
engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}, pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
Base = declarative_base()

def now(): return dt.datetime.utcnow()

class Table(Base):
    __tablename__ = "tables"
    id = Column(Integer, primary_key=True)
    zone = Column(String(32))

class Visit(Base):
    __tablename__ = "visits"
    id = Column(Integer, primary_key=True)
    table_id = Column(Integer)
    first_name = Column(String(64))
    intent = Column(String(16))  # focus|connect|chill
    interests = Column(Text, default="")
    stay_minutes = Column(Integer, default=60)
    created_at = Column(DateTime, default=now)
    active = Column(Boolean, default=True)

class Match(Base):
    __tablename__ = "matches"
    id = Column(Integer, primary_key=True)
    visit_ids = Column(Text)  # comma separated
    reason = Column(Text)
    status = Column(String(16), default="pending")  # pending|matched|declined
    accepts = Column(Text, default="")  # comma separated visit ids that accepted

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    visit_id = Column(Integer)
    items = Column(Text)
    status = Column(String(16), default="new")  # new|ready
    created_at = Column(DateTime, default=now)

ZONES = {1: "window", 2: "window", 3: "sofa", 4: "quiet", 5: "quiet", 6: "bar"}

def init_db():
    Base.metadata.create_all(engine)
    with Session() as s:
        for i, z in ZONES.items():
            if not s.get(Table, i):
                s.add(Table(id=i, zone=z))
        s.commit()
