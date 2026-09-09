import os
import re
from typing import Any
from urllib.parse import quote, urljoin

import httpx


DEFAULT_API_URL = "https://5echmsearch.kagangtuya.top"
DEFAULT_SITE_URL = "https://5echm.kagangtuya.top/"
MAX_PAGE_SIZE = 20
MAX_PREVIEW_CHARS = 600


class BuquanshuSearchError(RuntimeError):
    """Raised when the remote Buquanshu search service cannot be used."""


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).lower() in ("1", "true", "yes", "on")


class BuquanshuSearcher:
    """Client for the public search API used by the 5E 不全书 website."""

    def __init__(
        self,
        api_url: str | None = None,
        site_url: str | None = None,
        http_client: httpx.Client | None = None,
    ):
        self.api_url = (
            api_url or os.getenv("BUQUANSHU_SEARCH_API_URL") or DEFAULT_API_URL
        ).rstrip("/")
        self.site_url = (
            site_url or os.getenv("BUQUANSHU_SITE_URL") or DEFAULT_SITE_URL
        ).rstrip("/") + "/"
        self._owns_client = http_client is None
        self.http_client = http_client or httpx.Client(
            timeout=httpx.Timeout(
                float(os.getenv("BUQUANSHU_SEARCH_TIMEOUT", "20")),
                connect=float(os.getenv("BUQUANSHU_CONNECT_TIMEOUT", "10")),
            ),
            trust_env=_env_flag("BUQUANSHU_TRUST_ENV"),
            follow_redirects=True,
        )

    def close(self) -> None:
        if self._owns_client:
            self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def search(
        self,
        keyword: str,
        title_only: bool = False,
        category: str = "all",
        page: int = 1,
        page_size: int = 10,
    ) -> dict[str, Any]:
        keyword = str(keyword or "").strip()
        if not keyword:
            raise ValueError("keyword must not be empty")
        if len(keyword) > 200:
            raise ValueError("keyword must not exceed 200 characters")

        page = int(page)
        page_size = int(page_size)
        if page < 1:
            raise ValueError("page must be at least 1")
        if not 1 <= page_size <= MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

        params = {
            "keyword": keyword,
            "titleOnly": str(bool(title_only)).lower(),
            "category": str(category or "all").strip() or "all",
            "page": page,
            "pageSize": page_size,
        }
        try:
            response = self.http_client.get(f"{self.api_url}/api/search", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BuquanshuSearchError(f"不全书搜索服务请求失败: {exc}") from exc

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            raise BuquanshuSearchError("不全书搜索服务返回了无效数据")

        results = [
            self._normalize_result(item)
            for item in payload["results"]
            if isinstance(item, dict)
        ]
        return {
            "keyword": keyword,
            "total": int(payload.get("total") or 0),
            "page": int(payload.get("page") or page),
            "total_pages": int(payload.get("totalPages") or 0),
            "page_size": int(payload.get("pageSize") or page_size),
            "results": results,
        }

    def _normalize_result(self, item: dict[str, Any]) -> dict[str, Any]:
        path = str(item.get("path") or "").replace("\\", "/").lstrip("/")
        source_path = str(item.get("sourcePath") or path).replace("\\", "/")
        if source_path.lower().startswith("topics/"):
            source_path = source_path[7:]

        title = str(item.get("title") or "").strip()
        if not title:
            title = source_path.rsplit("/", 1)[-1]
            title = re.sub(r"\.html?$|\.htm$", "", title, flags=re.IGNORECASE)

        preview = item.get("preview") or item.get("snippet") or item.get("content") or ""
        preview = re.sub(r"\s+", " ", str(preview)).strip()
        if len(preview) > MAX_PREVIEW_CHARS:
            preview = preview[:MAX_PREVIEW_CHARS].rstrip() + "…"

        return {
            "index": item.get("index"),
            "title": title or "未命名页面",
            "category": str(item.get("category") or ""),
            "source_path": source_path,
            "rank": item.get("rank"),
            "preview": preview,
            "url": urljoin(self.site_url, quote(path, safe="/%:@?&=+#")) if path else "",
        }
