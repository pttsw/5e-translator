"""Run a configured data test against a disposable, independent project copy."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess

from .commands import contained
from .validation import ValidationError


def run_regression(options, candidate, relative, staging):
    report = {'passed': False, 'candidate_path': str(candidate), 'exit_code': None,
              'stdout': '', 'stderr': '', 'truncated': False}
    if options.regression_root is None:
        report['error'] = 'regression_not_configured'
        return report
    project = Path(staging) / '5etools'
    root = options.regression_root
    if contained(project.resolve(), root):
        raise ValidationError('regression_workspace_overlap')
    # Dereference links while copying so tests cannot write through them to the original.
    shutil.copytree(root, project, symlinks=False,
                    ignore=shutil.ignore_patterns('.git', '.env', '.env.*', '.agent-*'))
    target = project / options.regression_data_dir / relative
    if not contained(target.resolve(), project.resolve()):
        raise ValidationError('regression_target_escape')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(candidate, target)
    command = list(options.regression_command)
    if not command:
        scripts = json.loads((project / 'package.json').read_text()).get('scripts', {})
        name = next((n for n in ('test:data', 'test:json', 'test') if n in scripts), None)
        if name is None:
            report['error'] = 'regression_command_missing'
            return report
        command = ['npm', 'run', name]
    command = [arg.replace('{candidate}', str(target)).replace('{workspace}', str(project)) for arg in command]
    report.update(command=command, workspace=str(project), tested_candidate=str(target))
    with (Path(staging) / 'regression.stdout').open('wb') as out, (Path(staging) / 'regression.stderr').open('wb') as err:
        # The test process receives no application credentials from the parent environment.
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'LANG', 'LC_ALL', 'TMPDIR', 'SYSTEMROOT')}
        env['CI'] = '1'
        try:
            process = subprocess.Popen(command, cwd=project, stdout=out, stderr=err, env=env, start_new_session=True)
            try:
                report['exit_code'] = process.wait(timeout=options.regression_timeout)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                report['exit_code'] = process.wait()
                report['error'] = 'regression_timeout'
        except OSError:
            report['error'] = 'regression_start_failed'
    for stream in ('stdout', 'stderr'):
        path = Path(staging) / ('regression.' + stream)
        with path.open('rb') as handle:
            data = handle.read(8193)
        report[stream] = data[:8192].decode('utf-8', errors='replace')
        report['truncated'] |= len(data) > 8192
    report['passed'] = report['exit_code'] == 0 and 'error' not in report
    # Reject commands which modify or remove the tested candidate.
    if report['passed'] and (not target.is_file() or target.read_bytes() != Path(candidate).read_bytes()):
        report.update(passed=False, error='regression_candidate_changed')
    return report
