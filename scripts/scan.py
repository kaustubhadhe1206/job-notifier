"""Entry point for the GitHub Actions scanner (.github/workflows/scan.yml). See api/_core/scanner.py.

Run locally:  DATABASE_URL=... python scripts/scan.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))

if not os.environ.get("DATABASE_URL"):
    print("DATABASE_URL is not set (add it as a repository secret named DATABASE_URL). Nothing to do.")
    sys.exit(0)

from _core import scanner  # noqa: E402

result = scanner.run()
print("done:", result)
