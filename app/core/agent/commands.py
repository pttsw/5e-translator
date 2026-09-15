"""Commands and filesystem policy; no model participates in routing."""
from dataclasses import dataclass
from pathlib import Path
import shlex


class CommandError(ValueError):
    pass


@dataclass(frozen=True)
class Command:
    name: str
    path: str | None = None


def parse_command(text: str) -> Command:
    try:
        parts = shlex.split(text.strip())
    except ValueError as exc:
        raise CommandError('路径引号未闭合') from exc
    if not parts or parts[0] not in {'/file', '/ref', '/clear', '/refs', '/clear-refs', '/status', '/retry', '/cancel'}:
        raise CommandError('未知命令；阶段 A 支持 /file <JSON文件或目录> 和 /clear')
    name = parts[0]
    if name in {'/file', '/ref'}:
        if len(parts) < 2:
            raise CommandError(f'{name} 缺少路径')
        # One path, no flags: unquoted spaces are unambiguous.
        return Command(name, ' '.join(parts[1:]))
    if len(parts) != 1:
        raise CommandError(f'{name} 不接受参数')
    return Command(name)


def contained(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def validate_path(value: str, source_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = source_root / path
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise CommandError('路径不存在或无法解析') from exc
    if not contained(path, source_root):
        raise CommandError('路径超出允许的源目录')
    if not path.is_file() and not path.is_dir():
        raise CommandError('只接受普通文件或目录')
    if path.is_file() and path.suffix != '.json':
        raise CommandError('/file 只接受 .json 文件')
    return path


def discover_files(path: Path, source_root: Path) -> list[Path]:
    from app.core.utils.loader import find_files
    files = {validate_path(item, source_root) for item in find_files(str(path), file_extension='.json')}
    if not files:
        raise CommandError('目录中没有可处理的 .json 文件')
    return sorted(files, key=lambda item: item.relative_to(source_root).as_posix())


def output_path(root: Path, relative: str, source_root: Path) -> Path:
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise CommandError('非法输出相对路径')
    target = (root / rel).resolve()
    if not contained(target, root) or contained(target, source_root):
        raise CommandError('输出路径越界或可能覆盖源文件')
    return target
