from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from .models import LedgerTransaction, LedgerEntry

async def record_transaction(db: AsyncSession, key: str, reference: str,
                             description: str, entries: list[tuple[str, int, int]]):
    if sum(x[1] for x in entries) != sum(x[2] for x in entries):
        raise ValueError("Unbalanced ledger transaction")
    if any(debit < 0 or credit < 0 for _, debit, credit in entries):
        raise ValueError("Negative ledger entry")
    existing = await db.execute(select(LedgerTransaction).where(
        LedgerTransaction.idempotency_key == key))
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Duplicate financial operation")
    tx = LedgerTransaction(idempotency_key=key, reference=reference,
                           description=description)
    db.add(tx)
    await db.flush()
    for account, debit, credit in entries:
        db.add(LedgerEntry(transaction_id=tx.id, account=account,
                           debit_micro=debit, credit_micro=credit))
