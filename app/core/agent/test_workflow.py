"""Offline acceptance tests using the real memory DB and mock LLM."""
import json
from pathlib import Path
from unittest.mock import Mock
import pytest
from app.core.agent.commands import CommandError, discover_files, parse_command, validate_path
from app.core.agent.graph import TranslationGraph
from app.core.agent.nodes import TranslationNodes
from app.core.agent.state import AgentOptions
from app.core.agent.validation import ValidationError, validate_file


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv('TRANSLATOR_DB_BACKEND', 'memory')
    monkeypatch.setenv('TRANSLATOR_DISABLE_REDIS', '1')
    monkeypatch.setenv('TRANSLATOR_LLM_BACKEND', 'mock')
    monkeypatch.delenv('TRANSLATOR_MEMORY_DB_SEED', raising=False)
    monkeypatch.delenv('TRANSLATOR_MOCK_LLM_SEED', raising=False)
    from app.core.database.memory_db_dictionary import MemoryDBDictionary
    monkeypatch.setattr(MemoryDBDictionary, '_instance', None)
    source = tmp_path / 'source'
    source.mkdir()
    return AgentOptions(source, tmp_path / 'output', thread_num=1), MemoryDBDictionary()


def write_source(options, name='spells/spells-test.json'):
    path = options.source_root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'spell': [{'name': 'Test Light', 'source': 'PHB', 'level': 0,
                                         'entries': ['You create a bright light.', 'Roll {@dice 1d6}.']}]}), encoding='utf-8')
    return path


@pytest.mark.parametrize('text,path', [('/file a b.json', 'a b.json'),
                                     ('/file "a b.json"', 'a b.json'),
                                     ("/file 'a b.json'", 'a b.json')])
def test_command_paths(text, path):
    assert parse_command(text).path == path


@pytest.mark.parametrize('text', ['/file', '/filex x', '/unknown', '/clear extra', '/file "x'])
def test_invalid_commands(text):
    with pytest.raises(CommandError):
        parse_command(text)


def test_path_policy_and_discovery(workspace, tmp_path):
    options, _ = workspace
    a = write_source(options, 'a.json')
    b = write_source(options, 'nested/b.json')
    (options.source_root / 'ignored.txt').write_text('ignored')
    assert discover_files(options.source_root, options.source_root) == [a, b]
    outside = tmp_path / 'outside.json'
    outside.write_text('{}')
    link = options.source_root / 'escape.json'
    link.symlink_to(outside)
    for path in ['../outside.json', str(link), 'ignored.txt', 'missing.json']:
        with pytest.raises(CommandError):
            validate_path(path, options.source_root)
    with pytest.raises(CommandError):
        discover_files(options.source_root, options.source_root)


def test_empty_directory_and_unsupported_command(workspace):
    options, _ = workspace
    graph = TranslationGraph(options)
    for text in ['/file .', '/ref .', '/filex .']:
        assert graph.invoke(text)['stage'] == 'failed'


def test_output_cannot_overlap_source(workspace):
    options, _ = workspace
    for out in [options.source_root / 'out', options.source_root.parent]:
        with pytest.raises(CommandError):
            AgentOptions(options.source_root, out)


def test_real_pipeline_offline_and_legacy_compatibility(workspace, monkeypatch):
    options, db = workspace
    source = write_source(options)
    original = source.read_bytes()
    from app.core.translator.mock_adapter import MockAdapter
    translations = {'Test Light': '测试光亮', 'You create a bright light.': '你创造一道明亮的光。',
                    'Roll {@dice 1d6}.': '投掷{@dice 1d6}。'}
    monkeypatch.setattr(MockAdapter, '_translate', lambda self, en_str, uid='': translations.get(en_str, en_str))
    events = []
    state = TranslationGraph(options).invoke('/file spells/spells-test.json', on_progress=lambda s: events.append(s['stage']))
    assert state['progress']['success'] == 1, state
    assert events == ['analyse_and_reuse', 'translate', 'generate', 'validate', 'persist_and_write']
    result = state['file_results'][0]
    assert source.read_bytes() == original
    output = json.loads(Path(result['output_path']).read_text())
    assert output['spell'][0]['name'] == '测试光亮'
    assert output['spell'][0]['level'] == 0
    assert output['spell'][0]['source'] == 'PHB'
    assert '{!@' not in json.dumps(output)
    assert db.store_records and all(not row['proofread'] for row in db.store_records)
    assert db.word_usage and all(row.get('uid') for row in db.word_usage)
    assert all(Path(path).is_file() for path in result['cache_paths'])
    json.dumps(state)
    # Compare the CLI component chain using a fresh DB.
    from app.core.database.memory_db_dictionary import MemoryDBDictionary
    monkeypatch.setattr(MemoryDBDictionary, '_instance', None)
    from app.core.translator import (JsonAnalyser, JobNeedTranslateSetter, KnowledgeSetter,
                                    ByHandHandler, TermSetter, BatchJobProcessor, JsonGenerator)
    config = {'metadata': {'mode': '5et', 'cache': True, 'output_root': str(options.output_root / 'legacy'),
                           'generator_update_db': False}}
    infos = list(JsonAnalyser(source_root=str(options.source_root)).invoke([str(source)], config=config))
    for stage in [JobNeedTranslateSetter(), KnowledgeSetter(), ByHandHandler(), TermSetter(),
                  BatchJobProcessor(1, update=False), JsonGenerator(1)]:
        infos = list(stage.invoke(infos, config=config))
    assert infos[0].cn_obj == output
    saved_jobs = json.loads(Path(result['cache_paths'][1]).read_text())
    assert {job.uid for job in infos[0].job_list} == {job['uid'] for job in saved_jobs}


