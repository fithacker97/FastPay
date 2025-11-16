import os
from typing import Iterator, Optional
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session

"""
/home/jatin/FastPay/app/database.py

Lightweight SQLAlchemy setup for the FastPay app.

- Reads DATABASE_URL from env (defaults to sqlite:///./fastpay.db)
- Exposes: engine, SessionLocal, Base, get_db (dependency / context manager), init_db()
"""



DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fastpay.db")
SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in ("1", "true", "yes")

# sqlite requires special connect args
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=SQL_ECHO, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db() -> Iterator[Session]:
    """
    Provide a transactional scope around a series of operations.
    Suitable for FastAPI dependencies (yield).
    Usage in FastAPI:
        def get_db():
            db = SessionLocal()
            try:
                yield db
            finally:
                db.close()
    """
    db: Optional[Session] = None
    try:
        db = SessionLocal()
        yield db
    finally:
        if db:
            db.close()


def init_db(drop_all: bool = False) -> None:
    """
    Create all tables defined on Base metadata.
    Call with drop_all=True to drop existing tables first (use with caution).
    """
    if drop_all:
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)