"""Put the dashboard website together: the dashboard pages plus the data files, in one folder ready to upload.

    python deploy/build_site.py                       # data from the dashboard-data branch on GitHub
    python deploy/build_site.py --data some/folder    # data from a folder you already have (it holds meta.json etc.)

The result is dist/dashboard-site. The generated data never lives in the main branch. It comes from the
dashboard-data branch (written by the "Dashboard data" workflow) or from a folder you point at.
"""

import argparse
import io
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DASHBOARD = ROOT / "dashboard"
DEFAULT_OUT = ROOT / "dist" / "dashboard-site"
BRANCH = "origin/dashboard-data"

# The page loads only its own files, so it can say so. 'unsafe-inline' is for style attributes, which the
# charts use. Scripts are never allowed inline.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; connect-src 'self'; frame-ancestors 'self'"
)
HEADERS = f"""/*
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  X-Frame-Options: SAMEORIGIN
  Content-Security-Policy: {CONTENT_SECURITY_POLICY}

/data/*
  Cache-Control: no-cache
"""

REQUIRED_DATA = ["meta.json", "counties.json", "states.json", "checks.json"]


def data_from_git(target):
    """Unpack the data/ folder of the dashboard-data branch into target."""
    subprocess.run(["git", "fetch", "-q", "origin", "dashboard-data"], cwd=ROOT, check=True)
    command = ["git", "archive", "--format=tar", BRANCH, "data"]
    archive = subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(target.parent, filter="data")


def build(out=DEFAULT_OUT, data=None):
    out = Path(out)
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(DASHBOARD, out, ignore=shutil.ignore_patterns("data", "data.new", "data.old"))
    if data:
        shutil.copytree(data, out / "data")
    else:
        data_from_git(out / "data")
    missing = [name for name in REQUIRED_DATA if not (out / "data" / name).is_file()]
    if missing:
        shutil.rmtree(out)
        raise SystemExit(f"The data is missing {missing}. Run the Dashboard data workflow first.")
    (out / "_headers").write_text(HEADERS, encoding="utf-8", newline="\n")
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build the dashboard website folder.")
    parser.add_argument("--data", help="a folder of dashboard data (default: the dashboard-data branch)")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="where to put the site (default: dist/dashboard-site)")
    args = parser.parse_args(argv)
    out = build(args.out, args.data)
    print(f"Built {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
