"""
Logs Bot — Secondary bot client for campaign logs.
"""

from __future__ import annotations

from typing import Optional

from telethon import TelegramClient, events, Button

from core.config import get_settings
from core.logging import get_logger

log = get_logger("logs_bot")

_logs_bot: Optional[TelegramClient] = None

def get_logs_bot() -> Optional[TelegramClient]:
    """Get the active Logs Bot instance, or None if disabled."""
    return _logs_bot

async def init_logs_bot() -> None:
    """Initialize and start the logs bot if token is configured."""
    global _logs_bot
    settings = get_settings()

    if not settings.logs_bot_token:
        log.info("logs_bot.disabled", reason="No LOGS_BOT_TOKEN provided")
        return

    _logs_bot = TelegramClient(
        "session_logs_bot",
        settings.api_id,
        settings.api_hash,
    )

    # Register handlers
    @_logs_bot.on(events.NewMessage(pattern="/start"))
    async def on_start(event: events.NewMessage.Event) -> None:
        settings = get_settings()
        sender = await event.get_sender()
        username = getattr(sender, "username", None)
        
        # Whitelist Check
        if settings.allowed_usernames and username not in settings.allowed_usernames:
            return

        from repositories import users_repo
        await users_repo.update(event.sender_id, {"has_started_logs_bot": True})
        
        text = (
            "🚀 <b>LOGS BOT ACTIVATED</b>\n"
            "<b>Real-time notifications are now enabled.</b>"
        )
        await event.respond(text, parse_mode="html")

    await _logs_bot.start(bot_token=settings.logs_bot_token)
    me = await _logs_bot.get_me()
    log.info("logs_bot.started", bot_username=me.username)

async def send_campaign_start_log(owner_id: int, campaign: Any) -> None:
    """Send a notification that a campaign started with details."""
    if not _logs_bot:
        return
        
    ad_type = getattr(campaign, "ad_type", "custom")
    if ad_type == "custom":
        msg = campaign.message or "None"
        msg_disp = f"{msg[:30]}..." if len(msg) > 30 else msg
        ad_disp = f"📝 Custom ({msg_disp})"
    else:
        ad_disp = f"🔗 Forward ({campaign.forward_link})"

    text = (
        f"🚀 <b>Campaign Started</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Campaign:</b> {campaign.name}\n"
        f"<b>Ad Mode:</b> {ad_disp}\n"
        f"<b>Intervals:</b> {campaign.group_delay_seconds}s / {campaign.round_delay_seconds}s\n"
        f"<b>Rounds:</b> {'♾️ Infinite' if campaign.max_rounds == 0 else campaign.max_rounds}\n"
        f"<b>Accounts:</b> {len(campaign.account_ids)}\n"
        f"<b>Groups:</b> {len(campaign.group_ids)}"
    )
    try:
        await _logs_bot.send_message(owner_id, text, parse_mode="html")
    except Exception as e:
        log.error("logs_bot.send_error", owner_id=owner_id, error=str(e))

async def send_campaign_pause_log(owner_id: int, campaign_name: str) -> None:
    """Send a notification that a campaign was paused."""
    if not _logs_bot:
        return
        
    text = (
        f"⏸ <b>Campaign Paused</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"<b>Campaign:</b> {campaign_name}\n\n"
        f"<i>The campaign has been stopped immediately.</i>"
    )
    try:
        await _logs_bot.send_message(owner_id, text, parse_mode="html")
    except Exception as e:
        log.error("logs_bot.send_error", owner_id=owner_id, error=str(e))

async def send_ad_success_log(owner_id: int, campaign_name: str, account_phone: str, group_id: int, message_link: str) -> None:
    """Send a success log with view message button."""
    if not _logs_bot:
        return
        
    text = (
        f"✅ <b>Ad Sent Successfully</b>\n\n"
        f"<b>Campaign:</b> {campaign_name}\n"
        f"<b>Account:</b> +{account_phone.lstrip('+')}\n"
        f"<b>Group:</b> {group_id}"
    )
    
    buttons = []
    if message_link:
        buttons.append([Button.url("View Message", message_link)])
        
    try:
        await _logs_bot.send_message(owner_id, text, buttons=buttons, parse_mode="html")
    except Exception as e:
        log.error("logs_bot.send_error", owner_id=owner_id, error=str(e))
