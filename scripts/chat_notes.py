"""Local Q&A archive. No network, credentials, clipboard monitoring or UI automation."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timezone
from uuid import uuid4
from attachments import ImageArchive, attachment_specs, question_display, markdown_images, canonical_source

VERSION = "0.7.6"
ADAPTER = "codex-jsonl-2026-10"
TRANSPORT_PREFIXES = ("<environment_context>", "<external_codex_apps_")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def default_data_dir():
    if os.environ.get("CHAT_NOTES_DATA_DIR"):
        return Path(os.environ["CHAT_NOTES_DATA_DIR"])
    if os.name == "nt":
        # Desktop command sandboxes virtualize LOCALAPPDATA. Use the actual
        # profile path so skills, hooks, and the helper share one archive.
        return Path.home() / "AppData/Local/ChatNotes"
    return Path.home() / ".local/share/chat-notes"


def default_sessions():
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "sessions"


def user_text(content):
    text = content if isinstance(content, str) else "\n".join(
        x.get("text", "") for x in (content or [])
        if isinstance(x, dict) and x.get("type") in ("text", "input_text"))
    text = text.strip()
    if not text or text.startswith(TRANSPORT_PREFIXES):
        return ""
    if text.startswith("<send_user_message_question_reply>"):
        try:
            rows = json.loads(text.split(">", 1)[1].rsplit("</", 1)[0])
            return "\n".join(f"{x.get('question', '')}\n用户选择：{x.get('answer', '')}" for x in rows)
        except (ValueError, TypeError, AttributeError):
            return text
    return text


def normalize(text, positions=None):
    """Collapse whitespace and retain character positions in the raw answer."""
    positions = list(range(len(text))) if positions is None else positions
    chars, mapping = [], []
    for ch, pos in zip(text, positions):
        if ch.isspace():
            if chars and chars[-1] != " ":
                chars.append(" ")
                mapping.append(pos)
        else:
            chars.append(ch)
            mapping.append(pos)
    if chars and chars[-1] == " ":
        chars.pop()
        mapping.pop()
    return "".join(chars), mapping


def rendered_projection(raw):
    """Conservative Markdown projection, not a fuzzy or semantic match."""
    remove, code_ranges = set(), []
    in_fence, fence_start = False, 0
    for line in re.finditer(r"(?m)^.*(?:\n|$)", raw):
        if re.match(r"^\s*(" + chr(96) + r"{3,}|~{3,})", line.group()):
            remove.update(range(line.start(), line.end()))
            if in_fence:
                code_ranges.append((fence_start, line.start()))
            else:
                fence_start = line.end()
            in_fence = not in_fence
    if in_fence:
        code_ranges.append((fence_start, len(raw)))
    def outside_code(start):
        return not any(a <= start < b for a, b in code_ranges)
    for m in re.finditer(r"(?m)^[ \t]*(?:#{1,6}[ \t]+|>[ \t]?|[-+*][ \t]+|\d+[.)][ \t]+)", raw):
        if outside_code(m.start()):
            remove.update(range(m.start(), m.end()))
    for m in re.finditer(r"(?<!!)\[([^\]\n]+)\]\([^)\n]+\)", raw):
        if outside_code(m.start()):
            remove.add(m.start())
            remove.update(range(m.end(1), m.end()))
    for m in re.finditer(r"(\*\*|__|~~|\*|_)(?=\S)(.+?)(?<=\S)\1", raw):
        if outside_code(m.start()):
            remove.update(range(m.start(), m.start(2)))
            remove.update(range(m.end(2), m.end()))
    for m in re.finditer(chr(96) + r"([^" + chr(96) + r"\n]+)" + chr(96), raw):
        if outside_code(m.start()):
            remove.add(m.start())
            remove.add(m.end() - 1)
    indices = [i for i in range(len(raw)) if i not in remove]
    return normalize("".join(raw[i] for i in indices), indices)


def ranges_in_answer(raw, excerpt):
    quote, results = excerpt.strip(), []
    if not quote:
        return results
    start = 0
    while (pos := raw.find(quote, start)) >= 0:
        results.append((pos, pos + len(quote), "exact"))
        start = pos + 1
    needle, _ = normalize(quote)
    for method, (haystack, mapping) in (
        ("whitespace", normalize(raw)), ("markdown", rendered_projection(raw))):
        start = 0
        while needle and (pos := haystack.find(needle, start)) >= 0:
            bounds = (mapping[pos], mapping[pos + len(needle) - 1] + 1, method)
            if bounds[:2] not in [x[:2] for x in results]:
                results.append(bounds)
            start = pos + 1
    return results


class Archive:
    def __init__(self, data_dir=None, readonly=False):
        self.data_dir = Path(data_dir or default_data_dir())
        self.path = self.data_dir / "notes.sqlite3"
        if readonly:
            self.db = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True, timeout=3)
            self.db.row_factory = sqlite3.Row
            self.has_management = bool(self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='deleted_notes'").fetchone())
            self.images = ImageArchive(self.db)
            return
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=3)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
          id TEXT PRIMARY KEY, path TEXT, origin TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS questions (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
          message_id TEXT, text TEXT NOT NULL, timestamp TEXT, source TEXT,
          UNIQUE(session_id,turn_id,message_id));
        CREATE INDEX IF NOT EXISTS questions_turn ON questions(session_id,turn_id);
        CREATE TABLE IF NOT EXISTS answers (
          id TEXT PRIMARY KEY, session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
          text TEXT NOT NULL, hash TEXT NOT NULL, timestamp TEXT, status TEXT,
          UNIQUE(session_id,turn_id,hash));
        CREATE INDEX IF NOT EXISTS answers_turn ON answers(session_id,turn_id);
        CREATE TABLE IF NOT EXISTS notes (
          id TEXT PRIMARY KEY, title TEXT NOT NULL, excerpt TEXT NOT NULL,
          comment TEXT NOT NULL, created TEXT NOT NULL, answer_id TEXT NOT NULL,
          session_id TEXT NOT NULL, turn_id TEXT NOT NULL,
          question_snapshot TEXT NOT NULL, answer_snapshot TEXT NOT NULL,
          answer_hash TEXT NOT NULL, raw_start INTEGER NOT NULL, raw_end INTEGER NOT NULL,
          match_method TEXT NOT NULL, source_path TEXT);
        CREATE TABLE IF NOT EXISTS imports (
          path TEXT PRIMARY KEY, offset INTEGER, head_hash TEXT, state TEXT);
        CREATE TABLE IF NOT EXISTS deleted_notes (
          note_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS hidden_sessions (
          session_id TEXT PRIMARY KEY, hidden_at TEXT NOT NULL);
        PRAGMA user_version=2;
        """)
        self.has_management = True
        self.images = ImageArchive(self.db, writable=True)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def question(self, sid, turn, text, message_id=None, timestamp=None, source="transcript", images=None):
        text = user_text(text)
        if not sid or not turn or not text:
            return
        message_id = message_id or ("hook:" + digest(text))
        provisional = self.db.execute(
            "SELECT id FROM questions WHERE session_id=? AND turn_id=? AND text=? AND source='hook'",
            (sid, turn, text)).fetchone()
        if provisional and source != "hook":
            self.db.execute("DELETE FROM questions WHERE id=?", (provisional["id"],))
        if source == "hook" and self.db.execute(
            "SELECT 1 FROM questions WHERE session_id=? AND turn_id=? AND text=?",
            (sid, turn, text)).fetchone():
            return
        qid = digest(sid + "\0" + turn + "\0" + message_id)
        self.db.execute("INSERT OR IGNORE INTO questions VALUES(?,?,?,?,?,?,?)",
            (qid, sid, turn, message_id, text, timestamp or now(), source))
        if images:
            self.images.ingest("question",qid,images)
        self.db.execute("INSERT OR IGNORE INTO image_checked_questions VALUES(?)",(qid,))
        return qid

    def answer(self, sid, turn, text, timestamp=None, status="complete"):
        if not sid or not turn or not isinstance(text, str) or not text.strip():
            return
        sha = digest(text)
        aid = digest(sid + "\0" + turn + "\0" + sha)
        self.db.execute(
            "INSERT INTO answers VALUES(?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "status=CASE WHEN excluded.status='complete' THEN 'complete' ELSE answers.status END",
            (aid, sid, turn, text, sha, timestamp or now(), status))
        self.images.ingest("answer",aid,markdown_images(text))
        return aid

    def consume(self, row, state):
        kind, data = row.get("type"), row.get("payload") or {}
        stamp = row.get("timestamp") or now()
        if not isinstance(data, dict):
            return
        if kind == "session_meta":
            sid = data.get("id") or data.get("session_id")
            allowed = data.get("thread_source") in (None, "user") and not isinstance(data.get("source"), dict)
            state.update(session=sid, allowed=bool(sid and allowed),
                         paginated=data.get("history_mode") == "paginated")
            if state["allowed"]:
                self.db.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
                    "path=excluded.path,updated=excluded.updated",
                    (sid, state["path"], data.get("originator", ""), stamp))
            return
        if not state.get("allowed"):
            return
        sid, turn = state["session"], state.get("turn")
        if kind == "turn_context":
            state["turn"] = data.get("turn_id") or turn
        elif kind == "event_msg":
            event = data.get("type")
            if event == "task_started":
                state["turn"] = data.get("turn_id")
            elif event == "item_completed":
                item = data.get("item") or {}
                if item.get("type") == "UserMessage":
                    self.question(sid, data.get("turn_id") or turn, user_text(item.get("content")),
                                  item.get("id"), stamp, images=[x["path"] for x in item.get("content", [])
                                    if isinstance(x,dict) and x.get("type")=="local_image" and isinstance(x.get("path"),str)])
            elif event == "user_message" and not state.get("paginated"):
                self.question(sid, data.get("turn_id") or turn, data.get("message", ""),
                              data.get("id") or ("legacy:" + str(row.get("ordinal", stamp))), stamp)
            elif event == "task_complete":
                self.answer(sid, data.get("turn_id") or turn, data.get("last_agent_message"), stamp)
        # The tested response_item format has no reliable assistant channel.
        # Never ingest it: this avoids collecting reasoning or tool messages.

    def sync_file(self, path):
        path = Path(path).resolve()
        key = str(path)
        entry = self.db.execute("SELECT * FROM imports WHERE path=?", (key,)).fetchone()
        imported = 0
        with path.open("rb") as stream:
            head = stream.readline()
            if not head.endswith(b"\n"):
                return 0
            head_sha = hashlib.sha256(head).hexdigest()
            offset = entry["offset"] if entry else 0
            if entry and (head_sha != entry["head_hash"] or path.stat().st_size < offset):
                offset, entry = 0, None
            state = json.loads(entry["state"]) if entry else {"path": key}
            unchecked = state.get("session") and self.db.execute(
                "SELECT 1 FROM questions q WHERE session_id=? AND NOT EXISTS "
                "(SELECT 1 FROM image_checked_questions i WHERE i.id=q.id) LIMIT 1",(state["session"],)).fetchone()
            if state.get("images_version") != 1 or unchecked:
                # Re-read older imports once to recover native local_image fields.
                # Existing question/answer IDs remain idempotent. A per-question
                # marker also catches records imported by an older running service.
                offset=0;state={"path":key,"images_version":1}
            stream.seek(offset)
            while True:
                line = stream.readline()
                if not line or not line.endswith(b"\n"):
                    break  # The writer may still be appending this record.
                offset += len(line)
                try:
                    self.consume(json.loads(line), state)
                except (ValueError, TypeError, AttributeError) as exc:
                    self.log("adapter_record_error", path=key, error=type(exc).__name__)
                imported += 1
        self.db.execute(
            "INSERT INTO imports VALUES(?,?,?,?) ON CONFLICT(path) DO UPDATE SET "
            "offset=excluded.offset,head_hash=excluded.head_hash,state=excluded.state",
            (key, offset, head_sha, json.dumps(state)))
        return imported

    def sync(self, sessions=None):
        root = Path(sessions or default_sessions())
        counts = {"files_changed": 0, "records_read": 0, "errors": 0}
        if not root.exists():
            return {**counts, "warning": "找不到本地会话目录", "root": str(root)}
        paths = [root] if root.is_file() else root.rglob("*.jsonl")
        for path in paths:
            try:
                n = self.sync_file(path)
                counts["files_changed"] += bool(n)
                counts["records_read"] += n
                self.db.commit()
            except (OSError, sqlite3.Error) as exc:
                self.db.rollback()
                counts["errors"] += 1
                self.log("sync_error", path=str(path), error=type(exc).__name__)
        self.backfill_note_images()
        self.db.commit()
        return {**counts, **self.counts()}

    def log(self, event, **metadata):
        try:
            with (self.data_dir / "activity.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({"timestamp": now(), "event": event, **metadata}) + "\n")
        except OSError:
            pass

    def counts(self):
        counts = {name: self.db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                  for name in ("questions", "answers", "notes")}
        if self.has_management:
            counts["notes"] = self.db.execute("SELECT COUNT(*) FROM notes WHERE id NOT IN "
                "(SELECT note_id FROM deleted_notes)").fetchone()[0]
        return counts

    def questions_for(self, sid, turn):
        return [dict(x) for x in self.db.execute(
            "SELECT * FROM questions WHERE session_id=? AND turn_id=? ORDER BY timestamp,rowid",
            (sid, turn))]

    def matches(self, excerpt, session=None):
        results = []
        rows = self.db.execute("SELECT * FROM answers" +
            (" WHERE session_id=?" if session else "") + " ORDER BY timestamp DESC",
            (session,) if session else ())
        for row in rows:
            questions = self.questions_for(row["session_id"], row["turn_id"])
            if not questions:
                continue
            for start, end, method in ranges_in_answer(row["text"], excerpt):
                results.append({
                    "answer_id": row["id"], "session_id": row["session_id"],
                    "turn_id": row["turn_id"], "start": start, "end": end,
                    "method": method, "timestamp": row["timestamp"],
                    "question": "\n\n".join(x["text"] for x in questions),
                    "answer_preview": row["text"][max(0, start - 80):end + 120]})
        return results

    def save(self, excerpt, answer_id=None, start=None, comment="", title=None, session=None):
        matches = self.matches(excerpt, session)
        if answer_id:
            matches = [m for m in matches if m["answer_id"] == answer_id]
        if start is not None:
            matches = [m for m in matches if m["start"] == start]
        if len(matches) != 1:
            raise ValueError("未找到可确认的来源" if not matches else "有多个来源，请选择对应回答及位置")
        m = matches[0]
        answer = self.db.execute("SELECT * FROM answers WHERE id=?", (m["answer_id"],)).fetchone()
        source = self.db.execute("SELECT path FROM sessions WHERE id=?", (m["session_id"],)).fetchone()
        excerpt = excerpt.strip()
        existing = self.db.execute(
            "SELECT id FROM notes WHERE answer_id=? AND raw_start=? AND raw_end=? AND excerpt=? AND comment=?",
            (answer["id"], m["start"], m["end"], excerpt, comment)).fetchone()
        if existing:
            # An explicit new save of the same excerpt restores its original note.
            self.db.execute("DELETE FROM deleted_notes WHERE note_id=?", (existing["id"],))
            self.copy_note_images(existing["id"],m["session_id"],m["turn_id"],answer["id"])
            self.db.commit()
            return existing["id"]
        note_id = str(uuid4())
        self.db.execute("INSERT INTO notes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            note_id, title or excerpt.replace("\n", " ")[:48], excerpt, comment, now(),
            answer["id"], m["session_id"], m["turn_id"], m["question"], answer["text"],
            answer["hash"], m["start"], m["end"], m["method"], source["path"] if source else ""))
        self.copy_note_images(note_id,m["session_id"],m["turn_id"],answer["id"])
        self.db.commit()
        return note_id

    def notes(self, search="", trash=False):
        if self.has_management:
            query = "SELECT notes.*,d.deleted_at FROM notes LEFT JOIN deleted_notes d ON d.note_id=notes.id "
            condition = "d.deleted_at IS NOT NULL" if trash else "d.deleted_at IS NULL"
        else:
            if trash:
                return []
            query, condition = "SELECT notes.*,NULL deleted_at FROM notes ", "1=1"
        return [dict(x) for x in self.db.execute(
            query + "WHERE " + condition + " AND (instr(title,?) OR instr(excerpt,?) OR instr(comment,?) "
            "OR instr(question_snapshot,?)) ORDER BY julianday(created) DESC,notes.rowid DESC", (search,) * 4)]

    def note(self, note_id):
        row = self.db.execute("SELECT * FROM notes WHERE id=?", (note_id,)).fetchone()
        if not row:
            raise ValueError("找不到笔记")
        result = dict(row)
        deleted = self.db.execute("SELECT deleted_at FROM deleted_notes WHERE note_id=?", (note_id,)).fetchone() if self.has_management else None
        result["deleted_at"] = deleted[0] if deleted else None
        result["conversation_url"] = conversation_url(result["session_id"])
        return result

    def copy_note_images(self, note_id, sid, turn, answer_id):
        self.images.copy_links([("question",q["id"]) for q in self.questions_for(sid,turn)],"note_question",note_id)
        self.images.copy_links([("answer",answer_id)],"note_answer",note_id)

    def backfill_note_images(self):
        for note in self.db.execute("SELECT id,session_id,turn_id,answer_id FROM notes"):
            self.copy_note_images(note["id"],note["session_id"],note["turn_id"],note["answer_id"])

    def question_presentation(self, sid, turn, snapshot=None, note_id=None):
        questions=self.questions_for(sid,turn)
        raw=snapshot if snapshot is not None else "\n\n".join(q["text"] for q in questions)
        images=self.images.get("note_question",note_id) if note_id else [image for q in questions for image in self.images.get("question",q["id"])]
        known={canonical_source(image["source_url"]) for image in images}
        images.extend({"source_url":spec["path"],"name":spec["name"],"status":"missing","data_url":None}
                      for spec in attachment_specs(raw) if canonical_source(spec["path"]) not in known)
        return {"question_display":question_display(raw),"question_images":images}

    def set_note_deleted(self, note_id, deleted):
        self.note(note_id)  # Reject stale or unknown IDs before changing anything.
        if deleted:
            self.db.execute("INSERT OR IGNORE INTO deleted_notes VALUES(?,?)", (note_id, now()))
        else:
            self.db.execute("DELETE FROM deleted_notes WHERE note_id=?", (note_id,))
        self.db.commit()
        return self.note(note_id)

    def set_session_hidden(self, session_id, hidden):
        if not self.db.execute("SELECT 1 FROM questions WHERE session_id=? LIMIT 1", (session_id,)).fetchone():
            raise ValueError("找不到这个已保存会话")
        if hidden:
            self.db.execute("INSERT OR IGNORE INTO hidden_sessions VALUES(?,?)", (session_id, now()))
        else:
            self.db.execute("DELETE FROM hidden_sessions WHERE session_id=?", (session_id,))
        self.db.commit()

    def question_rows(self, search=""):
        return [dict(x) for x in self.db.execute(
            "SELECT q.*, (SELECT a.id FROM answers a WHERE a.session_id=q.session_id AND "
            "a.turn_id=q.turn_id ORDER BY a.timestamp DESC,a.rowid DESC LIMIT 1) answer_id "
            "FROM questions q WHERE instr(q.text,?) ORDER BY q.timestamp DESC,q.rowid DESC", (search,))]

    def hook(self, event):
        name, sid, turn = event.get("hook_event_name"), event.get("session_id"), event.get("turn_id")
        if name not in ("UserPromptSubmit", "Stop") or not sid or not turn:
            return
        path = event.get("transcript_path")
        if path and Path(path).is_file():
            try:
                self.sync_file(path)
            except OSError as exc:
                self.log("hook_import_error", error=type(exc).__name__)
        metadata = self.db.execute("SELECT state FROM imports WHERE path=?",
            (str(Path(path).resolve()),)).fetchone() if path else None
        if metadata and not json.loads(metadata["state"]).get("allowed"):
            return
        self.db.execute("INSERT OR IGNORE INTO sessions VALUES(?,?,?,?)", (sid, path or "", "hook", now()))
        if name == "UserPromptSubmit":
            self.question(sid, turn, event.get("prompt", ""), source="hook")
        else:
            self.answer(sid, turn, event.get("last_assistant_message"), status="stop")
        self.db.commit()
        self.log("hook_saved", hook=name, session=sid, turn=turn)


