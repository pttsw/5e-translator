# api_foo.py
from flask_restful import Resource, Api, request
from .restful_utils import *
from app.model import WordsModel, SourceModel, ProofreadModel, session
from .base import BaseApi
from app.core.file_progress_service import refresh_file_runtime
import json
from sqlalchemy import text

from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user

api = Api()

# @api.resource('/words','/words/<int:id>')
@api.resource('/words')
class WordsApi(Resource, BaseApi):
    model = WordsModel
    
    def _get(self, query):
        # 根据文件名进行子查询
        # query = query.filter(getattr(self.model, 'category').is_(None))
        # query = query.filter()
        source_file = request.args.get('source_file', '').strip()
        has_proofread = request.args.get('has_proofread', None, int)
        if has_proofread:
            has_proofread_subquery = ProofreadModel.query \
                .with_entities(ProofreadModel.word_id) \
                .distinct()
            if has_proofread == 1:
                query = query.filter(WordsModel.id.in_(has_proofread_subquery))
            else:
                query = query.filter(~WordsModel.id.in_(has_proofread_subquery))
        if source_file:
            source_subquery = SourceModel.query \
                .with_entities(SourceModel.word_id) \
                .filter(SourceModel.file.contains(source_file)) \
                .distinct()
            query = query.filter(WordsModel.id.in_(source_subquery))
        return query
    
    def _create(self, words):
        if words is None:
            raise Exception("words is none")

    
    def _delete(self, words):
        if words is None:
            raise Exception("words is none")

    
    def _update(self,words):
        if words is None:
            raise Exception("words is none")


@api.resource('/words/independent-translation')
class IndependentTranslationApi(Resource):
    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error("请先登录！")
        if current_user.roles != 'admin':
            return error("无权限！")
        if not request.is_json:
            return error("请求参数必须为JSON")

        data = request.get_json()
        current_file = str(data.get('current_file') or '').strip('/')
        word_id = data.get('word_id')
        en_str = str(data.get('en_str') or '')
        cn = str(data.get('cn') or '').strip()
        tag = data.get('tag')
        if not current_file or not word_id or not en_str or not cn:
            return error("新增独立翻译失败：缺少current_file、word_id、en_str或cn")

        current_word = WordsModel.query.get(word_id)
        if current_word is None:
            return error("新增独立翻译失败：当前词条不存在")
        if current_word.en != en_str:
            return error("新增独立翻译失败：英文原文与当前词条不一致")

        current_source = SourceModel.query.filter_by(
            word_id=current_word.id,
            file=current_file,
        ).first()
        if current_source is None:
            return error("新增独立翻译失败：当前文件未关联此词条")

        target_word = (
            WordsModel.query
            .filter(WordsModel.en == en_str)
            .filter(WordsModel.cn == cn)
            .filter(WordsModel.id != current_word.id)
            .order_by(WordsModel.id.asc())
            .first()
        )
        reused = target_word is not None

        try:
            if target_word is None:
                target_word = WordsModel(
                    en=en_str,
                    cn=cn,
                    json_file=current_file,
                    modified_by=current_user.get_id(),
                    source=current_word.source or "",
                    version=current_word.version or "0",
                )
                target_word.category = tag if tag is not None else current_word.category
                target_word.is_key = int(current_word.is_key or 0)
                target_word.proofread = int(current_word.proofread or 0)
                session.add(target_word)
                session.flush()

            target_source = SourceModel.query.filter_by(
                word_id=target_word.id,
                file=current_file,
            ).first()
            if target_source is None:
                session.add(SourceModel(
                    target_word.id,
                    current_file,
                    current_source.version or target_word.version or "0",
                ))
            session.delete(current_source)
            session.commit()
        except Exception as exc:
            session.rollback()
            print(f"新增独立翻译失败: {exc}")
            return error("新增独立翻译失败：数据库更新失败")

        try:
            progress = refresh_file_runtime(current_file)
        except Exception as exc:
            print(f"新增独立翻译缓存重建失败: {exc}")
            return error("独立翻译已写入数据库，但预览缓存重建失败，请同步当前文件")

        return success(data={
            'reused': reused,
            'word': {
                'id': target_word.id,
                'en': target_word.en,
                'cn': target_word.cn,
                'is_key': int(target_word.is_key or 0),
                'proofread': int(target_word.proofread or 0),
                'category': target_word.category,
            },
            'progress': progress,
        })

# @api.resource('/relations')
# class RelationApi(Resource, BaseApi):
#     model = WordsApi
#     @login_required
#     def get(self):
#         if not current_user.is_authenticated:
#             return error("请先登录！")
#         pageNum = request.args.get('page', None, int)
#         pageSize = request.args.get('limit', 10, int)
#         en = request.args.get('en', None, str)
#         pageItems = self.model.query.filter(getattr(self.model, 'en').contains(en)).paginate( page=pageNum, per_page=pageSize, error_out=False)
#         return success(data={"count": pageItems.total, "items": self.model.list_to_dict(pageItems.items)})

@api.resource('/replace-key')
class ReplaceKeyApi(Resource):
    
    @login_required
    def post(self):
        if current_user.roles != 'admin':
            return error(current_user.roles + "无法使用此接口")
        data = request.get_json()
        if data["wrongCn"] is None or data["rightCn"] is None or data["en"] is None:
            return error("参数不正确")
        # WordsModel.query.filter(WordsModel.en.in_(data['in_en'])).filter(WordsModel.cn.in_(data['nin_cn'])).update()
        try:
            stmt = text("update words set cn = replace(cn,:wrongCn, :rightCn ) where cn like :wrongLikeCn and cn not like :rightLikeCn and source = 'GPT' and en like :en and proofread =0;")
            # stmt = text("select * from words where cn like :wrongLikeCn and source = 'GPT' and en like :en and proofread =0;")
            result = session.execute(stmt, {
                "wrongCn": data['wrongCn'],
                "rightCn": data['rightCn'],
                "wrongLikeCn": f"%{data['wrongCn']}%",
                "rightLikeCn": f"%{data['rightCn']}%",
                "en": f"%{data['en']}%",
            })
            session.commit()
        except:
            session.rollback()
            return error("更新失败")
        return success(data={'count':result.rowcount})
