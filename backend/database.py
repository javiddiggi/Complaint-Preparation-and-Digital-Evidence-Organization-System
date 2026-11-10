import os
from pathlib import Path
from typing import Generator

from sqlalchemy import URL, create_engine, event, inspect
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


BACKEND_DIR = Path(__file__).resolve().parent
DATABASE_URL = os.getenv("CYBER_ASSIST_DATABASE_URL")
database_url = make_url(DATABASE_URL) if DATABASE_URL else URL.create(
    "sqlite", database=str(BACKEND_DIR / "cyber_assist.db")
)

engine = create_engine(
    database_url,
    connect_args={"check_same_thread": False} if database_url.drivername == "sqlite" else {},
)
if database_url.drivername == "sqlite":
    @event.listens_for(engine, "connect")
    def enable_sqlite_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session


def ensure_evidence_extraction_schema() -> None:
    columns = {column["name"] for column in inspect(engine).get_columns("evidence")}
    missing_columns = {
        "extracted_text": "TEXT",
        "extraction_status": "VARCHAR(32) NOT NULL DEFAULT 'not_extracted'",
        "extraction_message": "TEXT",
        "extracted_at": "DATETIME",
    }
    with engine.begin() as connection:
        for name, definition in missing_columns.items():
            if name not in columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE evidence ADD COLUMN {name} {definition}"
                )


def ensure_workflow_schema() -> None:
    incident_columns = {column["name"] for column in inspect(engine).get_columns("incidents")}
    evidence_columns = {column["name"] for column in inspect(engine).get_columns("evidence")}
    with engine.begin() as connection:
        for name, definition in {
            "client_id": "VARCHAR(64)",
            "client_case_id": "VARCHAR(64)",
            "workflow_data": "JSON",
        }.items():
            if name not in incident_columns:
                connection.exec_driver_sql(f"ALTER TABLE incidents ADD COLUMN {name} {definition}")
        if "structured_data" not in evidence_columns:
            connection.exec_driver_sql("ALTER TABLE evidence ADD COLUMN structured_data JSON")
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_incidents_client_case "
            "ON incidents (client_id, client_case_id)"
        )
