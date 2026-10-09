"""Gunicorn entry point; invalid configuration prevents boot."""
from production_app import create_application
application = create_application()
