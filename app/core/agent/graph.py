"""Finite Python state machine; fixed nodes and no LangGraph dependency."""
from copy import deepcopy
from threading import Event, Lock
from datetime import datetime, timezone
from pathlib import Path
from .commands import CommandError, discover_files, parse_command, validate_path
from .nodes import TranslationNodes, summarize
from .recovery import RecoveryError, RecoveryStore
from .state import AgentOptions, initial_state

FILE_STAGES = ('analyse_and_reuse', 'detect_conflicts', 'translate', 'generate', 'validate', 'persist_and_write')


class TranslationGraph:
    def __init__(self, options: AgentOptions, *, nodes_factory=TranslationNodes, references=None):
        from .reference_tools import ReferenceTools
        self.options = options
        self.references = references if references is not None else ReferenceTools()
        self.nodes_factory = nodes_factory
        self.state = None
        self._lock = Lock()
        self._cancel_event = Event()
        self.recovery = RecoveryStore(options.output_root)

    def invoke(self, text: str, *, on_progress=None):
        try:
            command = parse_command(text)
        except CommandError as exc:
            state = initial_state(text, self.options)
            state['errors'].append({'stage': state['stage'], 'code': str(exc)})
            state['stage'] = 'failed'
            return state
        if command.name == '/status':
            return self.status()
        if command.name == '/cancel':
            return self.cancel()
        if not self._lock.acquire(blocking=False):
            raise CommandError('本会话已有 /file 任务正在运行')
        try:
            state = initial_state(text, self.options)
            if command.name in {'/ref', '/refs', '/clear-refs'}:
                if command.name == '/ref':
                    state['stage'] = 'load_references'
                    with self.references.lock:
                        state['reference_result'] = self.references.store.load_path(command.path, self.options.reference_roots)
                elif command.name == '/clear-refs':
                    self.references.clear()
                state['references'] = self.references.store.summary()
                state['reference_index_ids'] = [item['index_id'] for item in state['references']]
                return summarize(state)
            if command.name not in {'/file', '/retry'}:
                raise CommandError(f'{command.name} 不支持')
            self.state = state
            self._cancel_event.clear()
            self.references.start_run()
            state['reference_index_ids'] = [item['index_id'] for item in self.references.store.summary()]
            recoveries = {}
            if command.name == '/retry':
                previous = self.recovery.load_run()
                if not previous:
                    raise CommandError('没有可重试的运行记录')
                for item in previous.get('file_results', []):
                    # "pending" is a durable sign of a process that stopped before
                    # it could classify the current file.
                    if item.get('status') in {'pending', 'failed', 'partial'} and item.get('checkpoint_path'):
                        checkpoint = self.recovery.load_checkpoint(item)
                        source = str(validate_path(item['source_path'], self.options.source_root))
                        if checkpoint.get('source_path') != source:
                            raise RecoveryError('recovery_source_mismatch')
                        recoveries[source] = checkpoint
                    elif (item.get('status') == 'failed'
                          or (item.get('status') == 'skipped' and item.get('reason') == 'cancelled')
                          or item.get('status') == 'pending'):
                        # No analyser checkpoint exists yet; retry this file from
                        # the source rather than pretending there is recoverable work.
                        source = str(validate_path(item['source_path'], self.options.source_root))
                        recoveries[source] = None
                if not recoveries:
                    raise CommandError('上次运行没有可重试的文件')
                state['source_paths'] = sorted(recoveries, key=lambda value: str(Path(value).relative_to(self.options.source_root)))
                state['retry_of'] = previous.get('run_id')
            else:
                state['stage'] = 'validate_paths'
                path = validate_path(command.path, self.options.source_root)
                state['stage'] = 'discover_files'
                state['source_paths'] = [str(item) for item in discover_files(path, self.options.source_root)]
            state['run_id'] = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            state['progress'] = {'files': len(state['source_paths']), 'completed': 0}
            self.recovery.save_run(state)
            for source in state['source_paths']:
                if self._cancel_event.is_set():
                    state['file_results'].append({'source_path': source, 'status': 'skipped', 'reason': 'cancelled'})
                    state['progress']['completed'] += 1
                    continue
                state['current_file'] = source
                result = {'source_path': source, 'status': 'pending'}
                if source in recoveries:
                    result['recovery'] = recoveries[source]
                state['file_results'].append(result)
                nodes = self.nodes_factory(self.options)
                nodes.references = self.references
                nodes.recovery_store = self.recovery
                nodes.cancel_event = self._cancel_event
                try:
                    for stage in FILE_STAGES:
                        if self._cancel_event.is_set():
                            raise InterruptedError('cancelled')
                        state['stage'] = stage
                        getattr(nodes, stage)(state, result)
                        self.recovery.save_run(state)
                        if on_progress:
                            try:
                                on_progress(deepcopy(state))
                            except Exception:
                                result['progress_callback_failed'] = True
                        if result['status'] == 'skipped':
                            break
                except Exception as exc:
                    nodes.error(state, result, exc)
                finally:
                    self.references.temporary.finish(getattr(nodes, 'processor', None))
                state['progress']['completed'] += 1
                self.recovery.save_run(state)
            state['reference_index_ids'] = [item['index_id'] for item in self.references.store.summary()]
            state['tool_calls'] = deepcopy(self.references.audit)
            summarized = summarize(state)
            self.recovery.save_run(summarized)
            return summarized
        except (CommandError, RecoveryError) as exc:
            state['errors'].append({'stage': state['stage'], 'code': str(exc)})
            state['stage'] = 'failed'
            return state
        finally:
            self.references.end_run()
            self._lock.release()

    def status(self):
        active = self._lock.locked()
        state = self.state
        if state is None:
            try:
                state = self.recovery.load_run()
            except RecoveryError as exc:
                return {'stage': 'failed', 'running': False, 'errors': [{'stage': 'status', 'code': str(exc)}]}
        if state is None:
            return {'stage': 'idle', 'running': False, 'progress': {}, 'file_results': [], 'token_usage': {}}
        try:
            result = deepcopy(state)
        except RuntimeError:
            result = dict(state)
        stage, current_file = result.get('stage'), result.get('current_file')
        summarize(result)
        result['stage'], result['current_file'] = stage, current_file
        result['running'] = active
        result['cancel_requested'] = self._cancel_event.is_set()
        return result

    def cancel(self):
        if not self._lock.locked() or self.state is None or self.state.get('command', '').split(maxsplit=1)[0] not in {'/file', '/retry'}:
            return {'stage': 'idle', 'running': False, 'cancel_requested': False,
                    'errors': [{'stage': 'cancel', 'code': '没有正在运行的文件任务'}]}
        self._cancel_event.set()
        result = self.status()
        result['cancel_requested'] = True
        return result


def build_graph(options=None, **kwargs):
    return TranslationGraph(options or AgentOptions.from_environment(), **kwargs)
