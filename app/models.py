import json
from . import db
from .clock import utcnow

SPLIT_MODES = ('equal', 'shares', 'percent', 'amount')


def parse_split(raw):
    """Decode a stored split into (mode, {member: weight}).

    Stored as a JSON list of names for an equal split, or
    {"mode": ..., "weights": {name: number}} for an uneven one.
    Every mode shares the amount in proportion to the weights.
    """
    if not raw:
        return 'equal', {}
    try:
        val = json.loads(raw)
    except Exception:
        return 'equal', {}
    if isinstance(val, list):
        return 'equal', {str(x): 1.0 for x in val if str(x).strip()}
    if isinstance(val, dict) and isinstance(val.get('weights'), dict):
        weights = {}
        for name, w in val['weights'].items():
            try:
                w = float(w)
            except (TypeError, ValueError):
                continue
            if str(name).strip() and w > 0:
                weights[str(name)] = w
        mode = val.get('mode') if val.get('mode') in SPLIT_MODES else 'shares'
        return mode, weights
    return 'equal', {}

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    creator = db.Column(db.String(64), nullable=False)
    timestamp = db.Column(db.DateTime, default=utcnow)

class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)  # the name it was uploaded with, shown and downloaded as
    # Name on disk when it had to differ from filename (another file already had that name)
    stored_name = db.Column(db.String(256))
    creator = db.Column(db.String(64), nullable=False)
    upload_time = db.Column(db.DateTime, default=utcnow)

    @property
    def disk_name(self):
        return self.stored_name or self.filename

class Media(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256))
    url = db.Column(db.String(512))
    creator = db.Column(db.String(64))
    download_time = db.Column(db.DateTime, default=utcnow)
    filepath = db.Column(db.String(512))
    status = db.Column(db.String(32), default='done')  # pending, done, error
    progress = db.Column(db.Text)  # latest progress line or JSON

class PDF(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256))
    creator = db.Column(db.String(64))
    upload_time = db.Column(db.DateTime, default=utcnow)
    compressed_path = db.Column(db.String(512))

class ShoppingItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item = db.Column(db.String(256), nullable=False)
    checked = db.Column(db.Boolean, default=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)
    # JSON-encoded list of tags (e.g., ["Costco", "Dairy"]) for filtering/grouping
    tags = db.Column(db.Text, default='[]')

class GroceryHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item = db.Column(db.String(256), nullable=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)

class HomeStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), default='Away')

class Chore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)
    done = db.Column(db.Boolean, default=False)
    due_date = db.Column(db.Date)
    recurring_id = db.Column(db.Integer)
    # JSON-encoded list of tags (e.g., ["Alice", "Weekend"]) for assignment/filtering
    tags = db.Column(db.Text, default='[]')


class RecurringChore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    creator = db.Column(db.String(64))
    tags = db.Column(db.Text, default='[]')
    interval = db.Column(db.Integer, default=1)
    unit = db.Column(db.String(8), default='day')  # day|week|month|year
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    last_generated_date = db.Column(db.Date)
    timestamp = db.Column(db.DateTime, default=utcnow)

class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    link = db.Column(db.String(512))
    ingredients = db.Column(db.Text)
    instructions = db.Column(db.Text)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)
    # JSON-encoded list of tags (e.g., ["Dessert", "Quick", "Vegetarian"]) for filtering/grouping
    tags = db.Column(db.Text, default='[]')

class ExpiryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)
    expiry_date = db.Column(db.Date)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)

class ShortURL(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_url = db.Column(db.String(512), nullable=False)
    short_code = db.Column(db.String(16), unique=True, nullable=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)

class QRCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    filename = db.Column(db.String(256), nullable=False)  # unused: the image is drawn from text when asked for
    original_input = db.Column(db.Text)  # what user typed (for history display)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)

class Notice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, default='')
    updated_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime, default=utcnow)

class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5))  # HH:MM (optional)
    start_date = db.Column(db.Date)
    start_time = db.Column(db.String(5))  # HH:MM (optional)
    end_date = db.Column(db.Date)
    end_time = db.Column(db.String(5))  # HH:MM (optional)
    all_day = db.Column(db.Boolean, default=False)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=utcnow)
    # New fields (phase 1) - added via auto-migration if missing
    category = db.Column(db.String(64))  # key referencing configured category
    color = db.Column(db.String(16))     # optional override hex color
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow)
    # Tie back to a recurring rule (if generated)
    recurring_id = db.Column(db.Integer)
    completed_at = db.Column(db.DateTime)
    deleted_at = db.Column(db.DateTime)

class MemberStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    text = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime, default=utcnow)

class RecurringExpense(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    unit_price = db.Column(db.Float, default=0.0)
    default_quantity = db.Column(db.Float, default=1.0)
    frequency = db.Column(db.String(16), default='daily')  # daily|weekly|monthly
    category = db.Column(db.String(64))
    monthly_mode = db.Column(db.String(16), default='day_of_month')  # calendar|day_of_month
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    last_generated_date = db.Column(db.Date)
    effective_from = db.Column(db.Date)  # apply changes from this date forward
    creator = db.Column(db.String(64))
    # JSON-encoded split, see parse_split (copied onto generated entries)
    split_with = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=utcnow)

    @property
    def split_members(self):
        return list(parse_split(self.split_with)[1])

    @property
    def split_mode(self):
        return parse_split(self.split_with)[0]

    @property
    def split_weights(self):
        return parse_split(self.split_with)[1]

class RecurringReminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    creator = db.Column(db.String(64))
    # Legacy fields kept for backward compatibility
    frequency = db.Column(db.String(16), default='daily')  # daily|weekly|monthly (legacy)
    monthly_mode = db.Column(db.String(16), default='day_of_month')  # calendar|day_of_month (legacy)
    # New flexible recurrence
    interval = db.Column(db.Integer, default=1)  # e.g., 1,2,3
    unit = db.Column(db.String(8), default='day')  # 'day'|'week'|'month'|'year'
    time = db.Column(db.String(5))  # optional HH:MM
    category = db.Column(db.String(64))
    color = db.Column(db.String(16))
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    last_generated_date = db.Column(db.Date)
    effective_from = db.Column(db.Date)
    timestamp = db.Column(db.DateTime, default=utcnow)

class ExpenseEntry(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    title = db.Column(db.String(256), nullable=False)
    category = db.Column(db.String(64))
    unit_price = db.Column(db.Float)
    quantity = db.Column(db.Float)
    amount = db.Column(db.Float, nullable=False)
    payer = db.Column(db.String(64))
    recurring_id = db.Column(db.Integer, db.ForeignKey('recurring_expense.id'))
    # Skipped recurring days stay in place (so they can be restored) but don't count anywhere
    skipped = db.Column(db.Boolean, default=False)
    # JSON-encoded split (see parse_split); empty/NULL means not shared
    split_with = db.Column(db.Text)
    # Settlement: payer paid split_with[0] back; excluded from spending totals
    is_settlement = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=utcnow)


class UndoStash(db.Model):
    """What a change replaced, kept for a few seconds so it can be undone (see app/undo.py)."""
    id = db.Column(db.Integer, primary_key=True)
    token = db.Column(db.String(32), unique=True, nullable=False)
    kind = db.Column(db.String(32), nullable=False)
    payload = db.Column(db.Text, nullable=False)  # JSON
    expires_at = db.Column(db.DateTime, nullable=False)
