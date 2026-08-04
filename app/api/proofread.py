# api_foo.py
from flask_restful import Resource, Api, request
from .restful_utils import *
from app.model import FileModule, ProofreadModel,WordsModel, session, SourceModel, UserModel
from .base import BaseApi
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text
from sqlalchemy.sql import func
from app.core.file_progress_service import clear_file_stale, mark_files_stale, refresh_file_runtime, refresh_progress_by_files, update_job_cache_word

import json

api = Api()


def _is_admin_user():
    return getattr(current_user, 'roles', '') == 'admin'


def _get_file_progress(file_path: str):
    if not file_path:
        return {}
    file_row = FileModule.query.filter_by(file=file_path).first()
    if file_row is None:
        return {}
    return {
        file_path: {
            'total': int(file_row.total or 0),
            'translate': int(file_row.translate or 0),
            'proofread': int(file_row.proofread or 0),
        }
    }


def _word_candidate_dict(word, input_cn=''):
    data = word.to_dict()
    data['matches_input'] = word.cn == input_cn
    return data


def _get_confirmed_replacement_data(word, input_cn=''):
    candidates = (
        WordsModel.query
        .filter(WordsModel.en == word.en)
        .order_by(WordsModel.proofread.desc(), WordsModel.modified_at.desc(), WordsModel.id.asc())
        .all()
    )
    has_input_match = any(candidate.cn == input_cn for candidate in candidates)
    return {
        'word_id': word.id,
        'en': word.en,
        'cn': input_cn,
        'items': [_word_candidate_dict(candidate, input_cn) for candidate in candidates],
        'has_input_match': has_input_match,
    }

