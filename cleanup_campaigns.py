import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    client = AsyncIOMotorClient("mongodb://localhost:27017")
    db = client["wpay_ads"]
    
    # Get all valid accounts
    valid_accounts = set()
    async for acc in db.accounts.find({}, {"_id": 1}):
        valid_accounts.add(str(acc["_id"]))
        
    # Get all valid groups
    valid_groups = set()
    async for grp in db.account_groups.find({}, {"_id": 1}):
        valid_groups.add(str(grp["_id"]))
        
    # Clean campaigns
    cursor = db.campaigns.find({})
    async for camp in cursor:
        old_accs = camp.get("account_ids", [])
        old_grps = camp.get("group_ids", [])
        
        new_accs = [aid for aid in old_accs if aid in valid_accounts]
        new_grps = [gid for gid in old_grps if gid in valid_groups]
        
        if len(old_accs) != len(new_accs) or len(old_grps) != len(new_grps):
            print(f"Cleaning campaign {camp['_id']}: {len(old_accs)}->{len(new_accs)} accs, {len(old_grps)}->{len(new_grps)} grps")
            await db.campaigns.update_one(
                {"_id": camp["_id"]},
                {"$set": {"account_ids": new_accs, "group_ids": new_grps}}
            )

    print("WPAY done.")
    
    db_az = client["az_ads"]
    # Get all valid accounts
    valid_accounts = set()
    async for acc in db_az.accounts.find({}, {"_id": 1}):
        valid_accounts.add(str(acc["_id"]))
        
    # Get all valid groups
    valid_groups = set()
    async for grp in db_az.account_groups.find({}, {"_id": 1}):
        valid_groups.add(str(grp["_id"]))
        
    # Clean campaigns
    cursor = db_az.campaigns.find({})
    async for camp in cursor:
        old_accs = camp.get("account_ids", [])
        old_grps = camp.get("group_ids", [])
        
        new_accs = [aid for aid in old_accs if aid in valid_accounts]
        new_grps = [gid for gid in old_grps if gid in valid_groups]
        
        if len(old_accs) != len(new_accs) or len(old_grps) != len(new_grps):
            print(f"Cleaning AZ campaign {camp['_id']}: {len(old_accs)}->{len(new_accs)} accs, {len(old_grps)}->{len(new_grps)} grps")
            await db_az.campaigns.update_one(
                {"_id": camp["_id"]},
                {"$set": {"account_ids": new_accs, "group_ids": new_grps}}
            )
            
    print("AZ done.")
    
    # Invalidate Redis caches
    import redis.asyncio as redis
    r = redis.from_url("redis://localhost:6379/0")
    await r.flushdb()
    
    r_az = redis.from_url("redis://localhost:6379/1")
    await r_az.flushdb()
    print("Redis flushed.")

if __name__ == "__main__":
    asyncio.run(main())
