"""API operations for the household lists: shopping, chores, notes, recipes and the expiry tracker."""
import json
from datetime import date

from flask_babel import gettext as _

from .. import admin

from ..blueprints import chores as chores_web
from ..blueprints import expiry as expiry_web
from ..blueprints import recipes as recipes_web
from ..blueprints import shopping as shopping_web
from ..models import Chore, ExpiryItem, Note, Recipe, RecurringChore, ShoppingItem, db
from ..security import sanitize_html, sanitize_tags, sanitize_text
from .core import ApiError
from .home import day_label, nothing_to_change
from .registry import iso, operation, tags_property


def _text(value: str, what: str) -> str:
    cleaned = sanitize_text(value)
    if not cleaned:
        raise ApiError(400, 'bad_request', _('%(field)s cannot be empty.', field=what))
    return cleaned


def _get(model, item_id, message):
    row = db.session.get(model, item_id)
    if row is None:
        raise ApiError(404, 'not_found', message)
    return row


# --- Shopping list ---

@operation('shopping_list', tool='shopping', level='read', method='GET', path='/shopping',
           title='Shopping list',
           description='Every item on the shopping list with its tags and whether it is ticked off. Unticked items come first.',
           idempotent=True)
def shopping_list(ctx, args):
    items = ShoppingItem.query.order_by(ShoppingItem.checked.asc(), ShoppingItem.timestamp.desc()).all()
    return {'items': [shopping_web.shopping_item_dict(i) for i in items]}


@operation('shopping_add', tool='shopping', level='write', method='POST', path='/shopping',
           title='Add to the shopping list',
           description='Add an item to the shopping list, optionally with tags such as a shop or an aisle.',
           properties={'item': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'What to buy, for example "Milk".'},
                       'tags': tags_property()},
           required=('item',), success_status=201)
def shopping_add(ctx, args):
    item = _text(args['item'], 'item')
    row = shopping_web.add_shopping_item(item, ctx.user, sanitize_tags(args.get('tags') or []))
    ctx.target = f'#{row.id} {item}'
    return {**shopping_web.shopping_item_dict(row), 'summary': _('Added to the shopping list: %(item)s.', item=item)}


@operation('shopping_set_checked', tool='shopping', level='write', method='PUT', path='/shopping/<int:item_id>/checked',
           title='Tick or untick a shopping item',
           description='Tick an item off (true) or put it back on the list (false). Setting the same value again changes nothing.',
           properties={'item_id': {'type': 'integer', 'in': 'path', 'description': 'The item id from the list.'},
                       'checked': {'type': 'boolean', 'in': 'body', 'description': 'true to tick it off, false to untick it.'}},
           required=('item_id', 'checked'), idempotent=True)
def shopping_set_checked(ctx, args):
    row = _get(ShoppingItem, args['item_id'], _('That item was not found.'))
    row.checked = args['checked']
    db.session.commit()
    ctx.target = f'#{row.id} {row.item}'
    summary = (_('%(item)s ticked off.', item=row.item) if row.checked
               else _('%(item)s is back on the shopping list.', item=row.item))
    return {**shopping_web.shopping_item_dict(row), 'summary': summary}


# --- Chores ---

def _chore(chore: Chore, rules: dict, today: date) -> dict:
    data = chores_web.chore_dict(chore)
    rule = rules.get(chore.recurring_id) if chore.recurring_id else None
    # A recurring chore ticked off early waits on its next date; it counts as done until then
    data['done'] = bool(chore.done) or (rule is not None and chores_web._ticked_for_now(chore, rule, today))
    data['timestamp'] = iso(chore.timestamp)
    return data


@operation('chores_list', tool='chores', level='read', method='GET', path='/chores',
           title='Chores',
           description=('Every chore with its due date, tags and whether it is done. Recurring chores have a recurring_id; '
                        'one ticked off for the current round counts as done until its next date.'),
           idempotent=True)
