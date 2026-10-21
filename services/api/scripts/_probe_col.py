import asyncio
import sys

sys.path.insert(0, ".")
from sqlalchemy import text

from app.models.database import async_session_factory


async def main():
    async with async_session_factory() as s:
        r = await s.execute(
            text(
                "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='quiz_items' AND column_name='image'"
            )
        )
        print("quiz_items.image =", r.all())
        r2 = await s.execute(text("SELECT version_num FROM alembic_version"))
        print("alembic =", r2.scalar())


asyncio.run(main())
