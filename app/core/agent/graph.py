"""Finite Python state machine; fixed nodes and no LangGraph dependency."""
from copy import deepcopy
from threading import Lock
from .commands import CommandError, discover_files, parse_command, validate_path
from .nodes import TranslationNodes, summarize
from .state import AgentOptions, initial_state

FILE_STAGES = ('analyse_and_reuse', 'translate', 'generate', 'validate', 'persist_and_write')


class TranslationGraph:
    def __init__(self, options: AgentOptions, *, nodes_factory=TranslationNodes, references=None):
        from .reference_tools import ReferenceTools
        self.options = options
        self.references = references if references is not None else ReferenceTools()
        self.nodes_factory = nodes_factory
        self.state = None
        self._lock = Lock()

    def invoke(self, text: str, *, on_progress=None):
        if not self._lock.acquire(blocking=False):
            raise CommandError('本会话已有 /file 任务正在运行')
        try:
            state = initial_state(text, self.options)
            self.state = state
            command = parse_command(text)
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
            if command.name != '/file':
                raise CommandError(f'{command.name} 尚未启用；可恢复命令属于阶段 D')
            self.references.start_run()
            state['reference_index_ids'] = [item['index_id'] for item in self.references.store.summary()]
            state['stage'] = 'validate_paths'
            path = validate_path(command.path, self.options.source_root)
            state['stage'] = 'discover_files'
            state['source_paths'] = [str(item) for item in discover_files(path, self.options.source_root)]
            state['progress'] = {'files': len(state['source_paths']), 'completed': 0}
            for source in state['source_paths']:
                state['current_file'] = source
                result = {'source_path': source, 'status': 'pending'}
                state['file_results'].append(result)
                nodes = self.nodes_factory(self.options)
                nodes.references = self.references
                try:
                    for stage in FILE_STAGES:
                        state['stage'] = stage
                        getattr(nodes, stage)(state, result)
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
            state['reference_index_ids'] = [item['index_id'] for item in self.references.store.summary()]
            state['tool_calls'] = deepcopy(self.references.audit)
            return summarize(state)
        except CommandError as exc:
            state['errors'].append({'stage': state['stage'], 'code': str(exc)})
            state['stage'] = 'failed'
            return state
        finally:
            self.references.end_run()
            self._lock.release()


def build_graph(options=None, **kwargs):
    return TranslationGraph(options or AgentOptions.from_environment(), **kwargs)
