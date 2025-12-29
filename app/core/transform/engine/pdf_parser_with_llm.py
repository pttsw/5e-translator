
from app.core.transform.bean.bestiary import Bestiary, Attr, is_camp, is_group, BasicBean
import re
import os
import sys
from llama_parse import LlamaParse
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

if sys.version_info < (3, 9):
    from typing import List
else:
    List = list

class PdfParser:
    
    def __init__(self, file_path):
        self.file_path = file_path
        self.chinese_punctuation = r"。！？；…"
        self.chinese_sentence_pattern = re.compile(f'([^{self.chinese_punctuation}]*[{self.chinese_punctuation}])')
    
        
    def parse(self):
        if not os.path.exists(self.file_path):
            raise FileNotFoundError(f"PDF文件不存在: {self.file_path}")
        if not self.file_path.lower().endswith('.pdf'):
            raise ValueError(f"文件不是PDF格式: {self.file_path}")
        
        try:
            parser = LlamaParse(
                result_type="markdown",  # 输出Markdown格式
                parse_mode="parse_page_with_agent",  # The parsing mode
                model="gemini-2.5-flash",  # The model to use
                skip_images=True,
                # verbose=True,
            )
            markdown_documents = parser.load_data(self.file_path)
            self.documents = []
            with open('output_markdown.md', 'w', encoding='utf-8') as f:
                
                for d in markdown_documents:
                    f.write(d.text)
                    markdown_line = d.text.replace('  ',' ').replace('\n\n','\n')
                    if markdown_line.strip() == '':
                        continue
                    if markdown_line.startswith('#'):
                        title = markdown_line.removeprefix('#').strip()
                        self.documents.append(Document(
                            page_content=title, 
                            metadata={'type': 'title'}
                        ))
                        continue
                    else:
                        self.documents.append(Document(
                            page_content=markdown_line, 
                            metadata={'type': 'content'}
                        ))
        except Exception as e:
            raise RuntimeError(f"处理PDF文件时出错: {str(e)}")
        
    def __split_sentences(self, text: str) -> (List[str]):
        """
        将中文文本分割为句子
        :param text: 未分句的中文文本
        :return: 分句后的中文句子列表
        """
        CHUNK_SIZE = 400

        # 知识库中相邻文本重合长度
        OVERLAP_SIZE = 30
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=CHUNK_SIZE,
            chunk_overlap=OVERLAP_SIZE
        )
        return text_splitter.split_text(text)
    def get_documents(self) -> (List[Document]):
        final_documents = []
        for d in self.documents:
            sentences = self.__split_sentences(d.page_content)
            for s in sentences:
                final_documents.append(Document(
                    page_content=s,
                    metadata=d.metadata
                ))
        return final_documents
    