"""Register this reviewed local package without touching hook trust."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import sys


def register(plugin_root, home):
    plugin_root, home = Path(plugin_root).resolve(), Path(home).resolve()
    relative = plugin_root.relative_to(home)
    if not (plugin_root / "plugin.json").is_file():
        raise ValueError("plugin.json missing")
    manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
    if manifest["name"] != "chat-notes":
        raise ValueError("Unexpected package identity")
    path = home / ".agents/plugins/marketplace.json"
    if path.exists():
        contents = path.read_text(encoding="utf-8-sig")
        marketplace = json.loads(contents)
    else:
        contents = None
        marketplace = {"name": "personal-chat-notes",
                       "interface": {"displayName": "我的本地插件"}, "plugins": []}
    if not isinstance(marketplace.get("plugins"), list) or not marketplace.get("name"):
        raise ValueError("Existing marketplace has an unsupported structure; not modified")
    entry = {
        "name": "chat-notes",
        "source": {"source": "local", "path": "./" + relative.as_posix()},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    matches = [x for x in marketplace["plugins"] if x.get("name") == "chat-notes"]
    if len(matches) > 1:
        raise ValueError("Existing duplicate plugin entries; not modified")
    if matches:
        matches[0].update(entry)
    else:
        marketplace["plugins"].append(entry)
    encoded = json.dumps(marketplace, ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if contents is not None and encoded != contents:
        backup = path.with_name("marketplace.chat-notes-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json")
        backup.write_text(contents, encoding="utf-8")
    temporary = path.with_name("marketplace.chat-notes-pending.json")
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(path)
    return marketplace["name"], path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--register-only", action="store_true")
    args = parser.parse_args()
    name, path = register(args.root, args.home)
    result = {"marketplace": name, "catalog": str(path), "registered": True, "hook_trust": "requires native review"}
    if not args.register_only:
        cli = args.cli
        if not cli:
            candidates = list((args.home / "AppData/Local/OpenAI/Codex/bin").glob("*/codex.exe"))
            cli = max(candidates, key=lambda x: x.stat().st_mtime) if candidates else None
        if not cli or not cli.is_file():
            result["installed"] = False
            result["next"] = "Restart ChatGPT and install from the personal plugin source."
        else:
            process = subprocess.run([str(cli), "plugin", "add", f"chat-notes@{name}", "--json"],
                                     capture_output=True, text=True, encoding="utf-8", errors="replace")
            result["installed"] = process.returncode == 0
            result["native_result"] = process.stdout.strip()
            if process.returncode:
                result["native_error"] = process.stderr.strip()
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result.get("installed", args.register_only) else 1


if __name__ == "__main__":
    raise SystemExit(main())
