
import os
from dotenv import load_dotenv
# 加载项目根目录下的 .env 文件
load_dotenv()

PARA_TOKEN = os.getenv("PARA_TOKEN")

PARA_API="https://paratranz.cn/api/projects"

PROJECT_ID=17213

PARA_OUTPUT_DIR="output-para"