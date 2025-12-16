import os
import json
import requests
from langchain_core.runnables import Runnable
from .bean import ParaStrings, ParaFile
from app.core.utils import need_translate_str
from config import PARA_API, PROJECT_ID, PARA_TOKEN
class ParaUpdater(Runnable):
    def __init__(self):
        self.file_list = self.__get_file_list()
        print(f"获取到的文件列表: {self.file_list}")
    def invoke(self, input, config=None, **kwargs):
        self.mode = config['metadata'].get('mode', '5et')
        if self.mode != 'splited':
            # 只处理splited模式
            for res in input:
                yield res
        for res in input:
            paraStrings = []
            for job in res.job_list:
                if job.en_str != "" and \
                    need_translate_str(job.en_str) and \
                    not self.__only_pattern(job.en_str) and \
                    job.cn_str != "":
                    if job.is_proofread:
                        paraStrings.append(ParaStrings(job.uid, job.en_str, job.cn_str))
                    else:
                        paraStrings.append(ParaStrings(job.uid, job.en_str, ""))
            paraFile = ParaFile(res.out_path, paraStrings)
            if not self.__upload_file(paraFile):
                print(f"文件上传失败，文件路径: {paraFile.file_path}")
                continue
            yield res
                
    def __only_pattern(self, en_str:str):
        # 开始和末尾是{@ }，且{@和}的数量相同
        if en_str.startswith("{@") and en_str.endswith("}") and en_str.count("{@") == en_str.count("}"):
            return True
        return False
    def __get_file_list(self):
        response = requests.get(f"{PARA_API}/{PROJECT_ID}/files")
        if response.status_code != 200:
            print(f"获取文件列表失败，状态码: {response.status_code}")
            return []
        file_list = []
        for item in response.json():
            file_list.append(item['name'])
        return file_list
    
    def __upload_file(self, paraFile):
        
        print(f"上传文件路径: {paraFile.rel_path}")
        if paraFile.rel_path in self.file_list:
            return True
        
        if not os.path.exists(os.path.dirname(paraFile.file_path)):
            os.makedirs(os.path.dirname(paraFile.file_path))
        with open(paraFile.file_path, "w") as f:
            # 将paraStrings转为Json字符串
            json_str = json.dumps([para.__upload_file_dict__() for para in paraFile.paraStrings], ensure_ascii=False, indent=2)
            f.write(json_str)
            
        # 调用PARA_API/projects/{projectId}/strings post接口上传文件
        # 构建请求头
        
        headers = {
            "Authorization": f"Bearer {PARA_TOKEN}",
        }
        # 构建请求体
        # Request body:
        # file:string($binary)
        # path:string

        # 发送POST请求
        # path是rel_path的文件夹路径
        # 准备文件对象
        file_to_upload = open(paraFile.file_path, "rb")

        # 正确构建请求：路径信息用data，文件用files
        print(f"{PARA_API}/{PROJECT_ID}/files")

        response = requests.post(
            f"{PARA_API}/{PROJECT_ID}/files",
            headers=headers,
            data={"path": os.path.dirname(paraFile.rel_path)},  # 文本字段放在data中
            files={"file": (os.path.basename(paraFile.file_path), file_to_upload)}  # 文件字段放在files中
        )
        # response = requests.post(f"{PARA_API}/projects/{PROJECT_ID}/files", headers=headers, data={"file": open(paraFile.file_path, "rb"),"path": os.path.dirname(paraFile.rel_path)})
        # 打印响应状态码和内容
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
        # 检查响应状态码
        if response.status_code == 200:
            print("文件上传成功")
            return True
        else:
            print(f"文件上传失败，状态码: {response.status_code}")
            if response.status_code == 400:
                print("文件已存在")
                return True
            return False