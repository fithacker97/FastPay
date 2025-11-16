from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from uuid import UUID, uuid4
from pydantic import BaseModel, EmailStr, Field, condecimal, validator

# Reusable decimal type for currency amounts (2 decimal places, > 0)
Money = condecimal(gt=0, max_digits=18, decimal_places=2)


class PaymentStatus(str, Enum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    refunded = "refunded"
    cancelled = "cancelled"


class BaseSchema(BaseModel):
    class Config:
        orm_mode = True


# User-related schemas
class UserCreate(BaseSchema):
    email: EmailStr
    password: str = Field(..., min_length=8)
    full_name: Optional[str] = None


class UserRead(BaseSchema):
    id: UUID
    email: EmailStr
    full_name: Optional[str] = None
    is_active: bool = True
    created_at: datetime


class UserUpdate(BaseSchema):
    full_name: Optional[str] = None
    is_active: Optional[bool] = None


# Authentication / Token schemas
class Token(BaseSchema):
    access_token: str
    token_type: str = "bearer"


class TokenPayload(BaseSchema):
    sub: Optional[str] = None
    exp: Optional[int] = None


# Account and balance schemas
class AccountRead(BaseSchema):
    id: UUID
    owner_id: UUID
    currency: str = Field(..., min_length=3, max_length=3)
    balance: Decimal
    created_at: datetime


# Payment / Transaction schemas
class PaymentBase(BaseSchema):
    amount: Money
    currency: str = Field(..., min_length=3, max_length=3)
    description: Optional[str] = None


class PaymentCreate(PaymentBase):
    source_account_id: UUID
    destination_account_id: UUID


class PaymentUpdate(BaseSchema):
    status: Optional[PaymentStatus] = None
    description: Optional[str] = None


class PaymentRead(PaymentBase):
    id: UUID
    reference: str
    status: PaymentStatus
    source_account_id: UUID
    destination_account_id: UUID
    created_at: datetime
    completed_at: Optional[datetime] = None

    @validator("reference", pre=True, always=True)
    def ensure_reference(cls, v):
        return v or f"pay_{uuid4().hex}"


# Transaction history item
class TransactionRead(BaseSchema):
    id: UUID
    account_id: UUID
    payment_id: Optional[UUID] = None
    amount: Decimal
    currency: str
    type: str  # "debit" or "credit"
    description: Optional[str] = None
    created_at: datetime


# Pagination helper
class PageMeta(BaseSchema):
    total: int
    page: int
    size: int


class PaginatedPayments(BaseSchema):
    items: List[PaymentRead]
    meta: PageMeta