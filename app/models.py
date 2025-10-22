from . import db
from datetime import datetime

class Note(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    creator = db.Column(db.String(64), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class File(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256), nullable=False)
    creator = db.Column(db.String(64), nullable=False)
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)

class Media(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256))
    url = db.Column(db.String(512))
    creator = db.Column(db.String(64))
    download_time = db.Column(db.DateTime, default=datetime.utcnow)
    filepath = db.Column(db.String(512))
    status = db.Column(db.String(32), default='done')  # pending, done, error
    progress = db.Column(db.Text)  # latest progress line or JSON

class PDF(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(256))
    creator = db.Column(db.String(64))
    upload_time = db.Column(db.DateTime, default=datetime.utcnow)
    compressed_path = db.Column(db.String(512))

class ShoppingItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item = db.Column(db.String(256), nullable=False)
    checked = db.Column(db.Boolean, default=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # JSON-encoded list of tags (e.g., ["Costco", "Dairy"]) for filtering/grouping
    tags = db.Column(db.Text, default='[]')

class GroceryHistory(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    item = db.Column(db.String(256), nullable=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class HomeStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(16), default='Away')

class Chore(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    description = db.Column(db.Text, nullable=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    done = db.Column(db.Boolean, default=False)
    # JSON-encoded list of tags (e.g., ["Alice", "Weekend"]) for assignment/filtering
    tags = db.Column(db.Text, default='[]')

class Recipe(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(256), nullable=False)
    link = db.Column(db.String(512))
    ingredients = db.Column(db.Text)
    instructions = db.Column(db.Text)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ExpiryItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(256), nullable=False)
    expiry_date = db.Column(db.Date)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class ShortURL(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    original_url = db.Column(db.String(512), nullable=False)
    short_code = db.Column(db.String(16), unique=True, nullable=False)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class QRCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.Text, nullable=False)
    filename = db.Column(db.String(256), nullable=False)
    original_input = db.Column(db.Text)  # what user typed (for history display)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Notice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, default='')
    updated_by = db.Column(db.String(64))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

class Reminder(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    time = db.Column(db.String(5))  # HH:MM (optional)
    title = db.Column(db.String(256), nullable=False)
    description = db.Column(db.Text)
    creator = db.Column(db.String(64))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    # New fields (phase 1) - added via auto-migration if missing
    category = db.Column(db.String(64))  # key referencing configured category
    color = db.Column(db.String(16))     # optional override hex color
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # Tie back to a recurring rule (if generated)
    recurring_id = db.Column(db.Integer)

class MemberStatus(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), nullable=False)
    text = db.Column(db.Text, default='')
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

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
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
