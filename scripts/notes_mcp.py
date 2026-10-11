"""Local stdio MCP server and native conversation-panel resource."""
from __future__ import annotations
import argparse
import ctypes
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from chat_notes import Archive, conversation_url, default_sessions
from attachments import question_display

PANEL_URI = "ui://chat-notes/panel.html"
VERSION = "0.7.6"


def schema(properties=None, required=None):
    result = {"type": "object", "properties": properties or {}, "additionalProperties": False}
    if required:
        result["required"] = required
    return result


TEXT = {"type": "string", "maxLength": 100000}
IDENTIFIER = {"type": "string", "maxLength": 256}


def tool(name, title, description, inputs, read_only=True, panel=False):
    meta = {"ui": {"visibility": ["model", "app"] if panel else ["app"]}}
    if panel:
        meta.update({
            "ui": {"resourceUri": PANEL_URI, "visibility": ["model", "app"]},
            "openai/outputTemplate": PANEL_URI,
            "openai/ui": {"entrypoints": [{"type": "thread"}]},
        })
    return {"name": name, "title": title, "description": description,
            "inputSchema": inputs, "_meta": meta,
            "outputSchema": {"type": "object", "properties": {"ok": {"const": True}},
                             "required": ["ok"], "additionalProperties": False},
            "annotations": {"readOnlyHint": read_only, "destructiveHint": False,
                            "openWorldHint": False}}


TOOLS = [
    tool("open_notes_panel", "问答笔记", "在当前对话旁打开本地问答笔记面板。",
         schema({"search": TEXT}), panel=True),
    tool("notes_panel_data", "刷新笔记", "读取笔记和问题列表。",
         schema({"search": TEXT, "session_id": IDENTIFIER,
                 "collection": {"type": "string", "maxLength": 16, "enum": ["active", "trash", "hidden"]},
                 "offset": {"type": "integer", "minimum": 0},
                 "limit": {"type": "integer", "minimum": 1, "maximum": 500}})),
    tool("get_note_source", "查看原回答", "读取笔记中保留的完整问题、回答和摘录位置。",
         schema({"note_id": IDENTIFIER}, ["note_id"])),
    tool("set_note_deleted", "删除或恢复笔记", "把指定笔记移入回收站，或恢复该笔记。",
         schema({"note_id": IDENTIFIER, "deleted": {"type": "boolean"}}, ["note_id", "deleted"]), read_only=False),
    tool("set_session_hidden", "隐藏或恢复会话", "在问题记录中隐藏或恢复指定会话。",
         schema({"session_id": IDENTIFIER, "hidden": {"type": "boolean"}}, ["session_id", "hidden"]), read_only=False),
    tool("get_question_source", "查看问答记录", "读取一个已保存问题对应的完整回答。",
         schema({"question_id": IDENTIFIER}, ["question_id"])),
    tool("read_copied_excerpt", "读取复制的片段", "仅在用户点击摘录按钮时读取剪贴板文本。",
         schema()),
    tool("match_excerpt", "查找摘录来源", "查找复制片段的原回答；相同文字保留全部候选。",
         schema({"excerpt": TEXT, "answer_id": IDENTIFIER, "session_id": IDENTIFIER,
                 "start": {"type": "integer", "minimum": 0}}, ["excerpt"])),
    tool("save_excerpt", "保存摘录", "按用户选择的来源保存摘录、备注和完整原文副本。",
         schema({"excerpt": TEXT, "answer_id": IDENTIFIER,
                 "start": {"type": "integer", "minimum": 0}, "comment": TEXT},
                ["excerpt", "answer_id", "start"]), read_only=False),
    tool("open_original_conversation", "打开原会话", "用户点击后，在 ChatGPT 中打开笔记的原会话。",
         schema({"note_id": IDENTIFIER}, ["note_id"])),
    tool("open_saved_conversation", "打开已保存会话", "用户点击后打开本地记录所属的会话。",
         schema({"session_id": IDENTIFIER}, ["session_id"])),
]


def clipboard_text():
    if os.name != "nt":
        raise ValueError("请直接把复制的文字粘贴到摘录框。")
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    user.OpenClipboard.argtypes = [wintypes.HWND]
    user.OpenClipboard.restype = wintypes.BOOL
    user.GetClipboardData.argtypes = [wintypes.UINT]
    user.GetClipboardData.restype = wintypes.HANDLE
    kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel.GlobalLock.restype = ctypes.c_void_p
    kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    for _ in range(5):
        if user.OpenClipboard(None):
            break
        time.sleep(0.03)
    else:
        raise ValueError("剪贴板暂时忙碌，请稍后重试或直接粘贴。")
    try:
        handle = user.GetClipboardData(13)
        if not handle:
            raise ValueError("剪贴板里没有文字，请先复制回答片段。")
        pointer = kernel.GlobalLock(handle)
        if not pointer:
            raise ValueError("无法读取复制内容，请直接粘贴。")
        try:
            return ctypes.wstring_at(pointer)[:100000]
        finally:
            kernel.GlobalUnlock(handle)
    finally:
        user.CloseClipboard()


