import hashlib, json, uuid
from pathlib import Path
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, Depends, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from typing import Literal
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import Base, engine, get_db
from .models import User, Wallet, Campaign, CampaignEscrow, Task, TaskClaim, Proof, Withdrawal, AdminAudit, Appeal, UserReport
from .auth import current_user, admin_user
from .schemas import CampaignCreate, TaskCreate, ProofCreate, ReviewProof, VipUpdate
from .ledger import record_transaction

app = FastAPI(title="Task Platform API", version="1.0.0")
origins = [x.strip() for x in settings.cors_origins.split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins or ["*"],
                   allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

@app.get("/health")
async def health():
    return {"status": "ok", "payments": settings.payment_provider}

@app.get("/api/me")
async def me(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user.id))).scalar_one()
    return {"id": user.id, "telegram_id": user.telegram_id, "name": user.first_name,
            "username": user.username, "role": user.role, "trust_level": user.trust_level,
            "vip_until": user.vip_until.isoformat() if user.vip_until else None,
            "is_vip": bool(user.vip_until and user.vip_until > datetime.now(timezone.utc)),
            "vip_features": json.loads(user.vip_features or "[]"),
            "wallet": {"available_micro": wallet.available_micro,
                       "pending_micro": wallet.pending_micro,
                       "reserved_micro": wallet.reserved_micro}}

@app.get("/api/tasks")
async def tasks(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(Task, Campaign).join(Campaign, Task.campaign_id == Campaign.id)
        .where(Task.status == "active", Campaign.status == "active", Campaign.advertiser_id != user.id))).all()
    return [{"id": t.id, "campaign_id": c.id, "title": t.title,
             "instructions": t.instructions, "proof_type": t.proof_type,
             "reward_micro": t.reward_micro, "slots": c.total_slots,
             "campaign_type": c.campaign_type, "platform": c.platform}
            for t, c in rows]

@app.get("/api/tasks/mine")
async def my_task_claims(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(TaskClaim, Task).join(Task, TaskClaim.task_id == Task.id)
        .where(TaskClaim.user_id == user.id, TaskClaim.status.in_(["started", "submitted", "approved", "rejected"])))) .all()
    return [{"claim_id": claim.id, "task_id": task.id, "title": task.title,
             "instructions": task.instructions, "status": claim.status}
            for claim, task in rows]

@app.post("/api/tasks/{task_id}/claim")
async def claim_task(task_id: int, accepted_terms: bool,
                     db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    if not accepted_terms:
        raise HTTPException(400, "You must accept task conditions")
    task = (await db.execute(select(Task).where(Task.id == task_id).with_for_update())).scalar_one_or_none()
    if not task or task.status != "active":
        raise HTTPException(404, "Task unavailable")
    campaign = (await db.execute(select(Campaign).where(Campaign.id == task.campaign_id).with_for_update())).scalar_one_or_none()
    if not campaign or campaign.status != "active" or campaign.advertiser_id == user.id:
        raise HTTPException(404, "Campaign unavailable")
    escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == campaign.id).with_for_update())).scalar_one_or_none()
    active_claims = (await db.execute(select(func.count(TaskClaim.id)).where(
        TaskClaim.task_id == task.id, TaskClaim.status.in_(["started", "submitted", "approved"])))).scalar_one()
    if active_claims >= campaign.total_slots or not escrow or escrow.remaining_micro < task.reward_micro:
        raise HTTPException(409, "No campaign slots or funded rewards remain")
    claim = TaskClaim(task_id=task.id, user_id=user.id, status="started")
    db.add(claim)
    try:
        await db.commit()
        await db.refresh(claim)
    except Exception:
        await db.rollback()
        raise HTTPException(409, "Task already claimed or unavailable")
    return {"claim_id": claim.id, "status": claim.status}

