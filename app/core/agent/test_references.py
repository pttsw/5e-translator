import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from langchain_core.messages import AIMessage

from .references import ReferenceContentProcessor, ReferenceStore, bilingual_name, text_blocks
from .reference_tools import ReferenceTools, TemporaryTermStore
from .reference_adapter import ReferenceToolAdapter
from .buquanshu_search import BuquanshuSearcher, BuquanshuSearchError
from .graph import TranslationGraph
from .state import AgentOptions

TEXT = '''# 战斗魔宠 Battle Familiar
二环 咒法（德鲁伊，魔契师，法师）
你咒唤出一只充满魔法力量的魔宠。
## 战斗 Combat
它可以进行攻击。
# 咒法师华盖 Conjurer's Canopy
奇物，普通
它创造一个透明力场华盖。华盖消失后，等待1d12小时才能再次使用。
'''


class EmptySearch:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def search(self, *args, **kwargs):
        return {'results': []}


def service(store=None, **kwargs):
    return ReferenceTools(store, term_reader=lambda query: [], searcher_factory=EmptySearch, **kwargs)


@pytest.mark.parametrize('text,pair', [
    ('火球术 Fireball', ('Fireball', '火球术')), ('火球术Fireball', ('Fireball', '火球术')),
    ('火球术（Fireball）', ('Fireball', '火球术')), ('Fireball（火球术）', ('Fireball', '火球术')),
    ('火球术 | Fireball', ('Fireball', '火球术')), ('Fireball 火球术', ('Fireball', '火球术')),
    ('你施展火球术 Fireball 攻击敌人。', None), ('Chinese\nEnglish', None),
])
def test_bilingual_evidence(text, pair):
    assert bilingual_name(text) == pair


def test_multiple_entities_nested_headings_and_evidence():
    result = ReferenceContentProcessor().process(TEXT, source_id='ref')
    entity = next(e for e in result['entities'] if e['name_en'] == 'Battle Familiar')
    assert entity['entity_type'] == 'spell'
    assert entity['complete']
    assert '它可以进行攻击。' in entity['description_cn']
    assert '华盖' not in entity['description_cn']
    for pair in result['translations']:
        evidence = pair['evidence_location']
        quote = TEXT[evidence['start']:evidence['end']]
        assert pair['en'] in quote and pair['cn'] in quote


def test_flat_forum_references_are_not_entity_boundaries():
    text = '''内容:
战斗魔宠 Battle Familiar
二环 咒法
你可以强化法术
寻获魔宠Find Familiar
赋予的魔宠，该生物保留原本的生物类型、阵营、生命值和其他特征。
升环施法。
高环阶会增强效果。
奥法艺术家 Arcane Artist
起源专长
你获得以下增益。
戏法 Cantrip。
你习得戏法
次级幻象Minor Illusion
。你施展该法术的施法属性是智力。
'''
    entities = ReferenceContentProcessor().process(text, source_id='forum')['entities']
    assert [e['name_en'] for e in entities] == ['Battle Familiar', 'Arcane Artist']
    assert '高环阶会增强效果。' in entities[0]['description_cn']
    assert 'Minor Illusion' in entities[1]['description_cn']


def test_toc_and_arbitrary_adjacent_names_do_not_become_full_entities():
    result = ReferenceContentProcessor().process('火球术 Fireball\n魔法飞弹 Magic Missile\nFire Shield\n火盾术', source_id='toc')
    assert not any(e['complete'] for e in result['entities'])
    assert len(result['translations']) == 2


def test_explicit_translation_table_preserves_category_and_conflict():
    text = '| English | 中文 | 类别 |\n| Fireball | 火球术 | spell |\n| Fireball | 火球 | spell |'
    result = ReferenceContentProcessor().process(text, source_id='table')
    assert [(t['en'], t['cn'], t['category']) for t in result['translations']] == [
        ('Fireball', '火球术', 'spell'), ('Fireball', '火球', 'spell')]
    assert all(t['conflict'] for t in result['translations'])


