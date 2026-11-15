"""Starts the app on this computer only. Add --public to try it as the hosted public site."""
import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--public"]
    if "--public" in sys.argv[1:]:
        os.environ["PUBLIC_SITE"] = "1"
    port = int(args[0]) if args else 8765
    uvicorn.run("app:app", host="127.0.0.1", port=port, app_dir=str(ROOT))