class NotesServer:
    def __init__(self, data_dir=None, sessions=None, watch=True):
        self.data_dir = data_dir
        self.sessions = sessions
        self.stop = threading.Event()
        with Archive(data_dir) as archive:
            self.data_dir = archive.data_dir
        if watch:
            threading.Thread(target=self.watch, daemon=True).start()

    def watch(self):
        while not self.stop.is_set():
            try:
                with Archive(self.data_dir) as archive:
                    archive.sync(self.sessions)
            except (OSError, sqlite3.Error):
                pass
            self.stop.wait(3)

    def data(self, args):
        search = args.get("search", "")
        limit = args.get("limit", 80)
        offset = args.get("offset", 0)
        sid = args.get("session_id")
        collection = args.get("collection", "active")
        titles = {}
        index = (Path(self.sessions).parent if self.sessions else default_sessions().parent) / "session_index.jsonl"
        try:
            with index.open(encoding="utf-8") as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if isinstance(row.get("id"), str) and isinstance(row.get("thread_name"), str):
                            titles[row["id"]] = row["thread_name"]
                    except (ValueError, AttributeError):
                        continue
        except OSError:
            pass
        with Archive(self.data_dir, readonly=True) as archive:
            groups = [dict(row) for row in archive.db.execute(
                "WITH activity AS (SELECT session_id,timestamp stamp,1 q,0 n FROM questions UNION ALL "
                "SELECT session_id,created,0,1 FROM notes WHERE id NOT IN (SELECT note_id FROM deleted_notes)) "
                "SELECT session_id id,SUM(q) question_count,SUM(n) note_count,MAX(stamp) updated,"
                "(SELECT text FROM questions WHERE session_id=activity.session_id "
                "ORDER BY julianday(timestamp),rowid LIMIT 1) first_question "
                "FROM activity GROUP BY session_id ORDER BY julianday(MAX(stamp)) DESC,session_id")]
            matching_notes = archive.notes(search)
            matching_questions = archive.question_rows(search)
            hidden = {row["session_id"]: row["hidden_at"] for row in archive.db.execute("SELECT * FROM hidden_sessions")}
            for group in groups:
                group["title"] = titles.get(group["id"]) or " ".join(question_display(group["first_question"] or "保留来源的会话").split())[:70]
                group["title_source"] = "client" if titles.get(group["id"]) else "first_question"
                group["notes_matches"] = sum(n["session_id"] == group["id"] for n in matching_notes)
                group["questions_matches"] = sum(q["session_id"] == group["id"] for q in matching_questions)
                group["title_matches"] = search.casefold() in group["title"].casefold()
                group["hidden"] = group["id"] in hidden
                group["hidden_at"] = hidden.get(group["id"])
                del group["first_question"]
            trashed = archive.notes(search, trash=True) if collection == "trash" else []
            for note in trashed:
                note["session_title"] = titles.get(note["session_id"]) or " ".join(note["question_snapshot"].split())[:70] or "笔记所属会话"
            hidden_groups = [g for g in groups if g["hidden"] and g["question_count"] and
                             (not search or g["title_matches"] or g["questions_matches"])]
            notes = [n for n in matching_notes if not sid or n["session_id"] == sid]
            questions = [q for q in matching_questions if not sid or q["session_id"] == sid]
            if sid:
                # Re-query in SQLite so equal times retain recorded row order and
                # timestamps with different UTC offsets are ordered correctly.
                question_ids = {q["id"] for q in questions}
                questions = [dict(q) for q in archive.db.execute(
                    "SELECT q.*, (SELECT a.id FROM answers a WHERE a.session_id=q.session_id AND "
                    "a.turn_id=q.turn_id ORDER BY julianday(a.timestamp) DESC,a.rowid DESC LIMIT 1) answer_id "
                    "FROM questions q WHERE q.session_id=? ORDER BY julianday(q.timestamp),q.rowid", (sid,))
                    if q["id"] in question_ids]
                note_ids = {n["id"] for n in notes}
                notes = [dict(n) for n in archive.db.execute(
                    "SELECT * FROM notes WHERE session_id=? ORDER BY julianday(created),rowid", (sid,))
                    if n["id"] in note_ids]
            return {
                "counts": archive.counts(), "search": search,
                "collection": collection,
                "trash_count": archive.db.execute("SELECT COUNT(*) FROM deleted_notes JOIN notes ON notes.id=deleted_notes.note_id").fetchone()[0],
                "hidden_count": sum(g["hidden"] and bool(g["question_count"]) for g in groups),
                "trash": [{k: n[k] for k in ("id", "title", "excerpt", "created", "session_id", "session_title", "deleted_at")}
                          for n in trashed[offset:offset+limit]],
                "trash_total": len(trashed),
                "hidden_sessions": hidden_groups[offset:offset+limit] if collection == "hidden" else [],
                "hidden_total": len(hidden_groups),
                "sessions": groups, "session_id": sid, "offset": offset, "limit": limit,
                "notes": [{k: n[k] for k in ("id", "title", "excerpt", "created", "session_id")} for n in notes[offset:offset+limit]],
                "questions": [{**{k: q[k] for k in ("id", "text", "timestamp", "answer_id", "session_id")},
                               "display_text": question_display(q["text"]), "has_images": bool(archive.images.available and archive.db.execute(
                                   "SELECT 1 FROM image_links WHERE owner_type='question' AND owner_id=? LIMIT 1",(q["id"],)).fetchone())}
                              for q in questions[offset:offset+limit]],
                "notes_total": len(notes), "questions_total": len(questions),
            }

    def validate(self, name, args):
        definition = next((x for x in TOOLS if x["name"] == name), None)
        if not definition:
            raise ValueError("未知笔记操作")
        fields = definition["inputSchema"]
        if not isinstance(args, dict) or set(args) - set(fields["properties"]):
            raise ValueError("请求参数不正确")
        for key in fields.get("required", []):
            if key not in args:
                raise ValueError("缺少参数：" + key)
        for key, value in args.items():
            definition = fields["properties"][key]
            if "enum" in definition and value not in definition["enum"]:
                raise ValueError("列表类型不正确")
            if definition["type"] == "boolean" and not isinstance(value, bool):
                raise ValueError("操作状态不正确")
            if definition["type"] == "string" and (
                not isinstance(value, str) or len(value) > definition["maxLength"]
            ):
                raise ValueError("文字或标识过长")
            if definition["type"] == "integer" and (
                not isinstance(value, int) or isinstance(value, bool) or value < definition.get("minimum", 0)
                or value > definition.get("maximum", 10000000)
            ):
                raise ValueError("位置或数量不正确")

    def call(self, name, args):
        self.validate(name, args)
        if name in ("open_notes_panel", "notes_panel_data"):
            return self.data(args)
        if name == "read_copied_excerpt":
            return {"excerpt": clipboard_text()}
        with Archive(self.data_dir, readonly=name not in ("save_excerpt", "set_note_deleted", "set_session_hidden")) as archive:
            if name == "set_note_deleted":
                note = archive.set_note_deleted(args["note_id"], args["deleted"])
                return {"note_id": note["id"], "deleted": bool(note["deleted_at"])}
            if name == "set_session_hidden":
                archive.set_session_hidden(args["session_id"], args["hidden"])
                return {"session_id": args["session_id"], "hidden": args["hidden"]}
            if name == "get_note_source":
                note=archive.note(args["note_id"])
                note.update(archive.question_presentation(note['session_id'],note['turn_id'],note['question_snapshot'],note['id']))
                note['answer_images']=archive.images.get('note_answer',note['id'])
                return note
            if name == "get_question_source":
                q = archive.db.execute("SELECT * FROM questions WHERE id=?", (args["question_id"],)).fetchone()
                if not q:
                    raise ValueError("找不到这个问题")
                answer = archive.db.execute(
                    "SELECT * FROM answers WHERE session_id=? AND turn_id=? ORDER BY timestamp DESC,rowid DESC LIMIT 1",
                    (q["session_id"], q["turn_id"])).fetchone()
                return {
                    **archive.question_presentation(q["session_id"],q["turn_id"]),
                    "answer_images": archive.images.get('answer',answer['id']) if answer else [],
                    "question_snapshot": "\n\n".join(x["text"] for x in archive.questions_for(q["session_id"], q["turn_id"])),
                    "answer_snapshot": answer["text"] if answer else "完整回答尚未保存，结束后会自动同步。",
                    "answer_id": answer["id"] if answer else None, "session_id": q["session_id"],
                    "turn_id": q["turn_id"], "conversation_url": conversation_url(q["session_id"]),
                }
            if name == "match_excerpt":
                matches = archive.matches(args["excerpt"], args.get("session_id"))
                if args.get("answer_id"):
                    matches = [m for m in matches if m["answer_id"] == args["answer_id"]]
                if "start" in args:
                    matches = [m for m in matches if m["start"] == args["start"]]
                return {"matches": matches[:50], "total": len(matches)}
            if name == "save_excerpt":
                nid = archive.save(args["excerpt"], args["answer_id"], args["start"], args.get("comment", ""))
                note=archive.note(nid)
                note.update(archive.question_presentation(note['session_id'],note['turn_id'],note['question_snapshot'],note['id']))
                note['answer_images']=archive.images.get('note_answer',nid)
                return {"saved": nid, "note": note}
            if name == "open_original_conversation":
                note = archive.note(args["note_id"])
                if os.name != "nt":
                    raise ValueError("当前环境请使用原会话链接。")
                os.startfile(note["conversation_url"])
                return {"opened": True}
            if name == "open_saved_conversation":
                sid = args["session_id"]
                found = archive.db.execute("SELECT 1 FROM questions WHERE session_id=? UNION ALL "
                    "SELECT 1 FROM notes WHERE session_id=? LIMIT 1", (sid, sid)).fetchone()
                if not found:
                    raise ValueError("找不到这个已保存会话")
                if os.name != "nt":
                    raise ValueError("当前环境请使用原会话链接。")
                os.startfile(conversation_url(sid))
                return {"opened": True}

    def dispatch(self, message):
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or not isinstance(message.get("params", {}), dict)
                or isinstance(message.get("id"), bool)):
            return self.error(None, -32600, "Invalid Request")
        ident = message.get("id")
        if ident is None:
            return None
        method, params = message.get("method"), message.get("params") or {}
        result = {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            protocol = requested if requested in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25") else "2025-06-18"
            result = {"protocolVersion": protocol, "serverInfo": {"name": "chat-notes", "version": VERSION},
                      "capabilities": {"tools": {}, "resources": {}}}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "resources/list":
            result = {"resources": [{"uri": PANEL_URI, "name": "问答笔记", "mimeType": "text/html;profile=mcp-app"}]}
        elif method == "resources/templates/list":
            result = {"resourceTemplates": []}
        elif method == "resources/read":
            if params.get("uri") != PANEL_URI:
                return self.error(ident, -32602, "未知界面资源")
            html = (Path(__file__).resolve().parents[1] / "web/dist/panel.html").read_text(encoding="utf-8")
            result = {"contents": [{
                "uri": PANEL_URI, "mimeType": "text/html;profile=mcp-app", "text": html,
                "_meta": {"ui": {"prefersBorder": True, "csp": {"connectDomains": [], "resourceDomains": []}},
                          "openai/ui": {"availableDisplayModes": ["inline", "pip", "fullscreen"]}}
            }]}
        elif method == "tools/call":
            try:
                data = self.call(params.get("name"), params.get("arguments", {}))
                # Full sources and clipboard text belong to the panel, not the
                # model conversation. MCP result metadata is component-private.
                result = {"content": [{"type": "text", "text": "本地笔记操作完成。"}],
                          "structuredContent": {"ok": True}, "_meta": {"chat-notes/result": data}}
            except (ValueError, OSError, sqlite3.Error) as exc:
                result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        elif method == "ping":
            result = {}
        else:
            return self.error(ident, -32601, "Method not found")
        return {"jsonrpc": "2.0", "id": ident, "result": result}

    @staticmethod
    def error(ident, code, message):
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": message}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--sessions", type=Path)
    parser.add_argument("--no-watch", action="store_true")
    args = parser.parse_args()
    server = NotesServer(args.data_dir, args.sessions, watch=not args.no_watch)
    try:
        for line in sys.stdin.buffer:
            try:
                message = json.loads(line)
                response = server.dispatch(message)
                if response is not None:
                    print(json.dumps(response, ensure_ascii=True), flush=True)
            except (ValueError, TypeError, AttributeError):
                print(json.dumps(server.error(None, -32700, "Parse error")), flush=True)
            except Exception:
                print("chat-notes: request failed", file=sys.stderr, flush=True)
                if isinstance(message, dict) and message.get("id") is not None:
                    print(json.dumps(server.error(message["id"], -32603, "Local notes request failed")), flush=True)
    finally:
        server.stop.set()


if __name__ == "__main__":
    main()
