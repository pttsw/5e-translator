"""Offline real-5etools schema acceptance probe in an isolated workspace."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--source', type=Path, required=True, help='English spells-aag.json')
    parser.add_argument('--node', default='node')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    os.environ.update(TRANSLATOR_DB_BACKEND='memory', TRANSLATOR_DISABLE_REDIS='1', TRANSLATOR_LLM_BACKEND='mock')
    from app.core.agent.graph import TranslationGraph
    from app.core.agent.state import AgentOptions
    work = Path(tempfile.mkdtemp(prefix='agent-schema-'))
    source = work / 'source'
    (source / 'spells').mkdir(parents=True)
    relative = 'spells/spells-aag.json'
    shutil.copyfile(args.source, source / relative)
    # Reuse the project's actual schema engine, limiting the probe to the candidate.
    script = """import {JsonTester} from '5etools-utils';
const tester = new JsonTester({fnGetSchemaId: () => 'spells/spells.json'});
await tester.pInit();
const result = await tester.pGetErrorsOnDirsWorkers({fileList: [process.argv[2]]});
if (result.errors.length) process.exitCode = 1;
"""
    launcher = (
        "require('node:fs').writeFileSync('agent-schema.mjs', process.argv[1]);"
        "const result = require('node:child_process').spawnSync(process.execPath,"
        "['agent-schema.mjs', process.argv[2]], {stdio: 'inherit'});"
        "process.exit(result.status ?? 1);"
    )
    options = AgentOptions(source, work / 'output', thread_num=1, regression_root=args.project,
        regression_command=(args.node, '-e', launcher, script, '{candidate}'))
    state = TranslationGraph(options).invoke('/file ' + relative)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    print(args.output)
    return 0 if state['progress']['success'] == 1 else 1


if __name__ == '__main__':
    raise SystemExit(main())
