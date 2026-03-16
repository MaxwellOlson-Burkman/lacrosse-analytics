"""WSGI config for lacrosse_site."""
import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lacrosse_site.settings")
application = get_wsgi_application()
