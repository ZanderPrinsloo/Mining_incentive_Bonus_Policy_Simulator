"""Start the Harmony Bonus Policy Simulator web app.

Local development (default): Flask's own dev server, with the interactive
debugger on — fine on a developer's own machine, never safe to expose to
anyone else (it allows arbitrary code execution through the debugger).

    python run_web.py

Deployed on Harmony's server: set APP_ENV=production in .env (or the real
environment) to serve through waitress instead — a plain WSGI server, no
debugger, safe to leave running and reachable over the network. Same command,
same codebase, just a different .env — matching how the Doornkop/Phakisa
dashboards run in both places too.

    APP_ENV=production python run_web.py

APP_HOST/APP_PORT (both optional) control the bind address in either mode —
default 127.0.0.1:5050 for local dev, 0.0.0.0:5050 for production (so it's
reachable from other machines on Harmony's network out of the box).
"""
import os

from web.app import create_app

if __name__ == "__main__":
    app = create_app()
    is_production = os.environ.get("APP_ENV", "development").strip().lower() == "production"
    default_host = "0.0.0.0" if is_production else "127.0.0.1"
    host = os.environ.get("APP_HOST", default_host)
    port = int(os.environ.get("APP_PORT", "5050"))

    if is_production:
        from waitress import serve
        print(f"Bonus Policy Simulator (production) running at http://{host}:{port}")
        serve(app, host=host, port=port)
    else:
        print(f"Bonus Policy Simulator running at http://{host}:{port}")
        app.run(debug=True, host=host, port=port)
