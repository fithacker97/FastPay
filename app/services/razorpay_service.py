from typing import Optional, Dict, Any
import os
import logging
import hmac
import hashlib
import requests
from requests.auth import HTTPBasicAuth

"""
/home/jatin/FastPay/app/services/razorpay_service.py

Lightweight Razorpay service wrapper.
Reads RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET from environment.

Provides:
- create_order(amount, currency='INR', receipt=None, notes=None)
    (amount in major currency units, e.g. 100.50 -> 10050 paise)
- fetch_order(order_id)
- fetch_payment(payment_id)
- capture_payment(payment_id, amount)
- refund_payment(payment_id, amount=None, notes=None)
- verify_payment_signature(order_id, payment_id, signature)

This wrapper will use the official `razorpay` package if available,
otherwise falls back to direct HTTP calls via `requests`.
"""


try:
        import razorpay  # type: ignore
except Exception:
        razorpay = None


logger = logging.getLogger(__name__)


class RazorpayServiceError(Exception):
        pass


class RazorpayService:
        def __init__(self, key_id: Optional[str] = None, key_secret: Optional[str] = None):
                self.key_id = key_id or os.getenv("RAZORPAY_KEY_ID")
                self.key_secret = key_secret or os.getenv("RAZORPAY_KEY_SECRET")
                if not self.key_id or not self.key_secret:
                        raise RazorpayServiceError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set in environment")

                if razorpay:
                        self.client = razorpay.Client(auth=(self.key_id, self.key_secret))
                        self._use_client = True
                else:
                        self.client = None
                        self._use_client = False
                        self._base = "https://api.razorpay.com/v1"
                        self._auth = HTTPBasicAuth(self.key_id, self.key_secret)

        @staticmethod
        def _to_paise(amount: float) -> int:
                # Accepts int/float in major currency (e.g., INR rupees). Returns integer paise.
                return int(round(amount * 100))

        def create_order(
                self,
                amount: float,
                currency: str = "INR",
                receipt: Optional[str] = None,
                notes: Optional[Dict[str, str]] = None,
                payment_capture: int = 1,
        ) -> Dict[str, Any]:
                payload = {
                        "amount": self._to_paise(amount),
                        "currency": currency,
                        "payment_capture": payment_capture,
                }
                if receipt:
                        payload["receipt"] = receipt
                if notes:
                        payload["notes"] = notes

                if self._use_client:
                        return self.client.order.create(payload)
                url = f"{self._base}/orders"
                resp = requests.post(url, auth=self._auth, json=payload, timeout=10)
                if not resp.ok:
                        logger.error("create_order failed: %s %s", resp.status_code, resp.text)
                        raise RazorpayServiceError(f"create_order failed: {resp.status_code} {resp.text}")
                return resp.json()

        def fetch_order(self, order_id: str) -> Dict[str, Any]:
                if self._use_client:
                        return self.client.order.fetch(order_id)
                url = f"{self._base}/orders/{order_id}"
                resp = requests.get(url, auth=self._auth, timeout=10)
                if not resp.ok:
                        raise RazorpayServiceError(f"fetch_order failed: {resp.status_code} {resp.text}")
                return resp.json()

        def fetch_payment(self, payment_id: str) -> Dict[str, Any]:
                if self._use_client:
                        return self.client.payment.fetch(payment_id)
                url = f"{self._base}/payments/{payment_id}"
                resp = requests.get(url, auth=self._auth, timeout=10)
                if not resp.ok:
                        raise RazorpayServiceError(f"fetch_payment failed: {resp.status_code} {resp.text}")
                return resp.json()

        def capture_payment(self, payment_id: str, amount: float) -> Dict[str, Any]:
                payload = {"amount": self._to_paise(amount)}
                if self._use_client:
                        return self.client.payment.capture(payment_id, payload)
                url = f"{self._base}/payments/{payment_id}/capture"
                resp = requests.post(url, auth=self._auth, json=payload, timeout=10)
                if not resp.ok:
                        raise RazorpayServiceError(f"capture_payment failed: {resp.status_code} {resp.text}")
                return resp.json()

        def refund_payment(self, payment_id: str, amount: Optional[float] = None, notes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
                payload: Dict[str, Any] = {}
                if amount is not None:
                        payload["amount"] = self._to_paise(amount)
                if notes:
                        payload["notes"] = notes

                if self._use_client:
                        return self.client.payment.refund(payment_id, payload)
                url = f"{self._base}/payments/{payment_id}/refund"
                resp = requests.post(url, auth=self._auth, json=payload or None, timeout=10)
                if not resp.ok:
                        raise RazorpayServiceError(f"refund_payment failed: {resp.status_code} {resp.text}")
                return resp.json()

        def verify_payment_signature(self, order_id: str, payment_id: str, signature: str) -> bool:
                """
                Verifies razorpay signature. signature should be the value from frontend (razorpay_payment_signature).
                Computation: hmac_sha256(order_id + '|' + payment_id, key_secret).hexdigest() (lowercase)
                """
                msg = f"{order_id}|{payment_id}".encode("utf-8")
                secret = self.key_secret.encode("utf-8")
                generated = hmac.new(secret, msg, hashlib.sha256).hexdigest()
                valid = hmac.compare_digest(generated, signature)
                if not valid:
                        logger.warning("verify_payment_signature failed for order=%s payment=%s", order_id, payment_id)
                return valid