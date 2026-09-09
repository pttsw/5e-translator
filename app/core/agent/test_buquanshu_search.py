import httpx
import pytest

from .buquanshu_search import BuquanshuSearchError, BuquanshuSearcher


def test_search_calls_remote_api_and_normalizes_results():
    def handler(request):
        assert request.url.params["keyword"] == "火球术"
        assert request.url.params["titleOnly"] == "true"
        assert request.url.params["category"] == "玩家手册"
        assert request.url.params["page"] == "2"
        assert request.url.params["pageSize"] == "3"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "index": 42,
                        "title": "火球术",
                        "path": "topics/玩家手册/法术/火球术.htm",
                        "sourcePath": "玩家手册/法术/火球术.htm",
                        "category": "玩家手册",
                        "rank": 99,
                        "preview": "造成\n  8d6 火焰伤害",
                        "content": "不应返回的完整正文",
                    }
                ],
                "total": 7,
                "page": 2,
                "totalPages": 3,
                "pageSize": 3,
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    searcher = BuquanshuSearcher(http_client=client)

    result = searcher.search(
        "火球术", title_only=True, category="玩家手册", page=2, page_size=3
    )

    assert result == {
        "keyword": "火球术",
        "total": 7,
        "page": 2,
        "total_pages": 3,
        "page_size": 3,
        "results": [
            {
                "index": 42,
                "title": "火球术",
                "category": "玩家手册",
                "source_path": "玩家手册/法术/火球术.htm",
                "rank": 99,
                "preview": "造成 8d6 火焰伤害",
                "url": "https://5echm.kagangtuya.top/topics/"
                "%E7%8E%A9%E5%AE%B6%E6%89%8B%E5%86%8C/%E6%B3%95%E6%9C%AF/"
                "%E7%81%AB%E7%90%83%E6%9C%AF.htm",
            }
        ],
    }


@pytest.mark.parametrize("page_size", [0, 21])
def test_search_rejects_invalid_page_size(page_size):
    searcher = BuquanshuSearcher(http_client=httpx.Client())

    with pytest.raises(ValueError, match="page_size"):
        searcher.search("火球术", page_size=page_size)


def test_search_wraps_remote_errors():
    def handler(request):
        return httpx.Response(503, request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    searcher = BuquanshuSearcher(http_client=client)

    with pytest.raises(BuquanshuSearchError, match="搜索服务请求失败"):
        searcher.search("火球术")
