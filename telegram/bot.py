"""
Bot client initialization and event handler registration.

This is the Telegram bot entry point. It:
- Initializes the bot client
- Registers /start and callback query handlers
- Handles text input for interactive flows (session import, campaign creation)
"""

from __future__ import annotations

from telethon import TelegramClient, events

from core.config import get_settings
from core.logging import get_logger
from repositories import users_repo
from telegram import callbacks, keyboards, menus
from telegram.states import get_context, set_context

log = get_logger("bot")

# Module-level bot client
_bot: TelegramClient | None = None


def get_bot() -> TelegramClient:
    """Return the bot client. Raises if not initialized."""
    if _bot is None:
        raise RuntimeError("Bot not initialized. Call init_bot() first.")
    return _bot


async def init_bot() -> TelegramClient:
    """
    Initialize the Telegram bot client and register all handlers.
    """
    global _bot
    settings = get_settings()

    _bot = TelegramClient(
        "aztech_bot",
        settings.api_id,
        settings.api_hash,
    )

    # Register handlers
    _register_handlers(_bot)

    # Connect
    await _bot.start(bot_token=settings.bot_token)  # type: ignore

    # Set bot reference in services that need it
    from services import notification_service
    notification_service.set_bot(_bot)

    me = await _bot.get_me()
    await log.ainfo("bot.started", username=me.username, id=me.id)

    return _bot


async def stop_bot() -> None:
    """Disconnect the bot client."""
    global _bot
    if _bot is not None:
        await _bot.disconnect()
        await log.ainfo("bot.stopped")
        _bot = None


