from flask import Blueprint, render_template, request, redirect, url_for, flash, current_app, abort
from ..models import db, QuickLink
from urllib.parse import urlparse

quick_links_bp = Blueprint('quick_links', __name__)

@quick_links_bp.before_request
def check_feature_toggle():
    config = current_app.config.get('HOMEHUB_CONFIG', {})
    if not config.get('feature_toggles', {}).get('quick_links', True):
        abort(404)

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
                # Ensure URL has scheme
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
                    db.session.commit()
                    flash('Quick Link updated successfully!', 'success')
                else:
                    flash('Title and URL are required!', 'error')
                
        return redirect(url_for('quick_links.manage_links'))
        
    config = current_app.config.get('HOMEHUB_CONFIG', {})
    links = QuickLink.query.order_by(QuickLink.category, QuickLink.title).all()
    return render_template('quick_links.html', links=links, config=config)