def chores_list(ctx, args):
    chores_web._ensure_current_recurring_chores(date.today())
    items = Chore.query.order_by(Chore.done.asc(), Chore.due_date.asc(), Chore.timestamp.desc()).all()
    rules = {r.id: r for r in RecurringChore.query.all()}
    today = date.today()
    return {'chores': [_chore(c, rules, today) for c in items]}


@operation('chores_add', tool='chores', level='write', method='POST', path='/chores',
           title='Add a chore',
           description='Add a one-off chore, optionally with a due date and tags (for example who it is for). Recurring chores are set up in HomeHub.',
           properties={'description': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 1000, 'description': 'What needs doing.'},
                       'due_date': {'type': 'string', 'format': 'date', 'in': 'body', 'description': 'Optional due date, YYYY-MM-DD.'},
                       'tags': tags_property('Tags, for example ["Priya", "Weekend"].')},
           required=('description',), success_status=201)
def chores_add(ctx, args):
    description = _text(args['description'], 'description')
    chore = Chore(description=description, creator=ctx.user, tags=json.dumps(sanitize_tags(args.get('tags') or [])),
                  due_date=args.get('due_date'))
    db.session.add(chore)
    db.session.commit()
    ctx.target = f'#{chore.id} {description}'
    due = args.get('due_date')
    summary = (_('Chore added: %(chore)s, due %(date)s.', chore=description, date=day_label(due)) if due
               else _('Chore added: %(chore)s.', chore=description))
    return {**_chore(chore, {}, date.today()), 'summary': summary}


def _current_chore(chore_id: int):
    """The chore after recurring chores are brought up to date, as the Chores page does on load.

    Returns (chore, its rule or None). An old copy of a recurring chore is refused, so the
    current round is the one that changes.
    """
    chores_web._ensure_current_recurring_chores(date.today())
    chore = _get(Chore, chore_id, _('That chore was not found.'))
    rule = db.session.get(RecurringChore, chore.recurring_id) if chore.recurring_id else None
    if rule is not None:
        active = chores_web.active_recurring_chore(rule.id)
        if active is not None and active.id != chore.id:
            raise ApiError(409, 'conflict', _('This is an old copy of the recurring chore %(chore)s. Use chore %(id)s, the current one.',
                                              chore=chore.description, id=active.id))
    return chore, rule


@operation('chores_set_done', tool='chores', level='write', method='PUT', path='/chores/<int:chore_id>/done',
           title='Mark a chore done or not done',
           description=('Set a chore done (true) or not done (false); setting the same value again changes nothing. '
                        'A recurring chore works as in HomeHub: done moves it to its next date, not done takes that back.'),
           properties={'chore_id': {'type': 'integer', 'in': 'path', 'description': 'The chore id from the list.'},
                       'done': {'type': 'boolean', 'in': 'body', 'description': 'true for done, false for not done.'}},
           required=('chore_id', 'done'), idempotent=True)
def chores_set_done(ctx, args):
    chore, rule = _current_chore(args['chore_id'])
    today = date.today()
    want = args['done']
    name = chore.description
    if rule is not None and chore.due_date:
        ticked = chores_web.recurring_chore_ticked(chore, rule, today)
        if want and ticked:
            summary = (_('%(chore)s is already done. Next due on %(date)s.', chore=name, date=day_label(chore.due_date))
                       if not chore.done else _('%(chore)s is already done.', chore=name))
        elif want:
            _before, next_due = chores_web.tick_recurring_chore(chore, rule)
            summary = (_('%(chore)s done. That was its last date.', chore=name) if next_due is None
                       else _('%(chore)s done. Next due on %(date)s.', chore=name, date=day_label(next_due)))
        elif ticked:
            reopened = chores_web.reopen_recurring_chore(chore, rule, today)
            if reopened is None:
                raise ApiError(409, 'conflict', _('This recurring chore has ended.'))
            summary = _('%(chore)s is open again. Due on %(date)s.', chore=name, date=day_label(reopened))
        else:
            summary = _('%(chore)s is not done.', chore=name)
    else:
        chore.done = want
        db.session.commit()
        summary = _('%(chore)s done.', chore=name) if want else _('%(chore)s is not done.', chore=name)
    ctx.target = f'#{chore.id} {name}'
    rules = {rule.id: rule} if rule is not None else {}
    return {**_chore(chore, rules, today), 'summary': summary}


