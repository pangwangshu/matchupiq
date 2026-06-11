from __future__ import annotations

try:
    from src.ui import render_admin_controls
except ModuleNotFoundError:
    from ui import render_admin_controls


render_admin_controls()
