from .base import PaymentProvider, PayoutResult

class DisabledProvider(PaymentProvider):
    async def create_deposit(self, user_id: int, amount_minor: int, reference: str) -> dict:
        raise RuntimeError("Payment provider is not configured. No real deposit was created.")

    async def create_payout(self, destination: str, amount_minor: int, reference: str) -> PayoutResult:
        raise RuntimeError("Payment provider is not configured. No real payout was created.")

    def verify_webhook(self, body: bytes, signature: str) -> bool:
        return False
