"""Check tracked source only; never inspect or print local private data."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_DIRS = {"data", "output", ".venv", "node_modules", "__pycache__", ".pytest_cache", "dist", "build"}
FORBIDDEN_SUFFIXES = {".pdf", ".sqlite", ".sqlite3", ".db", ".key", ".pem", ".log", ".pyc"}
TOKEN = re.compile(r"(?:\b(?:sk|tk)-[A-Za-z0-9_-]{25,}|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{30,})")


def main():
    result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    names = result.stdout.decode("utf-8").strip("\0").split("\0")
    issues = []
    for name in filter(None, names):
        path = Path(name)
        if set(path.parts) & FORBIDDEN_DIRS or path.suffix.lower() in FORBIDDEN_SUFFIXES or (path.name.startswith(".env") and path.name != ".env.example"):
            issues.append(f"Private/generated file tracked: {name}")
            continue
        raw = (ROOT / path).read_bytes()
        if name.startswith("static/vendor/katex/fonts/") and path.suffix.lower() in {".woff2", ".woff", ".ttf"}:
            if not all((ROOT / "static/vendor/katex" / p).is_file() for p in ("LICENSE", "fonts/OFL.txt", "fonts/NOTICE.txt")):
                issues.append("Vendored KaTeX fonts missing upstream licenses")
            continue
        if b"\0" in raw:
            issues.append(f"Unexpected binary in source release: {name}")
            continue
        text = raw.decode("utf-8-sig", errors="replace")
        for match in TOKEN.finditer(text):
            if not any(s in match.group().lower() for s in ("test", "dummy", "example", "placeholder")):
                issues.append(f"Potential credential in {name} (value hidden)")
                break
        if re.search(r"[A-Z]:[/\\](?:Users|论文阅读)[/\\]", text, re.I):
            issues.append(f"Personal absolute path in {name}")
    from backend.storage import DEFAULTS
    if DEFAULTS["endpoint"] or DEFAULTS["model"]:
        issues.append("Fresh installation must not select endpoint or model")
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    if "GNU AFFERO GENERAL PUBLIC LICENSE" not in license_text or len(license_text) < 30000:
        issues.append("Full GNU AGPL license is missing")
    if issues:
        print("\n".join(issues))
        return 1
    print(f"Release check passed: {len(names)} source files; no private/generated paths or apparent credentials.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