def _register_handlers(bot: TelegramClient) -> None:
    """Register all event handlers on the bot client."""

    @bot.on(events.NewMessage(pattern="/start"))
    async def on_start(event: events.NewMessage.Event) -> None:
        """Handle /start command — entry point for all users."""
        settings = get_settings()
        sender = await event.get_sender()
        username = getattr(sender, "username", None)
        
        # Whitelist Check
        if settings.allowed_usernames and username not in settings.allowed_usernames:
            await log.awarning("bot.unauthorized_access", user_id=event.sender_id, username=username)
            return

        user_id = event.sender_id

        # Get or create user
        await users_repo.get_or_create(
            user_id=user_id,
            username=username,
            first_name=getattr(sender, "first_name", None),
        )

        # Show Dashboard directly instead of welcome message
        from cache import dashboard_cache
        from services import dashboard_service
        
        # Try to get from cache, if not found, build it
        data = await dashboard_cache.get(user_id)
        if not data:
            data = await dashboard_service.build_dashboard(user_id)
            
        text = menus.render_dashboard(data)

        if settings.bot_image_url:
            await event.respond(
                file=settings.bot_image_url,
                message=text,
                buttons=keyboards.main_menu_keyboard(),
                parse_mode="html",
            )
        else:
            await event.respond(
                text,
                buttons=keyboards.main_menu_keyboard(),
                parse_mode="html",
            )

    @bot.on(events.CallbackQuery)
    async def on_callback(event: events.CallbackQuery.Event) -> None:
        """Handle all inline button presses."""
        settings = get_settings()
        sender = await event.get_sender()
        username = getattr(sender, "username", None)
        
        if settings.allowed_usernames and username not in settings.allowed_usernames:
            return

        try:
            await callbacks.route_callback(event)
        except Exception as exc:
            from telethon.errors import MessageNotModifiedError
            if isinstance(exc, MessageNotModifiedError):
                return
            await log.aerror(
                "callback.error",
                user_id=event.sender_id,
                data=event.data,
                error=str(exc),
            )
            try:
                await event.answer("An error occurred. Please try again.", alert=True)
            except Exception:
                pass

    @bot.on(events.NewMessage(func=lambda e: e.is_private))
    async def on_user_message(event: events.NewMessage.Event) -> None:
        """Central message handler for private chats."""
        settings = get_settings()
        sender = await event.get_sender()
        username = getattr(sender, "username", None)
        
        if settings.allowed_usernames and username not in settings.allowed_usernames:
            return

        if event.text and event.text.startswith("/"):
            # Command handled by other handlers
            return

        user_id = event.sender_id
        awaiting = await get_context(user_id, "awaiting_input")

        # Handle File Upload for Auto Join
        if awaiting == "autojoin_file" and event.document:
            filename = event.document.attributes[0].file_name if event.document.attributes else ""
            if not filename.lower().endswith(".txt"):
                await event.respond("❌ Please send a <b>.txt</b> file containing group links.", parse_mode="html")
                return

            file_bytes = await event.download_media(bytes)
            try:
                content = file_bytes.decode("utf-8")
                links = [line.strip() for line in content.split("\n") if line.strip()]
            except UnicodeDecodeError:
                await event.respond("❌ Invalid file encoding. Please upload a plain text (.txt) file in UTF-8 format.")
                return

            if not links:
                await event.respond("❌ No links found in the file.")
                return

            from telegram import menus, keyboards
            from services.joiner_service import start_auto_join
            from repositories import accounts_repo
            
            account_count = await accounts_repo.count_by_owner(user_id)
            total_joins = account_count * len(links)

            progress_msg = await event.respond(
                menus.render_autojoin_progress(0, 0, total_joins, "Starting...", len(links), account_count),
                buttons=keyboards.autojoin_progress_keyboard(),
                parse_mode="html"
            )

            async def update_callback(joined: int, failed: int, total: int, status: str = "Processing") -> None:
                try:
                    await progress_msg.edit(
                        menus.render_autojoin_progress(joined, failed, total, status, len(links), account_count),
                        buttons=keyboards.autojoin_progress_keyboard() if "Complete" not in status and "Cancel" not in status and "Error" not in status else None,
                        parse_mode="html"
                    )
                except Exception:
                    pass  # Ignore MessageNotModifiedError or similar

            await set_context(user_id, "awaiting_input", None)
            await start_auto_join(user_id, links, update_callback)
            return

        # Handle Session Upload
        if awaiting == "session_upload" and event.document:
            filename = event.document.attributes[0].file_name
            if not (filename.lower().endswith(".session") or filename.lower().endswith(".zip")):
                await event.respond("❌ Please send a <b>.session</b> file or a <b>.zip</b> archive.", parse_mode="html")
                return

            # Download file
            file_bytes = await event.download_media(bytes)
            
            # Progress message
            progress_msg = await event.respond(
                "📂 <b>SESSIONS IMPORT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                "⏳ <b>Status: Importing Sessions</b>\n\n"
                "✅ <b>Added: 0</b>\n"
                "❌ <b>Failed: 0</b>\n"
                "📊 <b>Total Found: 0</b>",
                parse_mode="html"
            )

            # Define update callback
            async def update_progress(joined, failed, total, status="Importing Sessions"):
                try:
                    text = (
                        f"📂 <b>SESSIONS IMPORT</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n\n"
                        f"⏳ <b>Status: {status}</b>\n\n"
                        f"✅ <b>Added: {joined}</b>\n"
                        f"❌ <b>Failed: {failed}</b>\n"
                        f"📊 <b>Total Found: {total}</b>"
                    )
                    await progress_msg.edit(text, parse_mode="html")
                except Exception:
                    pass

            from services.session_importer import import_from_file
            await import_from_file(user_id, file_bytes, filename, update_progress)
            return

        if not awaiting:
            return

        # Interactive Handlers (Phone, OTP, etc.)
        try:
            if awaiting == "auth_phone":
                await _handle_auth_phone_input(event)
            elif awaiting == "auth_otp":
                await _handle_auth_otp_input(event)
            elif awaiting == "auth_password":
                await _handle_auth_password_input(event)
            elif awaiting == "campaign_name":
                await _handle_campaign_name_input(event)
            elif awaiting == "duplicate_campaign":
                await _handle_duplicate_campaign_input(event)
            elif awaiting == "account_notes":
                await _handle_account_notes_input(event)
            elif awaiting == "autoreply_text":
                await _handle_autoreply_text_input(event)
            elif awaiting.startswith("cmp_ad_custom:"):
                await _handle_cmp_ad_custom(event, awaiting.split(":")[1])
            elif awaiting.startswith("cmp_ad_forward:"):
                await _handle_cmp_ad_forward(event, awaiting.split(":")[1])
            elif awaiting.startswith("cmp_int_group:"):
                await _handle_cmp_int_group(event, awaiting.split(":")[1])
            elif awaiting.startswith("cmp_int_round:"):
                await _handle_cmp_int_round(event, awaiting.split(":")[1])
            elif awaiting.startswith("cmp_rounds_max:"):
                await _handle_cmp_rounds_max(event, awaiting.split(":")[1])
            elif awaiting == "bulk_name":
                await _handle_bulk_name(event)
            elif awaiting == "bulk_bio":
                await _handle_bulk_bio(event)
            elif awaiting == "bulk_username":
                await _handle_bulk_username(event)
            elif awaiting == "bulk_photo":
                await _handle_bulk_photo(event)
            elif awaiting == "bulk_2fa_set":
                await _handle_bulk_2fa_set(event)
        except Exception as exc:
            await event.respond(f"❌ Error: {str(exc)}", parse_mode="html")
            await set_context(user_id, "awaiting_input", None)


    @bot.on(events.NewMessage(pattern="/stats"))
    async def on_stats(event: events.NewMessage.Event) -> None:
        """Display internal stats (admin only)."""
        user_id = event.sender_id
        settings = get_settings()

        user = await users_repo.get(user_id)
        if not (user and user.is_admin) and user_id not in settings.admin_user_ids:
            return

        from telegram.client_pool import client_pool
        from utils.metrics import metrics
        m = await metrics.stats()
        pool = await client_pool.stats()

        lines = ["📊 <b>System Stats</b>\n"]
        for k, v in m.items():
            lines.append(f"  {k}: <b>{v}</b>")
        lines.append(f"\n🔌 <b>Client Pool</b>")
        lines.append(f"  Total: <b>{pool['total_clients']}</b>/{pool['max_clients']}")
        lines.append(f"  Active: <b>{pool['active_borrows']}</b>")
        lines.append(f"  Idle: <b>{pool['idle_clients']}</b>")

        await event.respond("\n".join(lines), parse_mode="html")


