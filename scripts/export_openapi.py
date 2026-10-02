"""Generate the API schema without creating persistent service state."""
import json
from pathlib import Path
import secrets
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wallet_service.app import create_app

with tempfile.TemporaryDirectory() as tmp:
    app = create_app(Path(tmp) / "schema.sqlite3", secrets.token_urlsafe(32), secrets.token_urlsafe(32))
    (ROOT / "openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
