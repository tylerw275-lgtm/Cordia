import json
import logging

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.real_estate import LeaseClause

logger = logging.getLogger(__name__)

_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

LEASE_ANALYSIS_PROMPT = """You are a real estate lease analyst. Review the following lease document and:
1. Write a 1-2 sentence executive summary
2. Identify and flag key clauses with severity levels:
   - urgent: personal guarantees, unlimited liability, automatic renewal traps, punitive penalties
   - flag: rent escalation >5% annually, termination notice >60 days, unusual tenant restrictions
   - standard: typical maintenance responsibilities, standard renewal options

Return a JSON object with this exact structure:
{
  "summary": "...",
  "clauses": [
    {
      "clause_type": "renewal",
      "severity": "flag",
      "content": "exact or paraphrased clause text",
      "note": "plain English explanation of what this means and why it matters"
    }
  ]
}

LEASE DOCUMENT:
"""


async def analyze_lease_text(lease_id: str, raw_text: str, db: AsyncSession) -> tuple[str, list[LeaseClause]]:
    prompt = LEASE_ANALYSIS_PROMPT + raw_text[:8000]  # Truncate very long leases for safety

    response = await _client.messages.create(
        model=settings.claude_model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text
    # Extract JSON from response
    try:
        start = text.find("{")
        end = text.rfind("}") + 1
        data = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Lease analysis JSON parse error: {e}")
        return "Unable to parse lease analysis.", []

    import uuid
    from sqlalchemy import select
    from app.models.real_estate import Lease

    result = await db.execute(select(Lease).where(Lease.id == uuid.UUID(lease_id)))
    lease = result.scalar_one_or_none()
    if not lease:
        return data.get("summary", ""), []

    lease.summary = data.get("summary", "")
    stored_clauses = []
    for c in data.get("clauses", []):
        clause = LeaseClause(
            lease_id=lease.id,
            clause_type=c.get("clause_type", "unknown"),
            severity=c.get("severity", "standard"),
            content=c.get("content", ""),
            claude_note=c.get("note", ""),
        )
        db.add(clause)
        stored_clauses.append(clause)

    await db.commit()
    return lease.summary, stored_clauses