# ── Input Handlers ──────────────────────────────────────────

async def _handle_auth_phone_input(event: events.NewMessage.Event) -> None:
    """Handle phone number input for auth."""
    phone = event.text.strip()
    if not phone.startswith("+") or len(phone) < 8:
        await event.respond("❌ Invalid phone number. It must start with '+' and include the country code.")
        await set_context(event.sender_id, "awaiting_input", None)
        return
        
    msg = await event.respond("⏳ Requesting OTP from Telegram... Please wait.")
    
    from services import auth_service
    try:
        await auth_service.start_auth(event.sender_id, phone)
        await set_context(event.sender_id, "awaiting_input", "auth_otp")
        await msg.edit(
            f"✅ OTP requested for <b>{phone}</b>.\n\n"
            f"Please send the <b>5-digit code</b> you received in the Telegram app.\n"
            f"<i>You have 5 minutes to enter it.</i>",
            parse_mode="html"
        )
    except Exception as exc:
        await set_context(event.sender_id, "awaiting_input", None)
        await msg.edit(f"❌ Failed to request code: {str(exc)}", parse_mode="html")


async def _handle_auth_otp_input(event: events.NewMessage.Event) -> None:
    """Handle OTP input for auth."""
    otp = event.text.strip()
    # allow pure digits or things like '12345'
    otp = ''.join(c for c in otp if c.isdigit())
    
    msg = await event.respond("⏳ Verifying OTP...")
    from services import auth_service
    
    try:
        status = await auth_service.submit_otp(event.sender_id, otp)
        if status == "needs_password":
            await set_context(event.sender_id, "awaiting_input", "auth_password")
            await msg.edit("🔐 <b>2FA Enabled</b>\n\nPlease send your Two-Step Verification password.", parse_mode="html")
            return
            
        # Success! Finalize
        summary = await auth_service.finalize_auth(event.sender_id)
        from services import account_service
        account = await account_service.add_account(event.sender_id, summary["raw_session"])
        
        from repositories import account_groups_repo
        await account_groups_repo.save_groups(account.id, summary["groups"])
        
        await set_context(event.sender_id, "awaiting_input", None)
        await msg.edit(
            f"✅ <b>Account Added Successfully!</b>\n\n"
            f"👤 Name: <b>{summary['name']}</b>\n"
            f"🔗 Username: <b>{summary['username']}</b>\n"
            f"📝 Bio: <i>{summary['bio']}</i>\n"
            f"👥 Total Groups: <b>{summary['groups_count']}</b>",
            parse_mode="html"
        )
    except Exception as exc:
        await set_context(event.sender_id, "awaiting_input", None)
        await msg.edit(f"❌ OTP verification failed: {str(exc)}", parse_mode="html")
        
    try:
        await event.delete()
    except:
        pass


