"""QA environment workaround: patch SSL context for uv-managed Python 3.11 on Windows.

The uv-installed Python 3.11 on Windows fails ssl.create_default_context() at
module level in aiohttp when SSL_CERT_FILE points to a non-existent or
incompatible CA bundle. This conftest sets SSL_CERT_FILE to empty before any
import that triggers aiohttp, which resolves the issue for test runs.
"""
import os
import ssl

# Fix uv Python 3.11 Windows SSL issue: empty SSL_CERT_FILE avoids the
# _ssl.c:3108 error when aiohttp creates its module-level SSL context.
if os.name == 'nt' and 'SSL_CERT_FILE' not in os.environ:
    os.environ['SSL_CERT_FILE'] = ''

# Verify SSL works after the workaround
try:
    ssl.create_default_context()
except ssl.SSLError:
    # If still failing, try setting certifi's CA bundle
    try:
        import certifi
        os.environ['SSL_CERT_FILE'] = certifi.where()
        ssl.create_default_context()
    except Exception:
        # Last resort: disable verification for test only
        os.environ['SSL_CERT_FILE'] = ''
        os.environ['CURL_CA_BUNDLE'] = ''