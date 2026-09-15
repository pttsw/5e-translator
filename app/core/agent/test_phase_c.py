"""Conflict, regression and publication gates; no external services required."""
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.core.agent.test_workflow import workspace, write_source
from app.core.agent.conflicts import ConflictDictionary
from app.core.agent.graph import TranslationGraph
from app.core.agent.validation import ValidationError, validate_translation


def test_conflict_and_location_precedence(workspace):
    _, db = workspace
    context = SimpleNamespace(uid='u1', key_path='/spell/name', context_key='entity', context_label='')
    db.put('Light', '光亮', 'a.json', tag='spell', proofread=True, job_context=context)
    db.put('Light', '轻巧', 'b.json', tag='spell', proofread=True)
    reader = ConflictDictionary(db)
    assert reader.get('Light', 'a.json', True, tag='spell', job_context=context)['cn'] == '光亮'
    assert reader.get('Light', 'c.json', True, tag='spell', job_context=context) is None
    assert len(reader.conflicts['u1']['candidates']) == 2
    # A shared parent/context alone is not precise evidence.
    other = SimpleNamespace(uid='u2', key_path='/spell/entries', context_key='entity')
    assert reader.get('Light', 'a.json', True, tag='spell', job_context=other) is None


def test_unique_generic_reuse_is_read_only(workspace):
    _, db = workspace
    db.put('Light', '光亮', 'old.json', tag='spell', proofread=True)
    before = json.dumps(db.word_usage, default=str)
    reader = ConflictDictionary(db)
    assert reader.get('light', 'new.json', True, True, 'spell')['cn'] == '光亮'
    assert reader.get('Light', 'new.json', True, tag='item') is None
    assert json.dumps(db.word_usage, default=str) == before


def test_conflict_blocks_file_without_writes(workspace):
    options, db = workspace
    write_source(options, 'a.json')
    # Use the analyser's actual category.
    from app.core.translator.analyser.json_analyser import JsonAnalyser
    info = list(JsonAnalyser(source_root=str(options.source_root)).invoke(
        [str(options.source_root / 'a.json')], config={'metadata': {}}))[0]
    title = next(j for j in info.job_list if j.en_str == 'Test Light')
    for cn in ('光亮甲', '光亮乙'):
        db.put(title.en_str, cn, 'old.json', tag=title.tag, proofread=True)
    before = json.dumps(db.store_records, default=str)
    result = TranslationGraph(options).invoke('/file a.json')['file_results'][0]
    assert result['error'] == 'translation_conflict', result
    assert result['conflicts'] == 1
    assert result['conflict_details'][0]['uid'] == title.uid
    assert not (options.output_root / 'a.json').exists()
    assert json.dumps(db.store_records, default=str) == before
    assert Path(result['staging_path'], 'failure.json').exists()


@pytest.mark.parametrize('body,code', [
    ('import sys; print("bad schema"); sys.exit(2)', 'regression_failed'),
    ('import time; time.sleep(10)', 'regression_timeout'),
    ('import sys; open(sys.argv[1], "w").write("{}")', 'regression_candidate_changed'),
])
def test_regression_failures_preserve_artifacts(workspace, body, code):
    options, db = workspace
    write_source(options, 'a.json')
    (options.regression_root / 'check.py').write_text(body)
    options = replace(options, regression_timeout=0.1 if code == 'regression_timeout' else 5)
    options.output_root.mkdir()
    for suffix in ('', '.en', '.jobs'):
        (options.output_root / ('a.json' + suffix)).write_text('previous')
    result = TranslationGraph(options).invoke('/file a.json')['file_results'][0]
    assert result['error'] == code, result
    assert db.store_records == []
    for suffix in ('', '.en', '.jobs'):
        assert (options.output_root / ('a.json' + suffix)).read_text() == 'previous'
    assert (options.regression_root / 'check.py').read_text() == body
    assert not (options.regression_root / 'data/a.json').exists()


