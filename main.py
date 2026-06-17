import os
import sys


def _ensure_project_python():
    project_root = os.path.dirname(os.path.abspath(__file__))
    venv_python = os.path.join(project_root, ".venv", "bin", "python")
    current_python = os.path.abspath(sys.executable)
    if not os.path.exists(venv_python):
        return
    if current_python == os.path.abspath(venv_python):
        return
    os.execv(venv_python, [venv_python, __file__, *sys.argv[1:]])


_ensure_project_python()
os.environ.setdefault("SKIP_APP_BOOTSTRAP", "1")

import argparse
import copy
import statistics
from contextlib import contextmanager


@contextmanager
def cli_db_app_context():
    import pymysql
    from flask import Flask
    from app.model import db
    from config.settings import DB_CONFIG

    pymysql.install_as_MySQLdb()
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = (
        f"mysql://{DB_CONFIG['USER']}:{DB_CONFIG['PASSWORD']}"
        f"@{DB_CONFIG['HOST']}:{DB_CONFIG['PORT']}/{DB_CONFIG['DATABASE']}"
    )
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    with app.app_context():
        yield


def preview_batch_units(file_infos, batch_max_chars: int, include_all_jobs: bool = False, detail_limit: int = 5):
    from app.core.translator.batch_chunker import BatchChunker
    from app.core.utils import FileWorkInfo, need_translate_str

    chunker = BatchChunker(max_chars=batch_max_chars)
    for res in file_infos:
        if include_all_jobs:
            preview_file = FileWorkInfo(copy.deepcopy(res.job_list), copy.deepcopy(res.json_obj), res.json_path, res.out_path)
            preview_file.batch_meta = copy.deepcopy(getattr(res, "batch_meta", {}))
            for job in preview_file.job_list:
                job.need_translate = need_translate_str(job.en_str)
            working_res = preview_file
        else:
            working_res = res
        units = chunker.build_units(working_res)
        if not units:
            print(f"FILE {working_res.json_path} -> units=0")
            continue

        context_sizes = [len(unit.context_text) for unit in units]
        job_counts = [len(unit.jobs) for unit in units]
        retry_splits = []
        for unit in units:
            sub_units = chunker.split_retry_unit(unit)
            if sub_units:
                retry_splits.append({
                    "batch_id": unit.batch_id,
                    "child_jobs": [len(child.jobs) for child in sub_units],
                })

        print(
            f"FILE {working_res.json_path} -> "
            f"units={len(units)} jobs={sum(job_counts)} "
            f"context_chars[min/avg/max]={min(context_sizes)}/{int(statistics.mean(context_sizes))}/{max(context_sizes)} "
            f"jobs_per_unit[min/avg/max]={min(job_counts)}/{round(statistics.mean(job_counts), 1)}/{max(job_counts)} "
            f"retry_split_candidates={len(retry_splits)}"
        )

        largest_units = sorted(units, key=lambda unit: len(unit.context_text), reverse=True)[:detail_limit]
        for index, unit in enumerate(largest_units, start=1):
            print(
                "  "
                f"top{index} id={unit.batch_id} "
                f"jobs={len(unit.jobs)} "
                f"context_chars={len(unit.context_text)} "
                f"uids={[job.uid for job in unit.jobs[:3]]}"
            )
            sub_units = chunker.split_retry_unit(unit)
            if sub_units:
                print(
                    "    "
                    f"retry_children={len(sub_units)} "
                    f"child_jobs={[len(child.jobs) for child in sub_units]}"
                )


