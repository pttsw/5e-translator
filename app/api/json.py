# api_foo.py
import json
import os
from flask_restful import Resource, Api, request
from .restful_utils import *
from app.model import FileModule
from config import SPLITED_5ETOOLS_EN_PATH, logger, APP_TEMP_PATH
from app.core.translator import JsonAnalyser, JsonGenerator
from .base import BaseApi
from app.core.utils.parser import get_source_json_to_full
from app.core.utils.loader import write_file_work_infos
api = Api()

def find_json_files(root_folder:str):
    json_files = []
    file_list = FileModule.query.all()
    # 直接获取当前文件夹下的文件，不递归
    for file in os.listdir(root_folder):
        file_path = os.path.join(root_folder, file)
        # 确保是文件且以.json结尾
        rel_path = os.path.relpath(file_path, SPLITED_5ETOOLS_EN_PATH)
        if os.path.isfile(file_path) and file.endswith('.json'):
            in_db = False
            for db_file in file_list:
                if db_file.file == rel_path:
                    json_files.append({
                        'file': rel_path,
                        'display_name': rel_path.split('/')[-1],
                        'source_file': db_file.source_file,
                        'total': db_file.total,
                        'translate': db_file.translate,
                        'proofread': db_file.proofread,
                        'locked': db_file.locked,
                    })
                    in_db = True
                    break
            if not in_db:
                logger.warning(f"文件{rel_path}不在数据库中")
        elif os.path.isdir(file_path):
            # 查询file_list中以此文件夹为前缀的文件
            dir_file = {
                'file': rel_path,
                'display_name': get_source_json_to_full(rel_path.split('/')[-1]),
                'source_file': '',
                'total': 0,
                'translate': 0,
                'proofread': 0,
            }
            for db_file in file_list:
                if db_file.file.startswith(rel_path+'/'):
                    dir_file['total'] += db_file.total
                    dir_file['translate'] += db_file.translate
                    dir_file['proofread'] += db_file.proofread
            json_files.append(dir_file)
            
    return json_files

@api.resource('/json')
class JsonApi(Resource):
    def get(self):
        file_name = request.args.get('file', '', str)
        source = request.args.get('source', None, str)
        file_name = file_name.strip('/')
        if file_name and file_name != '':
            file_path = os.path.join(SPLITED_5ETOOLS_EN_PATH, file_name)
            if not os.path.exists(file_path):
                return error(f"{file_name}不存在")
            if os.path.isdir(file_path):
                # 递归获取子文件夹下的json文件
                files = find_json_files(file_path)
                return success(data=files)
            elif os.path.isfile(file_path):
                with open(file_path, 'r') as file:
                    db_file = FileModule.query.filter_by(file=file_name).first()
                    if db_file is None:
                        return error(f"{file_name}不在数据库中")
                    if db_file.locked:
                        return success(data={
                            'file': file_name,
                            'locked': True,
                            'cn_content': db_file.cn_json,
                            'json_content': db_file.en_json,
                        })
                    job_list, cn_obj = self.__get_job_list_by_file(file_path)
                    
                    content = file.read()
                    json_content = json.loads(content)
                    if source and isinstance(json_content,dict):
                        json_content = self.__check_source(json_content, source)
                    return success(data=[{
                        'file': file_name,
                        'source_file': '', # 这里应该用不上吧
                        'total': len(job_list),
                        'translate': 0,
                        'proofread': len([j for j in job_list if j['is_proofread'] == 0]),
                        'job_list': job_list,
                        'cn_content': cn_obj,
                        'json_content': json_content,
                    }])
        else:
            # 获取文件列表
            if not os.path.exists(SPLITED_5ETOOLS_EN_PATH):
                return error("数据目录不存在，请通知管理员检查")
            files = find_json_files(SPLITED_5ETOOLS_EN_PATH)
            return success(data=files)
        return error("参数错误")
    
    def __get_job_list_by_file(self, file_path):
        if SPLITED_5ETOOLS_EN_PATH not in file_path:
            return []
        job_list = []
        rel_path = os.path.relpath(file_path, SPLITED_5ETOOLS_EN_PATH)
        job_file_path = os.path.join(APP_TEMP_PATH, rel_path+'.jobs')
        cn_file_path = os.path.join(APP_TEMP_PATH, rel_path)
        if os.path.exists(job_file_path) and os.path.exists(cn_file_path):
            with open(job_file_path, 'r') as job_file:
                job_list = json.load(job_file)
            with open(cn_file_path, 'r') as cn_file:
                cn_obj = json.load(cn_file)
        else:
            file_work_infos = (JsonAnalyser()|JsonGenerator(1)).invoke([file_path],{'metadata':{'mode':'splited'}})
            os.makedirs(os.path.dirname(job_file_path), exist_ok=True)
            for file_work_info in file_work_infos:
            #     # 只留下数据库里有的
                file_work_info.job_list = [j.__dict__ for j in file_work_info.job_list if j.sql_id]
                write_file_work_infos(file_work_info, APP_TEMP_PATH)
            with open(job_file_path, 'r') as job_file:
                job_list = json.load(job_file)
            with open(cn_file_path, 'r') as cn_file:
                cn_obj = json.load(cn_file)
        return job_list, cn_obj
        
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