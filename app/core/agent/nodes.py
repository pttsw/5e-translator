"""Runtime objects are confined to one file; state holds only their summaries."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
from .commands import CommandError, output_path, validate_path
from .prompts import TRANSLATION_CONTRACT
from .validation import ValidationError, validate_file
from .token_usage import empty_token_usage, public_token_usage


class StageError(RuntimeError):
    pass


class TranslationNodes:
    def __init__(self, options):
        self.options = options
        self.info = None
        self.processor = None
        self.config = None
        self.token_usage = empty_token_usage()

    def analyse_and_reuse(self, state, result):
        self.state = state
        self.result = result
        from app.core.translator.analyser.json_analyser import JsonAnalyser
        from app.core.translator.job_need_translate_setter import JobNeedTranslateSetter
        path = validate_path(result['source_path'], self.options.source_root)
        with path.open(encoding='utf-8') as handle:
            source = json.load(handle)
        self.source = source
        if not isinstance(source, dict):
            raise StageError('expected_json_object')
        metadata = {**self.options.summary(), 'byhand': False, 'use_ai': True,
                    'generator_update_db': False, 'prompt_suffix': TRANSLATION_CONTRACT}
        self.config = {'metadata': metadata}
        analyser = JsonAnalyser(source_root=str(self.options.source_root), read_only=True)
        from .conflicts import ConflictDictionary
        self.reuse_dictionary = ConflictDictionary(analyser.dictionary)
        analyser.dictionary = self.reuse_dictionary
        infos = list(analyser.invoke([str(path)], config=self.config))
        if not infos:
            result.update(status='skipped', reason='analyser_skipped')
            return
        self.info = infos[0]
        actual_uids = {j.uid for j in self.info.job_list}
        self.reuse_dictionary.conflicts = {uid: detail for uid, detail in self.reuse_dictionary.conflicts.items()
                                           if uid in actual_uids}
        # Preserve analyser UID/rel_path; never derive output paths from model data.
        relative = path.relative_to(self.options.source_root).as_posix()
        self.info.out_path = relative
        for suffix in ('', '.en', '.jobs'):
            output_path(self.options.output_root, relative + suffix, self.options.source_root)
        list(JobNeedTranslateSetter().invoke([self.info], config=self.config))
        recovery = result.pop('recovery', None)
        if recovery is not None:
            from .recovery import apply_recovered_jobs
            recovered = apply_recovered_jobs(self.info, recovery)
            result.update(recovered)
        result.update(total=len(self.info.job_list),
                      pending=sum(job.need_translate for job in self.info.job_list),
                      reused=sum(not job.need_translate and job.cn_str is not None for job in self.info.job_list),
                      conflicts=len(self.reuse_dictionary.conflicts))
        self.options.output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.agent-', dir=self.options.output_root))
        result['staging_path'] = str(staging)
        self.config['metadata']['output_root'] = str(staging)
        self.config['metadata']['cancel_event'] = getattr(self, 'cancel_event', None)
        self.config['metadata']['checkpoint_callback'] = self._checkpoint
        self.token_usage = empty_token_usage()
        self._checkpoint()

    def detect_conflicts(self, state, result):
        result['conflict_details'] = list(self.reuse_dictionary.conflicts.values())
        if result['conflicts']:
            raise StageError('translation_conflict')

    def translate(self, state, result):
        from app.core.translator.batch_job_processor import BatchJobProcessor
        self.processor = BatchJobProcessor(self.options.thread_num, update=False)
        if not self.processor.ok:
            raise StageError('processor_initialization_failed')
        references = getattr(self, 'references', None)
        if references is not None:
            from .reference_adapter import ReferenceToolAdapter
            self.processor.adapter_transform = lambda adapter: ReferenceToolAdapter(adapter, references)
            self.processor.temporary_term_sink = references.temporary.add
            references.temporary.bind(self.processor)
        if not list(self.processor.invoke([self.info], config=self.config)):
            self._collect_token_usage()
            if getattr(self, 'cancel_event', None) is not None and self.cancel_event.is_set():
                raise InterruptedError('cancelled')
            raise StageError('translation_failed')
        self._collect_token_usage()
        self._checkpoint()

    def generate(self, state, result):
        from app.core.translator.json_generator import JsonGenerator
        generator = JsonGenerator(self.options.thread_num)
        if not generator.ok or not list(generator.invoke([self.info], config=self.config)):
            raise StageError('generation_failed')

    def validate(self, state, result):
        from app.core.utils.loader import write_file_work_infos
        from .regression import run_regression
        from .validation import remove_generated_names
        remove_generated_names(self.source, self.info.json_obj)
        remove_generated_names(self.source, self.info.cn_obj)
        result['validation'] = {'passed': False}
        validate_file(self.info)
        staged = write_file_work_infos(deepcopy(self.info), result['staging_path'])
        # Validate the exact serialized artifact, including writer normalization.
        staged.cn_obj = json.loads(Path(staged.json_path).read_text(encoding='utf-8'))
        result['validation'] = validate_file(staged)
        result['regression'] = run_regression(self.options, staged.json_path,
                                            self.info.out_path, result['staging_path'])
        if not result['regression']['passed']:
            raise ValidationError(result['regression'].get('error', 'regression_failed'))

    def persist_and_write(self, state, result):
        from app.core.translator.job_persister import persist_completed_job
        from app.core.utils.loader import write_file_work_infos
        if not result.get('validation', {}).get('passed') or not result.get('regression', {}).get('passed'):
            raise StageError('validation_required')
        # The validated candidate already exists in staging.
        for suffix in ('', '.en', '.jobs'):
            output_path(self.options.output_root, self.info.out_path + suffix, self.options.source_root)
        result['persisted'] = 0
        for job in self.info.job_list:
            if (job.need_translate or getattr(job, 'recovered_translation', False)) and not job.is_proofread:
                if not persist_completed_job(self.processor.dictionary, job, proofread=False):
                    raise StageError('persistence_failed')
                result['persisted'] += 1
        from .publication import publish
        # Refresh job metadata after persistence, preserving the validated JSON bytes.
        cache_info = deepcopy(self.info)
        cache_info.cn_obj = None
        write_file_work_infos(cache_info, result['staging_path'])
        self.info.json_path = publish(self.options, self.info.out_path, result['staging_path'])
        result.update(status='success', translated=result['pending'], output_path=self.info.json_path,
                      cache_paths=[self.info.json_path + '.en', self.info.json_path + '.jobs'])
        self._checkpoint()

    def error(self, state, result, exc):
        # Do not expose provider exceptions, connection strings or environment data.
        code = str(exc) if isinstance(exc, (StageError, ValidationError, CommandError)) else type(exc).__name__
        cancelled = isinstance(exc, InterruptedError) or code == 'cancelled'
        completed = sum(isinstance(job.cn_str, str) for job in self.info.job_list) if self.info is not None else 0
        result.update(status='partial' if cancelled and completed else 'failed', failed_stage=state['stage'],
                      error='cancelled' if cancelled else code, cancelled=cancelled)
        if self.info is not None:
            result['job_locations'] = [{'uid': j.uid, 'key_path': j.key_path,
                'entity_path': getattr(j, 'entity_path', '') or j.entry_path,
                'context_key': j.context_key, 'batch_id': getattr(j, 'batch_id', '')} for j in self.info.job_list]
            result['unfinished_uids'] = [job.uid for job in self.info.job_list if job.cn_str is None]
            if result.get('staging_path'):
                from app.core.utils.loader import write_file_work_infos
                try:
                    write_file_work_infos(deepcopy(self.info), result['staging_path'])
                except Exception:
                    result['diagnostic_write_failed'] = True
        if result.get('staging_path'):
            try:
                Path(result['staging_path'], 'failure.json').write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            except OSError:
                result['diagnostic_write_failed'] = True
        self._collect_token_usage()
        self._checkpoint()
        state['errors'].append({'source_path': result['source_path'], 'stage': state['stage'],
                                'code': 'cancelled' if cancelled else code})

    def _collect_token_usage(self):
        adapter = getattr(getattr(self, 'processor', None), 'adapter', None)
        wrapped = getattr(adapter, 'adapter', adapter)
        provider_usage = getattr(wrapped, 'token_usage', None)
        if isinstance(provider_usage, dict):
            self.token_usage = dict(provider_usage)
        self.result['token_usage'] = public_token_usage(self.token_usage)

    def _checkpoint(self):
        if self.info is None or not hasattr(self, 'result') or not self.result.get('staging_path'):
            return
        store = getattr(self, 'recovery_store', None)
        if store is not None:
            adapter = getattr(getattr(self, 'processor', None), 'adapter', None)
            wrapped = getattr(adapter, 'adapter', adapter)
            usage = getattr(wrapped, 'token_usage', getattr(self, 'token_usage', empty_token_usage()))
            self.result['token_usage'] = public_token_usage(usage)
            store.checkpoint_jobs(self.info, self.result, self.state.get('stage', ''),
                                  self.result['token_usage'])


def summarize(state):
    state['progress'].update({status: sum(item['status'] == status for item in state['file_results'])
                              for status in ('success', 'partial', 'failed', 'skipped')})
    state['stage'] = 'summarize'
    state['current_file'] = None
    token_usage = {}
    for key in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens', 'total_tokens'):
        values = [item.get('token_usage', {}).get(key) for item in state['file_results']]
        reported = [value for value in values if isinstance(value, int)]
        token_usage[key] = sum(reported) if reported else '未提供'
    state['token_usage'] = token_usage
    state['directory_summary'] = {
        'files': len(state['file_results']),
        'total_jobs': sum(item.get('total', 0) for item in state['file_results']),
        'reused_jobs': sum(item.get('reused', 0) for item in state['file_results']),
        'recovered_jobs': sum(len(item.get('recovered_uids', [])) for item in state['file_results']),
        'translated_jobs': sum(item.get('translated', 0) for item in state['file_results']),
        'conflicts': sum(item.get('conflicts', 0) for item in state['file_results']),
        'unfinished_jobs': sum(len(item.get('unfinished_uids', [])) for item in state['file_results']),
    }
    return state
