"""Archive explicitly attached local raster images; never fetch remote URLs."""
import base64
import hashlib
from pathlib import Path
import re

MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_IMAGES = 20
WRAPPER = re.compile(r"(?m)^# Files mentioned by the user:\s*\n(?P<header>[\s\S]*?)^## My request:\s*\n")
FILE = re.compile(r"(?m)^## (?P<name>[^\n]+?): (?P<path>[^\n]+)\nImage attachment: (?P<image>true|false)\s*(?:\n|$)")


def attachment_specs(text):
    return [{key:value.strip() for key,value in match.groupdict().items()} for wrapper in WRAPPER.finditer(text or "")
            for match in FILE.finditer(wrapper["header"]) if match["image"] == "true"][:MAX_IMAGES]


def question_display(text):
    def clean(match):
        files=list(FILE.finditer(match["header"]))
        return "" if files and all(file["image"] == "true" for file in files) else match[0]
    return WRAPPER.sub(clean, text or "").strip()


def canonical_source(value):
    value=value.replace("\\","/")
    return value.casefold() if re.match(r"^[A-Za-z]:/",value) else value


def markdown_images(text):
    # The desktop's generated local image Markdown uses an absolute path,
    # optionally inside angle brackets when the filename contains spaces.
    return [match[1].strip("<>").strip() for match in re.finditer(
        r"!\[[^\]\n]*\]\((<[^>\n]+>|[^\n)]+)\)", text or "")][:MAX_IMAGES]


def local_path(value):
    if not isinstance(value,str) or value.startswith(("//", "\\\\")):
        return None
    if not (re.match(r"^[A-Za-z]:[\\/]",value) or value.startswith("/")):
        return None
    path=Path(value)
    if path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
        return None
    return path


def image_mime(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if data.startswith(b"\xff\xd8\xff"): return "image/jpeg"
    if data.startswith((b"GIF87a",b"GIF89a")): return "image/gif"
    if data[:4]==b"RIFF" and data[8:12]==b"WEBP": return "image/webp"
    return None


class ImageArchive:
    def __init__(self, db, writable=False):
        self.db=db
        if writable:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS image_assets (
              id TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS image_links (
              owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
              source_url TEXT NOT NULL, name TEXT NOT NULL, asset_id TEXT, status TEXT NOT NULL,
              PRIMARY KEY(owner_type,owner_id,ordinal));
            CREATE TABLE IF NOT EXISTS image_checked_questions (id TEXT PRIMARY KEY);
            """)
        self.available=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='image_links'").fetchone())

    def ingest(self, kind, ident, paths):
        for ordinal,source in enumerate(dict.fromkeys(paths[:MAX_IMAGES])):
            # Once saved, the bytes are immutable, even if the temporary file changes.
            existing=self.db.execute("SELECT asset_id FROM image_links WHERE owner_type=? AND owner_id=? AND ordinal=?",
                                     (kind,ident,ordinal)).fetchone()
            if existing and existing[0]: continue
            path=local_path(source)
            name=source.replace("\\","/").rsplit("/",1)[-1][:200]
            status,asset="unsupported",None
            if path:
                try:
                    with path.open("rb") as stream:
                        data=stream.read(MAX_IMAGE_BYTES+1)
                    mime=image_mime(data)
                    if len(data)>MAX_IMAGE_BYTES: status="too_large"
                    elif mime:
                        asset=hashlib.sha256(data).hexdigest();status="saved"
                        self.db.execute("INSERT OR IGNORE INTO image_assets VALUES(?,?,?)",(asset,mime,data))
                except OSError:
                    status="missing"
            self.db.execute("INSERT INTO image_links VALUES(?,?,?,?,?,?,?) ON CONFLICT(owner_type,owner_id,ordinal) "
                "DO UPDATE SET source_url=excluded.source_url,name=excluded.name,asset_id=excluded.asset_id,status=excluded.status",
                (kind,ident,ordinal,source,name,asset,status))

    def copy_links(self, sources, target_kind, target_id):
        if not self.available: return
        if self.db.execute("SELECT 1 FROM image_links WHERE owner_type=? AND owner_id=? LIMIT 1",(target_kind,target_id)).fetchone(): return
        ordinal=0
        for kind,ident in sources:
            for row in self.db.execute("SELECT * FROM image_links WHERE owner_type=? AND owner_id=? ORDER BY ordinal",(kind,ident)):
                self.db.execute("INSERT OR IGNORE INTO image_links VALUES(?,?,?,?,?,?,?)",
                    (target_kind,target_id,ordinal,row['source_url'],row['name'],row['asset_id'],row['status']))
                ordinal+=1

    def get(self, kind, ident):
        if not self.available: return []
        rows=self.db.execute("SELECT l.*,a.mime,a.data FROM image_links l LEFT JOIN image_assets a ON a.id=l.asset_id "
            "WHERE l.owner_type=? AND l.owner_id=? ORDER BY ordinal",(kind,ident))
        return [{"source_url":r['source_url'],"name":r['name'],"status":r['status'],
                 "data_url":"data:"+r['mime']+";base64,"+base64.b64encode(r['data']).decode('ascii') if r['data'] else None}
                for r in rows]
