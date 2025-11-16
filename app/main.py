from datetime import datetime
from typing import List, Optional
from .enums import PaymentStatus
from uuid import uuid4
from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field, constr, root_validator, validator
from sqlalchemy import Column, DateTime, Enum, Integer, String, create_engine, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session, declarative_base, sessionmaker
import uvicorn

#!/usr/bin/env python3
"""
Simple FastPay main application.

Usage:
    pip install fastapi uvicorn sqlalchemy pydantic
    python /home/jatin/FastPay/app/main.py
"""



# ---------- Database setup ----------
SQLITE_URL = "sqlite:///./fastpay.db"
engine = create_engine(SQLITE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class PaymentStatus(str):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentORM(Base):
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True, index=True)
    amount_cents = Column(Integer, nullable=False)  # amount stored as integer cents
    currency = Column(String(3), nullable=False, index=True)
    method = Column(String(50), nullable=False)
    status = Column(String(20), nullable=False, default=PaymentStatus.PENDING)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


# ---------- Pydantic schemas ----------
class PaymentCreate(BaseModel):
    amount: float = Field(..., gt=0, description="Amount in major currency units (e.g. 12.34)")
    currency: constr(min_length=3, max_length=3) = Field(..., description="ISO 4217 currency code, e.g. USD")
    method: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=255)

    @validator("currency")
    def upper_currency(cls, v):
        return v.upper()

    @root_validator
    def check_amount_precision(cls, values):
        # Ensure at most 2 decimal places
        amount = values.get("amount")
        if amount is not None:
            cents = round(amount * 100)
            if abs(amount * 100 - cents) > 1e-6:
                raise ValueError("amount must have at most 2 decimal places")
        return values


class PaymentUpdate(BaseModel):
    description: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = Field(None)

    @validator("status")
    def validate_status(cls, v):
        if v is None:
            return v
        v = v.lower()
        allowed = {PaymentStatus.PENDING, PaymentStatus.COMPLETED, PaymentStatus.FAILED, PaymentStatus.REFUNDED}
        if v not in allowed:
            raise ValueError(f"status must be one of {sorted(list(allowed))}")
        return v


class PaymentRead(BaseModel):
    id: str
    amount: float
    currency: str
    method: str
    status: str
    description: Optional[str]
    created_at: datetime

    class Config:
        orm_mode = True


# ---------- Dependency ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ---------- App and routes ----------
app = FastAPI(title="FastPay API", version="0.1.0")


@app.get("/", summary="Service health")
def root():
    return {"service": "FastPay", "status": "ok", "time": datetime.utcnow().isoformat()}


@app.post("/payments", response_model=PaymentRead, status_code=status.HTTP_201_CREATED)
def create_payment(payload: PaymentCreate, db: Session = Depends(get_db)):
    payment_id = str(uuid4())
    amount_cents = int(round(payload.amount * 100))
    orm = PaymentORM(
        id=payment_id,
        amount_cents=amount_cents,
        currency=payload.currency,
        method=payload.method,
        description=payload.description,
        status=PaymentStatus.PENDING,
        created_at=datetime.utcnow(),
    )
    db.add(orm)
    db.commit()
    db.refresh(orm)
    return PaymentRead(
        id=orm.id,
        amount=orm.amount_cents / 100.0,
        currency=orm.currency,
        method=orm.method,
        status=orm.status,
        description=orm.description,
        created_at=orm.created_at,
    )


@app.get("/payments/{payment_id}", response_model=PaymentRead)
def get_payment(payment_id: str, db: Session = Depends(get_db)):
    stmt = select(PaymentORM).where(PaymentORM.id == payment_id)
    try:
        result = db.execute(stmt).scalar_one()
    except NoResultFound:
        raise HTTPException(status_code=404, detail="payment not found")
    return PaymentRead(
        id=result.id,
        amount=result.amount_cents / 100.0,
        currency=result.currency,
        method=result.method,
        status=result.status,
        description=result.description,
        created_at=result.created_at,
    )


@app.get("/payments", response_model=List[PaymentRead])
def list_payments(
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(PaymentORM)
    if status:
        stmt = stmt.where(PaymentORM.status == status.lower())
    stmt = stmt.order_by(PaymentORM.created_at.desc()).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [
        PaymentRead(
            id=r.id,
            amount=r.amount_cents / 100.0,
            currency=r.currency,
            method=r.method,
            status=r.status,
            description=r.description,
            created_at=r.created_at,
        )
        for r in rows
    ]


@app.put("/payments/{payment_id}", response_model=PaymentRead)
def update_payment(payment_id: str, payload: PaymentUpdate, db: Session = Depends(get_db)):
    stmt = select(PaymentORM).where(PaymentORM.id == payment_id)
    try:
        result = db.execute(stmt).scalar_one()
    except NoResultFound:
        raise HTTPException(status_code=404, detail="payment not found")

    if payload.description is not None:
        result.description = payload.description
    if payload.status is not None:
        # Simple state transition guard: don't allow going from refunded to completed, etc.
        allowed_transitions = {
            PaymentStatus.PENDING: {PaymentStatus.COMPLETED, PaymentStatus.FAILED, PaymentStatus.REFUNDED},
            PaymentStatus.COMPLETED: {PaymentStatus.REFUNDED},
            PaymentStatus.FAILED: set(),
            PaymentStatus.REFUNDED: set(),
        }
        current = result.status
        if payload.status not in allowed_transitions.get(current, set()):
            # allow noop
            if payload.status != current:
                raise HTTPException(
                    status_code=400,
                    detail=f"invalid status transition from {current} to {payload.status}",
                )
        result.status = payload.status

    db.add(result)
    db.commit()
    db.refresh(result)
    return PaymentRead(
        id=result.id,
        amount=result.amount_cents / 100.0,
        currency=result.currency,
        method=result.method,
        status=result.status,
        description=result.description,
        created_at=result.created_at,
    )


@app.delete("/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_payment(payment_id: str, db: Session = Depends(get_db)):
    stmt = select(PaymentORM).where(PaymentORM.id == payment_id)
    try:
        result = db.execute(stmt).scalar_one()
    except NoResultFound:
        raise HTTPException(status_code=404, detail="payment not found")
    db.delete(result)
    db.commit()
    return None


if __name__ == "__main__":

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)