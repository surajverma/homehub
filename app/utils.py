import random
import string
import os
import uuid
from werkzeug.utils import secure_filename
from PIL import Image

def generate_short_code(length=6):
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))

# Add more utility functions as needed

def handle_expense_attachment(file_obj, upload_folder):
    """
    Validates, compresses, and saves an uploaded expense attachment (image only).
    Returns the relative path to be stored in the database, e.g. 'uploads/expenses/abc-123.jpg'
    """
    if not file_obj or not file_obj.filename:
        return None
        
    # Check extension
    filename = secure_filename(file_obj.filename)
    ext = os.path.splitext(filename)[1].lower()
    allowed_exts = {'.png', '.jpg', '.jpeg', '.webp'}
    if ext not in allowed_exts:
        return None  # Or raise an exception, but for now we'll just ignore invalid files

    # Create directory if it doesn't exist
    expenses_upload_dir = os.path.join(upload_folder, 'expenses')
    os.makedirs(expenses_upload_dir, exist_ok=True)
    
    # Generate unique filename
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(expenses_upload_dir, unique_filename)
    
    # Try to compress using Pillow
    try:
        img = Image.open(file_obj)
        # Convert to RGB if it has alpha channel (for JPEG compatibility)
        if img.mode in ('RGBA', 'P') and ext in ('.jpg', '.jpeg'):
            img = img.convert('RGB')
        
        # Resize if too large
        max_size = (1200, 1200)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        
        # Save with compression
        save_kwargs = {}
        if ext in ('.jpg', '.jpeg'):
            save_kwargs = {'quality': 80, 'optimize': True}
        elif ext == '.webp':
            save_kwargs = {'quality': 80}
            
        img.save(file_path, **save_kwargs)
        
        # Return the relative path from the app root
        return f"uploads/expenses/{unique_filename}"
        
    except Exception as e:
        print(f"Error processing image: {e}")
        return None