def test_file_does_not_invoke_automatic_reference_setters(workspace, monkeypatch):
    options, _ = workspace
    write_source(options)
    from app.core.translator.knowledge_setter import KnowledgeSetter
    from app.core.translator.term_setter import TermSetter
    from app.core.translator.byhand_handler import ByHandHandler

    constructors = []
    for cls in (KnowledgeSetter, TermSetter, ByHandHandler):
        constructor = Mock(side_effect=AssertionError(f'/file must not construct {cls.__name__}'))
        monkeypatch.setattr(cls, '__init__', constructor)
        constructors.append(constructor)

    state = TranslationGraph(options).invoke('/file spells/spells-test.json')
    assert state['progress']['success'] == 1, state
    for constructor in constructors:
        constructor.assert_not_called()


def test_directory_failure_isolation_and_no_early_writes(workspace):
    options, db = workspace
    (options.source_root / 'a.json').write_text('{invalid')
    write_source(options, 'b.json')
    events = []

    class ObservedNodes(TranslationNodes):
        def validate(self, state, result):
            assert db.store_records == []
            assert self.processor.update is False
            assert self.config['metadata']['generator_update_db'] is False
            super().validate(state, result)
            events.append('validated')

    state = TranslationGraph(options, nodes_factory=ObservedNodes).invoke('/file .')
    assert [row['status'] for row in state['file_results']] == ['failed', 'success'], state
    assert events == ['validated']
    assert state['file_results'][0]['failed_stage'] == 'analyse_and_reuse'


def test_validation_failure_preserves_database_and_existing_output(workspace):
    options, db = workspace
    write_source(options, 'a.json')
    options.output_root.mkdir()
    old = options.output_root / 'a.json'
    old.write_text('previous output')

    class InvalidNodes(TranslationNodes):
        def generate(self, state, result):
            super().generate(state, result)
            self.info.cn_obj['spell'][0]['level'] = 5

    state = TranslationGraph(options, nodes_factory=InvalidNodes).invoke('/file a.json')
    assert state['file_results'][0]['failed_stage'] == 'validate', state
    assert db.store_records == []
    assert old.read_text() == 'previous output'
    assert (Path(state['file_results'][0]['staging_path']) / 'a.json').is_file()


def test_output_symlink_escape_is_rejected(workspace, tmp_path):
    options, db = workspace
    write_source(options, 'a.json')
    options.output_root.mkdir()
    target = tmp_path / 'target'
    target.write_text('keep')
    (options.output_root / 'a.json.jobs').symlink_to(target)
    state = TranslationGraph(options).invoke('/file a.json')
    assert state['progress']['failed'] == 1
    assert target.read_text() == 'keep'
    assert db.store_records == []


def test_concurrent_submission_preserves_active_state(workspace):
    options, _ = workspace
    write_source(options, 'a.json')
    graph = TranslationGraph(options)

    def reenter(state):
        with pytest.raises(CommandError, match='正在运行'):
            graph.invoke('/file a.json')
        assert graph.state['source_paths'] == state['source_paths']

    assert graph.invoke('/file a.json', on_progress=reenter)['progress']['success'] == 1


