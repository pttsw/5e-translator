import sys
if sys.version_info < (3, 9):
    from typing import List
else:
    List = list

import os
import re
from typing import List, Dict, Tuple
import sys
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

# 导入markdown库用于解析markdown文件
try:
    import markdown
    from bs4 import BeautifulSoup
except ImportError:
    print("请安装markdown和beautifulsoup4库以支持markdown文件解析: pip install markdown beautifulsoup4")
    sys.exit(1)

class MdParser:
    
    def __init__(self, file_path):
        self.file_path = file_path
        self.documents = []  # 存储解析后的段落
        self.paragraphs_with_format = []  # 存储带格式信息的段落

    def parse(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"Markdown文件不存在: {self.file_path}")
        
        # 检查文件格式是否为markdown
        file_ext = os.path.splitext(self.file_path)[1].lower()
        if file_ext not in ['.md', '.markdown', '.mkd', '.mdown']:
            raise ValueError(f"文件不是Markdown格式: {self.file_path}")
        
        try:
            self._parse_md_file()
            return self.documents
        except Exception as e:
            raise RuntimeError(f"处理Markdown文件时出错: {str(e)}")
        
    def _parse_md_file(self):
        """解析Markdown文件"""
        try:
            # 读取Markdown文件内容
            with open(self.file_path, 'r', encoding='utf-8', errors='replace') as f:
                md_content = f.read()
            
            # 将Markdown转换为HTML，然后提取纯文本
            html_content = markdown.markdown(md_content, output_format='html')
            soup = BeautifulSoup(html_content, 'html.parser')
            plain_text = soup.get_text()
            
            # 按段落分割文本
            paragraphs = plain_text.split('\n\n')
            
            # 处理每个段落
            for paragraph in paragraphs:
                paragraph = paragraph.strip()
                if paragraph:
                    split_contents = self.__split_sentences(paragraph)
                    for content_chunk in split_contents:
                        self.documents.append(Document(page_content=content_chunk))
        except Exception as e:
            raise RuntimeError(f"解析Markdown文件时出错: {str(e)}")

    def __split_sentences(self, text: str) -> List[str]:
        """
        将文本分割为适当大小的块
        """
        # 知识库中单段文本长度
        CHUNK_SIZE = 400
        
        # 知识库中相邻文本重合长度
        OVERLAP_SIZE = 30
        
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=OVERLAP_SIZE
        )
        
        return text_splitter.split_text(text)
    
    def get_documents(self) -> (List[Document]):
        
        return self.documents