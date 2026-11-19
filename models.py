from datetime import datetime, timezone
from sqlalchemy import String, BigInteger, Integer, Boolean, DateTime, ForeignKey, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .db import Base

def now():
    return datetime.now(timezone.utc)

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(20), default="user")
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)
    trust_level: Mapped[str] = mapped_column(String(20), default="beginner")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    vip_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    vip_features: Mapped[str] = mapped_column(Text, default="[]")

class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    advertiser_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text)
    platform: Mapped[str] = mapped_column(String(30), default="telegram")
    campaign_type: Mapped[str] = mapped_column(String(40), default="survey")
    budget_micro: Mapped[int] = mapped_column(BigInteger)
    reward_micro: Mapped[int] = mapped_column(BigInteger)
    total_slots: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class CampaignEscrow(Base):
    __tablename__ = "campaign_escrows"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), unique=True, index=True)
    remaining_micro: Mapped[int] = mapped_column(BigInteger)
    paid_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    instructions: Mapped[str] = mapped_column(Text)
    proof_type: Mapped[str] = mapped_column(String(30), default="text")
    reward_micro: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="active")

class TaskClaim(Base):
    __tablename__ = "task_claims"
    __table_args__ = (UniqueConstraint("task_id", "user_id", name="uq_task_user_claim"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="started")
    accepted_terms_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Proof(Base):
    __tablename__ = "proofs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("task_claims.id"), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    available_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    pending_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    reserved_micro: Mapped[int] = mapped_column(BigInteger, default=0)

class LedgerTransaction(Base):
    __tablename__ = "ledger_transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    reference: Mapped[str] = mapped_column(String(100))
    description: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("ledger_transactions.id"), index=True)
    account: Mapped[str] = mapped_column(String(100))
    debit_micro: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_micro: Mapped[int] = mapped_column(BigInteger, default=0)

class PaymentEvent(Base):
    __tablename__ = "payment_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_event_id: Mapped[str] = mapped_column(String(150), unique=True)
    event_type: Mapped[str] = mapped_column(String(50))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Withdrawal(Base):
    __tablename__ = "withdrawals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    amount_micro: Mapped[int] = mapped_column(BigInteger)
    fee_micro: Mapped[int] = mapped_column(BigInteger)
    net_micro: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    provider_reference: Mapped[str | None] = mapped_column(String(150))
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AdminAudit(Base):
    __tablename__ = "admin_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    admin_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100))
    target_type: Mapped[str] = mapped_column(String(50))
    target_id: Mapped[str] = mapped_column(String(100))
    details: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

Index("ix_proofs_hash_user", Proof.content_hash, Proof.user_id)

class Appeal(Base):
    __tablename__ = "appeals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    proof_id: Mapped[int] = mapped_column(ForeignKey("proofs.id"), unique=True)
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    admin_note: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class UserReport(Base):
    __tablename__ = "user_reports"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    target_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
