import sqlite3
import os

db_path = os.path.join('data', 'app.db')

if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute('ALTER TABLE quick_link ADD COLUMN order_index INTEGER DEFAULT 0')
        print('Added order_index to quick_link')
    except sqlite3.OperationalError as e:
        print('Column order_index probably already exists:', e)
        
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS quick_link_category (id INTEGER PRIMARY KEY, name VARCHAR(64) UNIQUE NOT NULL, order_index INTEGER DEFAULT 0)')
        print('Created quick_link_category table')
    except Exception as e:
        print('Error creating table:', e)
        
    # Populate quick_link_category with existing categories from quick_link
    try:
        cursor = conn.execute('SELECT DISTINCT category FROM quick_link WHERE category IS NOT NULL AND category != ""')
        categories = cursor.fetchall()
        for i, (cat,) in enumerate(categories):
            try:
                conn.execute('INSERT INTO quick_link_category (name, order_index) VALUES (?, ?)', (cat, i))
            except sqlite3.IntegrityError:
                pass # Already exists
        print('Populated existing categories.')
    except Exception as e:
        print('Error populating categories:', e)
        
    conn.commit()
    conn.close()
    print('Done migrating.')
else:
    print(f'Database not found at {db_path}')
