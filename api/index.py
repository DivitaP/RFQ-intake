"""Vercel Python function: serves the FastAPI app from backend/ at /api/*.

The static frontend (frontend/dist) and this function deploy as ONE Vercel project, so the
browser calls same-origin /api/... and there's no CORS or second host to manage.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.main import app  # noqa: E402,F401
