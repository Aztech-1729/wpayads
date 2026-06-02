import asyncio
import os
import sys

from app.startup import startup
from app.shutdown import shutdown
from repositories import accounts_repo, account_groups_repo
from telegram.client_pool import client_pool

async def refresh_all():
    print("Starting system...")
    await startup()
    
    # Get all active accounts
    accounts = await accounts_repo.get_all_active()
    print(f"\nFound {len(accounts)} active accounts to refresh.")
    
    for account in accounts:
        account_id_str = str(account.id)
        print(f"\nRefreshing account {account.phone} (ID: {account_id_str})...")
        try:
            # We don't delete existing groups to preserve `is_selected`
            # save_groups uses upsert, so it will simply update the `access_hash`
            async with client_pool.acquire(account_id_str) as client:
                print("  -> Fetching dialogs from Telegram...")
                dialogs = await client.get_dialogs()
                
                groups = []
                for d in dialogs:
                    if d.is_group or d.is_channel:
                        access_hash = getattr(d.entity, 'access_hash', 0) if d.entity else 0
                        groups.append({
                            "id": d.id,
                            "title": d.title,
                            "access_hash": access_hash
                        })
                
                print(f"  -> Found {len(groups)} groups/channels. Saving to DB...")
                await account_groups_repo.save_groups(account_id_str, groups)
                print(f"  -> SUCCESS! Saved access_hash for {len(groups)} groups.")
        except Exception as e:
            print(f"  -> FAILED to refresh {account.phone}: {e}")
            
    print("\nRefresh complete. Shutting down...")
    await shutdown()

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(refresh_all())
