"""
Entry point for the Stock Tracker day-trading research platform.
Run with: python run.py
"""
import os
import ssl
import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())

# macOS Python 3.10 doesn't trust the system CA store by default.
# Patch ssl.create_default_context so all outbound TLS (websockets, requests)
# uses the certifi bundle.
_orig_create_default_context = ssl.create_default_context

def _patched_create_default_context(purpose=ssl.Purpose.SERVER_AUTH, **kwargs):
    if "cafile" not in kwargs and "capath" not in kwargs and "cadata" not in kwargs:
        kwargs["cafile"] = certifi.where()
    return _orig_create_default_context(purpose, **kwargs)

ssl.create_default_context = _patched_create_default_context

import uvicorn
from app.config import settings
from app.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    uvicorn.run(
        "app.main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.APP_ENV == "development",
        log_level=settings.LOG_LEVEL.lower(),
        access_log=True,
    )
