"""Start the mock API from Supervisor options without printing credentials."""
import json
import os
from pathlib import Path
import sys


def main():
    options = json.loads(Path("/data/options.json").read_text())
    api = options.get("api_token", "")
    approval = options.get("approval_token", "")
    if len(api) < 24 or len(approval) < 24 or api == approval:
        sys.exit("Configure two distinct mock tokens, each at least 24 characters.")
    os.environ["MOCK_API_TOKEN"] = api
    os.environ["MOCK_APPROVAL_TOKEN"] = approval
    os.environ["MOCK_DB"] = "/data/wallet.sqlite3"
    # This add-on never needs Supervisor, host, device or HA API privileges.
    os.environ.pop("SUPERVISOR_TOKEN", None)
    os.umask(0o077)
    os.execvp("python", [
        "python", "-m", "uvicorn", "wallet_service.app:create_app",
        "--factory", "--host", "0.0.0.0", "--port", "8091",
    ])


if __name__ == "__main__":
    main()
