import asyncio
from database.mongo import get_db, init_mongo
from repositories import accounts_repo, account_groups_repo, campaigns_repo

async def main():
    print("Starting cleanup for WPAY ADS MAX...")
    from core.config import get_settings
    settings = get_settings()
    await init_mongo(settings.mongo_uri, settings.mongo_db)
    
    accounts = await accounts_repo._coll().find({}, {"_id": 1}).to_list(None)
    valid_accounts = [str(a["_id"]) for a in accounts]
    
    groups = await account_groups_repo._coll().find({}, {"_id": 1, "group_id": 1}).to_list(None)
    valid_groups = [str(g["_id"]) for g in groups]
    
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
    from core.config import get_settings
    settings = get_settings()
    
    try:
        r = redis.from_url(settings.redis_url)
        await r.flushdb()
        print("Redis caches flushed.")
    except Exception as e:
        print("Failed to flush redis:", e)
        
    print("Cleanup complete.")

if __name__ == "__main__":
    asyncio.run(main())
