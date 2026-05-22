import asyncio
from sqlalchemy import text
from forgeai.database import AsyncSessionFactory

async def check():
    async with AsyncSessionFactory() as s:
        rows = await s.execute(text("""
            SELECT t.title, t.current_state, tsh.metadata
            FROM tasks t
            JOIN task_state_history tsh ON tsh.task_id = t.id
            WHERE t.assigned_agent LIKE '%backend%'
            AND tsh.to_state = 'IN_PROGRESS'
            AND tsh.from_state = 'TESTING'
            ORDER BY t.created_at DESC
            LIMIT 3
        """))
        for row in rows.fetchall():
            meta = row[2] or {}
            print(f"Task: {row[0]}")
            print(f"State: {row[1]}")
            defect = meta.get("defect_report", "")
            print(f"Defect: {str(defect)[:400]}")
            print("---")

asyncio.run(check())
