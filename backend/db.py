import os
from sqlalchemy import create_engine, inspect, text
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
    # Postgres (Supabase in prod). Name the driver explicitly: SQLAlchemy 2.1 switched the default
    # for plain "postgresql://" URLs from psycopg2 to psycopg 3, which broke the first deploy.
    # pre_ping drops connections the pooler has closed while idle.
    url = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
    engine = create_engine(url, pool_pre_ping=True)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _add_missing_columns():
    """create_all() only creates missing *tables*. New nullable columns on tables that already
    exist (like the live Supabase ones) are added here -- enough for this project's small
    schema changes without bringing in a migration tool."""
    inspector = inspect(engine)
    for table in Base.metadata.sorted_tables:
        if not inspector.has_table(table.name):
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                column_type = column.type.compile(engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))


def init_db():
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

    if engine.dialect.name == "postgresql":
        # Supabase serves every table in the public schema through its REST API, readable with
        # the project's anon key unless Row Level Security is on. With RLS on and no policies
        # that API sees nothing, while our backend (connecting as the table owner) is unaffected.
        with engine.begin() as conn:
            for table in Base.metadata.tables:
                conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY'))
