import asyncio
from core.config import get_settings
import redis.asyncio as redis

async def main():
    settings = get_settings()
    try:
        r = redis.from_url(settings.redis_uri)
        await r.flushdb()
        print(f"Redis cache at {settings.redis_uri} flushed successfully.")
    except Exception as e:
        print(f"Failed to flush redis at {settings.redis_uri}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
