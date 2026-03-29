"""
Database configuration and connection setup
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base
import os
from dotenv import load_dotenv

load_dotenv()

# Database URL - defaults to SQLite for development
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./trading_app.db")

# Create engine
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL, 
        connect_args={"check_same_thread": False}  # SQLite specific
    )
else:
    engine = create_engine(DATABASE_URL)

# Create SessionLocal class
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()

def get_db():
    """Dependency to get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _migrate_columns():
    """
    Safely add new columns to existing tables that pre-date the current schema.
    SQLite does not support ALTER TABLE ADD COLUMN IF NOT EXISTS, so we query
    PRAGMA table_info and only add the column when it's absent.
    This is idempotent — safe to call on every startup.
    """
    migrations = [
        # table_name, column_name, column_definition
        ("mock_trades", "win_probability_score", "REAL"),
        ("mock_trades", "win_probability_grade", "VARCHAR(4)"),
    ]
    with engine.connect() as conn:
        for table, col, col_def in migrations:
            try:
                result = conn.execute(
                    __import__("sqlalchemy").text(f"PRAGMA table_info({table})")
                )
                existing_cols = {row[1] for row in result}
                if col not in existing_cols:
                    conn.execute(
                        __import__("sqlalchemy").text(
                            f"ALTER TABLE {table} ADD COLUMN {col} {col_def}"
                        )
                    )
                    conn.commit()
            except Exception as exc:
                # Non-fatal — if this fails (e.g. Postgres already has column),
                # we let the app start and rely on the model definition.
                import logging
                logging.getLogger(__name__).warning(
                    f"Column migration skipped for {table}.{col}: {exc}"
                )


def create_tables():
    """Create all database tables and run lightweight column migrations."""
    from models import Base
    Base.metadata.create_all(bind=engine)
    _migrate_columns()

if __name__ == "__main__":
    create_tables()
    print("Database tables created successfully!")