# --- Notes ---

def _note(n: Note) -> dict:
    return {'id': n.id, 'content': n.content, 'creator': n.creator, 'timestamp': iso(n.timestamp)}


@operation('notes_list', tool='notes', level='read', method='GET', path='/notes',
           title='Notes',
           description='The shared notes, newest first (simple HTML).',
           idempotent=True)
def notes_list(ctx, args):
    return {'notes': [_note(n) for n in Note.query.order_by(Note.timestamp.desc()).all()]}


@operation('notes_add', tool='notes', level='write', method='POST', path='/notes',
           title='Add a note',
           description='Add a shared note. Simple HTML is kept (bold, lists, links); anything else is removed.',
           properties={'content': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 20000, 'description': 'The note.'}},
           required=('content',), success_status=201)
def notes_add(ctx, args):
    content = sanitize_html(args['content'])
    if not content.strip():
        raise ApiError(400, 'bad_request', _('%(field)s cannot be empty.', field='content'))
    note = Note(content=content, creator=ctx.user)
    db.session.add(note)
    db.session.commit()
    ctx.target = f'#{note.id} {sanitize_text(content)[:80]}'
    return {**_note(note), 'summary': _('Note added.')}


# --- Recipes ---

def _recipe(r: Recipe) -> dict:
    return {
        'id': r.id, 'title': r.title, 'link': r.link or None, 'ingredients': r.ingredients or '',
        'instructions': r.instructions or '', 'tags': shopping_web.item_tags(r.tags), 'creator': r.creator,
        'timestamp': iso(r.timestamp),
    }


@operation('recipes_list', tool='recipes', level='read', method='GET', path='/recipes',
           title='Recipes',
           description='Every recipe with its link, ingredients, instructions (simple HTML) and tags, newest first.',
           idempotent=True)
def recipes_list(ctx, args):
    return {'recipes': [_recipe(r) for r in Recipe.query.order_by(Recipe.timestamp.desc()).all()]}


@operation('recipes_add', tool='recipes', level='write', method='POST', path='/recipes',
           title='Add a recipe',
           description='Add a recipe. It needs ingredients or instructions (or both); a link must be an http or https address.',
           properties={'title': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'The recipe name.'},
                       'link': {'type': 'string', 'in': 'body', 'maxLength': 512, 'description': 'Optional web address of the recipe.'},
                       'ingredients': {'type': 'string', 'in': 'body', 'maxLength': 20000, 'description': 'Ingredients (simple HTML allowed).'},
                       'instructions': {'type': 'string', 'in': 'body', 'maxLength': 20000, 'description': 'Instructions (simple HTML allowed).'},
                       'tags': tags_property('Tags, for example ["Dessert", "Quick"].')},
           required=('title',), success_status=201)
def recipes_add(ctx, args):
    title = _text(args['title'], 'title')
    link = sanitize_text(args.get('link') or '')
    if not recipes_web.recipe_link_ok(link):
        raise ApiError(400, 'bad_request', _('Invalid link URL.'))
    ingredients = sanitize_html(args.get('ingredients', ''))
    instructions = sanitize_html(args.get('instructions', ''))
    if not recipes_web.recipe_has_body(ingredients, instructions):
        raise ApiError(400, 'bad_request', _('Please add ingredients or instructions (or both).'))
    recipe = Recipe(title=title, link=link, ingredients=ingredients, instructions=instructions, creator=ctx.user,
                    tags=json.dumps(sanitize_tags(args.get('tags') or [])))
    db.session.add(recipe)
    db.session.commit()
    ctx.target = f'#{recipe.id} {title}'
    return {**_recipe(recipe), 'summary': _('Recipe added: %(title)s.', title=title)}


