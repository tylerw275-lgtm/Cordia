import uuid

from sqlalchemy.ext.asyncio import AsyncSession

TOOL_SCHEMAS = [
    {
        "name": "flag_lease_clauses",
        "description": "Analyze and flag key clauses in a lease document. Use when reviewing lease text. Stores findings to the database.",
        "input_schema": {
            "type": "object",
            "properties": {
                "lease_id": {
                    "type": "string",
                    "description": "UUID of the lease record in the database",
                },
                "clauses": {
                    "type": "array",
                    "description": "List of clauses found in the lease",
                    "items": {
                        "type": "object",
                        "properties": {
                            "clause_type": {
                                "type": "string",
                                "description": "Type of clause (e.g. renewal, termination, liability, rent_escalation, personal_guarantee)",
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["standard", "flag", "urgent"],
                                "description": "How important this clause is for Cordia to know about",
                            },
                            "content": {
                                "type": "string",
                                "description": "Exact or paraphrased text of the clause",
                            },
                            "note": {
                                "type": "string",
                                "description": "Plain-English explanation of what this means and why it matters",
                            },
                        },
                        "required": ["clause_type", "severity", "content", "note"],
                    },
                },
                "summary": {
                    "type": "string",
                    "description": "1-2 sentence executive summary of the lease for Cordia",
                },
            },
            "required": ["lease_id", "clauses", "summary"],
        },
    },
]


async def flag_clauses_handler(db: AsyncSession, lease_id: str, clauses: list[dict], summary: str, **kwargs) -> dict:
    from app.models.real_estate import Lease, LeaseClause
    from sqlalchemy import select

    result = await db.execute(select(Lease).where(Lease.id == uuid.UUID(lease_id)))
    lease = result.scalar_one_or_none()
    if not lease:
        return {"success": False, "message": f"Lease {lease_id} not found"}

    lease.summary = summary
    stored_clauses = []
    for c in clauses:
        clause = LeaseClause(
            lease_id=lease.id,
            clause_type=c["clause_type"],
            severity=c["severity"],
            content=c["content"],
            claude_note=c["note"],
        )
        db.add(clause)
        stored_clauses.append({"type": c["clause_type"], "severity": c["severity"]})

    await db.commit()
    urgent = [c for c in clauses if c["severity"] == "urgent"]
    flagged = [c for c in clauses if c["severity"] == "flag"]
    return {
        "success": True,
        "lease_id": lease_id,
        "clauses_stored": len(clauses),
        "urgent_count": len(urgent),
        "flagged_count": len(flagged),
        "summary": summary,
    }
