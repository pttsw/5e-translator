import json
import os

from app.core.utils import TranslatorStatus


class MockAdapter:
    def __init__(self, seed_path: str = ""):
        self.seed_path = seed_path or os.getenv("TRANSLATOR_MOCK_LLM_SEED", "").strip()
        self.translation_by_text = {}
        self.translation_by_uid = {}
        if self.seed_path:
            self._load_seed(self.seed_path)

    def _load_seed(self, seed_path: str):
        if not os.path.exists(seed_path):
            return
        with open(seed_path, "r") as fh:
            payload = json.load(fh)
        self.translation_by_text = payload.get("translations_by_text", {}) or {}
        self.translation_by_uid = payload.get("translations_by_uid", {}) or {}

    def sendText(self, text, promot: str = "", structured_output: bool = True):
        try:
            payload = json.loads(text)
        except Exception:
            payload = None

        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            return self._send_batch(payload)
        return self._send_single(payload, text)

    def _translate(self, en_str: str, uid: str = "") -> str:
        if uid and uid in self.translation_by_uid:
            return self.translation_by_uid[uid]
        if en_str in self.translation_by_text:
            return self.translation_by_text[en_str]
        return en_str

    def _send_batch(self, payload: dict):
        items = []
        for item in payload.get("items", []):
            uid = item.get("uid", "")
            en_str = item.get("en_str", "")
            items.append({
                "uid": uid,
                "trans_str": self._translate(en_str, uid=uid),
            })
        response = {
            "batch_id": payload.get("batch_id", ""),
            "source_hash": payload.get("source_hash", ""),
            "items": items,
            "add_terms": {},
        }
        return json.dumps(response, ensure_ascii=False), TranslatorStatus.SUCCESS

    def _send_single(self, payload, raw_text: str):
        if isinstance(payload, dict):
            source_text = payload.get("trans_str") or payload.get("en_str") or ""
        else:
            source_text = raw_text
        response = {
            "trans_str": self._translate(source_text),
            "add_terms": {},
        }
        return json.dumps(response, ensure_ascii=False), TranslatorStatus.SUCCESS
