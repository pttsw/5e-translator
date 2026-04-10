from flask import Response, request, send_file
from flask_restful import Api, Resource
from flask_login import login_required
from pathlib import Path
from html import escape
from urllib.parse import quote
import json
import mimetypes
import re
import subprocess

from .restful_utils import success, error

api = Api()

CHM_ROOT = Path('/data/DND5e_chm').resolve()
HTML_SUFFIXES = {'.htm', '.html'}
TEXT_EXTENSIONS = {'.css', '.js', '.txt', '.json', '.xml'}


def _safe_path(relative_path: str) -> Path:
    cleaned = (relative_path or '').strip().lstrip('/').replace('\\', '/')
    target = (CHM_ROOT / cleaned).resolve()
    if CHM_ROOT not in target.parents and target != CHM_ROOT:
        raise ValueError('非法路径')
    return target


def _decode_text(file_path: Path) -> str:
    raw = file_path.read_bytes()
    for encoding in ('gb18030', 'utf-8', 'gbk'):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='ignore')


def _to_rel_path(file_path: Path) -> str:
    return file_path.relative_to(CHM_ROOT).as_posix()


def _build_asset_url(relative_path: str) -> str:
    return f'/api/v1/chm/raw?path={quote(relative_path)}'


def _build_page_url(relative_path: str, query: str = '') -> str:
    url = f'/api/v1/chm/page?path={quote(relative_path)}'
    if query:
        url = f'{url}&q={quote(query)}'
    return url


def _strip_html(text: str) -> str:
    return re.sub(r'<[^>]+>', ' ', text or '')


def _highlight_text(text: str, query: str) -> str:
    if not text:
        return ''
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    parts = []
    last_index = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        parts.append(escape(text[last_index:start]))
        parts.append(f'<mark>{escape(text[start:end])}</mark>')
        last_index = end
    parts.append(escape(text[last_index:]))
    return ''.join(parts)


def _build_title_result(file_path: Path, query: str):
    rel_path = _to_rel_path(file_path)
    return {
        'path': rel_path,
        'title': file_path.stem,
        'title_highlight': _highlight_text(file_path.stem, query),
        'path_highlight': _highlight_text(rel_path, query)
    }


def _build_content_result(file_path: Path, query: str, snippet: str, line=''):
    rel_path = _to_rel_path(file_path)
    normalized_snippet = re.sub(r'\s+', ' ', _strip_html(snippet)).strip()
    return {
        'path': rel_path,
        'title': file_path.stem,
        'title_highlight': _highlight_text(file_path.stem, query),
        'path_highlight': _highlight_text(rel_path, query),
        'line': line,
        'snippet': normalized_snippet,
        'snippet_highlight': _highlight_text(normalized_snippet, query)
    }


def _rewrite_reference(match: re.Match, current_dir: Path, query: str = '') -> str:
    attribute = match.group(1)
    original = match.group(2).strip()
    if not original or original.startswith(('#', 'javascript:', 'data:', 'mailto:', 'http://', 'https://')):
        return match.group(0)

    clean_target = original.split('#', 1)[0].split('?', 1)[0]
    anchor = ''
    if '#' in original:
        anchor = f"#{original.split('#', 1)[1]}"

    try:
        resolved = _safe_path((current_dir / clean_target).relative_to(CHM_ROOT).as_posix())
    except Exception:
        return match.group(0)

    rel_path = _to_rel_path(resolved)
    if resolved.suffix.lower() in HTML_SUFFIXES:
        rewritten = f'{_build_page_url(rel_path, query)}{anchor}'
    else:
        rewritten = _build_asset_url(rel_path)
    return f'{attribute}="{rewritten}"'


def _rewrite_inline_url(match: re.Match, current_dir: Path) -> str:
    raw_target = match.group(1).strip().strip('"\'')
    if not raw_target or raw_target.startswith(('data:', 'http://', 'https://', '#')):
        return match.group(0)
    try:
        resolved = _safe_path((current_dir / raw_target).relative_to(CHM_ROOT).as_posix())
    except Exception:
        return match.group(0)
    return f'url("{_build_asset_url(_to_rel_path(resolved))}")'


