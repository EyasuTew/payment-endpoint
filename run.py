"""
Local development entry point.

Usage:
    flask --app run.py run
or simply:
    python run.py
"""

from dotenv import load_dotenv

load_dotenv()  # populate os.environ from a local .env file, if present

from app import create_app  # noqa: E402  (import after load_dotenv on purpose)

app = create_app()

if __name__ == "__main__":
    # app.run(debug=app.config.get("DEBUG", False))
    app.run(host="0.0.0.0", debug=app.config.get("DEBUG", False), port=5000)
