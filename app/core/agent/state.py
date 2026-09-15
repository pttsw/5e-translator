"""Only JSON-compatible summaries live in graph state."""
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict
from .commands import CommandError, contained


class TranslationAgentState(TypedDict):
    command: str
    source_paths: list[str]
    reference_index_ids: list[str]
    options: dict
    file_results: list[dict]
    current_file: str | None
    stage: str
    progress: dict
    errors: list[dict]
    cancelled: bool


@dataclass(frozen=True)
class AgentOptions:
    source_root: Path
    output_root: Path
    mode: str = '5et'
    thread_num: int = 2
    batch_max_chars: int = 12000
    cache: bool = True
    force: bool = False
    force_title: bool = False
    reference_roots: tuple[Path, ...] = ()

    def __post_init__(self):
        source = Path(self.source_root).expanduser().resolve(strict=True)
        output = Path(self.output_root).expanduser().resolve()
        if not source.is_dir():
            raise CommandError('源目录必须是目录')
        if contained(output, source) or contained(source, output):
            raise CommandError('源目录与输出目录不能重叠')
        if self.mode not in {'5et', 'homebrew', 'ua'}:
            raise CommandError('阶段 A 支持 5et、homebrew 和 ua 模式')
        if self.thread_num < 1 or self.batch_max_chars < 1:
            raise CommandError('线程数与批次预算必须大于零')
        object.__setattr__(self, 'source_root', source)
        object.__setattr__(self, 'output_root', output)
        object.__setattr__(self, 'reference_roots', tuple(Path(root).expanduser().resolve(strict=True)
                                                       for root in self.reference_roots) or (source,))

    @classmethod
    def from_environment(cls):
        import os
        from config import EN_PATH, OUT_PATH
        return cls(Path(os.getenv('TRANSLATION_AGENT_SOURCE_ROOT', EN_PATH)),
                   Path(os.getenv('TRANSLATION_AGENT_OUTPUT_ROOT', OUT_PATH)),
                   mode=os.getenv('TRANSLATION_AGENT_MODE', '5et'),
                   reference_roots=tuple(Path(root) for root in os.getenv('TRANSLATION_AGENT_REFERENCE_ROOTS', '').split(os.pathsep) if root))

    def summary(self):
        return {key: str(value) if isinstance(value, Path) else
                [str(root) for root in value] if key == 'reference_roots' else value for key, value in vars(self).items()}


def initial_state(command: str, options: AgentOptions) -> TranslationAgentState:
    return dict(command=command, source_paths=[], reference_index_ids=[], options=options.summary(),
                file_results=[], current_file=None, stage='parse_command', progress={}, errors=[], cancelled=False)