def _build_highlight_script(query: str) -> str:
    safe_query = json.dumps(query)
    return f"""
<style>
  .chm-search-highlight {{
    background: #ffe1a8;
    color: #8a4b00;
    border-radius: 3px;
    padding: 0 2px;
  }}
</style>
<script>
  (function() {{
    var query = {safe_query};
    if (!query) return;
    function walk(root) {{
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {{
        acceptNode: function(node) {{
          if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
          var parent = node.parentElement;
          if (!parent) return NodeFilter.FILTER_REJECT;
          var tag = parent.tagName;
          if (tag === 'SCRIPT' || tag === 'STYLE' || tag === 'NOSCRIPT' || tag === 'MARK') return NodeFilter.FILTER_REJECT;
          return node.nodeValue.toLowerCase().indexOf(query.toLowerCase()) === -1
            ? NodeFilter.FILTER_REJECT
            : NodeFilter.FILTER_ACCEPT;
        }}
      }});
      var nodes = [];
      while (walker.nextNode()) nodes.push(walker.currentNode);
      nodes.forEach(function(node) {{
        var text = node.nodeValue;
        var lower = text.toLowerCase();
        var queryLower = query.toLowerCase();
        var index = 0;
        var frag = document.createDocumentFragment();
        var hasMatch = false;
        while (true) {{
          var matchIndex = lower.indexOf(queryLower, index);
          if (matchIndex === -1) break;
          hasMatch = true;
          if (matchIndex > index) {{
            frag.appendChild(document.createTextNode(text.slice(index, matchIndex)));
          }}
          var mark = document.createElement('mark');
          mark.className = 'chm-search-highlight';
          mark.textContent = text.slice(matchIndex, matchIndex + query.length);
          frag.appendChild(mark);
          index = matchIndex + query.length;
        }}
        if (!hasMatch) return;
        if (index < text.length) {{
          frag.appendChild(document.createTextNode(text.slice(index)));
        }}
        node.parentNode.replaceChild(frag, node);
      }});
      var first = document.querySelector('mark.chm-search-highlight');
      if (first && typeof first.scrollIntoView === 'function') {{
        first.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
      }}
    }}
    if (document.readyState === 'loading') {{
      document.addEventListener('DOMContentLoaded', function() {{ walk(document.body); }}, {{ once: true }});
    }} else {{
      walk(document.body);
    }}
  }})();
</script>
"""


def _rewrite_html(html: str, file_path: Path, query: str = '') -> str:
    current_dir = file_path.parent
    html = re.sub(r'(href|src)=["\']([^"\']+)["\']', lambda m: _rewrite_reference(m, current_dir, query), html, flags=re.IGNORECASE)
    html = re.sub(r'url\(([^)]+)\)', lambda m: _rewrite_inline_url(m, current_dir), html, flags=re.IGNORECASE)
    if '<head' in html.lower():
        html = re.sub(
            r'<head([^>]*)>',
            '<head\\1><meta charset="utf-8"><base target="_self">',
            html,
            count=1,
            flags=re.IGNORECASE
        )
    if query:
        highlight_script = _build_highlight_script(query)
        if '</body>' in html.lower():
            html = re.sub(r'</body>', f'{highlight_script}</body>', html, count=1, flags=re.IGNORECASE)
        else:
            html = f'{html}{highlight_script}'
    return html


def _search_with_rg(query: str):
    command = [
        'rg', '-i', '-n', '--no-heading', '--max-count', '1',
        '--glob', '*.htm', '--glob', '*.html',
        query, str(CHM_ROOT)
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or '搜索失败')
    items = []
    for line in result.stdout.splitlines()[:60]:
        parts = line.split(':', 2)
        if len(parts) < 3:
            continue
        file_path = Path(parts[0])
        items.append(_build_content_result(file_path, query, parts[2], parts[1]))
    return items


