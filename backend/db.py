import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.models import Base

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/data.db")

# Ensure directory exists for SQLite
if DATABASE_URL.startswith("sqlite:///"):
    path = DATABASE_URL.replace("sqlite:///", "")
    dirpath = os.path.dirname(path)
    os.makedirs(dirpath, exist_ok=True)

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