@app.post("/api/proofs")
async def submit_proof(payload: ProofCreate, db: AsyncSession = Depends(get_db),
                       user: User = Depends(current_user)):
    claim = (await db.execute(select(TaskClaim).where(
        TaskClaim.id == payload.claim_id, TaskClaim.user_id == user.id).with_for_update())).scalar_one_or_none()
    if not claim or claim.status != "started":
        raise HTTPException(404, "Active claim not found")
    digest = hashlib.sha256(payload.content.strip().encode()).hexdigest()
    duplicate = (await db.execute(select(Proof).where(Proof.content_hash == digest))).scalar_one_or_none()
    if duplicate:
        raise HTTPException(409, "Duplicate proof detected")
    db.add(Proof(claim_id=claim.id, user_id=user.id, content=payload.content.strip(),
                 content_hash=digest))
    claim.status = "submitted"
    await db.commit()
    return {"status": "pending_review"}

@app.get("/api/campaigns/mine")
async def my_campaigns(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(Campaign).where(Campaign.advertiser_id == user.id))).scalars().all()
    output = []
    for c in rows:
        escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == c.id))).scalar_one_or_none()
        output.append({"id": c.id, "title": c.title, "status": c.status,
            "budget_micro": c.budget_micro, "reward_micro": c.reward_micro,
            "total_slots": c.total_slots, "remaining_micro": escrow.remaining_micro if escrow else 0,
            "paid_micro": escrow.paid_micro if escrow else 0})
    return output

@app.post("/api/campaigns")
async def create_campaign(payload: CampaignCreate, db: AsyncSession = Depends(get_db),
                         user: User = Depends(current_user)):
    required = payload.reward_micro * payload.total_slots
    if required > payload.budget_micro:
        raise HTTPException(400, "Campaign budget does not cover all rewards")
    wallet = (await db.execute(select(Wallet).where(Wallet.user_id == user.id).with_for_update())).scalar_one()
    if wallet.available_micro < payload.budget_micro:
        raise HTTPException(409, "Insufficient available balance to fund this campaign")
    wallet.available_micro -= payload.budget_micro
    wallet.reserved_micro += payload.budget_micro
    campaign = Campaign(advertiser_id=user.id, title=payload.title,
        description=payload.description, platform=payload.platform,
        campaign_type=payload.campaign_type, budget_micro=payload.budget_micro,
        reward_micro=payload.reward_micro, total_slots=payload.total_slots, status="pending_review")
    db.add(campaign)
    await db.flush()
    db.add(CampaignEscrow(campaign_id=campaign.id, remaining_micro=payload.budget_micro))
    db.add(Task(campaign_id=campaign.id, title=payload.title,
        instructions=payload.description, proof_type=payload.proof_type,
        reward_micro=payload.reward_micro, status="pending_review"))
    await record_transaction(db, key=f"campaign-fund:{campaign.id}",
        reference=str(campaign.id), description="Fund campaign from advertiser wallet",
        entries=[(f"wallet:{user.id}:available", payload.budget_micro, 0),
                 (f"campaign:{campaign.id}:escrow", 0, payload.budget_micro)])
    await db.commit()
    await db.refresh(campaign)
    return {"id": campaign.id, "status": campaign.status,
            "funded_micro": payload.budget_micro, "reserved_micro": payload.budget_micro,
            "message": "Campaign funded and submitted for administrator review."}

@app.get("/api/admin/overview")
async def admin_overview(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    users = (await db.execute(select(User))).scalars().all()
    campaigns = (await db.execute(select(Campaign))).scalars().all()
    proofs = (await db.execute(select(Proof).where(Proof.status == "pending"))).scalars().all()
    return {"users": len(users), "campaigns": len(campaigns), "pending_proofs": len(proofs)}

@app.get("/api/admin/campaigns")
async def admin_campaigns(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    rows = (await db.execute(select(Campaign).order_by(Campaign.created_at.desc()))).scalars().all()
    result = []
    for c in rows:
        escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == c.id))).scalar_one_or_none()
        result.append({"id": c.id, "advertiser_id": c.advertiser_id, "title": c.title,
            "type": c.campaign_type, "status": c.status, "budget_micro": c.budget_micro,
            "remaining_micro": escrow.remaining_micro if escrow else 0,
            "paid_micro": escrow.paid_micro if escrow else 0, "created_at": c.created_at.isoformat()})
    return result

