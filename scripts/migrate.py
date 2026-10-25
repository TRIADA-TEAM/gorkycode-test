"""Apply Alembic migrations, including adoption of legacy create_all databases."""

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

from app.config import get_settings


REVISION_0001_EVENT_COLUMNS = {
    "id",
    "source_id",
    "owner_session_id",
    "source",
    "type",
    "title",
    "description",
    "starts_at",
    "ends_at",
    "due_at",
    "timezone",
    "status",
    "raw_user_text",
    "tags",
    "search_text",
    "created_at",
    "updated_at",
}


def migrate() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    has_application_tables = bool({"events", "dialogue_sessions"} & tables)
    has_alembic_version = "alembic_version" in tables
    if has_application_tables and not has_alembic_version:
        if not {"events", "dialogue_sessions"} <= tables:
            raise RuntimeError("Legacy database has an incomplete application schema")
        event_columns = {column["name"] for column in inspector.get_columns("events")}
        missing = REVISION_0001_EVENT_COLUMNS - event_columns
        if missing:
            raise RuntimeError(
                "Legacy database schema is not compatible; missing columns: "
                + ", ".join(sorted(missing))
            )
        config = Config("alembic.ini")
        legacy_revision = "head" if "due_date" in event_columns else "0001_initial"
        command.stamp(config, legacy_revision)

    command.upgrade(Config("alembic.ini"), "head")


if __name__ == "__main__":
    migrate()