# --- Expiry tracker ---

def _expiry(item: ExpiryItem, today: date) -> dict:
    return {'id': item.id, 'name': item.name, 'expiry_date': iso(item.expiry_date),
            'days_left': expiry_web.days_left(item, today), 'creator': item.creator, 'timestamp': iso(item.timestamp)}


@operation('expiry_list', tool='expiry', level='read', method='GET', path='/expiry',
           title='Expiry tracker',
           description='Tracked items with their expiry date and days left (negative once expired), soonest first.',
           idempotent=True)
def expiry_list(ctx, args):
    today = date.today()
    return {'items': [_expiry(i, today) for i in ExpiryItem.query.order_by(ExpiryItem.expiry_date.asc()).all()]}


@operation('expiry_add', tool='expiry', level='write', method='POST', path='/expiry',
           title='Track an expiry date',
           description='Add an item with the date it expires.',
           properties={'name': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'The item, for example "Yoghurt".'},
                       'expiry_date': {'type': 'string', 'format': 'date', 'in': 'body', 'description': 'When it expires, YYYY-MM-DD.'}},
           required=('name', 'expiry_date'), success_status=201)
def expiry_add(ctx, args):
    name = _text(args['name'], 'name')
    item = ExpiryItem(name=name, expiry_date=args['expiry_date'], creator=ctx.user)
    db.session.add(item)
    db.session.commit()
    ctx.target = f'#{item.id} {name}'
    return {**_expiry(item, date.today()),
            'summary': _('Added %(name)s, expires %(date)s.', name=name, date=day_label(item.expiry_date))}


# --- Edits (only where the web app has an edit, with the same ownership rule) ---

def _owned(ctx, owner, message):
    """403 unless the acting person may change a record owned by ``owner`` (the web's rule)."""
    if not admin.can_modify(ctx.user, owner or ''):
        raise ApiError(403, 'forbidden', message)


def _edit_tags(args):
    """Tags sent with an edit replace the list; blank entries are dropped, as when adding."""
    if 'tags' not in args:
        return None
    return [t for t in args['tags'] if t.strip()]


def _tag_note(tags) -> str:
    return f" ({', '.join(tags)})" if tags else ''


@operation('shopping_edit', tool='shopping', level='write', method='PATCH', path='/shopping/<int:item_id>',
           title='Edit a shopping item',
           description='Change an item\'s text and/or its tags (the list sent replaces the old one). Only whoever added the item may change it.',
           properties={'item_id': {'type': 'integer', 'in': 'path', 'description': 'The item id from the list.'},
                       'item': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'New text.'},
                       'tags': tags_property('New tags, replacing the old ones, for example ["Costco", "Bakery"].')},
           required=('item_id',), idempotent=True)
def shopping_edit(ctx, args):
    if 'item' not in args and 'tags' not in args:
        raise nothing_to_change()
    row = _get(ShoppingItem, args['item_id'], _('That item was not found.'))
    _owned(ctx, row.creator, _('%(name)s cannot change this item.', name=ctx.user))
    new_item = args['item'] if 'item' in args and _text(args['item'], 'item') else None
    tags = shopping_web.edit_shopping_item(row, new_item, _edit_tags(args))
    ctx.target = f'#{row.id} {row.item}'
    return {**shopping_web.shopping_item_dict(row),
            'summary': _('Shopping item updated: %(item)s%(tags)s.', item=row.item, tags=_tag_note(tags))}


@operation('chores_edit', tool='chores', level='write', method='PATCH', path='/chores/<int:chore_id>',
           title='Edit a chore',
           description=('Change a one-off chore: description, due date (null clears it) and/or tags (the list sent replaces the old one). '
                        'Only whoever added the chore may change it. Recurring chores are changed in HomeHub.'),
           properties={'chore_id': {'type': 'integer', 'in': 'path', 'description': 'The chore id from the list.'},
                       'description': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 1000, 'description': 'New description.'},
                       'due_date': {'type': 'string', 'format': 'date', 'in': 'body', 'x-nullable': True,
                                    'description': 'New due date, YYYY-MM-DD, or null for none.'},
                       'tags': tags_property('New tags, replacing the old ones.')},
           required=('chore_id',), idempotent=True)
