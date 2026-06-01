import asyncio
from database.mongo import init_mongo, get_db
from core.config import get_settings

async def main():
    settings = get_settings()
    await init_mongo(settings.mongo_uri, settings.mongo_db)
    db = get_db()
    c = await db["campaigns"].find_one({"name": "wpay"})
    if c:
        print("Stats in DB:")
        print(c.get("stats"))
    else:
        print("Campaign not found")

asyncio.run(main())