@app.get("/api/admin/proofs")
async def admin_proofs(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    rows = (await db.execute(select(Proof).where(Proof.status == "pending"))).scalars().all()
    return [{"id": p.id, "user_id": p.user_id, "claim_id": p.claim_id,
             "content": p.content, "created_at": p.created_at.isoformat()} for p in rows]

@app.post("/api/admin/proofs/{proof_id}/review")
async def review_proof(proof_id: int, payload: ReviewProof,
                       db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    proof = (await db.execute(select(Proof).where(Proof.id == proof_id))).scalar_one_or_none()
    if not proof or proof.status != "pending":
        raise HTTPException(404, "Pending proof not found")
    proof.status, proof.reviewer_id, proof.review_note = payload.decision, admin.id, payload.note
    claim = (await db.execute(select(TaskClaim).where(TaskClaim.id == proof.claim_id).with_for_update())).scalar_one()
    task = (await db.execute(select(Task).where(Task.id == claim.task_id))).scalar_one()
    campaign = (await db.execute(select(Campaign).where(Campaign.id == task.campaign_id).with_for_update())).scalar_one()
    if payload.decision == "approved":
        escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == campaign.id).with_for_update())).scalar_one_or_none()
        advertiser_wallet = (await db.execute(select(Wallet).where(Wallet.user_id == campaign.advertiser_id).with_for_update())).scalar_one()
        worker_wallet = (await db.execute(select(Wallet).where(Wallet.user_id == proof.user_id).with_for_update())).scalar_one()
        if not escrow or escrow.remaining_micro < task.reward_micro or advertiser_wallet.reserved_micro < task.reward_micro:
            raise HTTPException(409, "Campaign escrow is insufficient; reward was not issued")
        escrow.remaining_micro -= task.reward_micro
        escrow.paid_micro += task.reward_micro
        advertiser_wallet.reserved_micro -= task.reward_micro
        worker_wallet.available_micro += task.reward_micro
        await record_transaction(db, key=f"proof-reward:{proof.id}", reference=str(proof.id),
            description="Approved task reward", entries=[
                (f"campaign:{campaign.id}:escrow", task.reward_micro, 0),
                (f"wallet:{proof.user_id}:available", 0, task.reward_micro)])
        approved_slots = (await db.execute(select(func.count(TaskClaim.id)).where(
            TaskClaim.task_id == task.id, TaskClaim.status == "approved"))).scalar_one() + 1
        if approved_slots >= campaign.total_slots:
            campaign.status = "completed"
            task.status = "completed"
            # Return any unused campaign budget to the advertiser after all slots settle.
            leftover = escrow.remaining_micro
            if leftover:
                escrow.remaining_micro = 0
                advertiser_wallet.reserved_micro -= leftover
                advertiser_wallet.available_micro += leftover
                await record_transaction(db, key=f"campaign-refund:{campaign.id}",
                    reference=str(campaign.id), description="Release unused campaign budget",
                    entries=[(f"campaign:{campaign.id}:escrow", leftover, 0),
                             (f"wallet:{campaign.advertiser_id}:available", 0, leftover)])
    proof.status, proof.reviewer_id, proof.review_note = payload.decision, admin.id, payload.note
    claim.status = payload.decision
    db.add(AdminAudit(admin_user_id=admin.id, action="proof_review",
        target_type="proof", target_id=str(proof.id),
        details=json.dumps({"decision": payload.decision, "note": payload.note})))
    await db.commit()
    return {"status": proof.status, "reward_micro": task.reward_micro if payload.decision == "approved" else 0}

@app.post("/api/admin/users/{user_id}/vip")
async def set_vip(user_id: int, payload: VipUpdate, db: AsyncSession = Depends(get_db),
                 admin: User = Depends(admin_user)):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    base = target.vip_until if target.vip_until and target.vip_until > datetime.now(timezone.utc) else datetime.now(timezone.utc)
    target.vip_until = base + timedelta(days=payload.days)
    target.vip_features = json.dumps(payload.features)
    db.add(AdminAudit(admin_user_id=admin.id, action="grant_vip",
        target_type="user", target_id=str(target.id),
        details=json.dumps({"days": payload.days, "features": payload.features})))
    await db.commit()
    return {"user_id": target.id, "vip_until": target.vip_until.isoformat(),
            "features": payload.features}

