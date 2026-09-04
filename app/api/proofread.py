# api_foo.py
from flask_restful import Resource, Api, request
from .restful_utils import *
from app.model import FileModule, ProofreadModel,WordsModel, session, SourceModel, UserModel
from .base import BaseApi
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from sqlalchemy import text
from sqlalchemy.sql import func
from app.core.file_progress_service import (
    enqueue_cache_refresh_jobs,
    mark_file_cache_error,
    mark_files_dirty,
    refresh_file_runtime,
)

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


PROOFREAD_NOTE = "专业术语可能出现译文不同的情况，请以最新的译文版本为准。"


def _db_bool(value):
    return int(value or 0) == 1


def _source_files_for_words(word_ids):
    word_ids = [word_id for word_id in word_ids if word_id]
    if not word_ids:
        return []
    rows = (
        session.query(SourceModel.file)
        .filter(SourceModel.word_id.in_(word_ids))
        .distinct()
        .all()
    )
    files = []
    seen = set()
    for row in rows:
        file_path = (row[0] or '').strip('/')
        key = file_path.lower()
        if not file_path or key in seen:
            continue
        seen.add(key)
        files.append(file_path)
    return files


def _build_changeset(changed_word_ids=None, affected_files=None, current_file='', revisions=None):
    affected_files = sorted({
        (file_path or '').strip('/')
        for file_path in (affected_files or [])
        if file_path
    })
    return {
        'changed_word_ids': sorted({int(word_id) for word_id in (changed_word_ids or []) if word_id}),
        'affected_files': affected_files,
        'current_file': current_file,
        'revisions': revisions or {},
    }


def _resolve_word(word_id=None, current_file='', en_str='', uid=''):
    word = WordsModel.query.get(word_id) if word_id is not None else None
    if word is not None:
        return word
    query = (
        WordsModel.query
        .join(SourceModel, SourceModel.word_id == WordsModel.id)
        .filter(SourceModel.file == current_file)
    )
    if uid:
        word = query.filter(SourceModel.uid == uid).first()
        if word is not None:
            return word
    if en_str:
        return query.filter(WordsModel.en == en_str).first()
    return None


def _current_usage(word, current_file='', uid=''):
    if not current_file:
        return None
    if uid:
        usage = SourceModel.query.filter_by(file=current_file, uid=uid).first()
        if usage is not None:
            return usage
    if word is None:
        return None
    return SourceModel.query.filter_by(word_id=word.id, file=current_file).first()


def _matching_word(en_str, cn):
    return (
        WordsModel.query
        .filter(text("BINARY words.en = BINARY :matching_en"))
        .filter(WordsModel.cn == cn)
        .params(matching_en=en_str)
        .order_by(WordsModel.proofread.desc(), WordsModel.is_key.desc(), WordsModel.id.asc())
        .first()
    )


def _decision_buttons(*actions):
    labels = {
        'global': '统一修改',
        'local': '仅修改此处',
        'modify': '修改',
        'cancel': '取消',
    }
    return [{'action': action, 'label': labels[action]} for action in actions]


