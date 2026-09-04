"""API for private, collaborative translation projects.

This module deliberately never imports SourceModel or ProofreadModel: custom
project work remains isolated from the shared 5etools translation corpus.
"""

import json
import secrets
import threading
from pathlib import Path

from flask import send_file
from flask_login import current_user, login_required
from flask_restful import Api, Resource, request
from sqlalchemy import inspect, or_

from app.core.custom_translation_service import (
    CUSTOM_ROOT, build_segments, export_docx, extract_blocks, find_similar_sources,
    prefill_exact_matches, run_5etools_conversion, safe_upload_name,
)
from app.model import (
    CustomInviteModel, CustomProjectMemberModel, CustomProjectModel,
    CustomSegmentModel, FileModule, WordsModel, db, ensure_custom_translation_table_schema,
)
from .restful_utils import error, success

api = Api()
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = False


def _ensure_custom_schema():
    """Create only the isolated custom tables when a deployment missed startup migration."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        ensure_custom_translation_table_schema()
        required = {
            "custom_project", "custom_project_member",
            "custom_project_invite", "custom_segment",
        }
        existing = set(inspect(db.engine).get_table_names())
        missing = required - existing
        if missing:
            raise RuntimeError("自定义项目数据表初始化失败：%s" % ", ".join(sorted(missing)))
        _SCHEMA_READY = True


def _user_id():
    _ensure_custom_schema()
    try:
        return int(current_user.get_id())
    except (TypeError, ValueError):
        return None


def _membership(project_id, user_id):
    return CustomProjectMemberModel.query.filter_by(project_id=project_id, user_id=user_id).first()


def _project_for_member(project_id):
    project = CustomProjectModel.query.get(project_id)
    user_id = _user_id()
    if project is None or user_id is None:
        return None, None
    member = _membership(project.id, user_id)
    if project.owner_id == user_id and member is None:
        member = CustomProjectMemberModel(project_id=project.id, user_id=user_id, role="owner")
        db.session.add(member)
        db.session.commit()
    return project, member


def _require_member(project_id):
    project, member = _project_for_member(project_id)
    if project is None:
        raise ValueError("项目不存在")
    if member is None:
        raise PermissionError("无权访问此项目")
    return project, member


def _require_owner(project_id):
    project, member = _require_member(project_id)
    if member.role != "owner":
        raise PermissionError("只有项目所有者可以执行此操作")
    return project


def _refresh_progress(project):
    rows = CustomSegmentModel.query.filter_by(project_id=project.id).all()
    project.total = len(rows)
    project.translated = sum(1 for row in rows if (row.cn_text or "").strip())
    project.proofread = sum(1 for row in rows if int(row.proofread or 0) == 1)
    return project


def _project_data(project, role=None):
    data = project.to_dict()
    data["role"] = role or ("owner" if project.owner_id == _user_id() else "editor")
    data["progress_percent"] = round((project.proofread or 0) * 100 / project.total, 1) if project.total else 0
    try:
        data["similar_matches"] = json.loads(project.similar_matches_json or "[]")
    except ValueError:
        data["similar_matches"] = []
    return data


def _existing_file_candidates(candidates):
    """Only offer redirects to files which the established proofread UI can open."""
    source_files = [item.get("source_file") for item in candidates if item.get("source_file")]
    if not source_files:
        return []
    rows = FileModule.query.filter(FileModule.source_file.in_(source_files)).all()
    split_paths = {row.source_file: row.file for row in rows if row.file}
    return [
        {**item, "existing_file_path": split_paths[item["source_file"]]}
        for item in candidates if item.get("source_file") in split_paths
    ]


@api.resource("/custom/projects", "/custom/projects/upload")
class CustomProjectsApi(Resource):
    @login_required
    def get(self):
        user_id = _user_id()
        memberships = CustomProjectMemberModel.query.filter_by(user_id=user_id).all()
        role_by_project = {item.project_id: item.role for item in memberships}
        project_ids = list(role_by_project)
        query = CustomProjectModel.query.filter(or_(CustomProjectModel.owner_id == user_id, CustomProjectModel.id.in_(project_ids or [-1])))
        projects = query.order_by(CustomProjectModel.modified_at.desc()).all()
        return success(data={"count": len(projects), "items": [_project_data(item, role_by_project.get(item.id)) for item in projects]})

    @login_required
    def post(self):
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return error("请选择要上传的文件")
        try:
            filename, suffix = safe_upload_name(upload.filename)
            user_id = _user_id()
            project = CustomProjectModel(
                title=(request.form.get("title") or Path(filename).stem).strip()[:255] or Path(filename).stem,
                owner_id=user_id,
                original_filename=filename,
                stored_path="",
                file_type=suffix,
                status="processing",
                source_match_status="pending",
            )
            db.session.add(project)
            db.session.flush()
            upload_dir = CUSTOM_ROOT / str(project.id)
            upload_dir.mkdir(parents=True, exist_ok=True)
            stored_path = upload_dir / ("original" + suffix)
            upload.save(str(stored_path))
            project.stored_path = str(stored_path)
            blocks = extract_blocks(stored_path)
            segments = build_segments(blocks)
            if not segments:
                raise ValueError("文件中没有可校对的英文文本")
            prefill_exact_matches(segments, WordsModel)
            for item in segments:
                db.session.add(CustomSegmentModel(project_id=project.id, **item))
            candidates = _existing_file_candidates(find_similar_sources(stored_path))
            project.similar_matches_json = json.dumps(candidates, ensure_ascii=False)
            project.source_match_status = "awaiting_confirmation" if candidates else "no_match"
            project.status = "awaiting_source_confirmation" if candidates else "active"
            _refresh_progress(project)
            db.session.add(CustomProjectMemberModel(project_id=project.id, user_id=user_id, role="owner"))
            db.session.commit()
            return success(message="上传成功", data={
                "project": _project_data(project, "owner"),
                "candidates": candidates,
                "requires_source_confirmation": bool(candidates),
            })
        except Exception as exc:
            db.session.rollback()
            return error(str(exc))


@api.resource("/custom/projects/<int:project_id>")
class CustomProjectApi(Resource):
    @login_required
    def get(self, project_id):
        try:
            project, member = _require_member(project_id)
            return success(data=_project_data(project, member.role))
        except (ValueError, PermissionError) as exc:
            return error(str(exc))


@api.resource("/custom/projects/<int:project_id>/confirm-source")
class CustomConfirmSourceApi(Resource):
    @login_required
    def post(self, project_id):
        try:
            project = _require_owner(project_id)
            payload = request.get_json(silent=True) or {}
            action = payload.get("action")
            if action == "continue":
                project.continued_after_match = 1
                project.source_match_status = "continued"
                project.status = "active"
                db.session.commit()
                return success(data={"project": _project_data(project), "redirect_file_path": ""})
            if action == "existing":
                candidate = payload.get("candidate") or {}
                source_file = str(candidate.get("existing_file_path") or candidate.get("source_file") or "").strip("/")
                if not source_file:
                    return error("请选择已有文件")
                project.source_match_status = "matched_existing"
                project.status = "redirected_to_existing"
                db.session.commit()
                return success(data={"project": _project_data(project), "redirect_file_path": source_file})
            return error("未知的确认操作")
        except (ValueError, PermissionError) as exc:
            return error(str(exc))


@api.resource("/custom/projects/<int:project_id>/segments")
class CustomSegmentsApi(Resource):
    @login_required
    def get(self, project_id):
        try:
            project, _ = _require_member(project_id)
            items = CustomSegmentModel.query.filter_by(project_id=project.id).order_by(CustomSegmentModel.seq.asc()).all()
            return success(data={"count": len(items), "items": [item.to_dict() for item in items], "progress": _project_data(project)})
        except (ValueError, PermissionError) as exc:
            return error(str(exc))


@api.resource("/custom/segments/<int:segment_id>")
class CustomSegmentApi(Resource):
    @login_required
    def put(self, segment_id):
        segment = CustomSegmentModel.query.get(segment_id)
        if segment is None:
            return error("分句不存在")
        try:
            project, _ = _require_member(segment.project_id)
            payload = request.get_json(silent=True) or {}
            if "en_text" in payload:
                value = str(payload["en_text"] or "").strip()
                if not value:
                    return error("英文原文不能为空")
                segment.en_text = value
            if "cn_text" in payload:
                segment.cn_text = str(payload["cn_text"] or "").strip()
            if "proofread" in payload:
                segment.proofread = 1 if payload["proofread"] else 0
            segment.modified_by = _user_id()
            _refresh_progress(project)
            db.session.commit()
            return success(message="分句已保存", data={"segment": segment.to_dict(), "progress": _project_data(project)})
        except (ValueError, PermissionError) as exc:
            return error(str(exc))
        except Exception:
            db.session.rollback()
            return error("保存分句失败")


@api.resource("/custom/projects/<int:project_id>/invites")
class CustomInvitesApi(Resource):
    @login_required
    def post(self, project_id):
        try:
            project = _require_owner(project_id)
            for _ in range(5):
                token = secrets.token_urlsafe(32)
                if CustomInviteModel.query.filter_by(token=token).first() is None:
                    invite = CustomInviteModel(project_id=project.id, token=token, created_by=_user_id())
                    db.session.add(invite)
                    db.session.commit()
                    return success(data={"token": token, "project_id": project.id})
            return error("生成邀请链接失败，请重试")
        except (ValueError, PermissionError) as exc:
            return error(str(exc))


@api.resource("/custom/invites/<string:token>/join")
class CustomInviteJoinApi(Resource):
    @login_required
    def post(self, token):
        invite = CustomInviteModel.query.filter_by(token=token).first()
        if invite is None:
            return error("邀请链接不存在或已失效")
        project = CustomProjectModel.query.get(invite.project_id)
        if project is None:
            return error("邀请项目不存在")
        member = _membership(project.id, _user_id())
        if member is None:
            db.session.add(CustomProjectMemberModel(project_id=project.id, user_id=_user_id(), role="editor"))
            db.session.commit()
        return success(message="已加入项目", data={"project": _project_data(project, "editor")})


@api.resource("/custom/projects/<int:project_id>/export/docx")
class CustomExportApi(Resource):
    @login_required
    def get(self, project_id):
        try:
            project = _require_owner(project_id)
            segments = CustomSegmentModel.query.filter_by(project_id=project.id).order_by(CustomSegmentModel.seq.asc()).all()
            export_path, report = export_docx(project, segments)
            project.export_path = export_path
            project.conversion_report = (project.conversion_report or "") + ("\n" if project.conversion_report else "") + report
            db.session.commit()
            return send_file(export_path, as_attachment=True, download_name=Path(export_path).name,
                             mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        except (ValueError, PermissionError) as exc:
            return error(str(exc))
        except Exception as exc:
            db.session.rollback()
            return error("导出失败：%s" % exc)


@api.resource("/custom/projects/<int:project_id>/convert-5etools")
class CustomConvertApi(Resource):
    @login_required
    def post(self, project_id):
        try:
            project = _require_owner(project_id)
            segments = CustomSegmentModel.query.filter_by(project_id=project.id).order_by(CustomSegmentModel.seq.asc()).all()
            if project.total and project.proofread < project.total:
                return success(data={"requires_proofread": True, "proofread": project.proofread, "total": project.total})
            result = run_5etools_conversion(project, segments, request.get_json(silent=True) or {})
            if result.get("requires_metadata"):
                return success(data=result)
            project.conversion_json_path = result["json_path"]
            project.conversion_report = result["report"]
            project.status = "converted"
            db.session.commit()
            return success(message="5etools JSON 转换完成", data=result)
        except (ValueError, PermissionError) as exc:
            return error(str(exc))
        except Exception as exc:
            db.session.rollback()
            return error("5etools 转换失败：%s" % exc)
