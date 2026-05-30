"""
Joiner service — Handles automated group joining across accounts.
"""

from __future__ import annotations

import asyncio
import random
import re
from typing import Dict, List, Optional

from telethon import TelegramClient, functions, types
from telethon.errors import (
    FloodWaitError, 
    InviteRequestSentError, 
    UserAlreadyParticipantError,
    ChatWriteForbiddenError
)

from core.logging import get_logger
from repositories import accounts_repo, account_groups_repo
from telegram.client_pool import client_pool

log = get_logger("joiner_service")

# Global state to track if joiner is running per user
_active_joiners: Dict[int, asyncio.Task] = {}
_joiner_locks: Dict[int, bool] = {}

def is_joiner_running(user_id: int) -> bool:
    """Check if an auto-join task is running for a user."""
    task = _active_joiners.get(user_id)
    return task is not None and not task.done()

async def cancel_joiner(user_id: int) -> bool:
    """Cancel a running joiner task."""
    task = _active_joiners.get(user_id)
    if task and not task.done():
        task.cancel()
        _joiner_locks.pop(user_id, None)
        return True
    return False

async def start_auto_join(user_id: int, links: List[str], update_callback) -> None:
    """Start the auto-join background task."""
    if is_joiner_running(user_id):
        return

    task = asyncio.create_task(_run_joiner_task(user_id, links, update_callback))
    _active_joiners[user_id] = task
    _joiner_locks[user_id] = True

async def _run_joiner_task(user_id: int, links: List[str], update_callback) -> None:
    """The background task that executes the joining logic."""
    try:
        accounts = await accounts_repo.list_by_owner(user_id)
        if not accounts:
            await update_callback(0, 0, len(links), "❌ No accounts found.")
            return

        total_links = len(links)
        joined = 0
        failed = 0
        
        # 50 joins per hour limit logic
        # Roughly 1 join every 72 seconds per account
        # We'll use a more dynamic approach
        
        for i, link in enumerate(links):
            # Pick a random account for this link to distribute load
            account = random.choice(accounts)
            account_id = str(account.id)
            
            clean_link = _sanitize_link(link)
            if not clean_link:
                failed += 1
                continue

            try:
                async with client_pool.acquire(account_id) as client:
                    # 1. Check if it's a group or channel
                    entity = await client.get_entity(clean_link)
                    
                    is_group = False
                    if isinstance(entity, (types.Chat, types.ChatForbidden)):
                        is_group = True
                    elif isinstance(entity, types.Channel):
                        if not entity.broadcast: # broadcast = channel, not broadcast = supergroup
                            is_group = True
                    
                    if not is_group:
                        await log.ainfo("joiner.skipping_channel", link=link)
                        failed += 1
                        continue

                    # 2. Join
                    await client(functions.channels.JoinChannelRequest(channel=entity))
                    
                    # 3. Refresh group list for this account in background
                    # (Simplified: we'll just wait for the next full refresh or do it now)
                    dialogs = await client.get_dialogs()
                    new_groups = [
                        {"id": d.id, "title": d.title, "is_selected": False}
                        for d in dialogs if d.is_group or d.is_channel
                    ]
                    await account_groups_repo.save_groups(account_id, new_groups)
                    
                    joined += 1

            except UserAlreadyParticipantError:
                joined += 1 # Count as success since we are in
            except (ChatWriteForbiddenError, InviteRequestSentError):
                joined += 1 # Count as success if request sent or we can't write (still joined)
            except FloodWaitError as e:
                await log.awarning("joiner.flood_wait", seconds=e.seconds)
                # If we hit flood, we might want to skip or wait
                # For now, mark as failed and continue
                failed += 1
                await asyncio.sleep(min(e.seconds, 10)) # Don't wait too long in task
            except Exception as e:
                await log.aerror("joiner.error", error=str(e), link=link)
                failed += 1

            # Update progress
            await update_callback(joined, failed, total_links)

            # Delay logic (Random 30-60s to stay under 50/hour safely per account)
            if i < total_links - 1:
                await asyncio.sleep(random.uniform(15, 30))

        await update_callback(joined, failed, total_links, "✅ Process Complete")

    except asyncio.CancelledError:
        await log.ainfo("joiner.cancelled", user_id=user_id)
        await update_callback(joined, failed, total_links, "🛑 Process Cancelled")
    except Exception as e:
        await log.aerror("joiner.fatal_error", error=str(e))
        await update_callback(joined, failed, total_links, f"❌ Error: {str(e)[:20]}")
    finally:
        _joiner_locks.pop(user_id, None)
        _active_joiners.pop(user_id, None)

def _sanitize_link(link: str) -> Optional[str]:
    """Extract username or hash from various link formats."""
    link = link.strip()
    if not link: return None
    
    # https://t.me/username
    # @username
    # t.me/joinchat/HASH
    
    if link.startswith("@"):
        return link[1:]
    
    match = re.search(r"t\.me/(?:joinchat/|addlist/|(?:\+))?([\w\d\-_]+)", link)
    if match:
        return match.group(1)
        
    return link
