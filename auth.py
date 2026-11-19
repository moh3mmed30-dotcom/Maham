import hashlib, hmac, json, time
from urllib.parse import parse_qsl
from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .config import settings
from .db import get_db
from .models import User, Wallet

def validate_init_data(init_data: str) -> dict:
    if not settings.bot_token:
        raise HTTPException(503, "Telegram bot is not configured")
    try:
        raw_pairs = parse_qsl(init_data, keep_blank_values=True)
        if len({k for k, _ in raw_pairs}) != len(raw_pairs):
            raise ValueError("duplicate initData keys")
        pairs = dict(raw_pairs)
        received_hash = pairs.pop("hash")
        auth_date = int(pairs["auth_date"])
        if time.time() - auth_date > settings.init_data_max_age_seconds or auth_date > time.time() + 30:
            raise ValueError("expired initData")
        data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", settings.bot_token.encode(), hashlib.sha256).digest()
        calculated = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            raise ValueError("invalid signature")
        user = json.loads(pairs["user"])
        return user
    except Exception:
        raise HTTPException(401, "Invalid or expired Telegram initData")

async def current_user(
    x_telegram_init_data: str = Header(default=""),
    db: AsyncSession = Depends(get_db)
) -> User:
    tg = validate_init_data(x_telegram_init_data)
    telegram_id = int(tg["id"])
    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(telegram_id=telegram_id, username=tg.get("username"),
                    first_name=tg.get("first_name"))
        db.add(user)
        await db.flush()
        db.add(Wallet(user_id=user.id))
        await db.commit()
        await db.refresh(user)
    if user.is_suspended:
        raise HTTPException(403, "Account suspended")
    if telegram_id in settings.admin_ids and user.role != "admin":
        user.role = "admin"
        await db.commit()
    return user

async def admin_user(user: User = Depends(current_user)) -> User:
    if user.telegram_id not in settings.admin_ids or user.role != "admin":
        raise HTTPException(403, "Administrator permission required")
    return user