def _search_title_matches(query: str):
    query_lower = query.lower()
    items = []
    for file_path in CHM_ROOT.rglob('*'):
        if not file_path.is_file() or file_path.suffix.lower() not in HTML_SUFFIXES:
            continue
        rel_path = _to_rel_path(file_path)
        if query_lower not in rel_path.lower() and query_lower not in file_path.stem.lower():
            continue
        items.append(_build_title_result(file_path, query))
        if len(items) >= 30:
            break
    return items


def _search_fallback(query: str):
    query_lower = query.lower()
    items = []
    for file_path in CHM_ROOT.rglob('*'):
        if not file_path.is_file() or file_path.suffix.lower() not in HTML_SUFFIXES:
            continue
        content = _decode_text(file_path)
        index = content.lower().find(query_lower)
        if index == -1:
            continue
        start = max(0, index - 40)
        end = min(len(content), index + 120)
        items.append(_build_content_result(file_path, query, content[start:end]))
        if len(items) >= 60:
            break
    return items


@api.resource('/chm/tree')
class ChmTreeApi(Resource):
    @login_required
    def get(self):
        try:
            relative_path = request.args.get('path', '')
            target = _safe_path(relative_path)
            if not target.is_dir():
                return error('目录不存在')
            items = []
            for child in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
                if child.name.startswith('.'):
                    continue
                rel_path = _to_rel_path(child)
                items.append({
                    'name': child.name,
                    'path': rel_path,
                    'is_dir': child.is_dir(),
                    'is_page': child.is_file() and child.suffix.lower() in HTML_SUFFIXES
                })
            return success(data={
                'path': _to_rel_path(target) if target != CHM_ROOT else '',
                'items': items
            })
        except Exception as exc:
            return error(str(exc))


@api.resource('/chm/search')
class ChmSearchApi(Resource):
    @login_required
    def get(self):
        query = (request.args.get('q') or '').strip()
        if not query:
            return success(data={'title_matches': [], 'content_matches': []})
        try:
            title_matches = _search_title_matches(query)
            try:
                content_matches = _search_with_rg(query)
            except Exception:
                content_matches = _search_fallback(query)
            title_paths = {item['path'] for item in title_matches}
            deduped_content_matches = []
            seen_paths = set()
            for item in content_matches:
                path = item['path']
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                if path in title_paths:
                    deduped_content_matches.append(item)
                else:
                    deduped_content_matches.append(item)
            return success(data={
                'title_matches': title_matches,
                'content_matches': deduped_content_matches
            })
        except Exception as exc:
            return error(str(exc))


@api.resource('/chm/page')
class ChmPageApi(Resource):
    @login_required
    def get(self):
        relative_path = request.args.get('path', '')
        query = (request.args.get('q') or '').strip()
        try:
            file_path = _safe_path(relative_path)
            if not file_path.is_file() or file_path.suffix.lower() not in HTML_SUFFIXES:
                return error('页面不存在')
            html = _rewrite_html(_decode_text(file_path), file_path, query)
            return Response(html, mimetype='text/html; charset=utf-8')
        except Exception as exc:
            return error(str(exc))


@api.resource('/chm/raw')
class ChmRawApi(Resource):
    @login_required
    def get(self):
        relative_path = request.args.get('path', '')
        try:
            file_path = _safe_path(relative_path)
            if not file_path.is_file():
                return error('资源不存在')
            if file_path.suffix.lower() in HTML_SUFFIXES:
                return error('请通过 page 接口访问页面')
            if file_path.suffix.lower() in TEXT_EXTENSIONS:
                return Response(_decode_text(file_path), mimetype=f'{mimetypes.guess_type(file_path.name)[0] or "text/plain"}; charset=utf-8')
            return send_file(str(file_path), mimetype=mimetypes.guess_type(file_path.name)[0] or 'application/octet-stream')
        except Exception as exc:
            return error(str(exc))
