# FastAPI Server Entry Point Skill

## Required files (all mandatory)
- src/main.py
- src/database.py
- src/routes/__init__.py
- requirements.txt
- .env.example

## src/main.py structure
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from src.routes import router

load_dotenv()
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")
```

## src/database.py structure
```python
import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ["DATABASE_URL"]
engine = create_async_engine(DATABASE_URL)
AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
```

## Common mistakes — never do these
- Never hardcode DATABASE_URL
- Always use async database operations
- Always add CORS middleware before routes
- Always use environment variables for secrets
