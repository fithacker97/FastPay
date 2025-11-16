from fastapi import APIRouter, Request, Header, HTTPException, BackgroundTasks, status
from typing import Any, Dict, Optional
import hmac
import hashlib
import os
import time
import logging
import json

logger = logging.getLogger("fastpay.webhooks")
router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Configuration via environment variables
STRIPE_SIGNING_SECRET = os.getenv("STRIPE_SIGNING_SECRET")  # optional
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")  # generic HMAC secret (optional)
TIMESTAMP_TOLERANCE_SECONDS = int(os.getenv("WEBHOOK_TIMESTAMP_TOLERANCE", "300"))


def _parse_stripe_signature_header(header_value: str) -> Optional[Dict[str, str]]:
    """
    Parse Stripe-like signature header: "t=timestamp,v1=signature[,v0=...]" -> dict
    """
    if not header_value:
        return None
    parts = header_value.split(",")
    out = {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _verify_hmac_signature(payload: bytes, signature: str, secret: str) -> bool:
    """
    Compute HMAC-SHA256(payload) with secret and compare to signature (hex).
    """
    if not secret or not signature:
        return False
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(mac, signature)


def _verify_stripe_signature(payload: bytes, header_value: str, secret: str) -> bool:
    """
    Verify a Stripe-compatible signature header: uses "t=... , v1=..."
    Recreates Stripe's simple scheme: HMAC_SHA256("{timestamp}.{payload}")
    """
    if not secret:
        # no secret configured -> accept but log
        logger.debug("No STRIPE_SIGNING_SECRET configured; skipping signature verification")
        return True
    parsed = _parse_stripe_signature_header(header_value)
    if not parsed or "t" not in parsed or "v1" not in parsed:
        logger.warning("Invalid stripe signature header")
        return False
    try:
        timestamp = int(parsed["t"])
    except ValueError:
        return False
    # timestamp tolerance
    if abs(time.time() - timestamp) > TIMESTAMP_TOLERANCE_SECONDS:
        logger.warning("Stripe signature timestamp outside tolerance")
        return False
    signed_payload = str(timestamp).encode("utf-8") + b"." + payload
    expected = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parsed["v1"])


async def _process_stripe_event(event: Dict[str, Any]) -> None:
    """
    Minimal router for Stripe event types. Extend to integrate with application services.
    """
    etype = event.get("type", "")
    data = event.get("data", {}).get("object", {})
    logger.info("Processing stripe event: %s", etype)
    try:
        if etype == "payment_intent.succeeded":
            await _handle_payment_succeeded(data)
        elif etype == "payment_intent.payment_failed":
            await _handle_payment_failed(data)
        else:
            logger.debug("Unhandled stripe event type: %s", etype)
    except Exception as e:
        logger.exception("Error processing stripe event: %s", e)


async def _handle_payment_succeeded(obj: Dict[str, Any]) -> None:
    """
    Placeholder: update order status, notify user, etc.
    """
    logger.info("Payment succeeded for object id=%s amount=%s", obj.get("id"), obj.get("amount_received"))


async def _handle_payment_failed(obj: Dict[str, Any]) -> None:
    """
    Placeholder: mark order failed, retry logic, notify user, etc.
    """
    logger.info("Payment failed for object id=%s last_payment_error=%s", obj.get("id"), obj.get("last_payment_error"))


async def _process_generic_event(payload: Dict[str, Any]) -> None:
    """
    Process generic webhook payloads. Route based on top-level keys or a 'type' field.
    """
    logger.info("Processing generic webhook payload")
    # Example: route by "type" field
    etype = payload.get("type") or payload.get("event") or ""
    if etype:
        logger.debug("Generic event type: %s", etype)
    # Add integration with application services here
    logger.debug("Generic payload: %s", json.dumps(payload)[:200])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.post("/stripe")
async def stripe_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
):
    """
    Endpoint to receive Stripe webhooks.
    Verifies Stripe-like signature if STRIPE_SIGNING_SECRET is configured.
    Processing is delegated to background tasks.
    """
    raw_body = await request.body()
    if STRIPE_SIGNING_SECRET:
        if not _verify_stripe_signature(raw_body, stripe_signature or "", STRIPE_SIGNING_SECRET):
            logger.warning("Stripe webhook signature verification failed")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    # parse JSON
    try:
        event = await request.json()
    except Exception:
        logger.exception("Invalid JSON in Stripe webhook")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    # background processing
    background_tasks.add_task(_process_stripe_event, event)
    return {"received": True}


@router.post("/generic")
async def generic_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    signature: Optional[str] = Header(None, alias="X-Signature"),
):
    """
    Generic webhook endpoint. If WEBHOOK_SECRET is set, validate an HMAC-SHA256 signature
    of the raw body against the X-Signature header (hex).
    """
    raw_body = await request.body()
    if WEBHOOK_SECRET:
        if not _verify_hmac_signature(raw_body, signature or "", WEBHOOK_SECRET):
            logger.warning("Generic webhook signature verification failed")
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid signature")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        logger.exception("Invalid JSON in generic webhook")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON")
    background_tasks.add_task(_process_generic_event, payload)
    return {"received": True}