"""
AI Tools Registry.

Defines the JSON schemas for the LLM and the Python wrapper functions that securely execute them.
Crucially, the `user_id` is always injected by the wrapper, never by the AI, ensuring data isolation.
"""

from typing import Dict, Any, Callable, Coroutine
from pydantic import BaseModel
import json

from repositories import accounts_repo
from repositories import campaigns_repo

# ── 1. Tool Schemas ──────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_dashboard_stats",
            "description": "Get the current dashboard statistics including total accounts, active campaigns, success rates, and overall health.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_campaigns_summary",
            "description": "Get a list of all campaigns for the user, including their status, success counts, and settings.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_account",
            "description": "DANGEROUS: Propose to delete an account from the database. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "The phone number of the account to delete (e.g. '8383388338')"
                    }
                },
                "required": ["phone"]
            }
        }
    }
]

# ── 2. Tool Wrappers (READ) ──────────────────────────────────
# These execute instantly and return data directly to the AI.

async def execute_get_dashboard_stats(user_id: int, kwargs: dict) -> str:
    """Fetch dashboard stats for the specific user."""
    # We can use the accounts repo to quickly get basic counts
    accounts = await accounts_repo.list_by_owner(user_id)
    total_accs = len(accounts)
    active_accs = sum(1 for a in accounts if a.status == "ACTIVE")
    banned_accs = sum(1 for a in accounts if a.status == "BANNED")
    
    campaigns = await campaigns_repo.list_by_owner(user_id)
    active_camps = sum(1 for c in campaigns if c.status == "ACTIVE")
    
    stats = {
        "total_accounts": total_accs,
        "active_accounts": active_accs,
        "banned_accounts": banned_accs,
        "active_campaigns": active_camps,
        "overall_health_score_average": sum(a.health_score for a in accounts) / total_accs if total_accs > 0 else 0
    }
    return json.dumps(stats)


async def execute_get_campaigns_summary(user_id: int, kwargs: dict) -> str:
    """Fetch campaign details for the specific user."""
    campaigns = await campaigns_repo.list_by_owner(user_id)
    summary = []
    for c in campaigns:
        summary.append({
            "name": c.name,
            "status": c.status,
            "target_groups": len(c.group_ids),
            "accounts_used": len(c.account_ids),
            "total_sent": c.stats.total_sent if hasattr(c, 'stats') else 0,
            "total_success": c.stats.total_success if hasattr(c, 'stats') else 0,
            "interval_seconds": c.group_delay_seconds
        })
    return json.dumps(summary)


# ── 3. Tool Wrappers (DANGEROUS/WRITE) ────────────────────────
# These do NOT execute instantly. They return a special trigger object
# that the AI service will catch and pass to the Action Queue.

async def propose_delete_account(user_id: int, kwargs: dict) -> str:
    """Propose an account deletion. Returns an action_request to the system."""
    phone = kwargs.get("phone")
    if not phone:
        return json.dumps({"error": "Phone number is required."})
        
    accounts = await accounts_repo.list_by_owner(user_id)
    target = next((a for a in accounts if a.phone == phone), None)
    
    if not target:
        return json.dumps({"error": f"You do not own an account with phone number {phone}."})
        
    # Return a specific structure that the ai_service will recognize as a QUEUE request
    return json.dumps({
        "_action_request": True,
        "action_type": "delete_account",
        "description": f"Delete account {phone}",
        "payload": {"account_id": target.id}
    })


# ── Registry ────────────────────────────────────────────────

TOOL_REGISTRY: Dict[str, Callable[[int, dict], Coroutine[Any, Any, str]]] = {
    "get_dashboard_stats": execute_get_dashboard_stats,
    "get_campaigns_summary": execute_get_campaigns_summary,
    "delete_account": propose_delete_account,
}