def _build_proofread_decision(word, cn, current_file='', uid='', en_str=''):
    if word is None:
        return {
            'requires_confirmation': False,
            'blocked': False,
            'default_action': 'normal',
        }

    is_key = _db_bool(word.is_key)
    is_confirmed = _db_bool(word.proofread)
    usage_count = SourceModel.query.filter_by(word_id=word.id).count()
    multiple = usage_count > 1
    is_admin = _is_admin_user()
    has_current_file = bool(current_file)

    message = ''
    buttons = []
    blocked = False
    if is_key and is_confirmed and not multiple and (not is_admin):
        blocked = True
        message = "此条目已被管理员确认，无法修改。如确定现有译文有误，请在群内@管理员"
    elif is_confirmed and multiple and (not is_admin) and (not has_current_file):
        blocked = True
        message = "此条目已被管理员确认，无法修改。如确定现有译文有误，请在群内@管理员"
    elif (not is_key) and is_confirmed and multiple and (not is_admin):
        blocked = True
        message = "此条目已被管理员确认，无法修改。如确定现有译文有误，请在群内@管理员"
    elif (not is_key) and is_confirmed and (not multiple) and (not is_admin):
        blocked = True
        message = "此条目已被管理员确认，无法修改。如确定现有译文有误，请在群内@管理员"
    elif multiple and is_confirmed and is_admin and has_current_file:
        message = "此条目在其他文件中被引用，是否统一修改？"
        buttons = _decision_buttons('global', 'local', 'cancel')
    elif multiple and is_confirmed and is_admin:
        message = "此条目在其他文件中被引用，是否统一修改？"
        buttons = _decision_buttons('global', 'cancel')
    elif multiple and is_confirmed and (not is_admin) and has_current_file:
        message = "此条目已被管理员确认，且在其他文件中被引用，仅可单独修改此处。"
        buttons = _decision_buttons('local', 'cancel')
    elif multiple and is_key and (not is_confirmed) and has_current_file:
        message = "此条目已有校对，且在其他文件中被引用，是否统一修改？"
        buttons = _decision_buttons('global', 'local', 'cancel')
    elif multiple and is_key and (not is_confirmed):
        message = "此条目已有校对，且在其他文件中被引用，是否统一修改？"
        buttons = _decision_buttons('global', 'cancel')
    elif (not multiple) and is_confirmed and is_admin:
        message = "此条目已被管理员确认，是否修改？"
        buttons = _decision_buttons('modify', 'cancel')
    elif (not multiple) and is_key and (not is_confirmed) and (not is_admin):
        message = "此条目已有校对，是否修改？"
        buttons = _decision_buttons('modify', 'cancel')

    item = {
        'word_id': word.id,
        'uid': uid,
        'en_str': word.en or en_str,
        'current_cn': word.cn,
        'new_cn': cn,
        'source_files': _source_files_for_words([word.id]),
        'current_file': current_file,
    }
    state = {
        'is_key': is_key,
        'proofread': is_confirmed,
        'usage_count': usage_count,
        'multiple_usage': multiple,
        'is_admin': is_admin,
    }
    return {
        'requires_confirmation': bool(buttons),
        'blocked': blocked,
        'message': message,
        'note': PROOFREAD_NOTE if (buttons or blocked) else '',
        'buttons': buttons,
        'default_action': 'normal',
        'state': state,
        'item': item,
    }


def _ensure_word_for_submit(word, user_id, current_file='', en_str='', uid='', tag=''):
    if word is not None:
        return word, False
    if not current_file or not en_str:
        raise ValueError("缺少创建词条所需的文件或英文原文")
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
    source = SourceModel(word.id, current_file, word.version or "0", uid=uid or "")
    session.add(source)
    return word, True


def _relink_current_usage(source_word, target_word, current_file='', uid=''):
    usage = _current_usage(source_word, current_file, uid)
    if usage is None:
        if not current_file:
            raise ValueError("缺少当前文件，无法仅修改此处")
        usage = SourceModel(target_word.id, current_file, target_word.version or "0", uid=uid or "")
        session.add(usage)
    else:
        usage.word_id = target_word.id
        usage.version = target_word.version or usage.version or "0"
        if uid and not usage.uid:
            usage.uid = uid
    return usage