async def _handle_auth_password_input(event: events.NewMessage.Event) -> None:
    """Handle 2FA password input for auth."""
    password = event.text.strip()
    msg = await event.respond("⏳ Verifying Password...")
    from services import auth_service
    
    try:
        await auth_service.submit_password(event.sender_id, password)
        # Success! Finalize
        summary = await auth_service.finalize_auth(event.sender_id)
        from services import account_service
        account = await account_service.add_account(event.sender_id, summary["raw_session"])
        
        from repositories import account_groups_repo
        await account_groups_repo.save_groups(account.id, summary["groups"])
        
        await set_context(event.sender_id, "awaiting_input", None)
        await msg.edit(
            f"✅ <b>Account Added Successfully!</b>\n\n"
            f"👤 Name: <b>{summary['name']}</b>\n"
            f"🔗 Username: <b>{summary['username']}</b>\n"
            f"📝 Bio: <i>{summary['bio']}</i>\n"
            f"👥 Total Groups: <b>{summary['groups_count']}</b>",
            parse_mode="html"
        )
    except Exception as exc:
        await set_context(event.sender_id, "awaiting_input", None)
        await msg.edit(f"❌ Password verification failed: {str(exc)}", parse_mode="html")
        
    try:
        await event.delete()
    except:
        pass


async def _handle_campaign_name_input(event: events.NewMessage.Event) -> None:
    """Handle campaign name input."""
    name = event.text.strip()
    
    from services import campaign_service
    try:
        # Create draft campaign immediately
        campaign = await campaign_service.create_campaign(
            owner_id=event.sender_id,
            name=name,
            message="",
        )
        await set_context(event.sender_id, "awaiting_input", None)
        
        # Open campaign view directly
        from cache import campaign_cache
        from telegram.menus import render_campaign_detail
        from telegram.keyboards import campaign_detail_keyboard
        
        data = await campaign_cache.get_summary(campaign.id)
        text = render_campaign_detail(data)
        buttons = campaign_detail_keyboard(campaign.id, campaign.status)
        await event.respond(text, buttons=buttons, parse_mode="html")
        
    except Exception as exc:
        await event.respond(f"❌ {str(exc)}", parse_mode="html")


# We don't need _handle_campaign_message_input anymore, but we can leave it or remove it.


async def _handle_duplicate_campaign_input(event: events.NewMessage.Event) -> None:
    """Handle campaign duplication name input."""
    from services import campaign_service

    new_name = event.text.strip()
    source_id = await get_context(event.sender_id, "duplicate_source")

    if not source_id:
        await event.respond("❌ Source campaign not found. Please try again.")
        return

    try:
        new_campaign = await campaign_service.duplicate_campaign(
            campaign_id=source_id,
            owner_id=event.sender_id,
            new_name=new_name,
        )
        await set_context(event.sender_id, "awaiting_input", None)
        await event.respond(
            f"✅ Campaign duplicated as <b>{new_campaign.name}</b>!",
            parse_mode="html",
        )
    except Exception as exc:
        await event.respond(f"❌ Error: {str(exc)}", parse_mode="html")


