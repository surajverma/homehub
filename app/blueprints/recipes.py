from flask import render_template, request, redirect, url_for, current_app, flash, jsonify
from ..models import db, Recipe
from ..blueprints import main_bp
from ..security import sanitize_text, sanitize_html, is_http_url
import json


@main_bp.route('/recipes', methods=['GET', 'POST'])
def recipes():
    if request.method == 'POST':
        recipe_id = request.form.get('recipe_id')
        title = sanitize_text(request.form['title'])
        link = sanitize_text(request.form.get('link'))
        if link and not is_http_url(link):
            flash('Invalid link URL.', 'error')
            recipes_list = Recipe.query.order_by(Recipe.timestamp.desc()).all()
            config = current_app.config['HOMEHUB_CONFIG']
            return render_template('recipes.html', recipes=recipes_list, config=config, form_title=title, form_link=link)
        ingredients = sanitize_html(request.form.get('ingredients', ''))
        instructions = sanitize_html(request.form.get('instructions', ''))
        creator = sanitize_text(request.form['creator'])
        
        # Handle tags (JSON array)
        raw_tags = request.form.get('tags', '').strip()
        tags_list = []
        if raw_tags:
            try:
                tags_list = json.loads(raw_tags)
                if not isinstance(tags_list, list):
                    tags_list = []
            except Exception:
                tags_list = [t.strip() for t in raw_tags.split(',') if t.strip()]
        tags_list = [sanitize_text(t) for t in tags_list if isinstance(t, str) and t.strip()]
        
        if not (ingredients and ingredients.strip()) and not (instructions and instructions.strip()):
            flash('Please add ingredients or instructions (or both).', 'error')
            recipes_list = Recipe.query.order_by(Recipe.timestamp.desc()).all()
            config = current_app.config['HOMEHUB_CONFIG']
            return render_template('recipes.html', recipes=recipes_list, config=config, form_title=title, form_link=link, form_ingredients=ingredients or '', form_instructions=instructions or '')
        
        if recipe_id:
            rec = Recipe.query.get_or_404(int(recipe_id))
            admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
            admin_aliases = {admin_name, 'Administrator', 'admin'}
            if creator in admin_aliases or creator == rec.creator:
                rec.title = title
                rec.link = link
                rec.ingredients = ingredients
                rec.instructions = instructions
                rec.tags = json.dumps(tags_list)
                db.session.commit()
                flash('Recipe updated.', 'success')
            return redirect(url_for('main.recipes'))
        else:
            recipe = Recipe(title=title, link=link, ingredients=ingredients, instructions=instructions, creator=creator, tags=json.dumps(tags_list))
            db.session.add(recipe)
            db.session.commit()
            flash('Recipe added.', 'success')
            return redirect(url_for('main.recipes'))
    
    # Filter by tags if provided
    filter_tags = request.args.get('tags')
    recipes_list = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    if filter_tags:
        try:
            selected = json.loads(filter_tags)
            if isinstance(selected, list) and selected:
                def match(item_tags):
                    try:
                        arr = json.loads(item_tags or '[]')
                    except Exception:
                        arr = []
                    return any(t in arr for t in selected)
                recipes_list = [r for r in recipes_list if match(r.tags)]
        except Exception:
            pass
    
    # Convert Recipe objects to dictionaries for JSON serialization in template
    recipes_dicts = []
    for r in recipes_list:
        recipes_dicts.append({
            'id': r.id,
            'title': r.title,
            'link': r.link,
            'ingredients': r.ingredients,
            'instructions': r.instructions,
            'creator': r.creator,
            'timestamp': r.timestamp.isoformat() if r.timestamp else None,
            'tags': r.tags or '[]'
        })
    
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('recipes.html', recipes=recipes_list, recipes_json=recipes_dicts, config=config)


@main_bp.route('/recipes/edit/<int:recipe_id>')
def edit_recipe(recipe_id):
    rec = Recipe.query.get_or_404(recipe_id)
    recipes_list = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    
    # Convert Recipe objects to dictionaries for JSON serialization
    recipes_dicts = []
    for r in recipes_list:
        recipes_dicts.append({
            'id': r.id,
            'title': r.title,
            'link': r.link,
            'ingredients': r.ingredients,
            'instructions': r.instructions,
            'creator': r.creator,
            'timestamp': r.timestamp.isoformat() if r.timestamp else None,
            'tags': r.tags or '[]'
        })
    
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template(
        'recipes.html',
        recipes=recipes_list,
        recipes_json=recipes_dicts,
        config=config,
        form_title=rec.title,
        form_link=rec.link,
        form_ingredients=rec.ingredients,
        form_instructions=rec.instructions,
        form_recipe_id=rec.id,
        form_tags=rec.tags or '[]',
    )


@main_bp.route('/recipes/delete/<int:recipe_id>', methods=['POST'])
def delete_recipe(recipe_id):
    recipe = Recipe.query.get_or_404(recipe_id)
    user = sanitize_text(request.form['user'])
    admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
    admin_aliases = {admin_name, 'Administrator', 'admin'}
    if user in admin_aliases or user == recipe.creator:
        db.session.delete(recipe)
        db.session.commit()
        flash('Recipe deleted.', 'success')
    return redirect(url_for('main.recipes'))


@main_bp.route('/api/recipes/<int:recipe_id>/tags', methods=['POST'])
def update_recipe_tags(recipe_id):
    """Update tags for a recipe via API (similar to chores/shopping)"""
    recipe = Recipe.query.get_or_404(recipe_id)
    try:
        data = request.get_json(force=True) or {}
        tags = data.get('tags', [])
        if not isinstance(tags, list):
            tags = []
        cleaned = []
        for t in tags:
            if isinstance(t, str):
                cleaned.append(sanitize_text(t))
        recipe.tags = json.dumps(cleaned)
        db.session.commit()
        return jsonify({"ok": True, "id": recipe.id, "tags": cleaned})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@main_bp.route('/api/recipes', methods=['GET'])
def api_get_recipes():
    """Get recipes with optional tag filtering"""
    tags = request.args.get('tags')
    items = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    if tags:
        try:
            sel = json.loads(tags)
            if isinstance(sel, list) and sel:
                def match(item_tags):
                    try:
                        arr = json.loads(item_tags or '[]')
                    except Exception:
                        arr = []
                    return any(t in arr for t in sel)
                items = [i for i in items if match(i.tags)]
        except Exception:
            pass
    
    result = []
    for r in items:
        try:
            tags_arr = json.loads(r.tags or '[]')
        except Exception:
            tags_arr = []
        result.append({
            'id': r.id,
            'title': r.title,
            'link': r.link,
            'creator': r.creator,
            'timestamp': r.timestamp.isoformat() if r.timestamp else None,
            'tags': tags_arr,
            'ingredients': r.ingredients,
            'instructions': r.instructions,
        })
    return jsonify(result)