@api.resource('/proofread')
class ProofreadApi(Resource, BaseApi):
    model = ProofreadModel
    @login_required
    def get(self):
        pageNum = request.args.get('page', None, int)
        pageSize = request.args.get('limit', 10, int)
        id = request.args.get('id', None, int)
        query = self.model.query

        if id:
            s = query.get(id)
            return self._get_id(s)
        else:
            sort_by = request.args.get('sort')
            equal_filters = dict()
            contain_filters = dict()
            not_equal_filters = dict()
            not_contain_filters = dict()
            for attr, v in request.args.items():
                if attr.startswith('eq_'):
                    equal_filters[attr[3:]] = v
                elif attr.startswith('in_'):
                    contain_filters[attr[3:]] = v
                elif attr.startswith('neq_'):
                    not_equal_filters[attr[4:]] = v
                elif attr.startswith('nin_'):
                    not_contain_filters[attr[4:]] = v
            for f in equal_filters:
                if equal_filters[f]:
                    query = query.filter(getattr(self.model, f) == equal_filters[f])
            for f in contain_filters:
                if contain_filters[f]:
                    query = query.filter(getattr(self.model, f).contains(contain_filters[f]))
            for f, v in not_equal_filters.items():
                if v:
                    query = query.filter(getattr(self.model, f) != v)
            for f, v in not_contain_filters.items():
                if v:
                    query = query.filter(getattr(self.model, f).notlike(f'%{v}%'))
            if sort_by:
                if sort_by[0] == '+':
                    query = query.order_by(getattr(self.model, sort_by[1:]).asc())
                elif sort_by[0] == '-':
                    query = query.order_by(getattr(self.model, sort_by[1:]).desc())
            query = self._get(query)
            if pageNum:
                pageItems = query.paginate(
                    page=pageNum, per_page=pageSize, error_out=False)
                items = [self._enrich_item(item) for item in pageItems.items]
                return success(data={"count": pageItems.total, "items": items})
            else:
                items = query.all()
                results = [self._enrich_item(item) for item in items]
        return success(data={"count": len(results), "items": results})

    def _enrich_item(self, item):
        data = item.to_dict()
        if item.modified_by:
            user = UserModel.query.get(item.modified_by)
            if user:
                data['modified_by_username'] = user.username
            else:
                data['modified_by_username'] = None
        else:
            data['modified_by_username'] = None
        return data
     
    @login_required
    def put(self):
        return error('禁止更新')
        params = request.get_json()
        if params['file'] is None or params['word_id'] is None:
            return error("更新失败：file和word_id不能为空")
        item = self.model.query.filter(getattr(self.model, 'file') == params['file'])\
            .filter(getattr(self.model, 'word_id') == params['word_id']).first()
        if item is None:
            return error(f"更新失败：没有找到相关条目")
        if item.update(commit=True,  updateFunc=self._update,**params) is None:
            return error(f"更新失败：数据库更新失败")
        return success(message=f"Item has been updated successfully.", data = item.to_dict())

    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error('请先登录！')
        user_id = current_user.get_id()
        if request.is_json:
            data = request.get_json()
            current_file = data.pop('current_file', '').strip('/')
            word_id = data.get('word_id')
            cn = data.get('cn')
            en_str = data.get('en_str')
            uid = data.get('uid')
            tag = data.get('tag')
            if cn is None:
                return error("新增失败：cn不能为空")

            word = WordsModel.query.get(word_id) if word_id is not None else None
            if word is None and word_id is not None:
                return error("新增失败：没有找到相关词条")
            if word is None:
                if not current_file or not en_str:
                    return error("新增失败：缺少创建词条所需的文件或英文原文")
                word = (
                    WordsModel.query
                    .join(SourceModel, SourceModel.word_id == WordsModel.id)
                    .filter(SourceModel.file == current_file)
                    .filter(WordsModel.en == en_str)
                    .first()
                )
            if word is not None and int(word.proofread or 0) == 1:
                usage_count = SourceModel.query.filter_by(word_id=word.id).count()
                if usage_count > 1:
                    replacement_data = _get_confirmed_replacement_data(word, cn)
                    replacement_data['requires_replacement'] = True
                    replacement_data['usage_count'] = usage_count
                    replacement_data['can_direct_update'] = False
                    return success(message="此已确认Job被多处引用，请选择替换目标", data=replacement_data)
                if not _is_admin_user():
                    return error("已确认内容仅管理员可直接修改，请联系管理员")

            # if ProofreadModel.query.filter_by(word_id = data['word_id']).filter_by(cn = data['cn']).first():
            #     return error("请勿重复提交已存在的翻译！")

            created_word = False
            created_source = False
            try:
                if word is None:
                    word = WordsModel(
                        en=en_str,
                        cn=en_str,
                        json_file=current_file,
                        modified_by=user_id,
                        source="",
                        version="0",
                    )
                    word.category = tag
                    word.is_key = 0
                    word.proofread = 0
                    session.add(word)
                    session.flush()
                    created_word = True
                word_id = word.id
                source = None
                if current_file:
                    if uid:
                        source = SourceModel.query.filter_by(file=current_file, uid=uid).first()
                    if source is None:
                        source = SourceModel.query.filter_by(
                            word_id=word_id, file=current_file
                        ).first()
                if current_file and source is None:
                    session.add(SourceModel(word_id, current_file, word.version or "0", uid=uid or ""))
                    created_source = True
                item = self.model(
                    word_id=word_id,
                    cn=cn,
                    modified_by=user_id,
                    accepted=0,
                )
                session.add(item)
                word.cn = cn
                word.proofread = 0
                word.is_key = 1
                word.modified_by = user_id
                session.commit()
            except Exception as e:
                session.rollback()
                print(f"提交校对失败: {e}")
                return error("新增失败：数据库更新失败")

            related_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id == item.word_id)
                .distinct()
                .all()
            ]
            if current_file:
                try:
                    if created_word or created_source:
                        progress = refresh_file_runtime(current_file)
                    else:
                        update_job_cache_word(
                            current_file,
                            item.word_id,
                            uid=uid,
                            en_str=en_str,
                            is_key=1,
                            is_proofread=0,
                            cn_str=item.cn
                        )
                        clear_file_stale(current_file)
                        progress = _get_file_progress(current_file)
                except Exception as exc:
                    print(f"提交校对缓存重建失败: {exc}")
                    return error("校对已写入数据库，但预览缓存重建失败，请刷新文件缓存")
            else:
                progress = {}
            stale_files = [file_path for file_path in related_files if file_path and file_path != current_file]
            mark_files_stale(stale_files)
            return success(message=f"新增成功", data={
                'proofread': item.to_dict(),
                'word': word.to_dict(),
                'auto_accepted': False,
                'progress': progress,
                'stale_files': stale_files,
            })
        else:
            return error("The request payload is not in JSON format")
    def _create(self, words):
        if words is None:
            raise Exception("words is none")

    
    def _delete(self, words):
        if words is None:
            raise Exception("words is none")

    
    def _update(self,words):
        if words is None:
            raise Exception("words is none")            


@api.resource('/proofread/confirmed-replacement-options')
class ConfirmedReplacementOptionsApi(Resource):
    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error('请先登录！')
        if not request.is_json:
            return error("请求参数必须为JSON")

        data = request.get_json()
        word_id = data.get('word_id')
        cn = str(data.get('cn') or '').strip()
        if not word_id:
            return error("检查失败：word_id不能为空")
        word = WordsModel.query.get(word_id)
        if word is None:
            return error("检查失败：没有找到相关词条")

        usage_count = SourceModel.query.filter_by(word_id=word.id).count()
        if usage_count <= 1:
            return success(data={
                'word_id': word.id,
                'en': word.en,
                'cn': cn,
                'usage_count': usage_count,
                'requires_replacement': False,
                'can_direct_update': _is_admin_user(),
            })

        replacement_data = _get_confirmed_replacement_data(word, cn)
        replacement_data['usage_count'] = usage_count
        replacement_data['requires_replacement'] = True
        replacement_data['can_direct_update'] = False
        return success(data=replacement_data)