def test_unconfigured_regression_fails_closed(workspace):
    options, db = workspace
    write_source(options, 'a.json')
    result = TranslationGraph(replace(options, regression_root=None)).invoke('/file a.json')['file_results'][0]
    assert result['error'] == 'regression_not_configured'
    assert db.store_records == []


@pytest.mark.parametrize('en,cn,reason', [
    ('Roll {@dice 1d6}.', '掷{@damage 1d6}。', 'tag_mismatch'),
    ('Range 30 feet.', '距离60尺。', 'number_mismatch'),
    ('Roll 1d6.', '掷6d1。', 'dice_mismatch'),
    ('Hello', '{你好', 'unbalanced_braces'),
    ('Hello', '{你好}', 'brace_mismatch'),
])
def test_translation_invariants(en, cn, reason):
    with pytest.raises(ValidationError, match=reason + ':u'):
        validate_translation(SimpleNamespace(uid='u', en_str=en, cn_str=cn))


def test_publication_rollback(workspace, monkeypatch):
    from app.core.agent import publication
    options, _ = workspace
    options.output_root.mkdir()
    stage = options.output_root / 'stage'
    stage.mkdir()
    for suffix in ('', '.en', '.jobs'):
        (options.output_root / ('a.json' + suffix)).write_text('old')
        (stage / ('a.json' + suffix)).write_text('new')
    original = publication.os.replace
    calls = []
    def fail_once(src, dst):
        calls.append(dst)
        if len(calls) == 3:
            raise OSError('disk error')
        return original(src, dst)
    monkeypatch.setattr(publication.os, 'replace', fail_once)
    with pytest.raises(OSError):
        publication.publish(options, 'a.json', stage)
    assert all((options.output_root / ('a.json' + suffix)).read_text() == 'old'
               for suffix in ('', '.en', '.jobs'))


def test_regression_default_command_and_truncation(workspace, monkeypatch):
    from app.core.agent.regression import run_regression
    options, _ = workspace
    stage = options.output_root
    stage.mkdir()
    candidate = stage / 'a.json'
    candidate.write_text('{}')
    (options.regression_root / 'package.json').write_text(json.dumps({
        'scripts': {'test:data': 'node -e "process.stdout.write(\'x\'.repeat(9000))"'}}))
    def process(command, **kwargs):
        kwargs['stdout'].write(b'x' * 9000)
        return SimpleNamespace(wait=lambda timeout: 0)
    monkeypatch.setattr('app.core.agent.regression.subprocess.Popen', process)
    result = run_regression(replace(options, regression_command=()), candidate, 'a.json', stage)
    assert result['passed'], result
    assert result['command'] == ['npm', 'run', 'test:data']
    assert result['truncated'] and len(result['stdout']) == 8192


def test_native_names_only_remove_generated_fields():
    from app.core.agent.validation import remove_generated_names
    source = {'items': [{'name': 'One'}, {'name': 'Two', 'ENG_name': 'Two'}]}
    target = {'items': [{'name': '一', 'ENG_name': 'One'}, {'name': '二', 'ENG_name': 'Two'}]}
    remove_generated_names(source, target)
    assert 'ENG_name' not in target['items'][0]
    assert target['items'][1]['ENG_name'] == 'Two'


def test_sql_candidate_reader_releases_connection_on_failure():
    from app.core.database.db_dictionary import DBDictionary
    calls = []
    class BrokenDB:
        def select(self, *args, **kwargs):
            return None
    owner = SimpleNamespace(db_list=[BrokenDB()],
        _DBDictionary__get_db_index=lambda: 0,
        _DBDictionary__release_db=lambda index: calls.append(index))
    with pytest.raises(RuntimeError, match='reuse_candidates_unavailable'):
        DBDictionary.get_reuse_candidates(owner, 'Light', 'a.json')
    assert calls == [0]


def test_dice_can_touch_chinese_text():
    validate_translation(SimpleNamespace(uid='u', en_str='Roll 1d6 + 2.', cn_str='掷1d6+2。'))
