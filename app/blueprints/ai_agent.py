from flask import Blueprint, request, jsonify, current_app
from ..models import db, HomeStatus, MemberStatus, Note, Chore, ShoppingItem, QuickLink, ExpenseEntry, RecurringExpense
from ..config import update_config
from datetime import datetime

ai_agent_bp = Blueprint('ai_agent', __name__)

def require_ai_token(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        config = current_app.config.get('HOMEHUB_CONFIG', {})
        token = config.get('ai_agent_token')
        
        if not token:
            return jsonify({'error': 'AI agent token not configured in config.yml'}), 501
            
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid Authorization header'}), 401
            
        provided_token = auth_header.split(' ', 1)[1]
        if provided_token != str(token):
            return jsonify({'error': 'Invalid token'}), 403
            
        return f(*args, **kwargs)
    return decorated_function

def handle_update_home_status(params, family):
    names = params.get('names', [])
    status = params.get('status', 'Away')
    
    if not isinstance(names, list):
        return {'success': False, 'error': 'names must be a list of strings'}
        
    updated = []
    for name in names:
        if name in family:
            hs = HomeStatus.query.filter_by(name=name).first()
            if hs:
                hs.status = status
            else:
                db.session.add(HomeStatus(name=name, status=status))
            updated.append(name)
            
    db.session.commit()
    return {'success': True, 'updated': updated, 'status': status}

def handle_update_member_status(params, family):
    names = params.get('names', [])
    text = params.get('text', '')
    
    if not isinstance(names, list):
        return {'success': False, 'error': 'names must be a list of strings'}
        
    updated = []
    now = datetime.utcnow()
    for name in names:
        if name in family:
            ms = MemberStatus.query.filter_by(name=name).first()
            if ms:
                ms.text = text
                ms.updated_at = now
            else:
                db.session.add(MemberStatus(name=name, text=text, updated_at=now))
            updated.append(name)
            
    db.session.commit()
    return {'success': True, 'updated': updated, 'text': text}

def handle_get_notes(params, family):
    notes = Note.query.order_by(Note.timestamp.desc()).all()
    result = []
    for n in notes:
        result.append({
            "id": n.id,
            "content": n.content,
            "creator": n.creator,
            "timestamp": n.timestamp.isoformat()
        })
    return {'success': True, 'notes': result}

def handle_add_note(params, family):
    content = params.get('content')
    creator = params.get('creator', 'AI Assistant')
    if not content:
        return {'success': False, 'error': 'content is required'}
    
    note = Note(content=content, creator=creator)
    db.session.add(note)
    db.session.commit()
    return {'success': True, 'note_id': note.id}

def handle_get_chores(params, family):
    chores = Chore.query.filter_by(done=False).order_by(Chore.timestamp.desc()).all()
    result = []
    for c in chores:
        result.append({
            "id": c.id,
            "description": c.description,
            "creator": c.creator,
            "due_date": c.due_date.isoformat() if c.due_date else None,
            "tags": c.tags
        })
    return {'success': True, 'chores': result}

def handle_add_chore(params, family):
    description = params.get('description')
    creator = params.get('creator', 'AI Assistant')
    due_date_str = params.get('due_date')
    tags = params.get('tags', '')
    
    if isinstance(tags, list):
        tags = ",".join(tags)
    
    if not description:
        return {'success': False, 'error': 'description is required'}
        
    due_date = None
    if due_date_str:
        try:
            due_date = datetime.strptime(due_date_str, '%Y-%m-%d').date()
        except ValueError:
            return {'success': False, 'error': 'due_date must be YYYY-MM-DD'}
            
    chore = Chore(description=description, creator=creator, due_date=due_date, tags=tags)
    db.session.add(chore)
    db.session.commit()
    return {'success': True, 'chore_id': chore.id}

def handle_mark_chore_done(params, family):
    chore_id = params.get('chore_id')
    if not chore_id:
        return {'success': False, 'error': 'chore_id is required'}
        
    chore = Chore.query.get(chore_id)
    if not chore:
        return {'success': False, 'error': 'Chore not found'}
        
    chore.done = True
    db.session.commit()
    return {'success': True, 'chore_id': chore_id}

def handle_get_shopping_list(params, family):
    items = ShoppingItem.query.filter_by(checked=False).order_by(ShoppingItem.timestamp.desc()).all()
    result = []
    for i in items:
        result.append({
            "id": i.id,
            "item": i.item,
            "creator": i.creator,
            "tags": i.tags
        })
    return {'success': True, 'items': result}

def handle_add_shopping_item(params, family):
    item = params.get('item')
    creator = params.get('creator', 'AI Assistant')
    tags = params.get('tags', '')
    
    if isinstance(tags, list):
        tags = ",".join(tags)
    
    if not item:
        return {'success': False, 'error': 'item is required'}
        
    si = ShoppingItem(item=item, creator=creator, tags=tags)
    db.session.add(si)
    db.session.commit()
    return {'success': True, 'item_id': si.id}

def handle_check_shopping_item(params, family):
    item_id = params.get('item_id')
    if not item_id:
        return {'success': False, 'error': 'item_id is required'}
        
    si = ShoppingItem.query.get(item_id)
    if not si:
        return {'success': False, 'error': 'ShoppingItem not found'}
        
    si.checked = True
    db.session.commit()
    return {'success': True, 'item_id': item_id}

def handle_delete_note(params, family):
    note_id = params.get('note_id')
    if not note_id:
        return {'success': False, 'error': 'note_id is required'}
    
    note = Note.query.get(note_id)
    if not note:
        return {'success': False, 'error': 'Note not found'}
        
    db.session.delete(note)
    db.session.commit()
    return {'success': True, 'note_id': note_id}

def handle_delete_chore(params, family):
    chore_id = params.get('chore_id')
    if not chore_id:
        return {'success': False, 'error': 'chore_id is required'}
        
    chore = Chore.query.get(chore_id)
    if not chore:
        return {'success': False, 'error': 'Chore not found'}
        
    db.session.delete(chore)
    db.session.commit()
    return {'success': True, 'chore_id': chore_id}

def handle_delete_shopping_item(params, family):
    item_id = params.get('item_id')
    if not item_id:
        return {'success': False, 'error': 'item_id is required'}
        
    si = ShoppingItem.query.get(item_id)
    if not si:
        return {'success': False, 'error': 'ShoppingItem not found'}
        
    db.session.delete(si)
    db.session.commit()
    return {'success': True, 'item_id': item_id}

def handle_get_quick_links(params, family):
    links = QuickLink.query.order_by(QuickLink.timestamp.desc()).all()
    result = []
    for l in links:
        result.append({
            "id": l.id,
            "title": l.title,
            "url": l.url,
            "category": l.category,
            "icon_keyword": l.icon_keyword,
            "show_on_dashboard": l.show_on_dashboard
        })
    return {'success': True, 'quick_links': result}

def handle_add_quick_link(params, family):
    title = params.get('title')
    url = params.get('url')
    if not title or not url:
        return {'success': False, 'error': 'title and url are required'}
        
    category = params.get('category', 'General')
    icon_keyword = params.get('icon_keyword', '')
    show_on_dashboard = params.get('show_on_dashboard', True)
    creator = params.get('creator', 'AI Assistant')
    
    ql = QuickLink(
        title=title, 
        url=url, 
        category=category, 
        icon_keyword=icon_keyword,
        show_on_dashboard=show_on_dashboard,
        creator=creator
    )
    db.session.add(ql)
    db.session.commit()
    return {'success': True, 'link_id': ql.id}

def handle_delete_quick_link(params, family):
    link_id = params.get('link_id')
    if not link_id:
        return {'success': False, 'error': 'link_id is required'}
        
    ql = QuickLink.query.get(link_id)
    if not ql:
        return {'success': False, 'error': 'QuickLink not found'}
        
    db.session.delete(ql)
    db.session.commit()
    return {'success': True, 'link_id': link_id}

def handle_edit_quick_link(params, family):
    link_id = params.get('link_id')
    if not link_id:
        return {'success': False, 'error': 'link_id is required'}
        
    ql = QuickLink.query.get(link_id)
    if not ql:
        return {'success': False, 'error': 'QuickLink not found'}
        
    if 'title' in params:
        ql.title = params['title']
    if 'url' in params:
        url = params['url']
        if not url.startswith('http://') and not url.startswith('https://'):
            url = 'https://' + url
        ql.url = url
    if 'category' in params:
        ql.category = params['category']
    if 'icon_keyword' in params:
        ql.icon_keyword = params['icon_keyword']
    if 'show_on_dashboard' in params:
        ql.show_on_dashboard = params['show_on_dashboard']
        
    db.session.commit()
    return {'success': True, 'link_id': ql.id}

def handle_get_config(params, family):
    config = current_app.config.get('HOMEHUB_CONFIG', {})
    safe_config = config.copy()
    safe_config.pop('password_hash', None)
    return {'success': True, 'config': safe_config}

def handle_update_config(params, family):
    # Only process fields that are actually provided in params
    if not params:
        return {'success': False, 'error': 'No configuration data provided.'}
        
    try:
        if 'password_hash' in params:
            del params['password_hash']
            
        update_config(params)
        
        updated_config = current_app.config.get('HOMEHUB_CONFIG', {})
        safe_config = updated_config.copy()
        safe_config.pop('password_hash', None)
        return {'success': True, 'message': 'Configuration updated', 'config': safe_config}
    except Exception as e:
        import traceback
        traceback.print_exc()
        return {'success': False, 'error': str(e)}


from datetime import date
import bleach

def _parse_date(d_str):
    if not d_str: return None
    try:
        from datetime import datetime
        return datetime.strptime(d_str, '%Y-%m-%d').date()
    except:
        return None

def _process_base64_attachment(b64_string, filename):
    if not b64_string or not filename:
        return None
    try:
        import base64
        import os
        from io import BytesIO
        from ..utils import handle_expense_attachment
        from flask import current_app
        
        content = base64.b64decode(b64_string)
        dummy_file = BytesIO(content)
        dummy_file.filename = filename
        
        upload_dir = os.path.abspath(os.path.join(current_app.root_path, '..', 'uploads'))
        return handle_expense_attachment(dummy_file, upload_dir)
    except Exception:
        return None

def _validate_and_process_attachment(params):
    b64 = params.get('attachment_base64')
    fname = params.get('attachment_filename')
    
    if not b64 and not fname:
        return None, None
        
    if bool(b64) != bool(fname):
        return None, 'Both attachment_base64 and attachment_filename are required if one is provided'
        
    try:
        import base64
        base64.b64decode(b64, validate=True)
    except Exception:
        return None, 'Invalid base64 string'
        
    path = _process_base64_attachment(b64, fname)
    if not path:
        return None, 'Failed to process attachment (invalid image or unsupported format)'
        
    return path, None

def handle_get_expenses(params, family):
    from .expenses import _generate_recurring_entries_until
    today = date.today()
    y = params.get('year', today.year)
    m = params.get('month', today.month)
    
    import calendar
    try:
        last_day = calendar.monthrange(y, m)[1]
    except Exception:
        return {'success': False, 'error': 'Invalid year or month'}
        
    target_date = date(y, m, last_day)
    _generate_recurring_entries_until(max(today, target_date))
    
    month_start = date(y, m, 1)
    month_end = date(y, m, last_day)
    
    entries = ExpenseEntry.query.filter(ExpenseEntry.date >= month_start, ExpenseEntry.date <= month_end).all()
    result = []
    for e in entries:
        result.append({
            "id": e.id,
            "title": e.title,
            "category": e.category,
            "amount": float(e.amount or 0),
            "date": e.date.isoformat(),
            "payer": e.payer,
            "is_paid": e.is_paid,
            "recurring_id": e.recurring_id,
            "has_attachment": bool(e.attachment_path),
            "attachment_path": e.attachment_path
        })
    return {'success': True, 'expenses': result}

def handle_add_expense(params, family):
    title = params.get('title')
    amount = params.get('amount')
    payer = params.get('payer')
    
    if not title or amount is None or payer is None:
        return {'success': False, 'error': 'title, amount, and payer are required'}
        
    if float(amount) < 0:
        return {'success': False, 'error': 'amount must be >= 0'}
        
    d = _parse_date(params.get('date')) or date.today()
    if d.year < 2000 or d.year > 2100:
        return {'success': False, 'error': 'date is out of reasonable range (2000-2100)'}
        
    category = bleach.clean(params.get('category') or '')
    payer = bleach.clean(payer)
    is_paid = params.get('is_paid', True)
    
    attachment_path, err = _validate_and_process_attachment(params)
    if err:
        return {'success': False, 'error': err}
    
    entry = ExpenseEntry(
        title=title,
        amount=float(amount),
        date=d,
        category=category,
        payer=payer,
        is_paid=is_paid,
        attachment_path=attachment_path
    )
    db.session.add(entry)
    db.session.commit()
    return {'success': True, 'expense_id': entry.id}

def handle_edit_expense(params, family):
    expense_id = params.get('expense_id')
    if not expense_id:
        return {'success': False, 'error': 'expense_id is required'}
        
    entry = ExpenseEntry.query.get(expense_id)
    if not entry:
        return {'success': False, 'error': 'Expense not found'}
        
    if 'title' in params: entry.title = params['title']
    if 'amount' in params:
        amt = float(params['amount'])
        if amt < 0:
            return {'success': False, 'error': 'amount must be >= 0'}
        entry.amount = amt
        
    if 'date' in params:
        d = _parse_date(params['date'])
        if d:
            if d.year < 2000 or d.year > 2100:
                return {'success': False, 'error': 'date is out of reasonable range (2000-2100)'}
            entry.date = d
            
    if 'category' in params: entry.category = bleach.clean(params['category'] or '')
    if 'payer' in params:
        payer_val = bleach.clean(params['payer'] or '')
        if not payer_val:
            return {'success': False, 'error': 'payer cannot be empty'}
        entry.payer = payer_val
        
    if 'is_paid' in params: entry.is_paid = bool(params['is_paid'])
    
    if 'attachment_base64' in params or 'attachment_filename' in params:
        new_path, err = _validate_and_process_attachment(params)
        if err:
            return {'success': False, 'error': err}
        if new_path:
            entry.attachment_path = new_path
    
    db.session.commit()
    return {'success': True, 'expense_id': entry.id}

def handle_delete_expense(params, family):
    expense_id = params.get('expense_id')
    if not expense_id:
        return {'success': False, 'error': 'expense_id is required'}
        
    entry = ExpenseEntry.query.get(expense_id)
    if not entry:
        return {'success': False, 'error': 'Expense not found'}
        
    db.session.delete(entry)
    db.session.commit()
    return {'success': True, 'expense_id': expense_id}

def handle_get_recurring_expenses(params, family):
    rules = RecurringExpense.query.all()
    result = []
    for r in rules:
        result.append({
            "id": r.id,
            "title": r.title,
            "category": r.category,
            "unit_price": float(r.unit_price or 0),
            "frequency": r.frequency,
            "monthly_mode": r.monthly_mode,
            "start_date": r.start_date.isoformat() if r.start_date else None,
            "end_date": r.end_date.isoformat() if r.end_date else None,
            "has_attachment": bool(r.attachment_path),
            "attachment_path": r.attachment_path
        })
    return {'success': True, 'recurring_rules': result}

def handle_add_recurring_expense(params, family):
    title = params.get('title')
    unit_price = params.get('unit_price')
    if not title or unit_price is None:
        return {'success': False, 'error': 'title and unit_price are required'}
        
    if float(unit_price) < 0:
        return {'success': False, 'error': 'unit_price must be >= 0'}
        
    frequency = params.get('frequency', 'monthly')
    sd = _parse_date(params.get('start_date')) or date.today()
    ed = _parse_date(params.get('end_date'))
    
    if ed and sd > ed:
        return {'success': False, 'error': 'end_date cannot be before start_date'}
        
    category = bleach.clean(params.get('category') or '')
    creator = params.get('creator', 'AI Assistant')
    monthly_mode = params.get('monthly_mode', 'day_of_month')
    
    attachment_path, err = _validate_and_process_attachment(params)
    if err:
        return {'success': False, 'error': err}
    
    rule = RecurringExpense(
        title=title,
        unit_price=float(unit_price),
        default_quantity=1.0,
        frequency=frequency,
        monthly_mode=monthly_mode,
        category=category,
        start_date=sd,
        end_date=ed,
        effective_from=sd,
        creator=creator,
        attachment_path=attachment_path
    )
    db.session.add(rule)
    db.session.commit()
    return {'success': True, 'recurring_id': rule.id}

def handle_edit_recurring_expense(params, family):
    rule_id = params.get('rule_id')
    if not rule_id:
        return {'success': False, 'error': 'rule_id is required'}
        
    r = RecurringExpense.query.get(rule_id)
    if not r:
        return {'success': False, 'error': 'Recurring rule not found'}
        
    # Strategy 'apply_from' to preserve history
    split_start = date.today()
    
    new_title = params.get('title', r.title)
    new_category = bleach.clean(params.get('category') or '') if 'category' in params else r.category
    new_unit_price = float(params.get('unit_price')) if 'unit_price' in params else r.unit_price
    if new_unit_price < 0:
        return {'success': False, 'error': 'unit_price must be >= 0'}
        
    new_frequency = params.get('frequency', r.frequency)
    new_start_date = _parse_date(params.get('start_date')) or r.start_date
    new_end_date = _parse_date(params.get('end_date')) or r.end_date

    if new_end_date and new_start_date and new_start_date > new_end_date:
        return {'success': False, 'error': 'end_date cannot be before start_date'}

    if split_start <= (r.effective_from or date.min):
        r.title = new_title
        r.category = new_category
        r.unit_price = new_unit_price
        r.frequency = new_frequency
        r.start_date = new_start_date
        r.end_date = new_end_date
    else:
        removed_from_old = ExpenseEntry.query.filter(
            ExpenseEntry.recurring_id == r.id,
            ExpenseEntry.date >= split_start
        ).delete()
        
        r.end_date = split_start
        
        new_attachment_path = r.attachment_path
        if 'attachment_base64' in params or 'attachment_filename' in params:
            new_path, err = _validate_and_process_attachment(params)
            if err:
                return {'success': False, 'error': err}
            if new_path:
                new_attachment_path = new_path
        
        new_rule = RecurringExpense(
            title=new_title,
            category=new_category,
            unit_price=new_unit_price,
            default_quantity=r.default_quantity,
            frequency=new_frequency,
            monthly_mode=r.monthly_mode,
            start_date=max(new_start_date or split_start, split_start),
            end_date=new_end_date,
            effective_from=split_start,
            creator=r.creator,
            attachment_path=new_attachment_path
        )
        db.session.add(new_rule)
        
    db.session.commit()
    return {'success': True, 'message': 'Recurring rule updated with history protection'}

def handle_delete_recurring_expense(params, family):
    rule_id = params.get('rule_id')
    if not rule_id:
        return {'success': False, 'error': 'rule_id is required'}
        
    r = RecurringExpense.query.get(rule_id)
    if not r:
        return {'success': False, 'error': 'Recurring rule not found'}
        
    delete_future_entries = params.get('delete_future_entries', True)
    
    if delete_future_entries:
        try:
            ExpenseEntry.query.filter_by(recurring_id=r.id).delete()
        except Exception:
            pass
            
    db.session.delete(r)
    db.session.commit()
    return {'success': True, 'rule_id': rule_id}


# The Universal Router Map
ACTION_ROUTER = {
    'update_home_status': handle_update_home_status,
    'update_member_status': handle_update_member_status,
    'get_notes': handle_get_notes,
    'add_note': handle_add_note,
    'delete_note': handle_delete_note,
    'get_chores': handle_get_chores,
    'add_chore': handle_add_chore,
    'mark_chore_done': handle_mark_chore_done,
    'delete_chore': handle_delete_chore,
    'get_shopping_list': handle_get_shopping_list,
    'add_shopping_item': handle_add_shopping_item,
    'check_shopping_item': handle_check_shopping_item,
    'delete_shopping_item': handle_delete_shopping_item,
    'get_quick_links': handle_get_quick_links,
    'add_quick_link': handle_add_quick_link,
    'edit_quick_link': handle_edit_quick_link,
    'delete_quick_link': handle_delete_quick_link,
    'get_config': handle_get_config,
    'update_config': handle_update_config,
    'get_expenses': handle_get_expenses,
    'add_expense': handle_add_expense,
    'edit_expense': handle_edit_expense,
    'delete_expense': handle_delete_expense,
    'get_recurring_expenses': handle_get_recurring_expenses,
    'add_recurring_expense': handle_add_recurring_expense,
    'edit_recurring_expense': handle_edit_recurring_expense,
    'delete_recurring_expense': handle_delete_recurring_expense
}

@ai_agent_bp.route('/api/ai/execute', methods=['POST'])
@require_ai_token
def execute_action():
    data = request.get_json()
    if not data or 'action' not in data or 'parameters' not in data:
        return jsonify({'error': 'Invalid payload. Requires action and parameters fields.'}), 400
        
    action = data['action']
    params = data['parameters']
    
    if action not in ACTION_ROUTER:
        return jsonify({'error': f"Unknown action: '{action}'"}), 400
        
    config = current_app.config.get('HOMEHUB_CONFIG', {})
    family = set(config.get('family_members', []))
    
    # Execute the matched handler
    result = ACTION_ROUTER[action](params, family)
    
    # HTTP status code based on success
    status_code = 200 if result.get('success') else 400
    return jsonify(result), status_code

@ai_agent_bp.route('/api/ai/schema', methods=['GET'])
def get_schema():
    """Outputs an OpenAI-compatible function calling schema array"""
    return jsonify([
        {
            "type": "function",
            "function": {
                "name": "homehub_controller",
                "description": "Send POST request to /api/ai/execute to interact with HomeHub modules (Notes, Chores, Shopping, Config, Status).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": list(ACTION_ROUTER.keys()),
                            "description": "The action to perform."
                        },
                        "parameters": {
                            "type": "object",
                            "description": "Parameters for the action. Requirements vary by action.",
                            "properties": {
                                "names": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "List of family member names to target (for update_home_status, update_member_status)."
                                },
                                "status": {
                                    "type": "string",
                                    "description": "For update_home_status: 'Home' or 'Away'."
                                },
                                "text": {
                                    "type": "string",
                                    "description": "For update_member_status: A short text status describing what they are doing."
                                },
                                "content": {
                                    "type": "string",
                                    "description": "For add_note: The content of the note."
                                },
                                "description": {
                                    "type": "string",
                                    "description": "For add_chore: The task description."
                                },
                                "due_date": {
                                    "type": "string",
                                    "description": "For add_chore: Due date in YYYY-MM-DD format (optional)."
                                },
                                "tags": {
                                    "type": "string",
                                    "description": "For add_chore, add_shopping_item: JSON-encoded array of strings, e.g. '[\"Mom\", \"Urgent\"]' (optional)."
                                },
                                "note_id": {
                                    "type": "integer",
                                    "description": "For delete_note: The ID of the note."
                                },
                                "chore_id": {
                                    "type": "integer",
                                    "description": "For mark_chore_done, delete_chore: The ID of the chore."
                                },
                                "item": {
                                    "type": "string",
                                    "description": "For add_shopping_item: The name of the item to buy."
                                },
                                "item_id": {
                                    "type": "integer",
                                    "description": "For check_shopping_item, delete_shopping_item: The ID of the shopping item."
                                },
                                "title": {
                                    "type": "string",
                                    "description": "For add_quick_link, edit_quick_link: The display name of the link."
                                },
                                "url": {
                                    "type": "string",
                                    "description": "For add_quick_link, edit_quick_link: The URL of the link."
                                },
                                "category": {
                                    "type": "string",
                                    "description": "For add_quick_link, edit_quick_link: Grouping category or tag (e.g., 'Media', 'Dashboard')."
                                },
                                "icon_keyword": {
                                    "type": "string",
                                    "description": "For add_quick_link, edit_quick_link: Icon slug from simpleicons.org (e.g., 'netflix', 'github'). Optional."
                                },
                                "show_on_dashboard": {
                                    "type": "boolean",
                                    "description": "For add_quick_link, edit_quick_link: Whether to show the link on the dashboard grid."
                                },
                                "link_id": {
                                    "type": "integer",
                                    "description": "For delete_quick_link, edit_quick_link: The ID of the quick link."
                                },
                                "family_members": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "For update_config: List of family member names."
                                },
                                "admin_name": {
                                    "type": "string",
                                    "description": "For update_config: Name of the admin."
                                },
                                "feature_toggles": {
                                    "type": "object",
                                    "description": "For update_config: Turn features on or off.",
                                    "properties": {
                                        "shopping_list": {"type": "boolean"},
                                        "media_downloader": {"type": "boolean"},
                                        "pdf_compressor": {"type": "boolean"},
                                        "qr_generator": {"type": "boolean"},
                                        "notes": {"type": "boolean"},
                                        "shared_cloud": {"type": "boolean"},
                                        "who_is_home": {"type": "boolean"},
                                        "personal_status": {"type": "boolean"},
                                        "chores": {"type": "boolean"},
                                        "recipes": {"type": "boolean"},
                                        "expiry_tracker": {"type": "boolean"},
                                        "url_shortener": {"type": "boolean"},
                                        "expense_tracker": {"type": "boolean"}
                                    }
                                },
                                "weather": {
                                    "type": "object",
                                    "description": "For update_config: Weather widget configuration.",
                                    "properties": {
                                        "enabled": {"type": "boolean"},
                                        "label": {"type": "string", "description": "Optional location label"},
                                        "latitude": {"type": "string", "description": "Latitude. e.g., '-7.283472'"},
                                        "longitude": {"type": "string", "description": "Longitude. e.g., '109.373667'"},
                                        "timezone": {"type": "string", "description": "Timezone e.g., 'Asia/Jakarta'"},
                                        "units": {"type": "string", "enum": ["metric", "imperial"]},
                                        "view": {"type": "string", "enum": ["compact", "detailed"]}
                                    }
                                },
                                "expense_id": {
                                    "type": "integer",
                                    "description": "For edit_expense, delete_expense."
                                },
                                "rule_id": {
                                    "type": "integer",
                                    "description": "For edit_recurring_expense, delete_recurring_expense."
                                },
                                "amount": {
                                    "type": "number",
                                    "description": "For add_expense, edit_expense."
                                },
                                "unit_price": {
                                    "type": "number",
                                    "description": "For add_recurring_expense, edit_recurring_expense."
                                },
                                "date": {
                                    "type": "string",
                                    "description": "For add_expense, edit_expense: Date in YYYY-MM-DD format."
                                },
                                "payer": {
                                    "type": "string",
                                    "description": "For add_expense, edit_expense."
                                },
                                "is_paid": {
                                    "type": "boolean",
                                    "description": "For add_expense, edit_expense."
                                },
                                "frequency": {
                                    "type": "string",
                                    "enum": ["daily", "weekly", "monthly", "yearly"],
                                    "description": "For add_recurring_expense, edit_recurring_expense."
                                },
                                "monthly_mode": {
                                    "type": "string",
                                    "enum": ["day_of_month", "calendar"],
                                    "description": "For add_recurring_expense."
                                },
                                "start_date": {
                                    "type": "string",
                                    "description": "For add_recurring_expense, edit_recurring_expense: Date in YYYY-MM-DD format."
                                },
                                "end_date": {
                                    "type": "string",
                                    "description": "For add_recurring_expense, edit_recurring_expense: Date in YYYY-MM-DD format."
                                },
                                "delete_future_entries": {
                                    "type": "boolean",
                                    "description": "For delete_recurring_expense: Set to true to delete future unpaid entries generated by this rule."
                                },
                                "year": {
                                    "type": "integer",
                                    "description": "For get_expenses."
                                },
                                "month": {
                                    "type": "integer",
                                    "description": "For get_expenses."
                                },
                                "attachment_base64": {
                                    "type": "string",
                                    "description": "For expenses: receipt/bukti pembayaran per-entry. For recurring expenses: bukti kontrak/langganan (template, not per-entry)."
                                },
                                "attachment_filename": {
                                    "type": "string",
                                    "description": "Original filename with extension (e.g. 'receipt.jpg'). Required if attachment_base64 is provided."
                                }
                            }
                        }
                    },
                    "required": ["action", "parameters"]
                }
            }
        }
    ])




