"""Entry point for the GitHub Actions scanner (.github/workflows/scan.yml). See api/_core/scanner.py.

Run locally:  DATABASE_URL=... python scripts/scan.py
"""
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "api"))

# Accept the secret pasted the way it appears in a .env file: with quotes or "DATABASE_URL=" in front.
url = (os.environ.get("DATABASE_URL") or "").strip()
if url.startswith("DATABASE_URL="):
    url = url.split("=", 1)[1].strip()
url = url.strip("\"'").strip()
os.environ["DATABASE_URL"] = url
if not url:
    print("DATABASE_URL is not set (add it as a repository secret named DATABASE_URL). Nothing to do.")
    sys.exit(0)


def describe(u: str) -> str:
    """The connection string without its password, to explain connection errors."""
    try:
        p = urlsplit(u)
        return f"scheme={p.scheme or '?'} host={p.hostname or '?'} port={p.port or '?'} password={'set' if p.password else 'missing'}"
    except ValueError:
        return "not a valid URL"


from _core import db, scanner  # noqa: E402

try:
    with db.connect() as conn:
        conn.execute("SELECT 1")
except Exception as e:  # noqa: BLE001
    print(f"Could not connect to the database ({describe(url)}): {type(e).__name__}: {str(e)[:300]}")
    print("Check the DATABASE_URL secret: it must be the same postgresql://... string Vercel uses "
          "(Supabase transaction pooler, port 6543), without quotes.")
    sys.exit(1)

result = scanner.run()
print("done:", result)