def test_validator_rejects_unresolved_and_modified_fields():
    from app.core.utils.file_work_info import FileWorkInfo
    from app.core.utils.job import Job
    job = Job('uid', 'Hello', '你好')
    info = FileWorkInfo([job], {'name': '{!@ uid}', 'source': 'PHB'}, 'x', 'x')
    info.cn_obj = {'name': '你好', 'source': 'PHB'}
    assert validate_file(info)['passed']
    for candidate in [{'name': '{!@ uid}', 'source': 'PHB'}, {'name': '错误译文', 'source': 'PHB'}, {'name': '你好', 'source': 'X'},
                      {'name': 1, 'source': 'PHB'}]:
        info.cn_obj = candidate
        with pytest.raises(ValidationError):
            validate_file(info)
    job.cn_str = None
    info.cn_obj = {'name': '你好', 'source': 'PHB'}
    with pytest.raises(ValidationError, match='unfinished_uid'):
        validate_file(info)


def test_agent_commands_bypass_chat_model(workspace):
    from app.core.agent.agent import DNDTranslatorAgent
    options, _ = workspace
    agent = DNDTranslatorAgent.__new__(DNDTranslatorAgent)
    agent.agent = Mock()
    agent.translation_options = options
    result = json.loads(agent.chat('/file missing.json'))
    assert result['stage'] == 'failed'
    assert list(agent.chat('/clear', stream=True)) == ['已清空对话；文件运行记录与会话参考资料保留。']
    agent.agent.invoke.assert_not_called()


def test_proofread_reuse_does_not_call_llm_or_overwrite_records(workspace, monkeypatch):
    options, db = workspace
    source = write_source(options, 'a.json')
    from app.core.translator.analyser.json_analyser import JsonAnalyser
    from app.core.translator.mock_adapter import MockAdapter
    info = list(JsonAnalyser(source_root=str(options.source_root)).invoke([str(source)], config={'metadata': {}}))[0]
    for job in info.job_list:
        db.put(job.en_str, job.en_str, job.rel_path, tag=job.tag, proofread=True, job_context=job)
    records = json.dumps(db.store_records, default=str)
    send = Mock(side_effect=AssertionError('reused jobs must not call LLM'))
    monkeypatch.setattr(MockAdapter, 'sendText', send)
    state = TranslationGraph(options).invoke('/file a.json')
    assert state['progress']['success'] == 1, state
    assert state['file_results'][0]['pending'] == 0
    assert json.dumps(db.store_records, default=str) == records
    send.assert_not_called()


def test_processor_failure_does_not_publish_or_persist(workspace, monkeypatch):
    options, db = workspace
    write_source(options, 'a.json')
    from app.core.translator.batch_job_processor import BatchJobProcessor
    monkeypatch.setattr(BatchJobProcessor, 'invoke', lambda *args, **kwargs: iter([]))
    state = TranslationGraph(options).invoke('/file a.json')
    assert state['file_results'][0]['failed_stage'] == 'translate'
    assert not (options.output_root / 'a.json').exists()
    assert db.store_records == []


def test_persistence_failure_keeps_candidate_and_existing_output(workspace, monkeypatch):
    options, _ = workspace
    write_source(options, 'a.json')
    options.output_root.mkdir()
    output = options.output_root / 'a.json'
    output.write_text('previous output')
    from app.core.translator import job_persister
    monkeypatch.setattr(job_persister, 'persist_completed_job', lambda *args, **kwargs: False)
    state = TranslationGraph(options).invoke('/file a.json')
    result = state['file_results'][0]
    assert result['error'] == 'persistence_failed'
    assert output.read_text() == 'previous output'
    assert (Path(result['staging_path']) / 'a.json').exists()


def test_progress_observer_cannot_invalidate_published_result(workspace):
    options, _ = workspace
    write_source(options, 'a.json')
    observer = Mock(side_effect=RuntimeError('display unavailable'))
    state = TranslationGraph(options).invoke('/file a.json', on_progress=observer)
    assert state['progress']['success'] == 1
    assert state['file_results'][0]['progress_callback_failed'] is True


def test_device_and_directory_symlink_policy(workspace, tmp_path):
    import os
    options, _ = workspace
    fifo = options.source_root / 'pipe.json'
    os.mkfifo(fifo)
    with pytest.raises(CommandError):
        validate_path(str(fifo), options.source_root)
    fifo.unlink()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'x.json').write_text('{}')
    (options.source_root / 'link').symlink_to(outside, target_is_directory=True)
    with pytest.raises(CommandError):
        discover_files(options.source_root, options.source_root)