@api.resource('/proofread/confirmed-replacement')
class ConfirmedReplacementApi(Resource):
    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error('请先登录！')
        if not request.is_json:
            return error("请求参数必须为JSON")

        data = request.get_json()
        current_file = str(data.get('current_file') or '').strip('/')
        uid = str(data.get('uid') or '').strip()
        word_id = data.get('word_id')
        target_word_id = data.get('target_word_id')
        cn = str(data.get('cn') or '').strip()
        if not current_file or not uid or not word_id:
            return error("替换失败：current_file、uid、word_id不能为空")

        current_word = WordsModel.query.get(word_id)
        if current_word is None:
            return error("替换失败：当前词条不存在")
        if int(current_word.proofread or 0) != 1:
            return error("替换失败：当前词条不是已确认Job")
        if SourceModel.query.filter_by(word_id=current_word.id).count() <= 1:
            return error("替换失败：当前Job未被多处引用")
        usage = SourceModel.query.filter_by(
            file=current_file,
            uid=uid,
            word_id=current_word.id,
        ).first()
        if usage is None:
            return error("替换失败：没有找到当前Job对应的使用记录")

        target_word = None
        created = False
        if target_word_id:
            target_word = WordsModel.query.get(target_word_id)
            if target_word is None:
                return error("替换失败：选择的词条不存在")
            if target_word.en != current_word.en:
                return error("替换失败：选择的词条英文原文不一致")
        else:
            if not cn:
                return error("替换失败：新增翻译不能为空")
            target_word = (
                WordsModel.query
                .filter(WordsModel.en == current_word.en)
                .filter(WordsModel.cn == cn)
                .order_by(WordsModel.id.asc())
                .first()
            )

        try:
            if target_word is None:
                target_word = WordsModel(
                    en=current_word.en,
                    cn=cn,
                    json_file=current_file,
                    modified_by=current_user.get_id(),
                    source=current_word.source or "",
                    version=current_word.version or "0",
                )
                target_word.category = current_word.category
                target_word.is_key = 1
                target_word.proofread = 1
                session.add(target_word)
                session.flush()
                created = True

            proofread = ProofreadModel(
                word_id=target_word.id,
                cn=target_word.cn,
                modified_by=current_user.get_id(),
                accepted=1,
            )
            session.add(proofread)
            usage.word_id = target_word.id
            usage.version = usage.version or target_word.version or "0"
            session.commit()
        except Exception as exc:
            session.rollback()
            print(f"替换已确认Job失败: {exc}")
            return error("替换失败：数据库更新失败")

        try:
            progress = refresh_file_runtime(current_file)
        except Exception as exc:
            print(f"替换已确认Job缓存重建失败: {exc}")
            return error("替换已写入数据库，但预览缓存重建失败，请刷新文件缓存")

        return success(message="替换成功", data={
            'created': created,
            'word': target_word.to_dict(),
            'progress': progress,
        })


