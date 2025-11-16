from typing import Optional
import os
import hmac
import hashlib
from decimal import Decimal
import logging
from fastapi import APIRouter, Request, HTTPException, status
from pydantic import BaseModel, Field, validator

# /home/jatin/FastPay/app/routers/payments.py
# Razorpay integration router for FastAPI



try:
    import razorpay
except Exception as e:
    raise ImportError("razorpay package is required. Install with `pip install razorpay`") from e

logger = logging.getLogger("payments")

# Load credentials from environment (do NOT hardcode keys)
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

if not (RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET):
    logger.warning("Razorpay credentials are not set in environment variables.")

client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET))

router = APIRouter(prefix="/payments", tags=["payments"])


def _to_paise(amount: Decimal) -> int:
    """
    Convert an amount in currency units (e.g., INR) to paise (smallest unit).
    Accepts Decimal for accuracy.
    """
    return int((amount * Decimal("100")).quantize(Decimal("1")))


def _verify_signature(received_signature: str, payload: bytes, secret: str) -> bool:
    """
    Generic HMAC SHA256 verification returning boolean.
    ``payload`` must be the raw bytes used for signature generation.
    """
    if not secret:
        return False
    computed = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, received_signature)


class CreateOrderRequest(BaseModel):
    amount: Decimal = Field(..., description="Amount in currency units (e.g., 100.50 for INR)")
    currency: str = Field("INR", max_length=10)
    receipt: Optional[str] = None
    payment_capture: int = Field(1, ge=0, le=1, description="1 for automatic capture, 0 for manual")

    @validator("amount")
    def positive_amount(cls, v: Decimal):
        if v <= 0:
            raise ValueError("amount must be positive")
        return v


class CreateOrderResponse(BaseModel):
    id: str
    amount: int
    currency: str
    status: str
    raw: dict


class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str


class VerifyPaymentResponse(BaseModel):
    valid: bool
    reason: Optional[str] = None


@router.post("/create-order", response_model=CreateOrderResponse)
def create_order(payload: CreateOrderRequest):
    """
    Create a Razorpay order. Amount is passed in currency units (e.g., INR).
    The amount sent to Razorpay will be in the smallest currency unit (paise).
    """
    try:
        paise = _to_paise(payload.amount)
        order_data = {
            "amount": paise,
            "currency": payload.currency,
            "payment_capture": payload.payment_capture,
        }
        if payload.receipt:
            order_data["receipt"] = payload.receipt

        razor_order = client.order.create(data=order_data)
        return CreateOrderResponse(
            id=razor_order.get("id"),
            amount=razor_order.get("amount"),
            currency=razor_order.get("currency"),
            status=razor_order.get("status"),
            raw=razor_order,
        )
    except razorpay.errors.BadRequestError as e:
        logger.exception("Bad request when creating razorpay order")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        logger.exception("Unexpected error creating razorpay order")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create order")


@router.post("/verify-payment", response_model=VerifyPaymentResponse)
def verify_payment(payload: VerifyPaymentRequest):
    """
    Verify a payment signature received from the frontend after payment capture.
    Razorpay signs as HMAC_SHA256(order_id + "|" + payment_id) using key_secret.
    """
    if not RAZORPAY_KEY_SECRET:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Razorpay secret not configured")

    message = f"{payload.razorpay_order_id}|{payload.razorpay_payment_id}".encode("utf-8")
    try:
        computed = hmac.new(RAZORPAY_KEY_SECRET.encode("utf-8"), message, hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(computed, payload.razorpay_signature)
        if not valid:
            return VerifyPaymentResponse(valid=False, reason="signature_mismatch")
        return VerifyPaymentResponse(valid=True)
    except Exception:
        logger.exception("Error verifying payment signature")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Signature verification failed")


@router.post("/webhook")
async def razorpay_webhook(request: Request):
    """
    Endpoint to receive Razorpay webhooks. It verifies the X-Razorpay-Signature header
    using the webhook secret. Returns 200 on valid signature; 400 otherwise.
    Note: further event processing (fulfillment, notifications, etc.) should be implemented
    according to your application needs.
    """
    signature = request.headers.get("X-Razorpay-Signature", "")
    body = await request.body()

    if not RAZORPAY_WEBHOOK_SECRET:
        logger.warning("RAZORPAY_WEBHOOK_SECRET is not configured; rejecting webhook")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret not configured")

    if not signature:
        logger.warning("Missing X-Razorpay-Signature header")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing signature header")

    try:
        if not _verify_signature(signature, body, RAZORPAY_WEBHOOK_SECRET):
            logger.warning("Invalid webhook signature")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")

        # Process the webhook JSON payload as required by your app:
        event = await request.json()
        # Example minimal handling: log and return success. Replace with real logic.
        logger.info("Received valid webhook: %s", event.get("event"))
        return {"status": "ok"}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Error processing webhook")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook processing error")