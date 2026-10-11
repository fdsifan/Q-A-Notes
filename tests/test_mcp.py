import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from chat_notes import Archive
from notes_mcp import NotesServer, PANEL_URI


class PanelServerTests(unittest.TestCase):
    def setUp(self):
        runs = ROOT / 'tests/.runs'
        runs.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=runs)
        self.root = Path(self.tmp.name)
        assert self.root.resolve().parent == runs.resolve()
        self.data = self.root / 'data'
        with Archive(self.data) as archive:
            archive.question('session1', 'turn1', 'source-question-sentinel', 'u1')
            self.answer = archive.answer('session1', 'turn1', '🙂 甲**乙** 与甲乙。\n完整回答-sentinel')
            archive.db.commit()
        self.server = NotesServer(self.data, watch=False)

    def tearDown(self):
        self.server.stop.set()
        self.tmp.cleanup()

    def request(self, method, params=None):
        return self.server.dispatch({'jsonrpc':'2.0', 'id':1, 'method':method, 'params':params or {}})

    def call(self, name, args=None):
        return self.request('tools/call', {'name':name, 'arguments':args or {}})['result']

    def test_native_panel_discovery_and_self_contained_resource(self):
        initialized = self.request('initialize', {'protocolVersion':'2025-06-18'})['result']
        self.assertEqual(initialized['serverInfo']['version'], '0.7.6')
        definitions = self.request('tools/list')['result']['tools']
        panel = next(x for x in definitions if x['name'] == 'open_notes_panel')
        self.assertEqual(panel['_meta']['ui']['resourceUri'], PANEL_URI)
        self.assertEqual(panel['_meta']['openai/ui']['entrypoints'], [{'type':'thread'}])
        for definition in definitions:
            if definition != panel:
                self.assertEqual(definition['_meta']['ui']['visibility'], ['app'])
        resource = self.request('resources/read', {'uri':PANEL_URI})['result']['contents'][0]
        self.assertEqual(resource['mimeType'], 'text/html;profile=mcp-app')
        self.assertEqual(resource['_meta']['ui']['csp'], {'connectDomains':[], 'resourceDomains':[]})
        self.assertIn('<script type="module">', resource['text'])
        self.assertNotIn('/*APP_BUNDLE*/', resource['text'])
        self.assertEqual(self.request('resources/read', {'uri':'file:///private'})['error']['code'], -32602)

    def test_full_sources_stay_component_private(self):
        result = self.call('open_notes_panel')
        visible = json.dumps({'content':result['content'], 'structuredContent':result['structuredContent']})
        self.assertNotIn('source-question-sentinel', visible)
        self.assertEqual(result['structuredContent'], {'ok':True})
        self.assertIn('source-question-sentinel', json.dumps(result['_meta']))

    def test_deleted_note_is_recoverable_and_survives_restart_and_sync(self):
        with Archive(self.data) as archive:
            nid=archive.save('完整回答-sentinel', self.answer)
            original=archive.note(nid)
        result=self.call('set_note_deleted',{'note_id':nid,'deleted':True})
        self.assertEqual(result['structuredContent'],{'ok':True})
        active=self.server.data({})
        self.assertEqual(active['counts']['notes'],0)
        self.assertEqual(active['notes'],[])
        self.assertEqual(active['trash_count'],1)
        self.assertEqual(active['sessions'][0]['note_count'],0)
        replacement=NotesServer(self.data,watch=False)
        with Archive(self.data) as archive:
            archive.answer('session1','turn1','🙂 甲**乙** 与甲乙。\n完整回答-sentinel')
            archive.db.commit()
            self.assertEqual(archive.notes(),[])
            self.assertEqual(archive.note(nid)['answer_snapshot'],original['answer_snapshot'])
        trash=replacement.data({'collection':'trash'})
        self.assertEqual([n['id'] for n in trash['trash']],[nid])
        self.assertEqual(trash['trash_total'],1)
        self.assertEqual(self.server.data({'collection':'trash','search':'does-not-exist'})['trash'],[])
        restored=self.call('set_note_deleted',{'note_id':nid,'deleted':False})
        self.assertFalse(restored['_meta']['chat-notes/result']['deleted'])
        restored=self.server.data({})
        self.assertEqual(restored['counts']['notes'],1)
        self.assertEqual(restored['trash_count'],0)
        self.assertEqual(restored['notes'][0]['id'],nid)
        with Archive(self.data,readonly=True) as archive:
            current=archive.note(nid)
            self.assertEqual((current['raw_start'],current['raw_end'],current['answer_snapshot']),
                             (original['raw_start'],original['raw_end'],original['answer_snapshot']))

    def test_hidden_session_preserves_questions_notes_and_survives_new_turn(self):
        with Archive(self.data) as archive:
            nid=archive.save('完整回答-sentinel',self.answer)
        self.call('set_session_hidden',{'session_id':'session1','hidden':True})
        with Archive(self.data) as archive:
            archive.question('session1','turn2','新增的问题','u2')
            archive.db.commit()
        replacement=NotesServer(self.data,watch=False)
        data=replacement.data({'collection':'hidden'})
        self.assertEqual(data['hidden_count'],1)
        self.assertEqual(data['hidden_total'],1)
        self.assertEqual(data['hidden_sessions'][0]['question_count'],2)
        self.assertTrue(data['sessions'][0]['hidden'])
        self.assertEqual(data['notes'][0]['id'],nid)
        self.assertEqual(data['counts'],{'questions':2,'answers':1,'notes':1})
        self.call('set_session_hidden',{'session_id':'session1','hidden':False})
        self.assertEqual(self.server.data({})['hidden_count'],0)
        self.assertFalse(self.server.data({})['sessions'][0]['hidden'])

    def test_management_rejects_unknown_ids_and_invalid_status(self):
        for tool,args in [('set_note_deleted',{'note_id':'missing','deleted':True}),
                          ('set_session_hidden',{'session_id':'missing','hidden':True}),
                          ('set_note_deleted',{'note_id':'missing','deleted':'false'}),
                          ('set_session_hidden',{'session_id':'session1','hidden':1}),
                          ('notes_panel_data',{'collection':'anything'})]:
            self.assertTrue(self.call(tool,args)['isError'])
        self.assertEqual(self.server.data({})['hidden_count'],0)

    def test_conversation_groups_titles_and_chronological_paging(self):
        sessions = self.root / 'sessions'
        sessions.mkdir()
        index = self.root / 'session_index.jsonl'
        index.write_text(json.dumps({'id':'session1','thread_name':'旧标题'})+'\ninvalid\n'+
            json.dumps({'id':'session1','thread_name':'我的会话'})+'\n', encoding='utf-8')
        self.server.sessions = sessions
        with Archive(self.data) as archive:
            archive.question('session1','late','晚问题','late','2026-10-11T11:00:00+08:00')
            archive.question('session1','early','早问题','early','2026-10-11T01:00:00Z')
            archive.question('session1','equal','同一时刻','equal','2026-10-11T03:00:00Z')
            archive.question('session2','other','另一个会话的问题','other','2026-10-11T00:00:00Z')
            archive.db.execute("UPDATE questions SET timestamp='2026-10-12T00:00:00Z' WHERE message_id='u1'")
            archive.db.commit()
        groups = self.call('notes_panel_data')['_meta']['chat-notes/result']['sessions']
        one = next(s for s in groups if s['id']=='session1')
        two = next(s for s in groups if s['id']=='session2')
        self.assertEqual((one['title'],one['question_count']),('我的会话',4))
        self.assertEqual(two['title'],'另一个会话的问题')
        args={'session_id':'session1','limit':2}
        first=self.call('notes_panel_data',args)['_meta']['chat-notes/result']
        second=self.call('notes_panel_data',{**args,'offset':2})['_meta']['chat-notes/result']
        self.assertEqual([q['text'] for q in first['questions']],['早问题','晚问题'])
        self.assertEqual([q['text'] for q in second['questions']],['同一时刻','source-question-sentinel'])
        self.assertEqual(first['questions_total'],4)
        self.assertTrue(all(q['session_id']=='session1' for q in first['questions']+second['questions']))

    def test_notes_and_excerpt_search_stay_in_selected_conversation(self):
        with Archive(self.data) as archive:
            archive.question('session2','turn2','会话二','u2')
            second=archive.answer('session2','turn2','甲乙，另一个会话的回答')
            first_note=archive.save('完整回答-sentinel',self.answer)
            second_note=archive.save('甲乙，另一个会话的回答',second)
            archive.db.commit()
        one=self.call('notes_panel_data',{'session_id':'session1'})['_meta']['chat-notes/result']
        two=self.call('notes_panel_data',{'session_id':'session2'})['_meta']['chat-notes/result']
        self.assertEqual([n['id'] for n in one['notes']],[first_note])
        self.assertEqual([n['id'] for n in two['notes']],[second_note])
        filtered=self.call('notes_panel_data',{'session_id':'session2','search':'不存在'})['_meta']['chat-notes/result']
        self.assertEqual(filtered['notes_total'],0)
        self.assertEqual(filtered['questions_total'],0)
        matches=self.call('match_excerpt',{'excerpt':'甲乙','session_id':'session1'})['_meta']['chat-notes/result']['matches']
        self.assertTrue(matches)
        self.assertTrue(all(m['session_id']=='session1' for m in matches))
        with patch('notes_mcp.os.startfile',create=True) as launch:
            bad=self.call('open_saved_conversation',{'session_id':'missing'})
            self.assertTrue(bad['isError']);launch.assert_not_called()

    def test_exact_selection_past_candidate_limit_keeps_unicode_position(self):
        with Archive(self.data) as archive:
            archive.question('session1','repeated','重复内容来源','repeated-user')
            raw='🙂 '+('同样文字。'*70)
            answer=archive.answer('session1','repeated',raw)
            archive.db.commit()
        start=2+5*60
        args={'excerpt':'同样文字','answer_id':answer,'start':start}
        match=self.call('match_excerpt',args)['_meta']['chat-notes/result']
        self.assertEqual(match['total'],1)
        self.assertEqual(match['matches'][0]['start'],start)
        saved=self.call('save_excerpt',args)['_meta']['chat-notes/result']['note']
        self.assertEqual(saved['raw_start'],start)
        self.assertEqual(saved['answer_snapshot'][start:saved['raw_end']],'同样文字')

    def test_ambiguous_unicode_capture_and_saved_snapshot(self):
        matches = self.call('match_excerpt', {'excerpt':'甲乙'})['_meta']['chat-notes/result']['matches']
        self.assertEqual({x['method'] for x in matches}, {'exact', 'markdown'})
        selected = next(x for x in matches if x['method'] == 'markdown')
        self.assertEqual(selected['start'], 2)  # Python code points, including the emoji.
        result = self.call('save_excerpt', {'excerpt':'甲乙', 'answer_id':self.answer,
            'start':selected['start'], 'comment':'我的理解'})['_meta']['chat-notes/result']
        note = self.call('get_note_source', {'note_id':result['saved']})['_meta']['chat-notes/result']
        self.assertEqual(note['question_snapshot'], 'source-question-sentinel')
        self.assertEqual(note['answer_snapshot'][note['raw_start']:note['raw_end']], '甲**乙')
        self.assertEqual(note['comment'], '我的理解')
        with Archive(self.data) as archive:
            archive.db.execute('DELETE FROM answers')
            archive.db.commit()
        snapshot = self.call('get_note_source', {'note_id':note['id']})['_meta']['chat-notes/result']
        self.assertEqual(snapshot['answer_snapshot'], note['answer_snapshot'])

    def test_invalid_writes_and_protocol_inputs_are_rejected(self):
        for args in ({'excerpt':'甲乙'}, {'excerpt':'甲乙', 'answer_id':self.answer, 'start':True},
                     {'excerpt':'wrong', 'answer_id':self.answer, 'start':2},
                     {'excerpt':'甲乙', 'answer_id':self.answer, 'start':2, 'extra':'unexpected'}):
            self.assertTrue(self.call('save_excerpt', args)['isError'])
        with Archive(self.data, readonly=True) as archive:
            self.assertEqual(archive.counts()['notes'], 0)
        for message in ([], {'jsonrpc':'2.0','id':True,'method':'ping'},
                        {'jsonrpc':'2.0','id':1,'method':'tools/call','params':[]}):
            self.assertEqual(self.server.dispatch(message)['error']['code'], -32600)
        result = self.request('tools/call', {'name':'read_copied_excerpt', 'arguments':False})['result']
        self.assertTrue(result['isError'])

    def test_clipboard_read_is_explicit_and_private(self):
        with patch('notes_mcp.clipboard_text', return_value='clipboard-private-sentinel') as clipboard:
            self.call('open_notes_panel')
            self.call('notes_panel_data')
            clipboard.assert_not_called()
            result = self.call('read_copied_excerpt')
            clipboard.assert_called_once()
            self.assertEqual(result['_meta']['chat-notes/result']['excerpt'], 'clipboard-private-sentinel')
            self.assertNotIn('clipboard-private-sentinel', json.dumps(result['content']))

    def test_stdio_roundtrip_has_only_protocol_output(self):
        messages = [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'open_notes_panel','arguments':{}}},
        ]
        completed = subprocess.run([sys.executable, str(ROOT/'scripts/notes_mcp.py'),
            '--data-dir', str(self.data), '--no-watch'],
            input='\n'.join(json.dumps(x) for x in messages)+'\n', capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([x['id'] for x in output], [1,2,3])
        self.assertEqual(output[-1]['result']['structuredContent'], {'ok':True})

    def test_watcher_records_without_panel_being_open(self):
        path = self.root / 'session.jsonl'
        rows = [
            {'type':'session_meta','payload':{'id':'session2','thread_source':'user','source':'vscode'}},
            {'type':'event_msg','payload':{'type':'task_started','turn_id':'turn2'}},
            {'type':'event_msg','payload':{'type':'item_completed','turn_id':'turn2','item':{
                'type':'UserMessage','id':'u2','content':[{'type':'text','text':'后台问题'}]}}},
            {'type':'event_msg','payload':{'type':'task_complete','turn_id':'turn2','last_agent_message':'后台回答'}},
        ]
        path.write_text(''.join(json.dumps(x, ensure_ascii=False)+'\n' for x in rows), encoding='utf-8')
        server = NotesServer(self.data, sessions=path, watch=True)
        try:
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                with Archive(self.data, readonly=True) as archive:
                    if archive.counts()['questions'] == 2:
                        self.assertEqual(archive.counts()['answers'], 2)
                        break
                time.sleep(.05)
            else:
                self.fail('Background transcript sync did not complete')
        finally:
            server.stop.set()
            time.sleep(.1)


if __name__ == '__main__':
    unittest.main()
