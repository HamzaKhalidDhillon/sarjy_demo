import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/data.db")

if DATABASE_URL.startswith("sqlite:///"):
    # Local dev: make sure the folder exists, and let FastAPI's threads share the connection
    path = DATABASE_URL.replace("sqlite:///", "")
    dirpath = os.path.dirname(path)
    os.makedirs(dirpath, exist_ok=True)
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # Postgres (Supabase in prod). pre_ping drops connections the pooler has closed while idle
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)

    if engine.dialect.name == "postgresql":
        # Supabase serves every table in the public schema through its REST API, readable with
        # the project's anon key unless Row Level Security is on. With RLS on and no policies
        # that API sees nothing, while our backend (connecting as the table owner) is unaffected.
        with engine.begin() as conn:
            for table in Base.metadata.tables:
                conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