@api.resource('/proofread/batch')
class BatchProofreadApi(Resource, BaseApi):
    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error('请先登录！')
        if not request.is_json:
            return error("The request payload is not in JSON format")

        params = request.get_json()
        current_file = params.get('current_file', '').strip('/')
        items = params.get('items') or []
        if not current_file:
            return error("批量校对失败：current_file不能为空")
        if not items:
            return error("批量校对失败：没有可提交条目")
        if len(items) > 1000:
            return error("批量校对失败：单次最多提交1000条")

        user_id = current_user.get_id()
        results = []
        related_word_ids = []
        try:
            for data in items:
                cn = str(data.get('cn') or '').strip()
                en_str = data.get('en_str')
                uid = data.get('uid')
                tag = data.get('tag')
                word_id = data.get('word_id')
                if not cn or not en_str or not uid:
                    raise ValueError("条目缺少cn、en_str或uid")

                word = WordsModel.query.get(word_id) if word_id is not None else None
                if word is None and word_id is not None:
                    raise ValueError(f"没有找到词条：{word_id}")
                if word is None:
                    word = (
                        WordsModel.query
                        .join(SourceModel, SourceModel.word_id == WordsModel.id)
                        .filter(SourceModel.file == current_file)
                        .filter(WordsModel.en == en_str)
                        .first()
                    )
                if word is None:
                    word = WordsModel(
                        en=en_str,
                        cn=en_str,
                        json_file=current_file,
                        modified_by=user_id,
                        source="",
                        version="0",
                    )
                    word.category = tag
                    word.is_key = 0
                    word.proofread = 0
                    session.add(word)
                    session.flush()

                source = SourceModel.query.filter_by(file=current_file, uid=uid).first()
                if source is None:
                    source = SourceModel.query.filter_by(
                        word_id=word.id,
                        file=current_file,
                    ).first()
                if source is None:
                    session.add(SourceModel(word.id, current_file, word.version or "0", uid=uid or ""))
                else:
                    source.word_id = word.id
                    source.version = word.version or source.version or "0"
                    if uid and not source.uid:
                        source.uid = uid

                proofread = ProofreadModel(
                    word_id=word.id,
                    cn=cn,
                    modified_by=user_id,
                    accepted=0,
                )
                session.add(proofread)
                word.cn = cn
                word.proofread = 0
                word.is_key = 1
                word.modified_by = user_id
                related_word_ids.append(word.id)
                results.append({
                    'uid': uid,
                    'en_str': en_str,
                    'cn_str': cn,
                    'sql_id': word.id,
                    'is_key': 1,
                    'is_proofread': 0,
                })
            session.commit()
        except Exception as exc:
            session.rollback()
            print(f"批量提交校对失败: {exc}")
            return error(f"批量校对失败：{exc}")

        try:
            refresh_file_runtime(current_file)
        except Exception as exc:
            print(f"批量校对缓存重建失败: {exc}")
            return error("批量校对已写入数据库，但预览缓存重建失败，请刷新文件缓存")
        related_files = [
            row[0] for row in session.query(SourceModel.file)
            .filter(SourceModel.word_id.in_(related_word_ids))
            .distinct()
            .all()
        ]
        stale_files = [
            file_path for file_path in related_files
            if file_path and file_path != current_file
        ]
        mark_files_stale(stale_files)
        return success(message="批量校对提交成功", data={
            'items': results,
            'progress': _get_file_progress(current_file),
            'stale_files': stale_files,
        })


@api.resource('/accepted')
class AcceptedApi(Resource, BaseApi):
    @login_required
    def post(self):
        if not current_user.is_authenticated:
            return error("请先登录！")
        if current_user.roles != 'admin':
            return error("无权限！")
        params = request.get_json()
        current_file = params.get('current_file', '').strip('/') if request.is_json else ''
        if 'id' in params.keys():
            proofread = ProofreadModel.query.get(params['id'])
            if proofread is None:
                return error("采纳失败：记录不存在")
            try:
                procedure = text("CALL accept(:proofreadId, :modifiedBy)")
                result = session.execute(procedure, {
                    'proofreadId': params['id'],
                    'modifiedBy': current_user.get_id()
                })
                session.commit()
            except Exception as e:
                session.rollback()
                print(f"存储过程执行失败: {e}")
                return error("采纳失败")
            related_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id == proofread.word_id)
                .distinct()
                .all()
            ]
            if current_file:
                update_job_cache_word(current_file, proofread.word_id, is_key=1, is_proofread=1, cn_str=proofread.cn)
                clear_file_stale(current_file)
                progress = _get_file_progress(current_file)
            else:
                progress = {}
            stale_files = [file_path for file_path in related_files if file_path and file_path != current_file]
            mark_files_stale(stale_files)
            return success(data={'progress': progress, 'stale_files': stale_files})
        elif 'file' in params.keys():
            wids = SourceModel.query.filter(SourceModel.file == params['file']).with_entities(SourceModel.word_id).distinct()
            pids = ProofreadModel.query.group_by(ProofreadModel.word_id
                                                 ).having(ProofreadModel.word_id.in_(wids)
                                                 ).having(func.max(ProofreadModel.accepted) == 0
                                                 ).with_entities(func.max(ProofreadModel.id)
                                                 ).all()
            # result = ProofreadModel.list_to_dict(pids)
            cid = current_user.get_id()
            for pid in pids:
                try:
                    procedure = text("CALL accept(:proofreadId, :modifiedBy)")
                    result = session.execute(procedure, {
                        'proofreadId': pid[0],
                        'modifiedBy': cid
                    })
                    session.commit()
                except Exception as e:
                    session.rollback()
                    print(f"存储过程执行失败: {e}")
                    return error("采纳失败")
            for pid in pids:
                proofread = ProofreadModel.query.get(pid[0])
                if proofread is not None:
                    update_job_cache_word(params['file'], proofread.word_id, is_key=1, is_proofread=1, cn_str=proofread.cn)
            clear_file_stale(params['file'])
            progress = _get_file_progress(params['file'])
            affected_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id.in_(wids))
                .distinct()
                .all()
            ]
            stale_files = [file_path for file_path in affected_files if file_path and file_path != params['file']]
            mark_files_stale(stale_files)
            return success(data={'accepted_count': len(pids), 'progress': progress, 'stale_files': stale_files})
        return success()