async def _handle_account_notes_input(event: events.NewMessage.Event) -> None:
    """Handle account notes input."""
    from services import account_service

    notes = event.text.strip()
    account_id = await get_context(event.sender_id, "notes_account_id")

    if not account_id:
        await event.respond("❌ Account not found. Please try again.")
        return

    try:
        await account_service.update_notes(account_id, event.sender_id, notes)
        await set_context(event.sender_id, "awaiting_input", None)
        await event.respond("✅ Notes updated!", parse_mode="html")
    except Exception as exc:
        await event.respond(f"❌ Error: {str(exc)}", parse_mode="html")


async def _handle_autoreply_text_input(event: events.NewMessage.Event) -> None:
    """Handle custom auto-reply message input."""
    text = event.text.strip()
    
    try:
        await users_repo.update(event.sender_id, {"autoreply_text": text})
        await set_context(event.sender_id, "awaiting_input", None)
        
        # Give them a button to go back to auto-reply settings
        from core.constants import CB
        from telegram import keyboards
        
        await event.respond(
            "✅ <b>Custom auto-reply message saved!</b>\n\n"
            f"<code>{text}</code>",
            parse_mode="html",
            buttons=keyboards.back_keyboard(CB.SETTINGS_AUTOREPLY)
        )
    except Exception as exc:
        await event.respond(f"❌ Error saving message: {str(exc)}", parse_mode="html")
async def _handle_cmp_ad_custom(event, campaign_id: str):
    from services import campaign_service
    from telegram.menus import render_campaign_detail
    from telegram.keyboards import campaign_detail_keyboard
    from cache import campaign_cache
    
    await campaign_service.update_campaign(campaign_id, ad_type="custom", message=event.text.strip(), forward_link=None)
    await set_context(event.sender_id, "awaiting_input", None)
    
    await event.respond("✅ <b>Custom message saved!</b>", parse_mode="html")
    
    from workers.cache_worker import warm_user_cache
    await warm_user_cache(event.sender_id)
    data = await campaign_cache.get_summary(campaign_id)
    text = render_campaign_detail(data)
    buttons = campaign_detail_keyboard(campaign_id, data.get("status", "UNKNOWN") if data else "UNKNOWN")
    await event.respond(text, buttons=buttons, parse_mode="html")

async def _handle_cmp_ad_forward(event, campaign_id: str):
    from services import campaign_service
    from telegram.menus import render_campaign_detail
    from telegram.keyboards import campaign_detail_keyboard
    from cache import campaign_cache
    
    await campaign_service.update_campaign(campaign_id, ad_type="forward", forward_link=event.text.strip())
    await set_context(event.sender_id, "awaiting_input", None)
    
    await event.respond("✅ <b>Forward link saved!</b>", parse_mode="html")
    
    from workers.cache_worker import warm_user_cache
    await warm_user_cache(event.sender_id)
    data = await campaign_cache.get_summary(campaign_id)
    text = render_campaign_detail(data)
    buttons = campaign_detail_keyboard(campaign_id, data.get("status", "UNKNOWN") if data else "UNKNOWN")
    await event.respond(text, buttons=buttons, parse_mode="html")

async def _handle_cmp_int_group(event, campaign_id: str):
    from services import campaign_service
    from telegram.menus import render_campaign_detail
    from telegram.keyboards import campaign_detail_keyboard
    from cache import campaign_cache
    try:
        val = int(event.text.strip())
        await campaign_service.update_campaign(campaign_id, group_delay_seconds=val)
        await set_context(event.sender_id, "awaiting_input", None)
        
        await event.respond("✅ <b>Group delay saved!</b>", parse_mode="html")
        
        from workers.cache_worker import warm_user_cache
        await warm_user_cache(event.sender_id)
        data = await campaign_cache.get_summary(campaign_id)
        text = render_campaign_detail(data)
        buttons = campaign_detail_keyboard(campaign_id, data.get("status", "UNKNOWN") if data else "UNKNOWN")
        await event.respond(text, buttons=buttons, parse_mode="html")
    except ValueError:
        await event.respond("❌ Invalid number. Try again.")

