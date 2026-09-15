"""Atomic, source-bound checkpoints used by /retry and crash recovery."""
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import os
import threading
import uuid


class RecoveryError(RuntimeError):
    pass


def source_digest(path):
    digest = sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    os.replace(temporary, path)


class RecoveryStore:
    def __init__(self, output_root):
        self.output_root = Path(output_root).resolve()
        self.root = self.output_root / '.agent-state'
        self.latest_path = self.root / 'latest.json'
        self.lock = threading.Lock()

    def _safe_path(self, path):
        resolved = Path(path).resolve()
        if resolved != self.output_root and self.output_root not in resolved.parents:
            raise RecoveryError('recovery_path_outside_output_root')
        return resolved

    def save_run(self, state):
        self._safe_path(self.latest_path)
        payload = deepcopy(state)
        payload['updated_at'] = datetime.now(timezone.utc).isoformat()
        with self.lock:
            _atomic_json(self.latest_path, payload)

    def load_run(self):
        try:
            payload = json.loads(self._safe_path(self.latest_path).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise RecoveryError('recovery_state_invalid') from exc
        return payload if isinstance(payload, dict) else None

    def checkpoint_jobs(self, info, result, stage, token_usage=None):
        from app.core.utils import clean_5etools_json
        staging = self._safe_path(result['staging_path'])
        jobs_path = staging / f'{info.out_path}.jobs'
        with self.lock:
            jobs_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = jobs_path.with_name(f'{jobs_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}')
            temporary.write_text(clean_5etools_json(info.job_list), encoding='utf-8')
            os.replace(temporary, jobs_path)
            checkpoint = {
                'source_path': result['source_path'],
                'source_hash': source_digest(result['source_path']),
                'out_path': info.out_path,
                'jobs_path': str(jobs_path),
                'stage': stage,
                'unfinished_uids': [job.uid for job in info.job_list if job.cn_str is None],
                'completed_uids': [job.uid for job in info.job_list if isinstance(job.cn_str, str)],
                'token_usage': token_usage or {},
            }
            checkpoint_path = staging / 'checkpoint.json'
            _atomic_json(checkpoint_path, checkpoint)
        result['checkpoint_path'] = str(checkpoint_path)
        result['unfinished_uids'] = checkpoint['unfinished_uids']
        return checkpoint

    def load_checkpoint(self, result):
        path = result.get('checkpoint_path')
        if not path and result.get('staging_path'):
            path = str(Path(result['staging_path']) / 'checkpoint.json')
        try:
            checkpoint = json.loads(self._safe_path(path).read_text(encoding='utf-8'))
            jobs = json.loads(self._safe_path(checkpoint['jobs_path']).read_text(encoding='utf-8'))
        except (TypeError, KeyError, OSError, ValueError) as exc:
            raise RecoveryError('recovery_checkpoint_invalid') from exc
        if checkpoint.get('source_hash') != source_digest(checkpoint.get('source_path', '')):
            raise RecoveryError('recovery_source_changed')
        checkpoint['jobs'] = jobs
        return checkpoint


def apply_recovered_jobs(info, checkpoint):
    from .validation import validate_translation
    records = {row.get('uid'): row for row in checkpoint.get('jobs', []) if isinstance(row, dict)}
    recovered = []
    rejected = []
    for job in info.job_list:
        # Fresh analyser/database reuse remains authoritative; checkpoints only
        # fill work that would otherwise be sent to the model again.
        if not job.need_translate:
            continue
        record = records.get(job.uid)
        candidate = record.get('cn_str') if record else None
        if (not isinstance(candidate, str) or record.get('en_str') != job.en_str
                or (record.get('tag') or '') != (job.tag or '')):
            continue
        original = job.cn_str
        job.cn_str = candidate
        try:
            validate_translation(job)
            valid = True
        except Exception:
            valid = False
        if not valid:
            job.cn_str = original
            rejected.append(job.uid)
            continue
        job.need_translate = False
        job.recovered_translation = True
        recovered.append(job.uid)
    return {'recovered_uids': recovered, 'rejected_uids': rejected}
