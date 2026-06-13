"""
Asrār — Entry Point
Run this to start the backend server.

Usage:
    python main.py
    python main.py --port 8080
"""

import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent
# Allow imports like `api.*` when running `python main.py` from this directory
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))


from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Asrār Agent Server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--host", type=str, default="127.0.0.1")
    parser.add_argument("--reload", action="store_true", default=False)
    args = parser.parse_args()

    print(f"""
  ████████████████████████████████
  ██  Asrār Agent  v0.1.0       ██
  ██  http://{args.host}:{args.port}   ██
  ████████████████████████████████
    """)

    uvicorn.run(
        "backend.api.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
