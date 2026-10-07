from sqlalchemy import create_engine, text

from app.db import Database


def test_v01_sqlite_database_gets_ppk2_log_columns(tmp_path):
    db_path = tmp_path / "old.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE measurements (id VARCHAR(36) PRIMARY KEY, name VARCHAR(160))"))
    engine.dispose()

    db = Database(f"sqlite:///{db_path}")
    db.create_all()
    with db.engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(measurements)"))}
    assert "ppk2_id" in cols
    assert "ppk2_config_json" in cols


def test_old_events_keep_wake_classification_after_additive_migration(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'events.db'}")
    with db.engine.begin() as conn:
        conn.execute(text("CREATE TABLE wake_events (id INTEGER PRIMARY KEY, sequence INTEGER)"))
        conn.execute(text("INSERT INTO wake_events (id, sequence) VALUES (1, 7)"))
    db.create_all()
    db.create_all()  # migration is idempotent
    with db.engine.begin() as conn:
        assert conn.execute(text("SELECT sequence, event_kind FROM wake_events WHERE id=1")).one() == (7, "wake")
