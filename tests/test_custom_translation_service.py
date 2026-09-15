from types import SimpleNamespace

from app.core.custom_translation_service import build_segments, export_docx, safe_upload_name
from app.model.custom_translation import (
    CustomProjectMemberModel,
    CustomProjectModel,
    CustomSegmentModel,
)


def test_custom_models_use_only_custom_tables():
    assert CustomProjectModel.__tablename__ == "custom_project"
    assert CustomProjectMemberModel.__tablename__ == "custom_project_member"
    assert CustomSegmentModel.__tablename__ == "custom_segment"
    assert "origin_ref_json" in CustomSegmentModel.__table__.columns


def test_segments_keep_sentence_order_and_origin():
    segments = build_segments([{
        "type": "paragraph",
        "text": "First sentence. Second sentence!",
        "origin": {"kind": "line", "index": 0},
    }])
    assert [item["en_text"] for item in segments] == ["First sentence.", "Second sentence!"]
    assert segments[0]["seq"] == 1
    assert '"sentence": 0' in segments[0]["origin_ref_json"]


def test_safe_upload_name_rejects_paths_and_unknown_types():
    assert safe_upload_name("../../module.DOCX") == ("module.DOCX", ".docx")
    try:
        safe_upload_name("module.exe")
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported extension was accepted")


def test_docx_export_keeps_untranslated_english(tmp_path):
    from docx import Document

    source = tmp_path / "source.docx"
    document = Document()
    paragraph = document.add_paragraph()
    paragraph.add_run("First sentence. Second sentence!")
    document.save(source)
    project = SimpleNamespace(
        id=987654,
        title="Unit test",
        original_filename="source.docx",
        stored_path=str(source),
        file_type=".docx",
    )
    segments = [
        SimpleNamespace(block_index=0, en_text="First sentence.", cn_text="第一句。", origin_ref_json='{"kind":"paragraph","index":0,"sentence":0}'),
        SimpleNamespace(block_index=0, en_text="Second sentence!", cn_text="", origin_ref_json='{"kind":"paragraph","index":0,"sentence":1}'),
    ]
    output, _ = export_docx(project, segments)
    assert "第一句。" in Document(output).paragraphs[0].text
    assert "Second sentence!" in Document(output).paragraphs[0].text