def test_failed_update_retains_previous_version(tmp_path):
    path = tmp_path / 'reference.txt'
    path.write_text(TEXT)
    store = ReferenceStore()
    store.load_path(str(path), (tmp_path,))
    summary = store.summary()
    path.write_bytes(b'\xff')
    assert store.load_path(str(path), (tmp_path,))['failed'] == 1
    assert store.summary() == summary


def test_fallback_is_once_and_rejects_fabricated_evidence():
    text = '这段文字讲述一个无名之物。'
    fallback = Mock(return_value={'entities': [{'start': 0, 'end': len(text), 'name_en': 'Invented',
                                               'name_cn': '无名之物', 'description_cn': text}]})
    result = ReferenceContentProcessor(fallback).process(text, source_id='ref')
    assert result['entities'] == []
    fallback.assert_called_once_with(text)
    fallback.return_value['entities'][0]['name_en'] = ''
    result = ReferenceContentProcessor(fallback).process(text, source_id='ref')
    assert result['entities'][0]['name_cn'] == '无名之物'
    assert result['entities'][0]['complete'] is False
    assert result['translations'] == []


def test_ref_commands_versioning_state_and_session_isolation(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    reference = tmp_path / 'references'
    reference.mkdir()
    path = reference / 'a file.txt'
    path.write_text(TEXT)
    options = AgentOptions(source, tmp_path / 'out', reference_roots=(reference,))
    graph = TranslationGraph(options, references=service())
    first = graph.invoke('/ref "a file.txt"')
    assert first['reference_result']['success'] == 1
    assert TEXT not in json.dumps(first, ensure_ascii=False)
    assert 'reference_chunks' not in first
    assert graph.invoke('/ref "a file.txt"')['reference_result']['skipped'] == 1
    path.write_text(TEXT.replace('战斗魔宠', '战斗使魔'))
    changed = graph.invoke('/ref "a file.txt"')
    assert changed['reference_index_ids'] != first['reference_index_ids']
    assert len(changed['reference_index_ids']) == 1
    assert TranslationGraph(options, references=service()).invoke('/refs')['references'] == []
    assert graph.invoke('/refs')['references']
    assert graph.invoke('/clear-refs')['references'] == []
    assert graph.references.lookup_entities(['Battle Familiar'])['results'] == []


def test_bad_reference_does_not_pollute_other_files(tmp_path):
    (tmp_path / 'ok.txt').write_text(TEXT)
    (tmp_path / 'bad.txt').write_bytes(b'\xff\xff')
    (tmp_path / 'unsupported.pdf').write_text('PDF')
    store = ReferenceStore()
    report = store.load_path(str(tmp_path), (tmp_path,))
    assert (report['success'], report['failed'], report['skipped']) == (1, 1, 1)
    assert report['unsupported'] == [str(tmp_path / 'unsupported.pdf')]
    assert len(store.documents) == 1
    outside = tmp_path.parent / 'outside-reference.txt'
    outside.write_text(TEXT)
    (tmp_path / 'escape.txt').symlink_to(outside)
    with pytest.raises(ValueError, match='超出'):
        store.load_path(str(tmp_path / 'escape.txt'), (tmp_path,))


def test_four_source_ranking_conflicts_phrase_queries_and_dedupe():
    store = ReferenceStore()
    store.add('# 火球术 Fireball\n三环 塑能\n造成火焰伤害。\n火球术 Fireball', 'session')
    store.add('# 火球 Fireball\n三环 塑能\n造成火焰伤害。', 'page', source_type='buquanshu')
    reader = Mock(return_value=[{'en': 'Fireball', 'cn': '火球术', 'category': 'spell', 'source': 'PHB'}])
    refs = ReferenceTools(store, term_reader=reader, searcher_factory=EmptySearch)
    from app.core.bean.term import Term
    refs.temporary.terms[('Fireball', '炎爆术', 'spell')] = Term('Fireball', 'spell', '炎爆术')
    response = refs.lookup_translations(['Fireball'], category='spell', limit_per_term=10)
    assert [r['source_rank'] for r in response['results']] == [1, 2, 3, 4]
    assert all(r['conflict'] for r in response['results'])
    assert response['results'][0]['confidence'] > response['results'][1]['confidence']
    refs.lookup_translations(['saving throw'])
    assert [call.args[0] for call in reader.call_args_list] == ['Fireball', 'saving throw']
    refs.lookup_translations(['Fireball'])
    assert reader.call_count == 2
    refs.end_run()
    assert refs.temporary.snapshot() == []


def test_entities_alias_and_full_body_budget():
    store = ReferenceStore()
    store.add(TEXT, 'local')
    refs = service(store)
    for query in ('Battle Familiar', '战斗魔宠', '战斗魔宠 Battle Familiar'):
        response = refs.lookup_entities([query], limit_per_name=1)
        assert response['results'][0]['complete']
        assert '它可以进行攻击。' in response['results'][0]['description_cn']
    response = service(store, max_chars=200).lookup_entities(['Battle Familiar'])
    assert response['truncated'] and not response['results']


def test_full_page_fetch_and_redirect_boundaries(monkeypatch):
    monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('8.8.8.8', 443))])
    def handler(request):
        if request.url.path == '/escape':
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/private'})
        return httpx.Response(200, headers={'content-type': 'text/html'}, text='''<html><body>
          <nav>不要保留导航</nav><h4>火球术｜Fireball</h4><p>三环 塑能<br>造成火焰伤害。</p>
          <h4>飞行术｜Fly</h4><p>三环 变化<br>赋予飞行能力。</p></body></html>''')
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        searcher = BuquanshuSearcher(http_client=client)
        page = searcher.fetch_page(searcher.site_url + 'ok')
        assert '导航' not in page['text']
        result = ReferenceContentProcessor().process(page['text'], source_id='page', blocks=page['blocks'])
        assert len(result['entities']) == 2
        assert all(e['complete'] for e in result['entities'])
        with pytest.raises(BuquanshuSearchError):
            searcher.fetch_page(searcher.site_url + 'escape')
        with pytest.raises(BuquanshuSearchError, match='budget'):
            searcher.fetch_page(searcher.site_url + 'ok', max_bytes=4)
        monkeypatch.setattr('socket.getaddrinfo', lambda *a, **k: [(2, 1, 6, '', ('127.0.0.1', 443))])
        with pytest.raises(BuquanshuSearchError, match='public'):
            searcher.fetch_page(searcher.site_url + 'ok')


