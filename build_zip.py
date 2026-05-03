import zipfile, os, fnmatch

ARCHIVE = "simulacrum_deploy.zip"
EXCLUDES = [
    "*.pyc", "__pycache__", ".venv", "venv", "env",
    ".git", ".gitignore", "instance", "*.db", "*.sqlite3",
    ".env", "build_zip.py", "uploads",
    ".htaccess",        # cPanel owns this — never overwrite it
    "passenger_wsgi.py", # cPanel generates this wrapper — our app lives in wsgi.py
    "*.egg-info", "dist", "build",
    "simulacrum_deploy.zip",
]

def should_exclude(path):
    norm = path.replace("\\", "/")
    parts = norm.split("/")
    for pat in EXCLUDES:
        if fnmatch.fnmatch(norm, pat):
            return True
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False

count = 0
with zipfile.ZipFile(ARCHIVE, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk("."):
        dirs[:] = [
            d for d in dirs
            if not should_exclude(os.path.join(root, d).lstrip("./\\"))
        ]
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, ".").replace("\\", "/")
            if rel == ARCHIVE:
                continue
            if not should_exclude(rel):
                zf.write(full, rel)
                count += 1

size = os.path.getsize(ARCHIVE)
print(f"Done. {ARCHIVE} — {count} files, {size // 1024} KB")
