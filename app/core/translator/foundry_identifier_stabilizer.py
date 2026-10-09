import re
import unicodedata

from langchain_core.runnables import Runnable


class FoundryIdentifierStabilizer(Runnable):
    """Keep class Foundry ScaleValue identifiers independent of translation."""

    _TARGET_PATH = "class/foundry.json"
    _PLACEHOLDER_RE = re.compile(r"^\{!@\s+([^}]+)\}$")

    @classmethod
    def _is_target_file(cls, json_path: str) -> bool:
        normalized = (json_path or "").replace("\\", "/").rstrip("/")
        return normalized == cls._TARGET_PATH or normalized.endswith(f"/{cls._TARGET_PATH}")

    @staticmethod
    def _slugify_english_title(title: str) -> str:
        value = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
        value = re.sub(r"['\u2018\u2019]", "", value.lower())
        return re.sub(r"[^a-z0-9]+", "-", value).strip("-")

    @classmethod
    def _resolve_english_title(cls, title, english_by_uid):
        if not isinstance(title, str):
            return None
        match = cls._PLACEHOLDER_RE.fullmatch(title)
        if not match:
            return title
        resolved = english_by_uid.get(match.group(1))
        return resolved if isinstance(resolved, str) else None

    @classmethod
    def _stabilize_node(cls, source, translated, english_by_uid) -> None:
        if isinstance(source, dict) and isinstance(translated, dict):
            if source.get("type") == "ScaleValue":
                source_config = source.get("configuration")
                translated_config = translated.get("configuration")
                if isinstance(source_config, dict) and isinstance(translated_config, dict):
                    identifier = source_config.get("identifier")
                    if not identifier:
                        english_title = cls._resolve_english_title(
                            source.get("title"), english_by_uid)
                        if english_title:
                            identifier = cls._slugify_english_title(english_title)
                    if identifier:
                        translated_config["identifier"] = identifier

            for key, source_value in source.items():
                if key in translated:
                    cls._stabilize_node(source_value, translated[key], english_by_uid)
            return

        if isinstance(source, list) and isinstance(translated, list):
            for source_value, translated_value in zip(source, translated):
                cls._stabilize_node(source_value, translated_value, english_by_uid)

    def invoke(self, input, config=None, **kwargs):
        for file_info in input:
            if self._is_target_file(file_info.json_path) and file_info.cn_obj is not None:
                english_by_uid = {
                    job.uid: job.en_str
                    for job in file_info.job_list
                    if isinstance(job.en_str, str)
                }
                self._stabilize_node(
                    file_info.json_obj, file_info.cn_obj, english_by_uid)
            yield file_info
