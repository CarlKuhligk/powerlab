from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import (
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    select,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


class Base(DeclarativeBase):
    pass


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), index=True)
    project: Mapped[str] = mapped_column(String(160), default="", index=True)
    device: Mapped[str] = mapped_column(String(160), default="", index=True)
    firmware: Mapped[str] = mapped_column(String(160), default="", index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    driver: Mapped[str] = mapped_column(String(32), default="ppk2")
    port: Mapped[str | None] = mapped_column(String(80), nullable=True)
    meter_mode: Mapped[str] = mapped_column(String(32), default="source")
    voltage_mv: Mapped[int] = mapped_column(Integer, default=3300)
    sample_rate_hz: Mapped[int] = mapped_column(Integer, default=100_000)
    status: Mapped[str] = mapped_column(String(32), default="created", index=True)

    scheduled_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    scheduled_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_duration_s: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_mode: Mapped[str] = mapped_column(String(20), default="now")
    stop_mode: Mapped[str] = mapped_column(String(20), default="manual")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    ppk2_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    ppk2_config_json: Mapped[str] = mapped_column(Text, default="{}")
    total_samples: Mapped[int] = mapped_column(BigInteger, default=0)
    detected_lost_samples: Mapped[int] = mapped_column(BigInteger, default=0)
    wake_count: Mapped[int] = mapped_column(Integer, default=0)
    sleep_current_ua: Mapped[float | None] = mapped_column(Float, nullable=True)
    average_current_ua: Mapped[float | None] = mapped_column(Float, nullable=True)
    peak_current_ua: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_charge_uc: Mapped[float] = mapped_column(Float, default=0.0)

    sleep_segments: Mapped[list["SleepSegment"]] = relationship(
        back_populates="measurement", cascade="all, delete-orphan"
    )
    wake_events: Mapped[list["WakeEvent"]] = relationship(
        back_populates="measurement", cascade="all, delete-orphan"
    )


class SleepSegment(Base):
    __tablename__ = "sleep_segments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measurement_id: Mapped[str] = mapped_column(
        ForeignKey("measurements.id", ondelete="CASCADE"), index=True
    )
    start_sample: Mapped[int] = mapped_column(BigInteger)
    end_sample: Mapped[int] = mapped_column(BigInteger)
    sample_count: Mapped[int] = mapped_column(BigInteger)
    mean_ua: Mapped[float] = mapped_column(Float)
    min_ua: Mapped[float] = mapped_column(Float)
    max_ua: Mapped[float] = mapped_column(Float)
    std_ua: Mapped[float] = mapped_column(Float)
    charge_uc: Mapped[float] = mapped_column(Float)

    measurement: Mapped[Measurement] = relationship(back_populates="sleep_segments")


class WakeEvent(Base):
    __tablename__ = "wake_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measurement_id: Mapped[str] = mapped_column(
        ForeignKey("measurements.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    start_sample: Mapped[int] = mapped_column(BigInteger)
    trigger_sample: Mapped[int] = mapped_column(BigInteger)
    end_sample: Mapped[int] = mapped_column(BigInteger)
    duration_us: Mapped[float] = mapped_column(Float)
    peak_ua: Mapped[float] = mapped_column(Float)
    mean_ua: Mapped[float] = mapped_column(Float)
    charge_uc: Mapped[float] = mapped_column(Float)
    raw_file: Mapped[str] = mapped_column(Text)
    digital_mask_seen: Mapped[int] = mapped_column(Integer, default=0)
    event_kind: Mapped[str] = mapped_column(String(20), default="wake", server_default="wake")

    measurement: Mapped[Measurement] = relationship(back_populates="wake_events")


class OverviewPoint(Base):
    __tablename__ = "overview_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measurement_id: Mapped[str] = mapped_column(
        ForeignKey("measurements.id", ondelete="CASCADE"), index=True
    )
    sample_index: Mapped[int] = mapped_column(BigInteger, index=True)
    current_ua: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String(32), default="sleep")
    event_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Marker(Base):
    __tablename__ = "markers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    measurement_id: Mapped[str] = mapped_column(
        ForeignKey("measurements.id", ondelete="CASCADE"), index=True
    )
    sample_index: Mapped[int] = mapped_column(BigInteger, index=True)
    label: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class Database:
    def __init__(self, url: str):
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args, future=True)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _sqlite_fk_on(dbapi_connection, connection_record):  # noqa: ARG001
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()
        self.SessionLocal = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate_sqlite()

    def _migrate_sqlite(self) -> None:
        """Tiny additive migration layer for pilot releases.

        v0.2 adds PPK2 identity/config logging. Existing v0.1 SQLite databases are
        upgraded in place so users can unpack the new pilot over an existing data
        directory without losing measurements.
        """
        if self.engine.dialect.name != "sqlite":
            return
        with self.engine.begin() as conn:
            columns = {
                row[1]
                for row in conn.execute(text("PRAGMA table_info(measurements)"))
            }
            if "ppk2_id" not in columns:
                conn.execute(text("ALTER TABLE measurements ADD COLUMN ppk2_id VARCHAR(512)"))
            if "ppk2_config_json" not in columns:
                conn.execute(text("ALTER TABLE measurements ADD COLUMN ppk2_config_json TEXT DEFAULT '{}'") )
            additions = {
                "scheduled_start_at": "DATETIME",
                "scheduled_end_at": "DATETIME",
                "requested_duration_s": "FLOAT",
                "start_mode": "VARCHAR(20) DEFAULT 'now'",
                "stop_mode": "VARCHAR(20) DEFAULT 'manual'",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    conn.execute(text(f"ALTER TABLE measurements ADD COLUMN {name} {ddl}"))
            event_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(wake_events)"))}
            if "event_kind" not in event_columns:
                conn.execute(text("ALTER TABLE wake_events ADD COLUMN event_kind VARCHAR(20) NOT NULL DEFAULT 'wake'"))

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_measurement(self, measurement_id: str) -> Measurement | None:
        with self.session() as s:
            return s.get(Measurement, measurement_id)

    def list_measurements(self, limit: int = 200) -> list[Measurement]:
        with self.session() as s:
            return list(
                s.scalars(
                    select(Measurement)
                    .order_by(Measurement.created_at.desc())
                    .limit(limit)
                )
            )


def model_to_dict(obj) -> dict:
    return {column.name: getattr(obj, column.name) for column in obj.__table__.columns}