def test_remote_preview_rejected_and_failures_are_isolated():
    searcher = EmptySearch()
    searcher.search = Mock(return_value={'results': [{'url': 'https://example.test/a'}]})
    searcher.fetch_page = Mock(return_value={'text': '火球术 Fireball', 'complete': False})
    refs = ReferenceTools(term_reader=lambda q: [], searcher_factory=lambda: searcher)
    response = refs.lookup_entities(['Fireball'])
    assert response['results'] == [] and response['errors'][0]['code'] == 'preview_only'
    assert refs.store.documents == {}
    refs.lookup_entities(['Fireball'])
    searcher.search.assert_called_once()


def test_task_tool_results_are_lazy_cached_and_never_enter_other_requests():
    refs = service()
    refs.store.add(TEXT, 'secret-session-reference')
    response = {'query': ['Battle Familiar'], 'results': [], 'truncated': False, 'errors': []}
    lookup = Mock(return_value=response)
    refs.lookup_translations = lookup
    model = Mock()
    model.bind_tools.return_value.bind.return_value = model
    call = {'name': 'lookup_translations', 'args': {'en_list': ['Battle Familiar']}, 'id': 'tool-1', 'type': 'tool_call'}
    model.invoke.side_effect = [AIMessage(content='', tool_calls=[call]), AIMessage(content='', tool_calls=[call]),
                                AIMessage(content='{"trans_str":"译文"}'), AIMessage(content='{"trans_str":"另一次"}')]
    adapter = ReferenceToolAdapter(type('Adapter', (), {'llm': model})(), refs)
    # Copy messages at invocation time, so subsequent local mutation cannot affect assertions.
    requests = []
    answers = iter(model.invoke.side_effect)
    model.invoke.side_effect = lambda messages: (requests.append(list(messages)), next(answers))[1]
    text, status = adapter.sendText('source', 'JSON only')
    assert '译文' in text
    lookup.assert_called_once_with(en_list=['Battle Familiar'])
    assert len(requests[0]) == 2 and TEXT not in str(requests[0])
    adapter.sendText('next source', 'JSON only')
    assert len(requests[-1]) == 2
    assert 'secret-session-reference' not in str(requests[-1])
    bound_tools = model.bind_tools.call_args.args[0]
    assert bound_tools
    assert all(tool['function']['strict'] is True for tool in bound_tools)


