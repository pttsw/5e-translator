# api_foo.py
import json
import os
from flask import current_app
from flask_restful import Resource, Api, request
from sqlalchemy import func
from .restful_utils import *
from app.model import FileModule
from config import SPLITED_5ETOOLS_EN_PATH, logger
from app.core.utils.parser import get_source_json_to_full
from app.core.file_progress_service import (
    enqueue_sync_split_file,
    ensure_jobs_cache,
    get_sync_task,
    get_progress_from_jobs,
    is_file_marked_stale,
    is_source_file_stale,
    sync_split_file,
    update_file_progress_from_jobs,
)
api = Api()

def list_json_entries(rel_dir: str):
    rel_dir = rel_dir.strip('/')
    json_files = []

    file_rows = (
        FileModule.query.with_entities(
            FileModule.file,
            FileModule.source_file,
            FileModule.total,
            FileModule.translate,
            FileModule.proofread,
            FileModule.locked,
            FileModule.stale,
        )
        .filter(FileModule.parent_dir == rel_dir)
        .order_by(FileModule.file)
        .all()
    )
    for row in file_rows:
        json_files.append({
            'file': row.file,
            'display_name': row.file.split('/')[-1],
            'source_file': row.source_file,
            'total': row.total,
            'translate': row.translate,
            'proofread': row.proofread,
            'locked': row.locked,
            'stale': row.stale,
        })

    prefix = f"{rel_dir}/" if rel_dir else ""
    remainder_expr = func.substr(FileModule.file, len(prefix) + 1)
    child_name_expr = func.substring_index(remainder_expr, '/', 1)
    dir_path_expr = child_name_expr if not rel_dir else func.concat(rel_dir, '/', child_name_expr)

    dir_rows = (
        FileModule.query.with_entities(
            dir_path_expr.label('file'),
            func.sum(FileModule.total).label('total'),
            func.sum(FileModule.translate).label('translate'),
            func.sum(FileModule.proofread).label('proofread'),
        )
        .filter(FileModule.file.like(f"{prefix}%"))
        .filter(remainder_expr.like('%/%'))
        .group_by(dir_path_expr)
        .order_by(dir_path_expr)
        .all()
    )
    for row in dir_rows:
        json_files.append({
            'file': row.file,
            'display_name': get_source_json_to_full(row.file.split('/')[-1]),
            'source_file': '',
            'total': int(row.total or 0),
            'translate': int(row.translate or 0),
            'proofread': int(row.proofread or 0),
        })
    return json_files

@api.resource('/json')
class JsonApi(Resource):
    def post(self):
        if not request.is_json:
            return error("请求体必须是JSON")
        params = request.get_json()
        task_id = params.get('task_id', '').strip()
        if task_id:
            task = get_sync_task(task_id)
            if task is None:
                return error("同步任务不存在")
            return success(data=task)
        file_name = params.get('file', '').strip('/')
        if not file_name:
            source_file = params.get('source_file', '').strip('/')
            if source_file:
                db_file = FileModule.query.filter_by(source_file=source_file).first()
                file_name = db_file.file if db_file else ''
        if not file_name:
            return error("file不能为空")
        try:
            sync_task = enqueue_sync_split_file(file_name, current_app._get_current_object())
            return success(data=sync_task)
        except Exception as exc:
            logger.error(f"同步文件失败: {file_name}, {exc}")
            return error(f"同步失败: {exc}")

    def get(self):
        file_name = request.args.get('file', '', str)
        source = request.args.get('source', None, str)
        file_name = file_name.strip('/')
        if file_name and file_name != '':
            file_path = os.path.join(SPLITED_5ETOOLS_EN_PATH, file_name)
            db_file = FileModule.query.filter_by(file=file_name).first()

            if db_file is not None and not os.path.exists(file_path):
                try:
                    sync_split_file(file_name)
                    db_file = FileModule.query.filter_by(file=file_name).first()
                except Exception as exc:
                    logger.error(f'拆分文件缺失，自动同步失败: {file_name}, {exc}')
                file_path = os.path.join(SPLITED_5ETOOLS_EN_PATH, file_name)

            if os.path.isdir(file_path):
                files = list_json_entries(file_name)
                return success(data=files)
            elif os.path.isfile(file_path):
                if db_file is None:
                    return error(f'{file_name}不在数据库中')
                if db_file.stale or is_file_marked_stale(file_name) or is_source_file_stale(db_file.source_file, file_name):
                    sync_split_file(file_name)
                    db_file = FileModule.query.filter_by(file=file_name).first()
                    if db_file is None:
                        return error(f'{file_name}已被移除')
                    file_path = os.path.join(SPLITED_5ETOOLS_EN_PATH, file_name)
                if db_file.locked:
                    return success(data={
                        'file': file_name,
                        'locked': True,
                        'cn_content': db_file.cn_json,
                        'json_content': db_file.en_json,
                    })
                job_list, cn_obj = self.__get_job_list_by_file(file_path)
                progress = get_progress_from_jobs(job_list)
                update_file_progress_from_jobs(file_name, job_list)

                with open(file_path, 'r') as file:
                    content = file.read()
                json_content = json.loads(content)
                if source and isinstance(json_content, dict):
                    json_content = self.__check_source(json_content, source)
                return success(data=[{
                    'file': file_name,
                    'source_file': '', # 这里应该用不上吧
                    'total': progress['total'],
                    'translate': progress['translate'],
                    'proofread': progress['proofread'],
                    'stale': db_file.stale,
                    'job_list': job_list,
                    'cn_content': cn_obj,
                    'json_content': json_content,
                }])
            dir_entries = list_json_entries(file_name)
            if dir_entries:
                return success(data=dir_entries)
            return error(f'{file_name}不存在')
        else:
            files = list_json_entries('')
            return success(data=files)
        return error('参数错误')

    def __get_job_list_by_file(self, file_path):
        if SPLITED_5ETOOLS_EN_PATH not in file_path:
            return []
        rel_path = os.path.relpath(file_path, SPLITED_5ETOOLS_EN_PATH)
        return ensure_jobs_cache(rel_path)
        
    def __check_source(self, json_dict, source):
        return_dict = {}
        if not isinstance(json_dict, dict):
            return json_dict
        if 'source' in json_dict.keys():
            if json_dict['source'] == source:
                return json_dict
            else:
                return None
        for k,v in json_dict.items():
            if isinstance(v, dict):
                return_dict[k] = self.__check_source(v, source)
            elif isinstance(v, list):
                return_dict[k] = []
                for vv in v:
                    res_temp = self.__check_source(vv, source)
                    if res_temp:
                        return_dict[k].append(self.__check_source(vv, source))
        return return_dict
