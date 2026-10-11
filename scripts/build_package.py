"""Build a clean distributable, excluding all conversations, tests and databases."""
from pathlib import Path
import hashlib
import json
import zipfile

root = Path(__file__).resolve().parents[1]
output = root.parent / "release"
version = json.loads((root / "plugin.json").read_text(encoding="utf-8"))["version"]
destination = output / version / "chat-notes"
allowed = [
    "plugin.json", ".codex-plugin/plugin.json", "mcp.json", ".mcp.json", "hooks/hooks.json",
    "skills/chat-notes/SKILL.md", "skills/chat-notes/agents/openai.yaml",
    "scripts/chat_notes.py", "scripts/attachments.py", "scripts/notes_mcp.py", "scripts/install_local.py",
    "web/dist/panel.html", "使用说明.md",
]
destination.mkdir(parents=True, exist_ok=True)
checksums = {}
for name in allowed:
    source, target = root / name, destination / name
    target.parent.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    target.write_bytes(data)
    checksums[name] = hashlib.sha256(data).hexdigest()
zip_path = output / ("问答笔记-" + version + ".zip")
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for name in allowed:
        archive.write(destination / name, "chat-notes/" + name)
(output / "package-checksums.json").write_text(json.dumps(checksums, indent=2), encoding="utf-8")
print(json.dumps({"package": str(zip_path), "folder": str(destination), "files": len(allowed)}, ensure_ascii=True))
