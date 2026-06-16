from app.core.utils.loader import _find_json_files, find_files


def test_find_files_skips_matching_file_names_in_directory(tmp_path):
    included = tmp_path / "include.json"
    skipped = tmp_path / "Death's Friend.json"
    matching_directory = tmp_path / "Death's Friend directory"
    matching_directory.mkdir()
    nested = matching_directory / "nested.json"

    included.write_text("{}", encoding="utf-8")
    skipped.write_text("{}", encoding="utf-8")
    nested.write_text("{}", encoding="utf-8")

    results = list(find_files(str(tmp_path), skip_file_name="Death's Friend"))

    assert results == [str(included), str(nested)]


def test_find_files_skips_a_matching_single_file(tmp_path):
    skipped = tmp_path / "prefix-skip-me-suffix.json"
    skipped.write_text("{}", encoding="utf-8")

    assert list(find_files(str(skipped), skip_file_name="skip-me")) == []


def test_find_json_files_runnable_input_remains_backward_compatible(tmp_path):
    json_file = tmp_path / "sample.json"
    json_file.write_text("{}", encoding="utf-8")

    assert list(_find_json_files(str(tmp_path))) == [str(json_file)]
    assert list(_find_json_files((str(tmp_path), "sample"))) == []
