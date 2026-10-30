from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from vendoo_studio.config import DATABASE_URL

# SQLite serialises writers. Studio writes from the request path *and* from
# background sweeps (Vendoo label sync, watch, bulk import), so a chat send can
# arrive while a sweep holds the write lock. sqlite3's stock 5s busy timeout is
# short enough that a Chrome round-trip inside a sweep pushed sends over it, and
# the seller saw the prompt die with "database is locked". Wait instead.
BUSY_TIMEOUT_SEC = 30


def database_url(raw: str = DATABASE_URL) -> str:
    """The configured URL, with Postgres pointed at the psycopg 3 driver.

    Hosts hand out ``postgres://`` or ``postgresql://`` URLs; SQLAlchemy would
    pick psycopg2 for those, which this app does not install.
    """
    url = make_url(raw)
    if url.drivername in {"postgres", "postgresql"}:
        url = url.set(drivername="postgresql+psycopg")
    return url.render_as_string(hide_password=False)


def make_engine(url: str):
    if make_url(url).get_backend_name() == "sqlite":
        sqlite_engine = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_SEC},
            # Never make a checkout wait for another session to close. Sweeps
            # open sessions on the event loop, and a request's session is only
            # returned by that same loop, so a capped pool that runs dry stalls
            # the whole server for the pool timeout on every checkout. SQLite
            # connections are cheap; the bound that matters is SQLite's own
            # write lock.
            max_overflow=-1,
            echo=False,
        )
        event.listen(sqlite_engine, "connect", enable_wal)
        return sqlite_engine
    # Postgres connections are not cheap, so the pool is capped. pre_ping
    # replaces connections a hosted database dropped while idle.
    return create_engine(url, pool_size=10, max_overflow=20, pool_pre_ping=True, echo=False)


def enable_wal(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    # connect_args only covers the driver's own waiting; the pragma is what the
    # engine honours on statements it issues outside that path.
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_SEC * 1000}")
    cursor.close()


engine = make_engine(database_url())
IS_SQLITE = engine.dialect.name == "sqlite"

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def dialect_insert(db):
    """``insert`` for the session's database, so ``on_conflict_do_nothing`` works on both."""
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def json_value(column, path: str):
    """The value at ``path`` ("a.b") in a JSON column, typed the same on SQLite and Postgres.

    Strings, numbers, booleans and None come back as themselves; objects and
    arrays come back decoded, as dicts and lists.
    """
    return column[tuple(path.split("."))]


def json_text(column, path: str):
    """The same value as text, for comparing inside SQL (``lower(...) == ...``)."""
    return json_value(column, path).as_string()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def load_models() -> None:
    """Import every model so ``Base.metadata`` describes the whole schema."""
    from vendoo_studio.models.listing_video import ListingVideo  # noqa: F401
    from vendoo_studio.models.conversation import Conversation  # noqa: F401
    from vendoo_studio.models.listing_template import ListingTemplate  # noqa: F401
    from vendoo_studio.models.listing_copy import ListingCopyReceipt  # noqa: F401
    from vendoo_studio.models.listing import Listing, ListingRevision  # noqa: F401
    from vendoo_studio.models.job import Job, JobEvent, VendooDraftCache  # noqa: F401
    from vendoo_studio.models.diagnostics import DiagnosticRun, FieldObservation  # noqa: F401
    from vendoo_studio.models.registry import FieldRegistry  # noqa: F401
    from vendoo_studio.models.fill_log import FillLogEntry  # noqa: F401
    from vendoo_studio.models.sourcing import SourceBox  # noqa: F401
    from vendoo_studio.models.scout import ScoutCheck  # noqa: F401
    from vendoo_studio.models.listing_evidence import ListingCorrection, SaleSnapshot  # noqa: F401
    from vendoo_studio.models.sale_event import SaleEvent  # noqa: F401
    from vendoo_studio.models.marketplace_sale import MarketplaceSale  # noqa: F401
    from vendoo_studio.models.marketplace_listing import MarketplaceListing  # noqa: F401
    from vendoo_studio.models.ebay_offer import EbayOfferAttempt, EbayOfferPolicy, EbayOfferRule  # noqa: F401
    from vendoo_studio.models.ebay_action import EbayAction  # noqa: F401
    from vendoo_studio.models.listing_promotion import ListingPromotion, MarketplaceOfferFloor  # noqa: F401
    from vendoo_studio.models.promotion_automation import PromotionAutomation, PromotionRun  # noqa: F401
    from vendoo_studio.models.ad_spend import AdSpend  # noqa: F401
    from vendoo_studio.models.similar_item import PhotoFingerprint, SimilarItemReview  # noqa: F401
    from vendoo_studio.models.assistant import AssistantMemory, AssistantMessage, AssistantThread  # noqa: F401
    from vendoo_studio.models.automation import (  # noqa: F401
        Automation,
        AutomationApplication,
        AutomationApproval,
        AutomationRelistGap,
        AutomationRun,
        ItemPriceBaseline,
    )
    from vendoo_studio.models.generation_timing import GenerationTiming  # noqa: F401
    from vendoo_studio.models.catalog import (  # noqa: F401
        CategoryFieldSchema,
        CategoryNode,
        CategorySchema,
        CategoryTree,
        CategoryTreeNode,
    )


def init_db():
    """Bring the database to the schema this build expects.

    Delegates to the migration runner, which snapshots before it changes
    anything and refuses a database written by a newer build.
    """
    from vendoo_studio.services.schema_migrations import ensure_schema

    load_models()
    ensure_schema(engine)


# Columns added to tables that had already shipped. ``create_all`` only creates
# whole tables, so a column added to an existing one never reaches a database
# built before it landed unless it is named here. Adding a column to a model
# without adding it here leaves older databases short of it, and the mismatch
# only shows up later as a query error.
BACKFILLED_COLUMNS: dict[str, dict[str, str]] = {
    "conversations": {
        "settled_at": "DATETIME",
        "unsettled_at": "DATETIME",
    },
    "field_registry": {
        "options_source": "VARCHAR",
    },
}


def _ensure_sqlite_columns(bind=None) -> None:
    target = bind if bind is not None else engine
    inspector = inspect(target)
    tables = set(inspector.get_table_names())
    statements = []

    for table, columns in BACKFILLED_COLUMNS.items():
        if table not in tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table)}
        statements += [
            f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}"
            for column, sql_type in columns.items()
            if column not in existing
        ]

    if not statements:
        return
    with target.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
