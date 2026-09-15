"""Phase D recovery, control-plane and usage acceptance tests."""
import json
from pathlib import Path
from threading import Event, Thread

from app.core.agent.graph import TranslationGraph
from app.core.agent.nodes import StageError, TranslationNodes
from app.core.agent.token_usage import add_response_usage, empty_token_usage, public_token_usage
from app.core.agent.test_workflow import workspace, write_source  # noqa: F401


def test_status_reports_live_progress_without_taking_run_lock(workspace):
    options, _ = workspace
    write_source(options, 'a.json')
    graph = TranslationGraph(options)
    snapshots = []

    def observe(_state):
        snapshots.append(graph.invoke('/status'))

    result = graph.invoke('/file a.json', on_progress=observe)
    assert result['progress']['success'] == 1
    assert snapshots and all(item['running'] for item in snapshots)
    assert snapshots[0]['current_file'].endswith('a.json')
    assert graph.invoke('/status')['running'] is False


def test_cancel_stops_at_stage_boundary_and_preserves_checkpoint(workspace):
    options, _ = workspace
    write_source(options, 'a.json')
    write_source(options, 'b.json')
    graph = TranslationGraph(options)
    cancel_result = []

    def cancel_after_analysis(state):
        if state['stage'] == 'analyse_and_reuse' and not cancel_result:
            cancel_result.append(graph.invoke('/cancel'))

    result = graph.invoke('/file .', on_progress=cancel_after_analysis)
    assert cancel_result[0]['cancel_requested'] is True
    assert result['file_results'][0]['error'] == 'cancelled'
    assert Path(result['file_results'][0]['checkpoint_path']).is_file()
    assert result['file_results'][1]['status'] == 'skipped'
    assert result['file_results'][1]['reason'] == 'cancelled'
    retried = graph.invoke('/retry')
    assert retried['progress']['success'] == 2
    assert len(retried['source_paths']) == 2


def test_cancel_waits_for_inflight_model_call_then_checkpoints(workspace, monkeypatch):
    options, _ = workspace
    write_source(options, 'a.json')
    from app.core.translator.mock_adapter import MockAdapter
    entered, release = Event(), Event()
    original = MockAdapter.sendText

    def blocked(self, text, *args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(self, text, *args, **kwargs)

    monkeypatch.setattr(MockAdapter, 'sendText', blocked)
    graph = TranslationGraph(options)
    result = {}
    worker = Thread(target=lambda: result.setdefault('state', graph.invoke('/file a.json')))
    worker.start()
    assert entered.wait(5)
    cancellation = graph.invoke('/cancel')
    assert cancellation['running'] is True
    assert cancellation['cancel_requested'] is True
    release.set()
    worker.join(10)
    assert not worker.is_alive()
    file_result = result['state']['file_results'][0]
    assert file_result['status'] == 'partial'
    assert file_result['error'] == 'cancelled'
    assert Path(file_result['checkpoint_path']).is_file()


def test_retry_recovers_completed_jobs_without_repeating_llm(workspace, monkeypatch):
    options, db = workspace
    write_source(options, 'a.json')
    from app.core.translator.mock_adapter import MockAdapter
    calls = []
    original = MockAdapter.sendText

    def counted(self, text, *args, **kwargs):
        calls.append(text)
        return original(self, text, *args, **kwargs)

    monkeypatch.setattr(MockAdapter, 'sendText', counted)

    class FailOnce(TranslationNodes):
        failed = False

        def generate(self, state, result):
            if not self.__class__.failed:
                self.__class__.failed = True
                raise StageError('simulated_interruption')
            return super().generate(state, result)

    graph = TranslationGraph(options, nodes_factory=FailOnce)
    first = graph.invoke('/file a.json')
    first_calls = len(calls)
    assert first['progress']['failed'] == 1
    checkpoint = Path(first['file_results'][0]['checkpoint_path'])
    assert checkpoint.is_file()
    # A hard process stop leaves the last durable file classification pending.
    latest = options.output_root / '.agent-state' / 'latest.json'
    latest_payload = json.loads(latest.read_text())
    latest_payload['file_results'][0]['status'] = 'pending'
    latest.write_text(json.dumps(latest_payload))

    retried = graph.invoke('/retry')
    assert retried['progress']['success'] == 1, retried
    assert len(calls) == first_calls
    assert retried['file_results'][0]['recovered_uids']
    assert retried['file_results'][0]['pending'] == 0
    assert retried['file_results'][0]['persisted'] == len(retried['file_results'][0]['recovered_uids'])
    assert db.store_records


def test_retry_rejects_changed_source(workspace):
    options, _ = workspace
    source = write_source(options, 'a.json')

    class AlwaysFail(TranslationNodes):
        def generate(self, state, result):
            raise StageError('stop')

    graph = TranslationGraph(options, nodes_factory=AlwaysFail)
    assert graph.invoke('/file a.json')['progress']['failed'] == 1
    payload = json.loads(source.read_text())
    payload['spell'][0]['entries'].append('Changed.')
    source.write_text(json.dumps(payload))
    retried = graph.invoke('/retry')
    assert retried['stage'] == 'failed'
    assert retried['errors'][0]['code'] == 'recovery_source_changed'


def test_provider_usage_and_unreported_fields_are_explicit():
    class Response:
        usage_metadata = {'input_tokens': 12, 'output_tokens': 3, 'total_tokens': 15,
                          'input_token_details': {'cache_read': 5}}
        response_metadata = {}

    usage = empty_token_usage()
    add_response_usage(usage, Response())
    assert public_token_usage(usage) == {
        'input_tokens': 12, 'output_tokens': 3, 'cache_read_tokens': 5,
        'cache_creation_tokens': '未提供', 'total_tokens': 15,
    }
