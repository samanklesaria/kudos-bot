"""Count non-empty, non-comment lines of Python, R, and SQL in the project."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["app.py", "dash_app.py", "cron", "schema"]

COMMENT_RE = {
    ".py": re.compile(r"^\s*#"),
    ".sql": re.compile(r"^\s*--")}

def count_lines(path):
    comment_re = COMMENT_RE[path.suffix]
    return sum(
        1 for line in path.read_text().splitlines()
        if line.strip() and not comment_re.match(line))

def find_files():
    for entry in INCLUDE:
        p = ROOT / entry
        if p.is_file() and p.suffix in COMMENT_RE:
            yield p
        elif p.is_dir():
            for ext in COMMENT_RE:
                yield from p.rglob(f"*{ext}")

if __name__ == "__main__":
    totals = {".py": 0, ".R": 0, ".sql": 0}
    for f in sorted(find_files()):
        n = count_lines(f)
        totals[f.suffix] += n
        print(f"  {n:4d}  {f.relative_to(ROOT)}")
    print()
    for ext, label in [(".py", "Python"), (".sql", "SQL")]:
        print(f"{label:>6s}: {totals[ext]:4d}")
    print(f" Total: {sum(totals.values()):4d}")