async def _handle_cmp_int_round(event, campaign_id: str):
    from services import campaign_service
    from telegram.menus import render_campaign_detail
    from telegram.keyboards import campaign_detail_keyboard
    from cache import campaign_cache
    try:
        val = int(event.text.strip())
        await campaign_service.update_campaign(campaign_id, round_delay_seconds=val)
        await set_context(event.sender_id, "awaiting_input", None)
        
        await event.respond("✅ <b>Round delay saved!</b>", parse_mode="html")
        
        from workers.cache_worker import warm_user_cache
        await warm_user_cache(event.sender_id)
        data = await campaign_cache.get_summary(campaign_id)
        text = render_campaign_detail(data)
        buttons = campaign_detail_keyboard(campaign_id, data.get("status", "UNKNOWN") if data else "UNKNOWN")
        await event.respond(text, buttons=buttons, parse_mode="html")
    except ValueError:
        await event.respond("❌ Invalid number. Try again.")

async def _handle_cmp_rounds_max(event, campaign_id: str):
    from services import campaign_service
    from telegram.menus import render_campaign_detail
    from telegram.keyboards import campaign_detail_keyboard
    from cache import campaign_cache
    try:
        val = int(event.text.strip())
        await campaign_service.update_campaign(campaign_id, max_rounds=val)
        await set_context(event.sender_id, "awaiting_input", None)
        
        await event.respond("✅ <b>Max rounds saved!</b>", parse_mode="html")
        
        from workers.cache_worker import warm_user_cache
        await warm_user_cache(event.sender_id)
        data = await campaign_cache.get_summary(campaign_id)
        text = render_campaign_detail(data)
        buttons = campaign_detail_keyboard(campaign_id, data.get("status", "UNKNOWN") if data else "UNKNOWN")
        await event.respond(text, buttons=buttons, parse_mode="html")
    except ValueError:
        await event.respond("❌ Invalid number. Try again.")


async def _handle_bulk_name(event: events.NewMessage.Event) -> None:
    text = event.text.strip()
    from telegram.menus import render_bulk_progress
    from telegram.keyboards import bulk_progress_keyboard, bulk_manager_keyboard
    msg = await event.respond(render_bulk_progress("Change Name", 0, 0, 0), buttons=bulk_progress_keyboard(), parse_mode="html")
    from services import bulk_service
    
    if "{rand}" in text:
        import random, string
        text = text.replace("{rand}", "".join(random.choices(string.digits, k=4)))
        
    async def run_task():
        async def update_progress(success, failed, total):
            try:
                await msg.edit(render_bulk_progress("Change Name", success, failed, total), buttons=bulk_progress_keyboard(), parse_mode="html")
            except Exception: pass
        success, failed = await bulk_service.bulk_update_profile(event.sender_id, first_name=text, progress_callback=update_progress)
        try:
            await msg.edit(render_bulk_progress("Change Name", success, failed, success+failed, "✅ Completed!"), buttons=bulk_manager_keyboard(), parse_mode="html")
        except Exception: pass

    import asyncio
    asyncio.create_task(run_task())
    await set_context(event.sender_id, "awaiting_input", None)

async def _handle_bulk_bio(event: events.NewMessage.Event) -> None:
    text = event.text.strip()
    from telegram.menus import render_bulk_progress
    from telegram.keyboards import bulk_progress_keyboard, bulk_manager_keyboard
    msg = await event.respond(render_bulk_progress("Change Bio", 0, 0, 0), buttons=bulk_progress_keyboard(), parse_mode="html")
    from services import bulk_service
    
    async def run_task():
        async def update_progress(success, failed, total):
            try:
                await msg.edit(render_bulk_progress("Change Bio", success, failed, total), buttons=bulk_progress_keyboard(), parse_mode="html")
            except Exception: pass
        success, failed = await bulk_service.bulk_update_profile(event.sender_id, about=text, progress_callback=update_progress)
        try:
            await msg.edit(render_bulk_progress("Change Bio", success, failed, success+failed, "✅ Completed!"), buttons=bulk_manager_keyboard(), parse_mode="html")
        except Exception: pass

    import asyncio
    asyncio.create_task(run_task())
    await set_context(event.sender_id, "awaiting_input", None)

