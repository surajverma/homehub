from flask import Blueprint, render_template, request, redirect, url_for, flash, send_from_directory, jsonify, current_app, session
from .config import load_config
from threading import Thread
from .models import db, Note, File, Media, PDF, ShoppingItem, GroceryHistory, HomeStatus, Chore, Recipe, ExpiryItem, ShortURL, QRCode, Notice, Reminder, MemberStatus, RecurringExpense, ExpenseEntry
from .utils import generate_short_code
"""
Legacy routes file kept for backward-compatibility of imports.
All routes have been refactored into blueprint modules under app.blueprints.
"""