@app.post("/api/admin/users/{user_id}/suspend")
async def suspend_user(user_id: int, db: AsyncSession = Depends(get_db),
                       admin: User = Depends(admin_user)):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    target.is_suspended = True
    db.add(AdminAudit(admin_user_id=admin.id, action="suspend_user",
        target_type="user", target_id=str(target.id)))
    await db.commit()
    return {"status": "suspended"}

@app.post("/api/wallet/withdrawals")
async def request_withdrawal(amount_micro: int, destination: str,
                             db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    # Safe by default: no payout without a configured, verified provider adapter.
    if settings.payment_provider == "disabled":
        raise HTTPException(503, "Payout provider is not configured")
    if amount_micro < 1000 or not destination.strip():
        raise HTTPException(400, "Invalid withdrawal details")
    raise HTTPException(501, "Implement provider-specific payout and atomic wallet reservation before enabling")

@app.post("/api/payments/deposits")
async def create_deposit():
    if settings.payment_provider == "disabled":
        raise HTTPException(503, "Deposit provider is not configured")
    raise HTTPException(501, "Implement and verify the selected provider adapter before enabling deposits")

@app.post("/api/payments/webhook")
async def payment_webhook(request: Request, x_provider_signature: str = Header(default="")):
    # Provider-specific signature verification and event schema are mandatory.
    raise HTTPException(501, "Configure provider adapter and webhook signature verification first")



class AppealCreate(BaseModel):
    proof_id: int
    message: str = Field(min_length=10, max_length=2000)

class ReportCreate(BaseModel):
    target_user_id: int
    reason: str = Field(min_length=10, max_length=1500)

class CampaignAction(BaseModel):
    action: Literal["pause", "resume", "cancel"]

class CampaignReview(BaseModel):
    decision: Literal["approved", "rejected"]
    note: str = Field(default="", max_length=1000)

class TrustUpdate(BaseModel):
    trust_level: Literal["beginner", "trusted", "professional", "premium"]

class AppealReview(BaseModel):
    decision: Literal["accepted", "rejected"]
    note: str = Field(default="", max_length=1000)

class WithdrawalReview(BaseModel):
    decision: Literal["paid", "rejected"]
    provider_reference: str = Field(default="", max_length=150)
    note: str = Field(default="", max_length=1000)

@app.get("/api/wallet/ledger")
async def wallet_ledger(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    account_names = [f"wallet:{user.id}:available", f"wallet:{user.id}:pending", f"wallet:{user.id}:reserved"]
    rows = (await db.execute(select(LedgerEntry, LedgerTransaction)
        .join(LedgerTransaction, LedgerEntry.transaction_id == LedgerTransaction.id)
        .where(LedgerEntry.account.in_(account_names))
        .order_by(LedgerTransaction.created_at.desc()).limit(100))).all()
    return [{"date": tx.created_at.isoformat(), "description": tx.description,
             "debit_micro": entry.debit_micro, "credit_micro": entry.credit_micro,
             "account": entry.account} for entry, tx in rows]

@app.get("/api/proofs/mine")
async def my_proofs(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(Proof).where(Proof.user_id == user.id).order_by(Proof.created_at.desc()))).scalars().all()
    return [{"id": p.id, "claim_id": p.claim_id, "status": p.status, "review_note": p.review_note, "created_at": p.created_at.isoformat()} for p in rows]

@app.post("/api/appeals")
async def create_appeal(payload: AppealCreate, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    proof = (await db.execute(select(Proof).where(Proof.id == payload.proof_id, Proof.user_id == user.id))).scalar_one_or_none()
    if not proof or proof.status != "rejected":
        raise HTTPException(404, "Rejected proof not found")
    exists = (await db.execute(select(Appeal).where(Appeal.proof_id == proof.id))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "An appeal already exists for this proof")
    appeal = Appeal(user_id=user.id, proof_id=proof.id, message=payload.message.strip())
    db.add(appeal)
    await db.commit()
    await db.refresh(appeal)
    return {"id": appeal.id, "status": appeal.status}

@app.get("/api/appeals/mine")
async def my_appeals(db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    rows = (await db.execute(select(Appeal).where(Appeal.user_id == user.id).order_by(Appeal.created_at.desc()))).scalars().all()
    return [{"id": a.id, "proof_id": a.proof_id, "message": a.message, "status": a.status,
             "admin_note": a.admin_note, "created_at": a.created_at.isoformat()} for a in rows]

@app.post("/api/reports")
async def report_user(payload: ReportCreate, db: AsyncSession = Depends(get_db), user: User = Depends(current_user)):
    if payload.target_user_id == user.id:
        raise HTTPException(400, "You cannot report yourself")
    target = (await db.execute(select(User).where(User.id == payload.target_user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    db.add(UserReport(reporter_id=user.id, target_user_id=target.id, reason=payload.reason.strip()))
    await db.commit()
    return {"status": "received"}

@app.get("/api/admin/users")
async def admin_users(q: str = "", db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    query = select(User).order_by(User.created_at.desc()).limit(200)
    if q.strip().isdigit():
        query = select(User).where(User.telegram_id == int(q.strip())).limit(50)
    users = (await db.execute(query)).scalars().all()
    return [{"id": u.id, "telegram_id": u.telegram_id, "username": u.username,
             "first_name": u.first_name, "role": u.role, "trust_level": u.trust_level,
             "suspended": u.is_suspended, "vip_until": u.vip_until.isoformat() if u.vip_until else None,
             "created_at": u.created_at.isoformat()} for u in users]

@app.post("/api/admin/users/{user_id}/trust")
async def update_trust(user_id: int, payload: TrustUpdate, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    target.trust_level = payload.trust_level
    db.add(AdminAudit(admin_user_id=admin.id, action="update_trust", target_type="user",
                      target_id=str(target.id), details=json.dumps({"trust_level": payload.trust_level})))
    await db.commit()
    return {"user_id": target.id, "trust_level": target.trust_level}

@app.post("/api/admin/users/{user_id}/unsuspend")
async def unsuspend_user(user_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    target = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "User not found")
    target.is_suspended = False
    db.add(AdminAudit(admin_user_id=admin.id, action="unsuspend_user", target_type="user", target_id=str(target.id)))
    await db.commit()
    return {"status": "active"}

@app.post("/api/admin/campaigns/{campaign_id}/review")
async def review_campaign(campaign_id: int, payload: CampaignReview, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    campaign = (await db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update())).scalar_one_or_none()
    if not campaign or campaign.status != "pending_review":
        raise HTTPException(404, "Campaign awaiting review not found")
    task = (await db.execute(select(Task).where(Task.campaign_id == campaign.id))).scalar_one_or_none()
    if payload.decision == "approved":
        campaign.status = "active"
        if task: task.status = "active"
    else:
        escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == campaign.id).with_for_update())).scalar_one()
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == campaign.advertiser_id).with_for_update())).scalar_one()
        amount = escrow.remaining_micro
        if amount:
            escrow.remaining_micro = 0; wallet.reserved_micro -= amount; wallet.available_micro += amount
            await record_transaction(db, key=f"campaign-review-refund:{campaign.id}", reference=str(campaign.id),
                description="Refund rejected campaign budget",
                entries=[(f"campaign:{campaign.id}:escrow", amount, 0), (f"wallet:{campaign.advertiser_id}:available", 0, amount)])
        campaign.status = "rejected"
        if task: task.status = "rejected"
    db.add(AdminAudit(admin_user_id=admin.id, action="review_campaign", target_type="campaign", target_id=str(campaign.id),
                      details=json.dumps({"decision": payload.decision, "note": payload.note})))
    await db.commit()
    return {"campaign_id": campaign.id, "status": campaign.status}

@app.post("/api/admin/campaigns/{campaign_id}/action")
async def campaign_action(campaign_id: int, payload: CampaignAction, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    campaign = (await db.execute(select(Campaign).where(Campaign.id == campaign_id).with_for_update())).scalar_one_or_none()
    if not campaign:
        raise HTTPException(404, "Campaign not found")
    task = (await db.execute(select(Task).where(Task.campaign_id == campaign.id))).scalar_one_or_none()
    escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == campaign.id).with_for_update())).scalar_one_or_none()
    if payload.action == "pause":
        if campaign.status != "active": raise HTTPException(409, "Only active campaigns can be paused")
        campaign.status = "paused"
        if task: task.status = "paused"
    elif payload.action == "resume":
        if campaign.status != "paused": raise HTTPException(409, "Only paused campaigns can be resumed")
        campaign.status = "active"
        if task: task.status = "active"
    else:
        active = (await db.execute(select(func.count(TaskClaim.id)).where(
            TaskClaim.task_id == task.id, TaskClaim.status.in_(["started", "submitted"])))) .scalar_one() if task else 0
        if active:
            raise HTTPException(409, "Cannot cancel while users have active or submitted claims")
        if campaign.status in ["completed", "cancelled"]:
            raise HTTPException(409, "Campaign is already closed")
        wallet = (await db.execute(select(Wallet).where(Wallet.user_id == campaign.advertiser_id).with_for_update())).scalar_one()
        if escrow and escrow.remaining_micro:
            amount = escrow.remaining_micro
            escrow.remaining_micro = 0
            wallet.reserved_micro -= amount
            wallet.available_micro += amount
            await record_transaction(db, key=f"campaign-cancel-refund:{campaign.id}", reference=str(campaign.id),
                description="Refund remaining campaign escrow after cancellation",
                entries=[(f"campaign:{campaign.id}:escrow", amount, 0),
                         (f"wallet:{campaign.advertiser_id}:available", 0, amount)])
        campaign.status = "cancelled"
        if task: task.status = "cancelled"
    db.add(AdminAudit(admin_user_id=admin.id, action=f"campaign_{payload.action}", target_type="campaign", target_id=str(campaign.id)))
    await db.commit()
    return {"campaign_id": campaign.id, "status": campaign.status}