def test_tool_round_limit_and_failures():
    refs = service()
    model = Mock()
    model.bind_tools.return_value.bind.return_value = model
    call = {'name': 'lookup_entities', 'args': {'names': ['missing']}, 'id': 't', 'type': 'tool_call'}
    model.invoke.return_value = AIMessage(content='', tool_calls=[call])
    adapter = ReferenceToolAdapter(type('Adapter', (), {'llm': model})(), refs)
    answer, status = adapter.sendText('source', 'JSON only')
    assert answer is None and model.invoke.call_count == adapter.MAX_ROUNDS


def test_category_threshold_and_provider_timeout_preserve_session_results():
    store = ReferenceStore()
    store.add(TEXT, 'ref')
    reader = Mock(side_effect=TimeoutError('sensitive connection details'))
    refs = ReferenceTools(store, term_reader=reader, searcher_factory=EmptySearch)
    response = refs.lookup_translations(['Battle Familiar'], category='spell')
    assert response['results'][0]['source_type'] == 'session_reference'
    assert response['errors'] == [{'source_type': 'term_table', 'code': 'TimeoutError'}]
    assert refs.lookup_translations(['Battle Familiar'], category='item')['results'] == []
    assert service(store, min_confidence=.95).lookup_translations(['Battle Familiar'])['results'] == []


def test_temporary_store_captures_finished_processor_without_retaining_it():
    from threading import Lock
    from app.core.bean.term import Term
    processor = type('Processor', (), {'cache_lock': Lock(), 'temple_terms': {Term('Shield', 'spell', '护盾术')}})()
    temporary = TemporaryTermStore()
    temporary.bind(processor)
    temporary.finish(processor)
    assert temporary.processors == []
    assert temporary.snapshot()[0]['cn'] == '护盾术'
    temporary.clear()
    assert temporary.snapshot() == []


def test_agent_ref_and_chat_share_tools_and_clear_preserves_references(tmp_path):
    from .agent import DNDTranslatorAgent
    from .tools import ToolRegistry
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'ref.txt').write_text(TEXT)
    agent = DNDTranslatorAgent.__new__(DNDTranslatorAgent)
    refs = service()
    agent.tool_registry = ToolRegistry(refs)
    agent.translation_options = AgentOptions(source, tmp_path / 'output')
    agent.messages = []
    state = json.loads(agent.execute_command('/ref ref.txt'))
    assert state['reference_result']['success'] == 1
    assert agent.translation_graph.references is refs
    agent.execute_command('/clear')
    assert json.loads(agent.execute_command('/refs'))['references']
    agent.execute_command('/clear-refs')
    assert refs.store.summary() == []
