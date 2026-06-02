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
    },
    {
        "type": "function",
        "function": {
            "name": "create_campaign",
            "description": "WRITE: Propose creating a new campaign. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the campaign"},
                    "ad_type": {"type": "string", "enum": ["custom", "forward"], "description": "Type of campaign"},
                    "message": {"type": "string", "description": "The custom message to send (if ad_type is custom)"},
                    "forward_link": {"type": "string", "description": "The link to the post to forward (if ad_type is forward)"},
                    "group_delay_seconds": {"type": "integer", "description": "Delay in seconds between sending to groups (default 15)"}
                },
                "required": ["name", "ad_type"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_campaign_status",
            "description": "WRITE: Propose starting (ACTIVE) or pausing (PAUSED) a campaign. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_name": {"type": "string", "description": "The exact name of the campaign"},
                    "status": {"type": "string", "enum": ["ACTIVE", "PAUSED"], "description": "The new status to apply"}
                },
                "required": ["campaign_name", "status"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_campaign_interval",
            "description": "WRITE: Propose changing the delay interval for a campaign. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_name": {"type": "string", "description": "The exact name of the campaign"},
                    "group_delay_seconds": {"type": "integer", "description": "The new delay interval in seconds"}
                },
                "required": ["campaign_name", "group_delay_seconds"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_campaign",
            "description": "DANGEROUS: Propose deleting a campaign entirely. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "campaign_name": {"type": "string", "description": "The exact name of the campaign to delete"}
                },
                "required": ["campaign_name"]
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

async def propose_create_campaign(user_id: int, kwargs: dict) -> str:
    name = kwargs.get("name")
    ad_type = kwargs.get("ad_type")
    
    payload = {
        "owner_id": user_id,
        "name": name,
        "ad_type": ad_type,
        "message": kwargs.get("message", ""),
        "forward_link": kwargs.get("forward_link", ""),
        "group_delay_seconds": kwargs.get("group_delay_seconds", 15)
    }
    
    return json.dumps({
        "_action_request": True,
        "action_type": "create_campaign",
        "description": f"Create campaign '{name}'",
        "payload": payload
    })

async def propose_edit_campaign_status(user_id: int, kwargs: dict) -> str:
    name = kwargs.get("campaign_name")
    status = kwargs.get("status")
    
    campaigns = await campaigns_repo.list_by_owner(user_id)
    target = next((c for c in campaigns if c.name.lower() == name.lower()), None)
    
    if not target:
        return json.dumps({"error": f"You do not own a campaign named '{name}'."})
        
    return json.dumps({
        "_action_request": True,
        "action_type": "edit_campaign_status",
        "description": f"Change '{name}' to {status}",
        "payload": {"campaign_id": target.id, "status": status}
    })

async def propose_edit_campaign_interval(user_id: int, kwargs: dict) -> str:
    name = kwargs.get("campaign_name")
    delay = kwargs.get("group_delay_seconds")
    
    campaigns = await campaigns_repo.list_by_owner(user_id)
    target = next((c for c in campaigns if c.name.lower() == name.lower()), None)
    
    if not target:
        return json.dumps({"error": f"You do not own a campaign named '{name}'."})
        
    return json.dumps({
        "_action_request": True,
        "action_type": "edit_campaign_interval",
        "description": f"Change '{name}' delay to {delay}s",
        "payload": {"campaign_id": target.id, "group_delay_seconds": delay}
    })

async def propose_delete_campaign(user_id: int, kwargs: dict) -> str:
    name = kwargs.get("campaign_name")
    
    campaigns = await campaigns_repo.list_by_owner(user_id)
    target = next((c for c in campaigns if c.name.lower() == name.lower()), None)
    
    if not target:
        return json.dumps({"error": f"You do not own a campaign named '{name}'."})
        
    return json.dumps({
        "_action_request": True,
        "action_type": "delete_campaign",
        "description": f"Delete campaign '{name}'",
        "payload": {"campaign_id": target.id}
    })


# ── Registry ────────────────────────────────────────────────

TOOL_REGISTRY: Dict[str, Callable[[int, dict], Coroutine[Any, Any, str]]] = {
    "get_dashboard_stats": execute_get_dashboard_stats,
    "get_campaigns_summary": execute_get_campaigns_summary,
    "delete_account": propose_delete_account,
    "create_campaign": propose_create_campaign,
    "edit_campaign_status": propose_edit_campaign_status,
    "edit_campaign_interval": propose_edit_campaign_interval,
    "delete_campaign": propose_delete_campaign,
}
