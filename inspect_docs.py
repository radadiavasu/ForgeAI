import asyncio
from sqlalchemy import text
from forgeai.database import AsyncSessionFactory

async def inspect():
    async with AsyncSessionFactory() as s:
        rows = await s.execute(text("""
            SELECT project_id, artefact_type, content
            FROM project_artefacts
            ORDER BY created_at DESC
            LIMIT 5
        """))
        for r in rows.fetchall():
            print(f"Project: {r[0]}")
            print(f"Type: {r[1]}")
            print(f"Content: {str(r[2])[:2000]}")
            print("---")

asyncio.run(inspect())