from app import create_app, db
from flask.helpers import get_debug_flag
import os

app = create_app()

if __name__ == '__main__':
    # Debug mode (reloader and in-browser debugger) only when asked for with FLASK_DEBUG=1
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)), debug=get_debug_flag())
