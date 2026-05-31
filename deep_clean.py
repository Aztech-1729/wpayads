import asyncio
from database.mongo import get_db, init_mongo
from repositories import accounts_repo, account_groups_repo, campaigns_repo

async def main():
    print("Starting Deep Cleanup for WPAY ADS MAX...")
    from core.config import get_settings
    settings = get_settings()
    await init_mongo(settings.mongo_uri, settings.mongo_db)
    
    # 1. Get all valid accounts
    accounts = await accounts_repo._coll().find({}, {"_id": 1}).to_list(None)
    valid_accounts = set([str(a["_id"]) for a in accounts])
    
    # 2. Delete ALL orphaned groups (groups whose account_id is not in valid_accounts)
    all_groups = await account_groups_repo._coll().find({}, {"_id": 1, "account_id": 1}).to_list(None)
    orphaned_group_ids = []
    valid_groups = set()
    for g in all_groups:
        if g.get("account_id") not in valid_accounts:
            orphaned_group_ids.append(g["_id"])
        else:
            valid_groups.add(str(g["_id"]))
            
    if orphaned_group_ids:
        print(f"Deleting {len(orphaned_group_ids)} orphaned groups...")
        await account_groups_repo._coll().delete_many({"_id": {"$in": orphaned_group_ids}})
    
    # 3. Clean campaigns
    campaigns = await campaigns_repo._coll().find({}).to_list(None)
    for camp in campaigns:
        old_accs = camp.get("account_ids", [])
        old_grps = camp.get("group_ids", [])
        
        new_accs = [aid for aid in old_accs if aid in valid_accounts]
        new_grps = [gid for gid in old_grps if gid in valid_groups]
        
        if len(old_accs) != len(new_accs) or len(old_grps) != len(new_grps):
            print(f"Cleaning campaign {camp['_id']}: {len(old_accs)}->{len(new_accs)} accs, {len(old_grps)}->{len(new_grps)} grps")
            await campaigns_repo.update_fields(str(camp["_id"]), {
                "account_ids": new_accs,
                "group_ids": new_grps
            })

    from cache import campaign_cache, dashboard_cache
    import redis.asyncio as redis
    
    try:
        r = redis.from_url("redis://localhost:6379/0")
        await r.flushdb()
        print("Redis caches flushed.")
    except Exception as e:
        print("Failed to flush redis:", e)
        
    print("Cleanup complete.")

if __name__ == "__main__":
    asyncio.run(main())