def _execute_proofread_action(
    word,
    cn,
    user_id,
    current_file='',
    uid='',
    en_str='',
    tag='',
    action='normal',
    refresh_current=True,
    enqueue_related=True,
):
    if action not in {'normal', 'global', 'local', 'modify'}:
        raise ValueError("未知校对动作")

    word, created_word = _ensure_word_for_submit(word, user_id, current_file, en_str, uid, tag)
    original_word_id = word.id
    affected_word_ids = {word.id}
    affected_files = set(_source_files_for_words([word.id]))
    if current_file:
        affected_files.add(current_file)
    degraded_to_local = False
    proofread_item = None
    target_word = word

    if action == 'local':
        matched_word = _matching_word(word.en, cn)
        if matched_word is not None:
            target_word = matched_word
            affected_word_ids.add(matched_word.id)
            affected_files.update(_source_files_for_words([matched_word.id]))
        else:
            target_word = WordsModel(
                en=word.en,
                cn=cn,
                json_file=current_file or word.json_file,
                modified_by=user_id,
                source=word.source or "",
                version=word.version or "0",
            )
            target_word.category = word.category or tag
            target_word.is_key = 1
            target_word.proofread = 0
            session.add(target_word)
            session.flush()
            affected_word_ids.add(target_word.id)
            proofread_item = ProofreadModel(
                word_id=target_word.id,
                cn=cn,
                modified_by=user_id,
                accepted=0,
            )
            session.add(proofread_item)
        _relink_current_usage(word, target_word, current_file, uid)
    else:
        matched_word = _matching_word(word.en, cn)
        if matched_word is not None and matched_word.id != word.id:
            if current_file:
                target_word = matched_word
                affected_word_ids.add(matched_word.id)
                affected_files.update(_source_files_for_words([matched_word.id]))
                degraded_to_local = True
                _relink_current_usage(word, matched_word, current_file, uid)
            else:
                proofread_item = ProofreadModel(
                    word_id=word.id,
                    cn=cn,
                    modified_by=user_id,
                    accepted=0,
                )
                session.add(proofread_item)
                word.cn = cn
                word.proofread = 0
                word.is_key = 1
                word.modified_by = user_id
        else:
            proofread_item = ProofreadModel(
                word_id=word.id,
                cn=cn,
                modified_by=user_id,
                accepted=0,
            )
            session.add(proofread_item)
            word.cn = cn
            word.proofread = 0
            word.is_key = 1
            word.modified_by = user_id

    if current_file:
        usage = _current_usage(word, current_file, uid)
        if usage is None:
            _relink_current_usage(word, target_word, current_file, uid)

    affected_files.update(_source_files_for_words(list(affected_word_ids)))
    dirty_revisions = mark_files_dirty(list(affected_files), commit=False)
    session.commit()

    progress = {}
    cache_error = ""
    if refresh_current:
        try:
            progress = (
                refresh_file_runtime(current_file, cache_revision=dirty_revisions.get(current_file))
                if current_file else {}
            )
        except Exception as exc:
            print(f"提交校对缓存重建失败: {exc}")
            cache_error = "校对已写入数据库，但预览缓存重建失败，请刷新文件缓存"
            mark_file_cache_error(current_file, cache_error)
    elif current_file:
        progress = _get_file_progress(current_file)

    stale_files = [
        file_path for file_path in affected_files
        if file_path and file_path != current_file
    ]
    cache_jobs = (
        enqueue_cache_refresh_jobs(
            dirty_revisions,
            exclude_files=[current_file],
            reason=f"proofread:{action}",
            priority=50,
        )
        if enqueue_related else []
    )
    status_message = (
        "系统发现与新译文相同的条目，为防止过度修改，降级为：“仅修改此处”"
        if degraded_to_local else
        "校对成功"
    )
    return {
        'proofread': proofread_item.to_dict() if proofread_item is not None else {},
        'word': target_word.to_dict(),
        'item': {
            'uid': uid,
            'en_str': target_word.en,
            'cn_str': target_word.cn,
            'sql_id': target_word.id,
            'is_key': target_word.is_key,
            'is_proofread': target_word.proofread,
            'previous_word_id': original_word_id,
        },
        'auto_accepted': False,
        'progress': progress,
        'stale_files': stale_files,
        'cache_jobs': cache_jobs,
        'changeset': _build_changeset(
            changed_word_ids=affected_word_ids,
            affected_files=affected_files,
            current_file=current_file,
            revisions=dirty_revisions,
        ),
        'cache_status': 'error' if cache_error else 'fresh',
        'cache_error': cache_error,
        'degraded_to_local': degraded_to_local,
        'status_message': status_message,
        'action': action,
        'created_word': created_word,
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
            current_file = str(data.get('current_file') or '').strip('/')
            word_id = data.get('word_id')
            cn = str(data.get('cn') or '').strip()
            en_str = data.get('en_str')
            uid = data.get('uid')
            tag = data.get('tag')
            action = str(data.get('action') or '').strip()
            if not cn:
                return error("新增失败：cn不能为空")

            word = _resolve_word(word_id, current_file, en_str, uid)
            if word is None and word_id is not None:
                return error("新增失败：没有找到相关词条")
            decision = _build_proofread_decision(word, cn, current_file, uid, en_str)
            if decision.get('blocked'):
                return success(message=decision.get('message'), data=decision)
            allowed_actions = {
                button['action']
                for button in decision.get('buttons', [])
                if button.get('action') != 'cancel'
            }
            if not action and decision.get('requires_confirmation'):
                return success(message=decision.get('message'), data=decision)
            if action:
                if action == 'cancel':
                    return error("已取消校对")
                if decision.get('requires_confirmation') and action not in allowed_actions:
                    return error("新增失败：当前状态不允许此校对动作")
            else:
                action = decision.get('default_action') or 'normal'

            try:
                result = _execute_proofread_action(
                    word,
                    cn,
                    user_id,
                    current_file=current_file,
                    uid=uid,
                    en_str=en_str,
                    tag=tag,
                    action=action,
                )
                return success(message=result['status_message'], data=result)
            except RuntimeError as exc:
                session.rollback()
                return error(str(exc))
            except Exception as e:
                session.rollback()
                print(f"提交校对失败: {e}")
                return error("新增失败：数据库更新失败")
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
        pending_confirmations = []
        stale_files = []
        cache_jobs = []
        changed_word_ids = []
        affected_files = []
        revisions = {}
        progress = {}
        for index, data in enumerate(items):
            cn = str(data.get('cn') or '').strip()
            en_str = data.get('en_str')
            uid = data.get('uid')
            tag = data.get('tag')
            word_id = data.get('word_id')
            if not cn or not en_str or not uid:
                return error("批量校对失败：条目缺少cn、en_str或uid")

            word = _resolve_word(word_id, current_file, en_str, uid)
            if word is None and word_id is not None:
                return error(f"批量校对失败：没有找到词条：{word_id}")

            submit_data = {
                'current_file': current_file,
                'word_id': word.id if word is not None else word_id,
                'en_str': en_str,
                'uid': uid,
                'tag': tag,
                'cn': cn,
            }
            decision = _build_proofread_decision(word, cn, current_file, uid, en_str)
            if decision.get('requires_confirmation') or decision.get('blocked'):
                decision['batch_index'] = index
                decision['submit_data'] = submit_data
                pending_confirmations.append(decision)
                continue

            try:
                result = _execute_proofread_action(
                    word,
                    cn,
                    user_id,
                    current_file=current_file,
                    uid=uid,
                    en_str=en_str,
                    tag=tag,
                    action='normal',
                    refresh_current=False,
                    enqueue_related=False,
                )
            except RuntimeError as exc:
                session.rollback()
                return error(str(exc))
            except Exception as exc:
                session.rollback()
                print(f"批量提交校对失败: {exc}")
                return error(f"批量校对失败：{exc}")
            results.append(result['item'])
            progress = result.get('progress') or progress
            stale_files.extend(result.get('stale_files') or [])
            cache_jobs.extend(result.get('cache_jobs') or [])
            changeset = result.get('changeset') or {}
            changed_word_ids.extend(changeset.get('changed_word_ids') or [])
            affected_files.extend(changeset.get('affected_files') or [])
            revisions.update(changeset.get('revisions') or {})

        cache_error = ""
        if revisions.get(current_file):
            try:
                progress = refresh_file_runtime(
                    current_file,
                    cache_revision=revisions.get(current_file),
                )
            except Exception as exc:
                print(f"批量校对缓存重建失败: {exc}")
                cache_error = "批量校对已写入数据库，但预览缓存重建失败，请刷新文件缓存"
                mark_file_cache_error(current_file, cache_error)
        cache_jobs = enqueue_cache_refresh_jobs(
            revisions,
            exclude_files=[current_file],
            reason="batch-proofread",
            priority=50,
        )

        deduped_stale_files = []
        seen_stale_files = set()
        for file_path in stale_files:
            key = file_path.lower()
            if not file_path or key in seen_stale_files:
                continue
            seen_stale_files.add(key)
            deduped_stale_files.append(file_path)
        return success(message="批量校对提交成功", data={
            'items': results,
            'pending_confirmations': pending_confirmations,
            'progress': progress or _get_file_progress(current_file),
            'stale_files': deduped_stale_files,
            'cache_jobs': cache_jobs,
            'cache_status': 'error' if cache_error else 'fresh',
            'cache_error': cache_error,
            'changeset': _build_changeset(
                changed_word_ids=changed_word_ids,
                affected_files=affected_files,
                current_file=current_file,
                revisions=revisions,
            ),
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
            dirty_revisions = mark_files_dirty(related_files)
            progress = {}
            cache_error = ""
            if current_file:
                try:
                    progress = refresh_file_runtime(
                        current_file,
                        cache_revision=dirty_revisions.get(current_file),
                    )
                except Exception as exc:
                    print(f"采纳校对缓存重建失败: {exc}")
                    cache_error = "采纳已写入数据库，但预览缓存重建失败，请刷新文件缓存"
                    mark_file_cache_error(current_file, cache_error)
            stale_files = [file_path for file_path in related_files if file_path and file_path != current_file]
            cache_jobs = enqueue_cache_refresh_jobs(
                dirty_revisions,
                exclude_files=[current_file],
                reason="accept-proofread",
                priority=40,
            )
            return success(data={
                'progress': progress,
                'stale_files': stale_files,
                'cache_jobs': cache_jobs,
                'changeset': _build_changeset(
                    changed_word_ids=[proofread.word_id],
                    affected_files=related_files,
                    current_file=current_file,
                    revisions=dirty_revisions,
                ),
                'cache_status': 'error' if cache_error else 'fresh',
                'cache_error': cache_error,
            })
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
            changed_word_ids = []
            for pid in pids:
                proofread = ProofreadModel.query.get(pid[0])
                if proofread is not None:
                    changed_word_ids.append(proofread.word_id)
            affected_files = [
                row[0] for row in session.query(SourceModel.file)
                .filter(SourceModel.word_id.in_(wids))
                .distinct()
                .all()
            ]
            dirty_revisions = mark_files_dirty(affected_files)
            current_file = params['file']
            progress = {}
            cache_error = ""
            try:
                progress = refresh_file_runtime(
                    current_file,
                    cache_revision=dirty_revisions.get(current_file),
                )
            except Exception as exc:
                print(f"批量采纳缓存重建失败: {exc}")
                cache_error = "采纳已写入数据库，但预览缓存重建失败，请刷新文件缓存"
                mark_file_cache_error(current_file, cache_error)
            stale_files = [file_path for file_path in affected_files if file_path and file_path != params['file']]
            cache_jobs = enqueue_cache_refresh_jobs(
                dirty_revisions,
                exclude_files=[current_file],
                reason="accept-file",
                priority=40,
            )
            return success(data={
                'accepted_count': len(pids),
                'progress': progress,
                'stale_files': stale_files,
                'cache_jobs': cache_jobs,
                'changeset': _build_changeset(
                    changed_word_ids=changed_word_ids,
                    affected_files=affected_files,
                    current_file=current_file,
                    revisions=dirty_revisions,
                ),
                'cache_status': 'error' if cache_error else 'fresh',
                'cache_error': cache_error,
            })
        return success()