async def _handle_bulk_username(event: events.NewMessage.Event) -> None:
    text = event.text.strip()
    from telegram.menus import render_bulk_progress
    from telegram.keyboards import bulk_progress_keyboard, bulk_manager_keyboard
    msg = await event.respond(render_bulk_progress("Change Username", 0, 0, 0), buttons=bulk_progress_keyboard(), parse_mode="html")
    from services import bulk_service
    
    async def run_task():
        async def update_progress(success, failed, total):
            try:
                await msg.edit(render_bulk_progress("Change Username", success, failed, total), buttons=bulk_progress_keyboard(), parse_mode="html")
            except Exception: pass
        success, failed = await bulk_service.bulk_update_username(event.sender_id, text, progress_callback=update_progress)
        try:
            await msg.edit(render_bulk_progress("Change Username", success, failed, success+failed, "✅ Completed!"), buttons=bulk_manager_keyboard(), parse_mode="html")
        except Exception: pass

    import asyncio
    asyncio.create_task(run_task())
    await set_context(event.sender_id, "awaiting_input", None)

async def _handle_bulk_photo(event: events.NewMessage.Event) -> None:
    if not event.photo:
        await event.respond("❌ Please send a photo.")
        return
        
    msg = await event.respond("Downloading photo... ⏳")
    import os, uuid
    filename = f"temp_photo_{uuid.uuid4().hex}.jpg"
    await event.download_media(file=filename)
    
    from telegram.menus import render_bulk_progress
    from telegram.keyboards import bulk_progress_keyboard, bulk_manager_keyboard
    await msg.edit(render_bulk_progress("Change Photo", 0, 0, 0), buttons=bulk_progress_keyboard(), parse_mode="html")
    from services import bulk_service
    
    async def run_task():
        async def update_progress(success, failed, total):
            try:
                await msg.edit(render_bulk_progress("Change Photo", success, failed, total), buttons=bulk_progress_keyboard(), parse_mode="html")
            except Exception: pass
        success, failed = await bulk_service.bulk_upload_profile_photo(event.sender_id, filename, progress_callback=update_progress)
        if os.path.exists(filename):
            os.remove(filename)
        try:
            await msg.edit(render_bulk_progress("Change Photo", success, failed, success+failed, "✅ Completed!"), buttons=bulk_manager_keyboard(), parse_mode="html")
        except Exception: pass

    import asyncio
    asyncio.create_task(run_task())
    await set_context(event.sender_id, "awaiting_input", None)

async def _handle_bulk_2fa_set(event: events.NewMessage.Event) -> None:
    text = event.text.strip()
    from telegram.menus import render_bulk_progress
    from telegram.keyboards import bulk_progress_keyboard, bulk_manager_keyboard
    msg = await event.respond(render_bulk_progress("Set 2FA", 0, 0, 0), buttons=bulk_progress_keyboard(), parse_mode="html")
    from services import bulk_service
    
    async def run_task():
        async def update_progress(success, failed, total):
            try:
                await msg.edit(render_bulk_progress("Set 2FA", success, failed, total), buttons=bulk_progress_keyboard(), parse_mode="html")
            except Exception: pass
        success, failed = await bulk_service.bulk_manage_2fa(event.sender_id, text, progress_callback=update_progress)
        try:
            await msg.edit(render_bulk_progress("Set 2FA", success, failed, success+failed, "✅ Completed!"), buttons=bulk_manager_keyboard(), parse_mode="html")
        except Exception: pass

    import asyncio
    asyncio.create_task(run_task())
    await set_context(event.sender_id, "awaiting_input", None)
