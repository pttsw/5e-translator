"""Runtime objects are confined to one file; state holds only their summaries."""
from copy import deepcopy
from pathlib import Path
import json
import tempfile
from .commands import CommandError, output_path, validate_path
from .prompts import TRANSLATION_CONTRACT
from .validation import ValidationError, validate_file


class StageError(RuntimeError):
    pass


class TranslationNodes:
    def __init__(self, options):
        self.options = options
        self.info = None
        self.processor = None
        self.config = None

    def analyse_and_reuse(self, state, result):
        from app.core.translator.analyser.json_analyser import JsonAnalyser
        from app.core.translator.job_need_translate_setter import JobNeedTranslateSetter
        path = validate_path(result['source_path'], self.options.source_root)
        with path.open(encoding='utf-8') as handle:
            source = json.load(handle)
        if not isinstance(source, dict):
            raise StageError('expected_json_object')
        metadata = {**self.options.summary(), 'byhand': False, 'use_ai': True,
                    'generator_update_db': False, 'prompt_suffix': TRANSLATION_CONTRACT}
        self.config = {'metadata': metadata}
        analyser = JsonAnalyser(source_root=str(self.options.source_root), read_only=True)
        infos = list(analyser.invoke([str(path)], config=self.config))
        if not infos:
            result.update(status='skipped', reason='analyser_skipped')
            return
        self.info = infos[0]
        # Preserve analyser UID/rel_path; never derive output paths from model data.
        relative = path.relative_to(self.options.source_root).as_posix()
        self.info.out_path = relative
        for suffix in ('', '.en', '.jobs'):
            output_path(self.options.output_root, relative + suffix, self.options.source_root)
        list(JobNeedTranslateSetter().invoke([self.info], config=self.config))
        result.update(total=len(self.info.job_list),
                      pending=sum(job.need_translate for job in self.info.job_list),
                      reused=sum(not job.need_translate and job.cn_str is not None for job in self.info.job_list),
                      conflicts=None)
        # Conflict classification belongs to C; never invent a zero count.
        self.options.output_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix='.agent-', dir=self.options.output_root))
        result['staging_path'] = str(staging)
        self.config['metadata']['output_root'] = str(staging)

    def translate(self, state, result):
        from app.core.translator.batch_job_processor import BatchJobProcessor
        self.processor = BatchJobProcessor(self.options.thread_num, update=False)
        references = getattr(self, 'references', None)
        if references is not None:
            from .reference_adapter import ReferenceToolAdapter
            self.processor.adapter_transform = lambda adapter: ReferenceToolAdapter(adapter, references)
            references.temporary.bind(self.processor)
        if not self.processor.ok:
            raise StageError('processor_initialization_failed')
        if not list(self.processor.invoke([self.info], config=self.config)):
            raise StageError('translation_failed')

    def generate(self, state, result):
        from app.core.translator.json_generator import JsonGenerator
        generator = JsonGenerator(self.options.thread_num)
        if not generator.ok or not list(generator.invoke([self.info], config=self.config)):
            raise StageError('generation_failed')

    def validate(self, state, result):
        result['validation'] = validate_file(self.info)

    def persist_and_write(self, state, result):
        from app.core.translator.job_persister import persist_completed_job
        from app.core.utils.loader import write_file_work_infos
        if not result.get('validation', {}).get('passed'):
            raise StageError('validation_required')
        # Keep a candidate even if persistence or publication fails.
        write_file_work_infos(deepcopy(self.info), result['staging_path'])
        for suffix in ('', '.en', '.jobs'):
            output_path(self.options.output_root, self.info.out_path + suffix, self.options.source_root)
        result['persisted'] = 0
        for job in self.info.job_list:
            if job.need_translate and not job.is_proofread:
                if not persist_completed_job(self.processor.dictionary, job, proofread=False):
                    raise StageError('persistence_failed')
                result['persisted'] += 1
        write_file_work_infos(self.info, str(self.options.output_root))
        result.update(status='success', translated=result['pending'], output_path=self.info.json_path,
                      cache_paths=[self.info.json_path + '.en', self.info.json_path + '.jobs'])

    def error(self, state, result, exc):
        # Do not expose provider exceptions, connection strings or environment data.
        code = str(exc) if isinstance(exc, (StageError, ValidationError, CommandError)) else type(exc).__name__
        result.update(status='failed', failed_stage=state['stage'], error=code)
        if self.info is not None:
            result['unfinished_uids'] = [job.uid for job in self.info.job_list if job.cn_str is None]
            if result.get('staging_path'):
                from app.core.utils.loader import write_file_work_infos
                try:
                    write_file_work_infos(deepcopy(self.info), result['staging_path'])
                except Exception:
                    result['diagnostic_write_failed'] = True
        state['errors'].append({'source_path': result['source_path'], 'stage': state['stage'], 'code': code})


def summarize(state):
    state['progress'].update({status: sum(item['status'] == status for item in state['file_results'])
                              for status in ('success', 'partial', 'failed', 'skipped')})
    state['stage'] = 'summarize'
    state['current_file'] = None
    return state
