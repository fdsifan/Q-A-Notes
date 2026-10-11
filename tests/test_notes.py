import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from chat_notes import Archive, digest, ranges_in_answer, export_markdown, conversation_url, default_data_dir


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        test_runs = Path(__file__).resolve().parent / ".runs"
        test_runs.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=test_runs)
        self.root = Path(self.tmp.name)
        # Verify the absolute deletion target before TemporaryDirectory.cleanup.
        assert self.root.resolve().parent == test_runs.resolve()
        self.archive = Archive(self.root / "data")
        self.path = self.root / "session.jsonl"

    def tearDown(self):
        self.archive.close()
        self.tmp.cleanup()

    def test_legacy_archive_upgrade_keeps_existing_snapshots(self):
        self.write(self.fixture())
        nid=self.archive.save('把片段保存到笔记。')
        original=self.archive.note(nid)
        self.archive.db.executescript('DROP TABLE deleted_notes; DROP TABLE hidden_sessions; PRAGMA user_version=1;')
        with Archive(self.root/'data',readonly=True) as legacy:
            self.assertEqual(legacy.notes()[0]['id'],nid)
            self.assertEqual(legacy.counts()['notes'],1)
        with Archive(self.root/'data') as upgraded:
            self.assertEqual(upgraded.db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(upgraded.note(nid),original)
            upgraded.set_note_deleted(nid,True)
            self.assertEqual(upgraded.notes(),[])
            upgraded.set_note_deleted(nid,False)
            self.assertEqual(upgraded.notes()[0]['id'],nid)

    def test_explicit_resave_restores_same_note_without_duplicate(self):
        self.write(self.fixture())
        nid=self.archive.save('把片段保存到笔记。')
        self.archive.set_note_deleted(nid,True)
        self.assertEqual(self.archive.save('把片段保存到笔记。'),nid)
        self.assertIsNone(self.archive.note(nid)['deleted_at'])
        self.assertEqual(self.archive.counts()['notes'],1)

    def record(self, kind, payload):
        return {"timestamp": "2026-10-10T12:00:00Z", "type": kind, "payload": payload}

    def fixture(self, sid="s1", guardian=False):
        return [
            self.record("session_meta", {"id": sid, "history_mode": "paginated",
                "thread_source": "guardian_review" if guardian else "user",
                "source": {"subagent": "guardian"} if guardian else "vscode"}),
            self.record("event_msg", {"type": "task_started", "turn_id": "t1"}),
            self.record("response_item", {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "如何保存？"}]}),
            self.record("event_msg", {"type": "item_completed", "turn_id": "t1",
                "item": {"type": "UserMessage", "id": "u1",
                "content": [{"type": "text", "text": "如何保存？"}]}}),
            self.record("response_item", {"type": "message", "role": "assistant",
                "content": [{"type": "output_text", "text": "private reasoning sentinel"}]}),
            self.record("event_msg", {"type": "item_completed", "turn_id": "t1",
                "item": {"type": "CommandExecution", "output": "secret-tool-sentinel"}}),
            self.record("event_msg", {"type": "task_complete", "turn_id": "t1",
                "last_agent_message": "🙂 **原回答**：把片段保存到笔记。\n\n保留完整来源。"}),
        ]

    def write(self, rows):
        self.path.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8")
        self.archive.sync(self.path)

    def test_archive_only_visible_question_and_final(self):
        self.write(self.fixture())
        self.assertEqual(self.archive.counts(), {"questions": 1, "answers": 1, "notes": 0})
        dump = "\n".join(self.archive.db.iterdump())
        self.assertNotIn("private reasoning sentinel", dump)
        self.assertNotIn("secret-tool-sentinel", dump)

    def test_sync_idempotent_and_partial_line(self):
        rows = self.fixture()
        self.write(rows[:-1])
        self.assertEqual(self.archive.counts()["answers"], 0)
        tail = (json.dumps(rows[-1], ensure_ascii=False) + "\n").encode("utf-8")
        with self.path.open("ab") as f:
            f.write(tail[:17])
        self.archive.sync(self.path)
        self.assertEqual(self.archive.counts()["answers"], 0)
        with self.path.open("ab") as f:
            f.write(tail[17:])
        self.archive.sync(self.path)
        self.archive.sync(self.path)
        self.assertEqual(self.archive.counts()["answers"], 1)

    def test_exclude_guardian(self):
        self.write(self.fixture(guardian=True))
        self.assertEqual(self.archive.counts()["questions"], 0)
        self.assertEqual(self.archive.counts()["answers"], 0)

    def test_steered_questions_retain_turn(self):
        rows = self.fixture()
        rows.insert(-1, self.record("event_msg", {"type": "item_completed", "turn_id": "t1",
            "item": {"type": "UserMessage", "id": "u2", "content": [{"type": "text", "text": "也保存来源"}]}}))
        self.write(rows)
        m = self.archive.matches("保留完整来源。")[0]
        self.assertIn("如何保存？", m["question"])
        self.assertIn("也保存来源", m["question"])

    def test_ambiguous_answers_never_auto_select(self):
        self.write(self.fixture())
        self.archive.question("s2", "t2", "另一个问题", "u2")
        self.archive.answer("s2", "t2", "保留完整来源。")
        with self.assertRaises(ValueError):
            self.archive.save("保留完整来源。")
        matches = self.archive.matches("保留完整来源。", "s1")
        nid = self.archive.save("保留完整来源。", matches[0]["answer_id"], matches[0]["start"])
        self.assertEqual(self.archive.note(nid)["session_id"], "s1")

    def test_repeated_occurrence_requires_position(self):
        self.archive.question("s", "t", "问题", "u")
        aid = self.archive.answer("s", "t", "重复。重复。")
        with self.assertRaises(ValueError):
            self.archive.save("重复。", aid)
        nid = self.archive.save("重复。", aid, 3)
        self.assertEqual(self.archive.note(nid)["raw_start"], 3)

    def test_snapshot_survives_answer_replacement_and_source_removal(self):
        self.write(self.fixture())
        nid = self.archive.save("保留完整来源。")
        original = self.archive.note(nid)
        self.archive.answer("s1", "t1", "这是一份重新生成的回答。")
        self.archive.db.execute("DELETE FROM answers")
        self.archive.db.execute("DELETE FROM questions")
        self.path.unlink()
        saved = self.archive.note(nid)
        self.assertEqual(saved["answer_snapshot"], original["answer_snapshot"])
        self.assertEqual(saved["answer_hash"], digest(saved["answer_snapshot"]))
        self.assertEqual(saved["answer_snapshot"][saved["raw_start"]:saved["raw_end"]], saved["excerpt"])

    def test_markdown_and_whitespace_mapping(self):
        raw = "🙂 先讲结论。\n\n1. **保存原文**，再查看[原会话](https://example.test)。"
        found = ranges_in_answer(raw, "保存原文，再查看原会话。")
        self.assertEqual(len(found), 1)
        a, b, method = found[0]
        self.assertEqual(method, "markdown")
        self.assertEqual(a, raw.index("保存原文"))
        self.assertIn("保存原文**", raw[a:b])
        self.assertEqual(ranges_in_answer("甲\n\n乙", "甲  乙"), [(0, 4, "whitespace")])
        self.assertEqual(ranges_in_answer(raw, "保存原问"), [])

    def test_code_preserves_literal_styling(self):
        raw = chr(96) * 3 + "python\nx = '**literal**'\n" + chr(96) * 3
        self.assertEqual(ranges_in_answer(raw, "x = '**literal**'")[0][2], "exact")
        self.assertEqual(ranges_in_answer(raw, "x = 'literal'"), [])

    def test_exact_and_formatted_occurrences_are_both_candidates(self):
        raw = "甲**乙** 与甲乙"
        self.archive.question("s", "t", "问题", "u")
        aid = self.archive.answer("s", "t", raw)
        positions = self.archive.matches("甲乙")
        self.assertEqual(len(positions), 2)
        self.assertEqual({x["method"] for x in positions}, {"exact", "markdown"})
        with self.assertRaises(ValueError):
            self.archive.save("甲乙", aid)

    def test_hook_prompt_merges_with_transcript_and_repetition_does_not_duplicate(self):
        self.archive.hook({"hook_event_name": "UserPromptSubmit", "session_id": "s1",
                           "turn_id": "t1", "prompt": "如何保存？"})
        self.write(self.fixture())
        self.archive.hook({"hook_event_name": "Stop", "session_id": "s1", "turn_id": "t1",
            "last_assistant_message": "🙂 **原回答**：把片段保存到笔记。\n\n保留完整来源。"})
        self.assertEqual(self.archive.counts()["questions"], 1)
        self.assertEqual(self.archive.counts()["answers"], 1)

    def test_environment_and_clarification(self):
        rows = self.fixture()
        for ident, text in (("env", "<environment_context>private config</environment_context>"),
                            ("reply", '<send_user_message_question_reply>[{"question":"在哪使用？","answer":"电脑客户端"}]</send_user_message_question_reply>')):
            rows.insert(-1, self.record("event_msg", {"type": "item_completed", "turn_id": "t1",
                "item": {"type": "UserMessage", "id": ident, "content": [{"type": "text", "text": text}]}}))
        self.write(rows)
        self.assertEqual(self.archive.counts()["questions"], 2)
        self.assertIn("电脑客户端", self.archive.matches("保留完整来源。")[0]["question"])

    def test_export_keeps_trace_and_duplicate_save_is_idempotent(self):
        self.write(self.fixture())
        first = self.archive.save("保留完整来源。", comment="我的理解")
        second = self.archive.save("保留完整来源。", comment="我的理解")
        self.assertEqual(first, second)
        text = export_markdown(self.archive.notes())
        for value in ("如何保存？", "原回答", "我的理解", "codex://threads/s1", first):
            self.assertIn(value, text)
        with self.assertRaises(ValueError):
            conversation_url("evil?path=anything")

    def test_readonly_archive_and_sandbox_path(self):
        import os
        from unittest.mock import patch
        self.write(self.fixture())
        nid = self.archive.save("保留完整来源。")
        with Archive(self.root / "data", readonly=True) as reader:
            self.assertEqual(reader.note(nid)["id"], nid)
            with self.assertRaises(Exception):
                reader.db.execute("DELETE FROM notes")
        if os.name == "nt":
            with patch.dict(os.environ, {"LOCALAPPDATA": "C:/sandbox.virtual/AppData", "CHAT_NOTES_DATA_DIR": ""}):
                self.assertEqual(default_data_dir(), Path.home() / "AppData/Local/ChatNotes")

    def test_gui_source_highlight_and_capture(self):
        import tkinter as tk
        from notes_window import NotesWindow
        self.write(self.fixture())
        nid = self.archive.save("保留完整来源。")
        root = tk.Tk()
        root.withdraw()
        app = NotesWindow(root, self.root / "data", self.path, start_worker=False)
        try:
            app.note_tree.selection_set(nid)
            app.select_note()
            bounds = app.source_text.tag_ranges("excerpt")
            self.assertEqual(len(bounds), 2)
            self.assertEqual(app.source_text.get(bounds[0], bounds[1]), "保留完整来源。")
            app.capture("保留完整来源。")
            root.update_idletasks()
            dialog = next(x for x in root.winfo_children() if isinstance(x, tk.Toplevel))
            def descendants(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from descendants(child)
            buttons = [x for x in descendants(dialog) if x.winfo_class() == "TButton"]
            next(x for x in buttons if x.cget("text") == "保存笔记").invoke()
            self.assertEqual(app.store.counts()["notes"], 1)
        finally:
            app.close()


if __name__ == "__main__":
    unittest.main()
