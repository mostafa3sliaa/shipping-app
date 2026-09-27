import sys
import os
import traceback

# Ensure root directory is on sys.path
root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

try:
    from app import app
except Exception:
    err = traceback.format_exc()
    def app(environ, start_response):
        start_response('200 OK', [('Content-Type', 'text/plain; charset=utf-8')])
        return [f"APPLICATION STARTUP ERROR:\n\n{err}".encode('utf-8')]
