from flask import render_template, request, redirect, url_for, current_app, flash
from ..models import db, Recipe
from ..blueprints import main_bp
from ..security import sanitize_text, sanitize_html, is_http_url
from datetime import datetime


@main_bp.route('/recipes', methods=['GET', 'POST'])
def recipes():
    if request.method == 'POST':
        recipe_id = request.form.get('recipe_id')
        title = sanitize_text(request.form['title'])
        link = sanitize_text(request.form.get('link'))
        if link and not is_http_url(link):
            flash('Invalid link URL.', 'error')
            recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
            config = current_app.config['HOMEHUB_CONFIG']
            return render_template('recipes.html', recipes=recipes, config=config, form_title=title, form_link=link)
        ingredients = sanitize_html(request.form.get('ingredients'))
        instructions = sanitize_html(request.form.get('instructions'))
        creator = sanitize_text(request.form['creator'])
        if not (ingredients and ingredients.strip()) and not (instructions and instructions.strip()):
            flash('Please add ingredients or instructions (or both).', 'error')
            recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
            config = current_app.config['HOMEHUB_CONFIG']
            return render_template('recipes.html', recipes=recipes, config=config, form_title=title, form_link=link, form_ingredients=ingredients or '', form_instructions=instructions or '')
        if recipe_id:
            rec = Recipe.query.get_or_404(int(recipe_id))
            admin_name = current_app.config['HOMEHUB_CONFIG'].get('admin_name', 'Administrator')
            admin_aliases = {admin_name, 'Administrator', 'admin'}
            if creator in admin_aliases or creator == rec.creator:
                rec.title = title
                rec.link = link
                rec.ingredients = ingredients
                rec.instructions = instructions
                db.session.commit()
                flash('Recipe updated.', 'success')
            return redirect(url_for('main.recipes'))
        else:
            recipe = Recipe(title=title, link=link, ingredients=ingredients, instructions=instructions, creator=creator)
            db.session.add(recipe)
            db.session.commit()
            return redirect(url_for('main.recipes'))
    recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template('recipes.html', recipes=recipes, config=config)


@main_bp.route('/recipes/edit/<int:recipe_id>')
def edit_recipe(recipe_id):
    rec = Recipe.query.get_or_404(recipe_id)
    recipes = Recipe.query.order_by(Recipe.timestamp.desc()).all()
    config = current_app.config['HOMEHUB_CONFIG']
    return render_template(
        'recipes.html',
        recipes=recipes,
        config=config,
        form_title=rec.title,
        form_link=rec.link,
        form_ingredients=rec.ingredients,
        form_instructions=rec.instructions,
        form_recipe_id=rec.id,
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
    return redirect(url_for('main.recipes'))