def conversation_url(session):
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", session):
        raise ValueError("无效会话标识")
    return "codex://threads/" + session


def export_markdown(notes):
    chunks = ["# 我的问答笔记\n"]
    for n in notes:
        chunks.extend([
            f"## {n['title']}\n", f"保存时间：{n['created']}\n",
            "### 摘录\n", n["excerpt"] + "\n", "### 我的备注\n", (n["comment"] or "（无）") + "\n",
            "### 原问题\n", n["question_snapshot"] + "\n", "### 完整原回答\n", n["answer_snapshot"] + "\n",
            f"[打开原会话]({conversation_url(n['session_id'])})\n",
            f"笔记标识：{n['id']}；回合：{n['turn_id']}；原文位置：{n['raw_start']}–{n['raw_end']}；"
            f"回答 SHA-256：{n['answer_hash']}\n"])
    return "\n".join(chunks)


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description="本地问答笔记")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--sessions", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "status", "hook", "list"):
        sub.add_parser(name)
    show = sub.add_parser("show")
    show.add_argument("--note", required=True)
    for name in ("match", "save"):
        sp = sub.add_parser(name)
        sp.add_argument("--text-file", type=Path, required=True)
        sp.add_argument("--session")
        if name == "save":
            sp.add_argument("--answer")
            sp.add_argument("--start", type=int)
            sp.add_argument("--comment-file", type=Path)
            sp.add_argument("--title")
    export = sub.add_parser("export")
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "hook":
        try:
            with Archive(args.data_dir) as store:
                store.hook(json.load(sys.stdin))
        except Exception:
            pass  # Logging must never block an ordinary question.
        print("{}")
        return 0
    try:
        with Archive(args.data_dir, readonly=args.command in ("status", "list", "show", "export")) as store:
            if args.command == "sync":
                emit(store.sync(args.sessions))
            elif args.command == "status":
                emit({**store.counts(), "database": str(store.path), "version": VERSION, "adapter": ADAPTER})
            elif args.command == "list":
                emit([{k: n[k] for k in ("id", "title", "excerpt", "created")} for n in store.notes()])
            elif args.command == "show":
                emit(store.note(args.note))
            elif args.command == "export":
                args.output.write_text(export_markdown(store.notes()), encoding="utf-8")
                emit({"exported": str(args.output)})
            else:
                store.sync(args.sessions)
                text = args.text_file.read_text(encoding="utf-8-sig")
                if args.command == "match":
                    emit(store.matches(text, args.session))
                else:
                    comment = args.comment_file.read_text(encoding="utf-8-sig") if args.comment_file else ""
                    emit({"saved": store.save(text, args.answer, args.start, comment, args.title, args.session)})
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        emit({"error": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