@app.get("/api/admin/appeals")
async def admin_appeals(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    rows = (await db.execute(select(Appeal).where(Appeal.status == "open").order_by(Appeal.created_at.asc()))).scalars().all()
    return [{"id": a.id, "user_id": a.user_id, "proof_id": a.proof_id, "message": a.message,
             "created_at": a.created_at.isoformat()} for a in rows]

@app.post("/api/admin/appeals/{appeal_id}/review")
async def review_appeal(appeal_id: int, payload: AppealReview, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    appeal = (await db.execute(select(Appeal).where(Appeal.id == appeal_id).with_for_update())).scalar_one_or_none()
    if not appeal or appeal.status != "open":
        raise HTTPException(404, "Open appeal not found")
    appeal.status, appeal.admin_note, appeal.reviewed_by = payload.decision, payload.note, admin.id
    if payload.decision == "accepted":
        proof = (await db.execute(select(Proof).where(Proof.id == appeal.proof_id).with_for_update())).scalar_one()
        claim = (await db.execute(select(TaskClaim).where(TaskClaim.id == proof.claim_id).with_for_update())).scalar_one()
        task = (await db.execute(select(Task).where(Task.id == claim.task_id))).scalar_one()
        campaign = (await db.execute(select(Campaign).where(Campaign.id == task.campaign_id).with_for_update())).scalar_one()
        escrow = (await db.execute(select(CampaignEscrow).where(CampaignEscrow.campaign_id == campaign.id).with_for_update())).scalar_one()
        advertiser_wallet = (await db.execute(select(Wallet).where(Wallet.user_id == campaign.advertiser_id).with_for_update())).scalar_one()
        worker_wallet = (await db.execute(select(Wallet).where(Wallet.user_id == appeal.user_id).with_for_update())).scalar_one()
        if escrow.remaining_micro < task.reward_micro or advertiser_wallet.reserved_micro < task.reward_micro:
            raise HTTPException(409, "Campaign escrow is insufficient")
        escrow.remaining_micro -= task.reward_micro; escrow.paid_micro += task.reward_micro
        advertiser_wallet.reserved_micro -= task.reward_micro; worker_wallet.available_micro += task.reward_micro
        await record_transaction(db, key=f"appeal-reward:{appeal.id}", reference=str(appeal.id), description="Reward after accepted appeal",
            entries=[(f"campaign:{campaign.id}:escrow", task.reward_micro, 0),
                     (f"wallet:{appeal.user_id}:available", 0, task.reward_micro)])
        proof.status = "approved"; claim.status = "approved"
        approved_count = (await db.execute(select(func.count(TaskClaim.id)).where(
            TaskClaim.task_id == task.id, TaskClaim.status == "approved"))).scalar_one()
        if approved_count >= campaign.total_slots:
            campaign.status = "completed"; task.status = "completed"
            leftover = escrow.remaining_micro
            if leftover:
                escrow.remaining_micro = 0; advertiser_wallet.reserved_micro -= leftover; advertiser_wallet.available_micro += leftover
                await record_transaction(db, key=f"appeal-campaign-refund:{campaign.id}", reference=str(campaign.id),
                    description="Release unused campaign budget after appeal completion",
                    entries=[(f"campaign:{campaign.id}:escrow", leftover, 0),
                             (f"wallet:{campaign.advertiser_id}:available", 0, leftover)])
    db.add(AdminAudit(admin_user_id=admin.id, action="review_appeal", target_type="appeal", target_id=str(appeal.id),
                      details=json.dumps({"decision": payload.decision, "note": payload.note})))
    await db.commit()
    return {"id": appeal.id, "status": appeal.status}

@app.get("/api/admin/withdrawals")
async def admin_withdrawals(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    rows = (await db.execute(select(Withdrawal).order_by(Withdrawal.created_at.desc()).limit(200))).scalars().all()
    return [{"id": w.id, "user_id": w.user_id, "amount_micro": w.amount_micro, "fee_micro": w.fee_micro,
             "net_micro": w.net_micro, "status": w.status, "provider_reference": w.provider_reference,
             "created_at": w.created_at.isoformat()} for w in rows]

@app.get("/api/admin/reports")
async def admin_reports(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    rows = (await db.execute(select(UserReport).where(UserReport.status == "open").order_by(UserReport.created_at.asc()).limit(200))).scalars().all()
    return [{"id": r.id, "reporter_id": r.reporter_id, "target_user_id": r.target_user_id,
             "reason": r.reason, "created_at": r.created_at.isoformat()} for r in rows]

@app.post("/api/admin/reports/{report_id}/close")
async def close_report(report_id: int, db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    report = (await db.execute(select(UserReport).where(UserReport.id == report_id).with_for_update())).scalar_one_or_none()
    if not report or report.status != "open":
        raise HTTPException(404, "Open report not found")
    report.status = "closed"
    db.add(AdminAudit(admin_user_id=admin.id, action="close_report", target_type="report", target_id=str(report.id)))
    await db.commit()
    return {"id": report.id, "status": report.status}

@app.get("/api/admin/audit")
async def admin_audit(db: AsyncSession = Depends(get_db), admin: User = Depends(admin_user)):
    rows = (await db.execute(select(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(200))).scalars().all()
    return [{"id": a.id, "admin_user_id": a.admin_user_id, "action": a.action, "target_type": a.target_type,
             "target_id": a.target_id, "details": a.details, "created_at": a.created_at.isoformat()} for a in rows]


PROJECT_ROOT = Path(__file__).resolve().parents[2]
app.mount("/admin", StaticFiles(directory=str(PROJECT_ROOT / "admin"), html=True), name="admin")
app.mount("/", StaticFiles(directory=str(PROJECT_ROOT / "miniapp"), html=True), name="miniapp")
