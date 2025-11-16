from decimal import Decimal
import uuid
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db import transaction
from django.db.models import F

"""
/home/jatin/FastPay/app/models.py

Django ORM models for a simple payment system:
- Account: one-to-one wallet for a user with atomic credit/debit methods.
- Merchant: basic merchant profile.
- PaymentTransaction: records transfers/payments with UUID ids and statuses.
- Refund: links to PaymentTransaction for partial/full refunds.

Adjust AUTH_USER_MODEL, currency choices and business logic as needed.
"""



CURRENCY_CHOICES = [
    ("USD", "US Dollar"),
    ("EUR", "Euro"),
    ("INR", "Indian Rupee"),
]


class Account(models.Model):
    """
    A wallet/account tied to a user. Use atomic operations to update balance.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="account"
    )
    balance = models.DecimalField(
        max_digits=18,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0.00"))],
    )
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"Account(user={self.user}, balance={self.balance} {self.currency})"

    def credit(self, amount: Decimal):
        """
        Atomically increase balance. `amount` must be >= 0.
        Returns the new balance as Decimal.
        """
        if amount < 0:
            raise ValueError("credit amount must be non-negative")
        with transaction.atomic():
            Account.objects.filter(pk=self.pk).update(balance=F("balance") + amount)
            # Refresh self to reflect DB value reliably
            self.refresh_from_db(fields=["balance"])
            return self.balance

    def debit(self, amount: Decimal):
        """
        Atomically decrease balance if sufficient funds exist.
        Raises ValueError on insufficient funds or negative amount.
        Returns the new balance as Decimal.
        """
        if amount < 0:
            raise ValueError("debit amount must be non-negative")
        with transaction.atomic():
            # Use select_for_update to ensure consistent check/update in concurrent scenarios
            a = Account.objects.select_for_update().get(pk=self.pk)
            if a.balance < amount:
                raise ValueError("insufficient funds")
            a.balance = F("balance") - amount
            a.save(update_fields=["balance"])
            a.refresh_from_db(fields=["balance"])
            # sync self to updated instance
            self.balance = a.balance
            return self.balance


class Merchant(models.Model):
    """
    Basic merchant profile; extend as needed.
    """
    name = models.CharField(max_length=255)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="merchant_owned"
    )
    reference_id = models.CharField(max_length=100, blank=True, db_index=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"Merchant({self.name})"


class PaymentTransaction(models.Model):
    """
    Records a payment between parties. Amounts are positive values.
    """
    STATUS_PENDING = "PENDING"
    STATUS_COMPLETED = "COMPLETED"
    STATUS_FAILED = "FAILED"
    STATUS_REFUNDED = "REFUNDED"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_REFUNDED, "Refunded"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    from_account = models.ForeignKey(
        Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="outgoing_transactions"
    )
    to_account = models.ForeignKey(
        Account, on_delete=models.SET_NULL, null=True, blank=True, related_name="incoming_transactions"
    )
    merchant = models.ForeignKey(Merchant, on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    currency = models.CharField(max_length=3, choices=CURRENCY_CHOICES, default="USD")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING)
    reference = models.CharField(max_length=255, blank=True, db_index=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["reference"]),
        ]
        ordering = ["-created_at"]

    def __str__(self):
        return f"PaymentTransaction({self.id} {self.amount} {self.currency} {self.status})"

    def mark_completed(self):
        if self.status != self.STATUS_COMPLETED:
            self.status = self.STATUS_COMPLETED
            self.completed_at = models.functions.Now()
            self.save(update_fields=["status", "completed_at"])

    def mark_failed(self, reason: str = ""):
        self.status = self.STATUS_FAILED
        if reason:
            self.metadata.setdefault("failure_reasons", []).append(reason)
        self.save(update_fields=["status", "metadata"])


class Refund(models.Model):
    """
    Refund linked to a PaymentTransaction. Partial refunds allowed.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction = models.ForeignKey(PaymentTransaction, on_delete=models.CASCADE, related_name="refunds")
    amount = models.DecimalField(max_digits=18, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Refund({self.id} for {self.transaction_id} amount={self.amount})"

    def process(self):
        """
        Basic refund processing: credit back to from_account and record processed_at.
        Business rules (fees, partial/full checks) should be enforced by callers.
        """
        tx = self.transaction
        if tx.status not in (tx.STATUS_COMPLETED,):
            raise ValueError("can only refund completed transactions")

        with transaction.atomic():
            src = tx.to_account  # where funds were sent originally
            dst = tx.from_account
            if dst is None:
                raise ValueError("original payer account not available for refund")

            # debit from merchant/receiver (src) and credit back to payer (dst)
            if src is None:
                raise ValueError("source account for refund not available")
            # ensure source has funds
            src = Account.objects.select_for_update().get(pk=src.pk)
            if src.balance < self.amount:
                raise ValueError("insufficient funds in source account to process refund")

            # perform updates
            src.balance = F("balance") - self.amount
            src.save(update_fields=["balance"])

            dst.balance = F("balance") + self.amount
            dst.save(update_fields=["balance"])

            # mark refund processed
            self.processed_at = models.functions.Now()
            self.save(update_fields=["processed_at"])

            # update transaction status if fully refunded
            total_refunded = sum([r.amount for r in tx.refunds.all()])
            if total_refunded >= tx.amount:
                tx.status = tx.STATUS_REFUNDED
                tx.save(update_fields=["status"])

            return self