from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class PayoutResult:
    accepted: bool
    provider_reference: str | None = None
    status: str = "pending"

class PaymentProvider(ABC):
    @abstractmethod
    async def create_deposit(self, user_id: int, amount_minor: int, reference: str) -> dict:
        raise NotImplementedError

    @abstractmethod
    async def create_payout(self, destination: str, amount_minor: int, reference: str) -> PayoutResult:
        raise NotImplementedError

    @abstractmethod
    def verify_webhook(self, body: bytes, signature: str) -> bool:
        raise NotImplementedError
