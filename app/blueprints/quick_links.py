from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort, jsonify
from ..models import db, QuickLink, QuickLinkCategory
from urllib.parse import urlparse
from collections import OrderedDict

quick_links_bp = Blueprint('quick_links', __name__)

@quick_links_bp.before_request
def check_feature_toggle():
    config = current_app.config.get('HOMEHUB_CONFIG', {})
    if not config.get('feature_toggles', {}).get('quick_links', True):
        abort(404)

@quick_links_bp.route('/quick-links/reorder-links', methods=['POST'])
def reorder_links():
    data = request.get_json()
    link_ids = data.get('link_ids', [])
    new_category = data.get('category')
    
    for index, link_id in enumerate(link_ids):
        link = QuickLink.query.get(link_id)
        if link:
            link.order_index = index
            if new_category is not None:
                link.category = new_category
    db.session.commit()
    return jsonify({"success": True})

@quick_links_bp.route('/quick-links/reorder-categories', methods=['POST'])
def reorder_categories():
    data = request.get_json()
    categories = data.get('categories', [])
    for index, cat_name in enumerate(categories):
        cat = QuickLinkCategory.query.filter_by(name=cat_name).first()
        if not cat:
            cat = QuickLinkCategory(name=cat_name)
            db.session.add(cat)
        cat.order_index = index
    db.session.commit()
    return jsonify({"success": True})

@quick_links_bp.route('/quick-links', methods=['GET', 'POST'])
def manage_links():
    if request.method == 'POST':
        action = request.form.get('action')
        
        if action == 'add':
            title = request.form.get('title')
            url = request.form.get('url')
            category = request.form.get('category', 'General')
            icon_keyword = request.form.get('icon_keyword', '').strip()
            show_on_dashboard = request.form.get('show_on_dashboard') == 'on'
            
            if title and url:
                if not url.startswith('http://') and not url.startswith('https://'):
                    url = 'https://' + url
                    
                new_link = QuickLink(
                    title=title,
                    url=url,
                    category=category,
                    icon_keyword=icon_keyword,
                    show_on_dashboard=show_on_dashboard,
                    creator="User"
                )
                db.session.add(new_link)
                # Also ensure category exists
                cat = QuickLinkCategory.query.filter_by(name=category).first()
                if not cat:
                    cat = QuickLinkCategory(name=category)
                    db.session.add(cat)
                db.session.commit()
                flash('Quick Link added successfully!', 'success')
            else:
                flash('Title and URL are required!', 'error')
                
        elif action == 'delete':
            link_id = request.form.get('link_id')
            link = QuickLink.query.get(link_id)
            if link:
                db.session.delete(link)
                db.session.commit()
                flash('Quick Link deleted!', 'success')
                
        elif action == 'toggle_dashboard':
            link_id = request.form.get('link_id')
            link = QuickLink.query.get(link_id)
            if link:
                link.show_on_dashboard = not link.show_on_dashboard
                db.session.commit()
                
        elif action == 'edit':
            link_id = request.form.get('link_id')
            link = QuickLink.query.get(link_id)
            if link:
                title = request.form.get('title')
                url = request.form.get('url')
                if title and url:
                    if not url.startswith('http://') and not url.startswith('https://'):
                        url = 'https://' + url
                    link.title = title
                    link.url = url
                    link.category = request.form.get('category', 'General')
                    link.icon_keyword = request.form.get('icon_keyword', '').strip()
                    cat = QuickLinkCategory.query.filter_by(name=link.category).first()
                    if not cat:
                        cat = QuickLinkCategory(name=link.category)
                        db.session.add(cat)
                    db.session.commit()
                    flash('Quick Link updated successfully!', 'success')
                else:
                    flash('Title and URL are required!', 'error')
                
        return redirect(url_for('quick_links.manage_links'))
        
    config = current_app.config.get('HOMEHUB_CONFIG', {})
    qlinks = QuickLink.query.outerjoin(
        QuickLinkCategory, QuickLink.category == QuickLinkCategory.name
    ).order_by(QuickLinkCategory.order_index.asc().nulls_last(), QuickLink.order_index.asc()).all()
    
    grouped_links = OrderedDict()
    for ql in qlinks:
        grouped_links.setdefault(ql.category, []).append(ql)
        
    return render_template('quick_links.html', grouped_links=grouped_links, config=config)