def chores_edit(ctx, args):
    if not any(k in args for k in ('description', 'due_date', 'tags')):
        raise nothing_to_change()
    chores_web._ensure_current_recurring_chores(date.today())
    chore = _get(Chore, args['chore_id'], _('That chore was not found.'))
    if chore.recurring_id:
        raise ApiError(409, 'conflict', _('This chore belongs to a recurring chore. Recurring chores are changed in HomeHub.'))
    _owned(ctx, chore.creator, _('%(name)s cannot change this chore.', name=ctx.user))
    description = args['description'] if 'description' in args and _text(args['description'], 'description') else None
    if 'due_date' in args:
        chore.due_date = args['due_date']
    chores_web.update_chore_fields(chore, description, _edit_tags(args))
    ctx.target = f'#{chore.id} {chore.description}'
    due = (_(', due %(date)s', date=day_label(chore.due_date)) if chore.due_date else '')
    return {**_chore(chore, {}, date.today()),
            'summary': _('Chore updated: %(chore)s%(due)s.', chore=chore.description, due=due)}


@operation('recipes_edit', tool='recipes', level='write', method='PATCH', path='/recipes/<int:recipe_id>',
           title='Edit a recipe',
           description=('Change a recipe: any of title, link, ingredients, instructions and tags (the list sent replaces the old one). '
                        'It must still have ingredients or instructions, and a link must be an http or https address. '
                        'Only whoever added the recipe may change it.'),
           properties={'recipe_id': {'type': 'integer', 'in': 'path', 'description': 'The recipe id from the list.'},
                       'title': {'type': 'string', 'in': 'body', 'minLength': 1, 'maxLength': 256, 'description': 'New name.'},
                       'link': {'type': 'string', 'in': 'body', 'maxLength': 512, 'description': 'New web address; empty removes it.'},
                       'ingredients': {'type': 'string', 'in': 'body', 'maxLength': 20000, 'description': 'New ingredients (simple HTML allowed).'},
                       'instructions': {'type': 'string', 'in': 'body', 'maxLength': 20000, 'description': 'New instructions (simple HTML allowed).'},
                       'tags': tags_property('New tags, replacing the old ones.')},
           required=('recipe_id',), idempotent=True)
def recipes_edit(ctx, args):
    if not any(k in args for k in ('title', 'link', 'ingredients', 'instructions', 'tags')):
        raise nothing_to_change()
    recipe = _get(Recipe, args['recipe_id'], _('That recipe was not found.'))
    _owned(ctx, recipe.creator, _('%(name)s cannot change this recipe.', name=ctx.user))
    title = _text(args['title'], 'title') if 'title' in args else recipe.title
    link = sanitize_text(args['link']) if 'link' in args else (recipe.link or '')
    if not recipes_web.recipe_link_ok(link):
        raise ApiError(400, 'bad_request', _('Invalid link URL.'))
    ingredients = sanitize_html(args['ingredients']) if 'ingredients' in args else (recipe.ingredients or '')
    instructions = sanitize_html(args['instructions']) if 'instructions' in args else (recipe.instructions or '')
    if not recipes_web.recipe_has_body(ingredients, instructions):
        raise ApiError(400, 'bad_request', _('Please add ingredients or instructions (or both).'))
    recipe.title = title
    recipe.link = link
    recipe.ingredients = ingredients
    recipe.instructions = instructions
    tags = _edit_tags(args)
    if tags is not None:
        recipe.tags = json.dumps(sanitize_tags(tags))
    db.session.commit()
    ctx.target = f'#{recipe.id} {title}'
    return {**_recipe(recipe), 'summary': _('Recipe updated: %(title)s.', title=title)}
