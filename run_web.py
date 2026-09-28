"""Start the Bonus Policy Simulator web app (local development).

Flask's own dev server, with the interactive debugger on — fine on a
developer's own machine, never safe to expose to anyone else.

For deployment on Harmony's server, use run_server.py (foreground) or
run_service.py (Windows service) instead — see DEPLOY.md.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.app import create_app

if __name__ == "__main__":
    app = create_app()
    print("Bonus Policy Simulator running at http://localhost:5050")
    app.run(debug=True, port=5050)