def iter_with_total_progress(results, total_files: int):
    from app.core.utils.console_progress import console_progress

    console_progress.set_total(total_files, label="Total")
    try:
        for file_work_info in results:
            yield file_work_info
            console_progress.advance_total()
    finally:
        console_progress.clear_all()


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest='function', required=True)
    
    # 为 transform 命令创建子解析器
    transform_parser = subparsers.add_parser('transform')
    
    # 为 translate 命令创建子解析器
    translate_parser = subparsers.add_parser('translate')
    translate_parser.add_argument('--en', default='',
                                  help='Path to the English data, default to the value in config')
    translate_parser.add_argument('--thread_num', default=10, type=int,
                                  help='Number of threads to use, default to 10')
    translate_parser.add_argument('--byhand', action='store_true', default=False,
                                  help='Whether to use by hand mode, default to False')

    translate_parser.add_argument('--force', action='store_true', default=False,
                                  help='Whether to force translate, update unproofreaded words, default to False')
    translate_parser.add_argument('--force-title', action='store_true', default=False,
                                  help='Whether to force translate, update unproofreaded titles, default to False')
    translate_parser.add_argument('--mode', default='5et', type=str,
                                  help='Mode to use, default to 5et, can be 5et, splited, homebrew, plu or ua')
    translate_parser.add_argument('--cache', action='store_false', default=True,
                                  help='Whether to use cache terms, default to True')
    translate_parser.add_argument('--file', default='', type=str,
                                  help='Only process one file. In splited mode this should be the relative split json path.')
    translate_parser.add_argument('--batch', action='store_true', default=False,
                                  help='Whether to translate by batch unit, default to False')
    translate_parser.add_argument('--batch-max-chars', default=12000, type=int,
                                  help='Max context chars per batch chunk, default to 12000')
    translate_parser.add_argument('--batch-preview', action='store_true', default=False,
                                  help='Only preview batch split results, default to False')
    translate_parser.add_argument('--batch-preview-all', action='store_true', default=False,
                                  help='Preview using all translatable jobs, ignoring current DB translation state')
    translate_parser.add_argument('--batch-preview-detail-limit', default=5, type=int,
                                  help='How many largest preview units to print per file, default to 5')
    translate_parser.add_argument('--mock-db', action='store_true', default=False,
                                  help='Use in-memory mock database backend for this run')
    translate_parser.add_argument('--mock-db-seed', default='', type=str,
                                  help='Optional JSON seed file for in-memory mock database')
    translate_parser.add_argument('--mock-llm', action='store_true', default=False,
                                  help='Use local mock LLM adapter for this run')
    translate_parser.add_argument('--mock-llm-seed', default='', type=str,
                                  help='Optional JSON seed file for mock LLM translations')
    translate_parser.add_argument('--model', default='', type=str,
                                  help='Override LLM model (e.g. mimo-v2.5-pro, mimo-v2-flash). Sets base_url to xiaomimimo API automatically')
    translate_parser.add_argument('--mimo-api-key', default='', type=str,
                                  help='API key for xiaomimimo model (or set MIMO_API_KEY env)')
    translate_parser.add_argument('--no-ai', dest='use_ai', action='store_false', default=True,
                                  help='Do not use AI for entries missing from the database; keep English text and translate known tag values')
    translate_parser.add_argument('--skip-file-name', default='', type=str,
                                  help='Skip files whose file name contains this value')
    
    search_parser = subparsers.add_parser('search')
    search_parser.add_argument('--query', default='', type=str,
                                  help='Search query')
    
    clean_parser = subparsers.add_parser('clean')
    clean_parser.add_argument('--en', default='',
                                help='Path to the English data, default to the value in config')
    
    #
    term_parser = subparsers.add_parser('term')
    term_parser.add_argument('--en', default='', type=str,
                                  help='Term source path for analysis, supports txt/csv/docx, or use words for add-mode word source sync')
    term_parser.add_argument('--mode', default='add', type=str,
                                  help='Mode to use, default to add, can be add, dump, analyze/analyse, sync or para')
    
    embed_parser = subparsers.add_parser('embed')
    embed_parser.add_argument('--dir', default='/data/DND5e_chm/艾伯伦：从终末战争中崛起', type=str,
                                  help='Path to the directory to embed')
    
    chm_parser = subparsers.add_parser('chm')
    chm_parser.add_argument('--dir', default='/data/DND5e_chm/', type=str,
                                  help='Path to the CHM directory')
    chm_parser.add_argument('--rebuild', action='store_true', help='Force rebuilding the local CHM search index')

    # 为 retry-failed 命令创建子解析器，用于从 failed_jobs.json 重试失败的 jobs
    retry_parser = subparsers.add_parser('retry-failed')
    retry_parser.add_argument('--file', required=True, type=str,
                              help='Path to the failed_jobs json file to retry')
    retry_parser.add_argument('--thread_num', default=10, type=int,
                              help='Number of threads to use, default to 10')
    retry_parser.add_argument('--byhand', action='store_true', default=False,
                              help='Whether to use by hand mode, default to False')

    sync_parser = subparsers.add_parser('sync-splited')
    sync_parser.add_argument('--source-file', required=True, type=str,
                             help='Source JSON file or directory under configured source roots; directories are processed recursively')
    sync_parser.add_argument('--skip-jobs', action='store_true', default=False,
                            help='Only rebuild split files and file table, skip jobs cache')

    
    args = parser.parse_args()
    
    # 从数据库中编码
    if args.function == 'transform':
        from app.cli import transform_proofread

        transform_proofread()
        
    # 数据解析流程
    elif args.function == 'translate':
        from app.core.utils import find_files, find_json_files, write_translate_cache
        from app.core.translator import (
            BatchJobProcessor,
            ByHandHandler,
            JobNeedTranslateSetter,
            JobProcessor,
            JsonAnalyser,
            JsonGenerator,
            KnowledgeSetter,
            NonAiFallbackSetter,
            TermSetter,
        )

        if args.mock_db:
            os.environ['TRANSLATOR_DB_BACKEND'] = 'memory'
            os.environ['TRANSLATOR_DISABLE_REDIS'] = '1'
            if args.mock_db_seed:
                os.environ['TRANSLATOR_MEMORY_DB_SEED'] = args.mock_db_seed
        elif args.mock_db_seed:
            os.environ['TRANSLATOR_MEMORY_DB_SEED'] = args.mock_db_seed
        if args.mock_llm:
            os.environ['TRANSLATOR_LLM_BACKEND'] = 'mock'
            if args.mock_llm_seed:
                os.environ['TRANSLATOR_MOCK_LLM_SEED'] = args.mock_llm_seed
        elif args.mock_llm_seed:
            os.environ['TRANSLATOR_MOCK_LLM_SEED'] = args.mock_llm_seed
        if args.model:
            os.environ['SILICONFLOW_MODEL'] = args.model
            mimo_models = ('mimo-v2.5-pro', 'mimo-v2-flash')
            if args.model in mimo_models:
                os.environ['SILICONFLOW_BASE_URL'] = 'https://token-plan-cn.xiaomimimo.com/v1'
                if args.mimo_api_key:
                    os.environ['MIMO_API_KEY'] = args.mimo_api_key
        if args.en == '':
            if args.mode == '5et':
                from config.settings import EN_PATH

                args.en = EN_PATH
            elif args.mode == 'splited':
                from config.settings import SPLITED_5ETOOLS_EN_PATH
                args.en = SPLITED_5ETOOLS_EN_PATH
            elif args.mode == 'homebrew':
                from config.settings import HOMEBREW_EN_PATH
                args.en = HOMEBREW_EN_PATH
            elif args.mode == 'plu':
                from config.settings import PLU_EN_PATH
                args.en = PLU_EN_PATH
            elif args.mode == 'ua':
                from config.settings import UA_EN_PATH
                args.en = UA_EN_PATH
        if args.file:
            if args.mode == 'splited':
                from app.core.file_progress_service import get_split_file_path

                args.en = get_split_file_path(args.file.strip('/'))
            else:
                args.en = args.file

        config = {'byhand': args.byhand, 'force': args.force, 'force_title': args.force_title, 'mode': args.mode, 'cache': args.cache, 'batch': args.batch, 'batch_max_chars': args.batch_max_chars, 'use_ai': args.use_ai}
        if args.batch_preview:
            preview_pipeline = find_json_files|JsonAnalyser()|JobNeedTranslateSetter()
            if args.use_ai:
                preview_pipeline = preview_pipeline|KnowledgeSetter()|ByHandHandler()|TermSetter()
            res = preview_pipeline.invoke((args.en, args.skip_file_name), config=config)
            preview_batch_units(
                res,
                args.batch_max_chars,
                include_all_jobs=args.batch_preview_all,
                detail_limit=max(args.batch_preview_detail_limit, 0),
            )
            return

        if args.use_ai:
            processor = BatchJobProcessor(args.thread_num, update=True) if args.batch else JobProcessor(args.thread_num, update=True)
            pipeline = find_json_files|JsonAnalyser()|JobNeedTranslateSetter()|KnowledgeSetter()|ByHandHandler()|TermSetter()|write_translate_cache|processor|JsonGenerator(args.thread_num)|write_translate_cache
        else:
            pipeline = find_json_files|JsonAnalyser()|NonAiFallbackSetter()|write_translate_cache|JsonGenerator(args.thread_num)|write_translate_cache
        res = pipeline.invoke((args.en, args.skip_file_name), config=config)
        # res = (find_json_files|JsonAnalyser()|JobNeedTranslateSetter()|write_translate_cache).invoke(args.en, config={'byhand': args.byhand, 'force': args.force, 'force_title': args.force_title, 'mode': args.mode, 'cache': args.cache})
        # res = (find_json_files|JsonAnalyser()).invoke(args.en, config={'byhand': args.byhand, 'force': args.force, 'force_title': args.force_title, 'mode': args.mode, 'cache': args.cache})
        # res = (find_json_files|JsonAnalyser()|JsonGenerator(args.thread_num)|write_translate_cache).invoke(args.en, config={'byhand': args.byhand, 'force': args.force, 'force_title': args.force_title, 'mode': args.mode, 'cache': args.cache})
        # res = (find_json_files|JsonAnalyser()|JobNeedTranslateSetter()|KnowledgeSetter()|ByHandHandler()|TermSetter()|write_translate_cache).invoke(args.en, config={'byhand': args.byhand, 'force': args.force, 'force_title': args.force_title, 'splited': args.splited})
        # res = (find_json_files|JsonAnalyser()|JobNeedTranslateSetter()|write_translate_cache|JobProcessor(args.thread_num, update=True)).invoke(args.en, config={'byhand': args.byhand, 'force': args.force})
        # res = (find_json_files|JsonAnalyser()|JobNeedTranslateSetter()|KnowledgeSetter()|ByHandHandler()|write_translate_cache).invoke(args.en, config={'byhand': args.byhand, 'force': args.force, 'force_title': args.force_title})
        total_files = sum(1 for _ in find_files(args.en, skip_file_name=args.skip_file_name))
        for _ in iter_with_total_progress(res, total_files):
            pass
    elif args.function == 'search':
        from app.cli import search_knowledge

        res = search_knowledge()
        print(res)
    elif args.function == 'clean':
        from app.core.utils import find_json_files
        from app.core.translator import JsonAnalyser
        if args.en == '':
            from config.settings import EN_PATH

            args.en = EN_PATH

        res = (find_json_files|JsonAnalyser()).invoke(args.en)
    elif args.function == 'term':
        from app.cli import (
            add_confirmed_term,
            add_mysql_terms_to_redis,
            combine_temp_terms_to_csv,
            load_term_from_text,
            sync_terms_to_mysql,
        )
        if args.en == '':
            from config.settings import EN_PATH

            args.en = EN_PATH

        if args.mode == 'add':
        # res = (find_json_files|TermFromJson()|AddTermCnFromDB()).invoke(args.en)
        # for t in res:
        #     print(t.category, t.en, t.cn)
            add_mysql_terms_to_redis()
        elif args.mode == 'dump':
            combine_temp_terms_to_csv()
        elif args.mode in ('analyze', 'analyse'):
            from app.core.database import DBDictionary

            input_path = args.en
            if not input_path:
                input_path = '/data/5e-translator/app/core/crawler/valda'

            term_source = ''
            while not term_source:
                term_source = input('请输入术语来源简称（写入 term.source）: ').strip()
                if not term_source:
                    print('来源简称不能为空，请重新输入。')

            file_paths = []
            if os.path.isfile(input_path):
                file_paths.append(input_path)
            else:
                for root, dirs, files in os.walk(input_path):
                    for file in files:
                        if not file.lower().endswith(('.txt', '.csv', '.docx')):
                            continue
                        file_paths.append(os.path.join(root, file))

            db = DBDictionary(conn_num=1)
            for file_path in file_paths:
                print(f'analyzing {file_path}')
                terms = load_term_from_text(file_path)
                if not terms:
                    continue
                db.get_bunch(terms.keys(), ['' for _ in range(len(terms.keys()))], '')
                for en, cn in terms.items():
                    db_bean = db.get(en, load_from_sql=False)
                    if db_bean is None:
                        print(f'{en} not found in db')
                    else:
                        if db_bean['proofread']:
                            print(f'{en} proofread, skip. db: {db_bean["cn"]}, text: {cn}')
                            add_confirmed_term(
                                db, en, db_bean['cn'], db_bean.get('category'), term_source
                            )
                            continue
                        print(f'{en} 没有校对： db: {db_bean["cn"]}, text: {cn}')
                        if db_bean['cn'] == cn:
                            print(f'{en} 没有校对，但是 db 中的 cn 与 text 中的 cn 相同，自动校对')
                            if db.update(db_bean['sql_id'], cn, proofread=True):
                                add_confirmed_term(
                                    db, en, cn, db_bean.get('category'), term_source
                                )
                            continue
                        resp = input(f'更新 {en} 为 {cn}? (Y/n): ')
                        if resp.strip() == 'skip':
                            continue
                        new_cn = cn
                        if resp.lower() != 'y' and resp.strip() != '':
                            new_cn = input('New cn: ')
                        if db.update(db_bean['sql_id'], new_cn, proofread=True):
                            add_confirmed_term(
                                db, en, new_cn, db_bean.get('category'), term_source
                            )
                            # print(f'{en} cn not match, db: {db_bean["cn"]}, text: {cn}')
        elif args.mode == 'sync':
            sync_terms_to_mysql(args.en)
        elif args.mode == 'para':
            from app.core.para import set_terms_to_para

            set_terms_to_para()
        else:
            print('Unknown mode')
        # 输出术语
        # combine_temp_terms_to_csv()
    elif args.function == 'embed':
        from app.cli import load_files_into_chroma_db

        # load_files_into_chroma_db(args.dir)
        load_files_into_chroma_db('/data/DND5e_chm/Generator/Generated/txt/小独与追寻失落之角')
    elif args.function == 'chm':
        from pathlib import Path
        from app.core.chm_search_index import ChmSearchIndex

        result = ChmSearchIndex(Path(args.dir)).sync(force=True, rebuild=args.rebuild)
        print(result)
    elif args.function == 'retry-failed':
        # 从失败文件中读取 jobs 并重试
        import json
        from app.core.translator import JobProcessor
        from app.core.utils import FileWorkInfo, Job

        failed_file = args.file
        try:
            with open(failed_file, 'r') as fh:
                failed_list = json.load(fh)
        except Exception as e:
            print(f'无法读取失败文件: {e}')
            return

        jobs = []
        for jd in failed_list:
            try:
                j = Job(jd.get('uid'), jd.get('en_str'), jd.get('cn_str'), rel_path=jd.get('rel_path', ''), tag=jd.get('tag', ''), knowledge=jd.get('knowledge', []), current_names=jd.get('current_names', []), is_proofread=jd.get('is_proofread', False), sql_id=jd.get('sql_id', None), modified_at=jd.get('modified_at', 0))
                # j.err_time = 1
                # j.last_answer = jd.get('last_answer', '')
                # 保证需要翻译
                j.need_translate = True
                jobs.append(j)
            except Exception as e:
                print(f'构建 Job 失败: {e} - {jd}')

        if len(jobs) == 0:
            print('没有可重试的 Job')
            return

        # 使用已在文件顶部导入的 JobProcessor 重新处理这些 jobs
        # 把 jobs 包装成 FileWorkInfo，out_path 使用 failed 文件名的 basename 作为占位
        out_base = os.path.basename(failed_file)
        file_info = FileWorkInfo(jobs, {}, failed_file, os.path.join('retry', out_base))
        processor = JobProcessor(args.thread_num, update=True)
        # 注意：JobProcessor.invoke 内部通过 config['metadata'] 获取参数
        cfg = {'metadata': {'byhand': args.byhand, 'force': False, 'force_title': False, 'mode': 'splited'}}
        res = processor.invoke([file_info], config=cfg)
        for r in iter_with_total_progress(res, 1):
            print(len(r.job_list), getattr(r, 'json_path', ''))
    elif args.function == 'sync-splited':
        from app.core.file_progress_service import sync_source_path

        with cli_db_app_context():
            res = sync_source_path(args.source_file, rebuild_jobs=not args.skip_jobs)
        print(res)
        
if __name__ == '__main__':
    main()
