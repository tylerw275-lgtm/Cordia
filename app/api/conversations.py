import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.conversation import Conversation, Message

router = APIRouter(prefix="/api/v1/conversations", tags=["conversations"])


@router.get("")
async def list_conversations(db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Conversation).order_by(Conversation.updated_at.desc()))
    convs = result.scalars().all()
    return {
        "conversations": [
            {"id": str(c.id), "phone": c.phone_number, "active": c.is_active, "created": c.created_at.isoformat()}
            for c in convs
        ]
    }


@router.get("/{conv_id}/messages")
async def get_messages(conv_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conv_id)
        .order_by(Message.created_at)
    )
    messages = result.scalars().all()
    return {
        "messages": [
            {"id": str(m.id), "role": m.role, "content": m.content, "created": m.created_at.isoformat()}
            for m in messages
        ]
    }


@router.delete("/{conv_id}")
async def reset_conversation(conv_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> dict:
    result = await db.execute(select(Conversation).where(Conversation.id == conv_id))
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    conv.is_active = False
    await db.commit()
    return {"reset": True, "conversation_id": str(conv_id)}
