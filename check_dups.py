import asyncio
from database.mongo import init_mongo, get_db
from core.config import get_settings

async def main():
    settings = get_settings()
    await init_mongo(settings.mongo_uri, settings.mongo_db)
    db = get_db()
    accs = await db["accounts"].find().to_list(length=None)
    phones = [a.get("phone") for a in accs]
    import collections
    print(f"Total accounts: {len(phones)}")
    print(f"Unique phones: {len(set(phones))}")
    print("Most common:", collections.Counter(phones).most_common(5))

asyncio.run(main())
