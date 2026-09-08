"""QA conftest: SSL workaround for uv Python 3.11 on Windows.

The uv-managed Python 3.11 on Windows fails ssl.create_default_context()
when SSL_CERT_FILE is set to an empty string (common in MSYS/Git Bash environments).
This causes aiohttp module-level initialization to crash with _ssl.c:3108 error.

Fix: Set SSL_CERT_FILE to certifi's CA bundle path before any SSL-dependent
module (aiohttp, openai) is imported. Must run before collection.
"""
import os
import sys

if os.name == 'nt' and os.environ.get('SSL_CERT_FILE', None) == '':
    try:
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
    except ImportError:
        # Remove the empty value so Python falls back to its default
        del os.environ['SSL_CERT_FILE']

# Ensure webui and chatbot are importable from test dirs
root = os.path.dirname(os.path.abspath(__file__))
for subdir in ('webui', 'chatbot'):
    p = os.path.join(root, subdir)
    if p not in sys.path:
        sys.path.insert(0, p)