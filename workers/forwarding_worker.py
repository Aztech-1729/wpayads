"""
Forwarding worker — Executes message forwarding tasks.

Picks up active campaigns, selects accounts via rotation weights,
and forwards messages to target groups.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from core.config import get_settings
from core.logging import get_logger
from repositories import campaigns_repo, account_groups_repo
from services import forwarding_service, rotation_service
from telegram.client_pool import client_pool
from utils.metrics import WORKER_RUNS, metrics

log = get_logger("forwarding_worker")


async def run_forwarding_cycle() -> None:
    """
    Run a single forwarding cycle.

    Processes all active campaigns, selecting accounts and forwarding
    messages to assigned groups.
    """
    campaigns = await campaigns_repo.get_active()

    if not campaigns:
        return

    await log.ainfo("forwarding_worker.cycle_start", campaigns=len(campaigns))

    for campaign in campaigns:
        try:
            await process_campaign(campaign)
        except Exception as exc:
            await log.awarning(
                "forwarding_worker.campaign_error",
                campaign_id=campaign.id,
                error=str(exc),
            )

    await metrics.increment(WORKER_RUNS)
    await log.ainfo("forwarding_worker.cycle_complete")


async def process_campaign(campaign) -> None:
    """
    Process a single campaign — select accounts, forward to groups.
    """
    if not campaign.account_ids or not campaign.group_ids:
        return

    # Check schedule (basic interval check)
    # TODO: Full cron support via scheduler_worker
    if campaign.stats.last_run_at:
        delay = campaign.round_delay_seconds or get_settings().default_forward_delay_seconds
        # No more plan limits

    # Select accounts using rotation weights
    selected = await rotation_service.select_accounts(
        campaign.account_ids,
        count=len(campaign.account_ids),
    )

    if not selected:
        await log.awarning(
            "forwarding_worker.no_eligible_accounts",
            campaign_id=campaign.id,
        )
        return

    # Load target groups from account_groups
    groups = await account_groups_repo.list_by_ids(campaign.group_ids)
    if not groups:
        return

    # Distribute groups by account_id
    account_groups_map = {}
    for g in groups:
        acc_id = g.get("account_id")
        if not acc_id:
            continue
        if acc_id not in account_groups_map:
            account_groups_map[acc_id] = []
        account_groups_map[acc_id].append({
            "_id": str(g.get("_id")),
            "group_id": g.get("group_id"),
            "topic_id": g.get("topic_id"),
        })

    tasks = []
    task_acc_ids = []
    for i, account_id in enumerate(selected):
        acc_groups = account_groups_map.get(account_id, [])
        if not acc_groups:
            continue
            
        # Stagger task creation/start for safety (0.2s per account)
        async def staggered_forward(acc_id, camp, groups, delay, offset):
            if offset > 0:
                await asyncio.sleep(offset)
            return await forward_for_account(acc_id, camp, groups, delay)

        tasks.append(
            staggered_forward(
                acc_id=account_id,
                camp=campaign,
                groups=acc_groups,
                delay=campaign.group_delay_seconds,
                offset=i * 0.2
            )
        )
        task_acc_ids.append(account_id)

    from telegram.logs_bot import send_campaign_start_log
    await send_campaign_start_log(campaign.owner_id, campaign)

    await log.ainfo("forwarding_worker.starting_parallel_tasks", count=len(tasks), account_ids=task_acc_ids)
    
    # Execute concurrently and track for cancellation
    from services.task_manager import register_task
    task_bundle = asyncio.gather(*tasks, return_exceptions=True)
    register_task(campaign.id, task_bundle)
    
    try:
        results = await task_bundle
    except asyncio.CancelledError:
        await log.ainfo("forwarding_worker.task_cancelled", campaign_id=campaign.id)
        return

    # Check if campaign was paused while tasks were running
    from repositories import campaigns_repo as c_repo
    current_camp = await c_repo.get(campaign.id)
    from core.constants import CampaignStatus
    if current_camp and current_camp.status != CampaignStatus.ACTIVE:
        await log.ainfo("forwarding_worker.campaign_no_longer_active", campaign_id=campaign.id)
        return

    # Update campaign stats
    total_success = 0
    total_failed = 0
    for result in results:
        if isinstance(result, dict):
            total_success += result.get("success", 0)
            total_failed += result.get("failed", 0)

    await campaigns_repo.update_stats(campaign.id, {
        "total_sent": campaign.stats.total_sent + total_success + total_failed,
        "total_success": campaign.stats.total_success + total_success,
        "total_failed": campaign.stats.total_failed + total_failed,
        "last_run_at": datetime.utcnow(),
    })


async def forward_for_account(
    account_id: str,
    campaign,
    groups: list[dict],
    delay: float,
) -> dict:
    """Forward message from a specific account to a set of groups."""
    try:
        from repositories import accounts_repo as acc_repo
        account = await acc_repo.get(account_id)
        health_score = account.health_score if account else 100

        async with client_pool.acquire(account_id) as client:
            return await forwarding_service.forward_to_groups(
                client=client,
                account_id=account_id,
                campaign=campaign,
                groups=groups,
                delay=delay,
                health_score=health_score,
            )
    except Exception as exc:
        await log.awarning(
            "forwarding_worker.account_error",
            account_id=account_id,
            error=str(exc),
        )
        return {"success": 0, "failed": len(groups), "total": len(groups)}


async def run(stop_event: asyncio.Event | None = None) -> None:
    """
    Main worker loop. Runs forwarding on schedule.
    """
    settings = get_settings()
    interval = settings.default_forward_delay_seconds * 10  # Run cycles less frequently

    await log.ainfo("forwarding_worker.started")

    while True:
        if stop_event and stop_event.is_set():
            break

        try:
            await run_forwarding_cycle()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            await log.aerror("forwarding_worker.error", error=str(exc))

        # Wait for either the polling interval OR an instant trigger
        from services.forwarding_trigger import wait_for_trigger
        await wait_for_trigger(timeout=max(interval, 10))
