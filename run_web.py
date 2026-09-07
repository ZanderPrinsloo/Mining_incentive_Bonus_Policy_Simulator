"""Start the Harmony Bonus Policy Simulator web app."""
from web.app import create_app

if __name__ == "__main__":
    app = create_app()
    print("Bonus Policy Simulator running at http://localhost:5050")
    app.run(debug=True, port=5050)
