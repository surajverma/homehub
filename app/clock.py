"""Time helpers shared by the whole app."""
import sqlite3
from datetime import date, datetime, timezone


def utcnow() -> datetime:
    """Now in UTC without tzinfo, which is how every timestamp in the database is stored."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# Python 3.12 deprecated sqlite3's built-in date/datetime adapters. These write the same text
# those did, for the few raw values that reach the driver without going through SQLAlchemy's types.
sqlite3.register_adapter(date, lambda value: value.isoformat())
sqlite3.register_adapter(datetime, lambda value: value.isoformat(' '))
