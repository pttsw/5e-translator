# 翻译 Agent：阶段 A / B / C / D

`python main.py agent` 的交互输入和 `DNDTranslatorAgent.chat()` 都会在调用聊天模型前解析斜杠命令。
`/file <文件或目录>` 执行同步翻译；带空格路径可加引号，也可直接输入。
相对路径以允许的源目录为基准。`/clear` 只清空对话，保留系统提示和文件运行记录。
`/ref <文件或目录>` 加载会话参考，`/refs` 列出来源及统计，`/clear-refs` 清空参考与查询缓存。
`/status` 可在文件任务运行时读取快照，`/cancel` 发出协作式取消，`/retry` 只处理上次运行未完成的文件。

配置默认沿用 `EN_PATH` 和 `OUT_PATH`，可用以下环境变量覆盖：

- `TRANSLATION_AGENT_SOURCE_ROOT`：允许读取的源目录。
- `TRANSLATION_AGENT_OUTPUT_ROOT`：输出目录，不能与源目录重叠。
- `TRANSLATION_AGENT_MODE`：`5et`（默认）、`homebrew` 或 `ua`。切换模式时同时指定对应源目录。
- `TRANSLATION_AGENT_REFERENCE_ROOTS`：参考资料允许目录，以系统路径分隔符分隔（Linux 为 `:`）；默认只允许源目录。

程序化调用无需初始化聊天模型：

```python
from pathlib import Path
from app.core.agent.graph import build_graph
from app.core.agent.state import AgentOptions

graph = build_graph(AgentOptions(
    Path('/path/to/english'), Path('/path/to/output'),
    regression_root=Path('/path/to/5etools'),
))
result = graph.invoke('/file "spells/spells-phb.json"')
```

## 执行及保存规则

`graph.py` 使用普通 Python 有限状态机，不导入 LangGraph，不依赖 LLM 路由。
每个文件创建独立的 analyser、setter、processor 和 generator；可序列化状态只存储摘要。
源路径在展开和分析时检查真实路径；目录不跟随符号链接，越界的 JSON 文件链接会被拒绝。
目录按源目录下的相对路径排序；一个文件失败不阻断后续文件。同一个图实例拒绝并发任务。

固定流水线：

```text
parse → validate_paths → discover
  → JsonAnalyser → JobNeedTranslateSetter → detect_conflicts
  → BatchJobProcessor(update=False) → JsonGenerator(generator_update_db=False)
  → validate_file → isolated_regression → persist_completed_job → publish
```

`/file` 在冲突检查通过后进入 processor，不调用 `TermSetter`、`KnowledgeSetter` 或 `ByHandHandler`。
processor 自身的批次上下文、已有译文和临时术语缓存继续沿用现有行为。

基础校验检查 JSON、全部 Job 终结、UID 唯一且回填正确、无内部占位符，以及 analyser 模板中的非翻译字段和结构不变。
快速检查另外验证 TAG、花括号、数字和骰子；有限拆分、单条降级沿用原 processor。快速检查或隔离回归失败都不会保存译文或覆盖正式输出。
自动译文保持未校对；已校对条目不在新增译文保存集合中。公共保存器保留 `job_context`，包含 UID、路径和上下文信息。
候选文件、术语缓存及失败诊断保存在输出根下的独立 `.agent-*` 目录，正式产物为 JSON、`.en` 和 `.jobs`。
保存器复用现有逐条写库接口，不提供整文件数据库事务；写库中途失败时结果包含已保存数量，正式 JSON 不发布。
写文件沿用逐文件原子替换，不承诺数据库与三个输出文件的跨资源原子事务。

## 各场景提示词

`prompts.py` 提供聊天系统提示及文件任务约束；文件约束通过 processor 的 `prompt_suffix` 配置传入，跟随批量重试和单条降级。

| 场景 | 提示词及协议 |
| --- | --- |
| 普通聊天、术语解释 | `CHAT_SYSTEM`，禁止虚构执行和测试结果 |
| 首次批量翻译、拆分重试 | 现有 `PROMOT_BATCH` + `TRANSLATION_CONTRACT`，严格返回本次 UID 集合及批次标识 |
| 单条降级、无参考资料 | `Job.to_llm_question()` 的单条协议 + 文件约束 |
| processor 自身的已有译文、临时术语 | 沿用原有上下文和提示协议；不通过 setter 自动注入外部参考 |
| TAG 或格式修复 | 现有 `PROMOT_CORRECT_TAG` + 文件约束，区分 `last_answer` 和既有中文 |
| 解析、路径、复用、生成、校验、保存、汇总 | 纯代码执行，无 LLM 提示词 |

阶段 A 沿用原有批次上下文和 `BatchChunker` 协议，确保与 `main.py translate --batch` 的 Job 覆盖及输出兼容。
完整实体双视图的 `EntityContextBuilder`/`EntityTranslationTask` 仍为后续翻译处理器改造项，本阶段未实现。
参考资料已在阶段 B 接入；阶段 C 增加 analyser 复用冲突判定和隔离 5etools 发布门禁，文件摘要返回实际冲突数量与候选详情。恢复命令属于阶段 D。
processor Token 统计独立记录在每个文件的 `token_usage` 及总状态中，不使用聊天模型的计数代表文件翻译消耗。字段为输入、输出、缓存命中、缓存未命中/写入及总量；供应商未返回的类别显示“未提供”。

## 阶段 D：状态、取消与恢复

`/file` 和 `/retry` 会把运行快照原子写入输出根目录的 `.agent-state/latest.json`，并在每个文件的 `.agent-*` 候选目录保存 `checkpoint.json`、`.jobs` 和失败摘要。每个已完成批次/单项后刷新 `.jobs`，因此进程在后续阶段中断时不必重复请求已完成 UID。

`/retry` 先对源文计算 SHA-256，然后重新运行 analyser，只合并 UID、英文和 TAG 均一致且通过快速校验的检查点译文。源文变更时返回 `recovery_source_changed`，应重新执行 `/file <path>`。在 analyser 之前失败或因取消而未开始的文件会从源文重试。

`/cancel` 不终止已发出的模型请求；它在翻译单元/固定阶段边界停止新工作，完成当前检查点后返回 `partial` 或 `failed`。目录中尚未开始的文件标记为 `skipped/cancelled`，可由 `/retry` 继续。`directory_summary` 汇总文件、Job、复用、恢复、新译、冲突和未完成数量。

## 阶段 B：会话资料与两个聚合工具

示例配置与命令：

```bash
TRANSLATION_AGENT_REFERENCE_ROOTS=/data/5e-translator/app/core/crawler python main.py agent
```

```text
/ref forum_posts2129.txt
/refs
/file spells/spells-phb.json
/clear-refs
```

当前文件 loader 支持 UTF-8/BOM 的 TXT、Markdown；其他后缀明确列为不支持，不会假装完成 PDF/OCR。
`ReferenceContentProcessor` 同时处理会话文本和不全书 HTML 的结构化正文，保留偏移、行号或 DOM 位置。
规则支持双语标题、明确译名表和法术/物品/怪物/背景/专长数据卡特征；目录译名可进入译文索引，但不会被当成完整实体说明。
嵌套标题的内容保留在父实体中；正文中的法术引用不作为新的实体边界。缺失英文名或中文名时不补造名称。
相同译文合并证据，不同译法保留。文件内容更新后替换本会话的旧版本；加载失败保留上一成功版本。
原文保存在 `ReferenceStore`，图状态仅保存 `reference_index_ids`、来源与统计，不包含全文。
支持注入一次结构化提取 fallback；默认使用确定性规则。fallback 的名称、正文及偏移必须逐字验证，无法确认完整边界时仍标记 `complete=false`。

Agent 默认只向模型暴露以下两个工具，原始 `search_terms`、`search_buquanshu` 仅保留为内部兼容函数：

| 工具 | 来源优先级 | 返回内容 |
| --- | --- | --- |
| `lookup_translations` | term 表 → 会话译名 → 不全书译名 → 本次运行临时术语 | 英中候选、类别、证据、冲突、来源等级和工程置信度 |
| `lookup_entities` | 会话实体 → 不全书完整实体 | 中英名称、类型、完整正文及来源证据 |

英文短语按整体查询，允许规范化大小写；不会把 `saving throw` 自动拆成两个单词。
置信度使用计划中的不重叠来源区间，表示排序分数而不是正确率；类别为空或脏数据仍如实返回，不宣称 term 命中是唯一确认译法。
不全书先搜索，再按返回 URL 抓取同站 HTTPS 全文，校验重定向与公网地址，拒绝越界 URL、非 HTML 和过大响应。
搜索预览不进入完整实体结果。页面处理结果按 URL/内容摘要保存在当前会话，重复查询复用缓存；失败记录不写入任何全局库。

`ReferenceToolAdapter` 复用 processor 的模型客户端，仅在模型调用 Tool 后添加参考结果。每次批次或单条请求独立维护对话和调用缓存；批量失败后的单条降级也使用同一组工具。
没有 Tool 调用时，`/ref` 原文、term 查询结果、不全书内容不会预先进入请求，也不会修改 Job 或译文骨架。
`TemporaryTermStore` 只收集本次 `/file` 的 processor 临时术语，文件间可复用，结束后清空，不写入 `term` 表。
当前仍沿用阶段 A 的批次载荷；完整实体双视图载荷改造尚未实现。

默认预算：每次 Tool 最多 10 个查询、每项最多 10 条结果、返回不超过 24000 字符；实体超过预算时整条省略并标记 `truncated`，不裁剪正文冒充完整。
每次聚合查询有 30 秒调度预算，单次网络请求最多 10 秒、最多抓取 3 个页面。每个模型请求最多 4 轮 Tool、每轮最多 4 次调用，并检查 32768 Token 总预算（文本按 UTF-8 字节数保守估计）。
会话存储最多 100 份文档/32 MiB，单个本地文本最多 4 MiB、远端页面最多 2 MiB。
运行摘要的 `tool_calls` 记录查询、返回来源 ID、耗时与错误码，不保存凭据或完整异常信息。

## 真实来源验证

只读探测脚本，不调用 LLM，不写数据库：

```bash
.venv/bin/python scripts/inspect_agent_references.py --output /tmp/agent-reference-probe.json
```

2026-09-15 使用 `forum_posts2129.txt` 实测识别 138 个实体、去重后 372 个译名对；其中有些目录项/边界不明确记录不标为完整。
会话查询 `Battle Familiar` 返回法术正文 878 字符，`Conjurer's Canopy` 返回物品正文 232 字符；原文证据位置随结果返回。
`Fireball` 的不全书全文来自《玩家手册2024》“三环”页，提取出 236 字符完整说明，包含 8d6 火焰伤害及升环规则。

实际 term 行示例：`Fireball → 火球术 / spell`、`Magic Missile → 魔法飞弹 / spell`、`Arcana Domain → 奥秘领域 / 空类别 / words-sync`。
大小写归一化查询还会发现 `fireball` 的空类别和 `bestiary` 记录，因此返回保留类别及来源，避免掩盖数据质量差异。
同一次 Fireball 聚合查询中，term 候选置信度为 1.0、会话候选为 0.94、不全书候选为 0.84；这是配置的来源排序分数。

## 回归测试

```bash
.venv/bin/python -m pytest -q app/core/agent
.venv/bin/python -m pytest -q app/core/translator/tests/batch_processing_test.py app/core/translator/tests/job_processor_dedupe_test.py app/core/translator/tests/json_generator_test.py
TRANSLATOR_DB_BACKEND=memory TRANSLATOR_DISABLE_REDIS=1 .venv/bin/python -m pytest -q app/core/translator/tests/base_analyser_uid_test.py app/core/translator/tests/job_need_translate_setter_test.py
```

Agent 集成测试使用 `MemoryDBDictionary` 和 mock LLM，不连接 MySQL、Redis、Chroma 或网络 LLM。
旧 translator 测试会修改 `sys.modules`，因此与 Agent 测试分进程执行。
`job_procecssor_test.py` 的 11 个过时断言失败在替换回 HEAD 版本 processor 后仍可复现，不属于本次新增回归。

真实英文 `spells-aag.json`（9 个 Job）已在 `/tmp` 独立目录通过完整流程。
原生 5etools schema 校验会拒绝既有 analyser 自动添加的 `ENG_name`；A 为保持旧输出兼容而保留该字段。
因此 A 的基础校验不等同于原生 schema 测试通过。阶段 C 的发布策略与配置见下节。


## 阶段 C：冲突、验证与发布

`/file` 现在依次执行分析、冲突检查、翻译、生成、快速检查、隔离回归、保存与发布。

- 分析词典通过只读候选 API 查询所有 `words` 候选，绕过单值 Redis 缓存；不会在读取时补写 `word_usage`。
- 同文件 UID 优先，其次同文件 `key_path`；位置候选必须译文一致才能复用。没有位置证据时，仅同英文、同类别、中文一致的候选可复用。大小写允许归一化。
- 多译文冲突返回 `translation_conflict`、候选数据库 ID、类别、校对状态与 Job UID。当前选择阻断该文件，保留候选供处理；不让模型自动裁决或覆盖原记录。目录中的其他文件继续运行。
- 快速检查覆盖完整 JSON、未终结 Job、UID 回填、TAG 类型/数量、花括号、数字、骰子及模板非翻译字段。数字必须逐项保留阿拉伯数字，转换成中文数字会被拒绝。
- 发布前移除 analyser 自动增加的 `ENG_name`（源文件本来存在的字段保留）。这是相对阶段 A 的输出变化，使原生英文输入可以接受原生 schema 检查；其他中文扩展字段仍需匹配项目自己的 schema。

### 必需的回归配置

```bash
export TRANSLATION_AGENT_REGRESSION_ROOT=/path/to/5etools
# 可选：JSON 参数数组，无 shell 展开；占位符是隔离副本中的候选文件与项目目录。
export TRANSLATION_AGENT_REGRESSION_COMMAND='["npm", "run", "test:data"]'
export TRANSLATION_AGENT_REGRESSION_DATA_DIR=data
export TRANSLATION_AGENT_REGRESSION_TIMEOUT=300
```

Python 调用方可使用 `AgentOptions(regression_root=..., regression_command=(...), ...)`。
未指定命令时从该项目 `package.json` 依次选择 `test:data`、`test:json`、`test`；未配置项目时返回 `regression_not_configured`，保留候选，不写库或发布。
运行环境必须满足目标项目的 Node 和已安装依赖要求。命令须实际检查候选文件，不能配置为无条件成功。

每个候选在输出目录的 `.agent-*` 下保留完整项目副本（忽略 `.git`、`.env*` 与 Agent 临时目录），复制而非链接文件和依赖。原项目不会收到候选写入或测试写入。
默认候选写入 `<副本>/data/<源文件相对路径>`；homebrew/UA 使用相应测试项目、目录和命令。复制整个项目有磁盘与时间成本，诊断目录需按使用情况清理。
子进程不继承应用凭据环境；超时会终止进程组。结果记录命令、退出码、工作目录、候选路径以及各最多 8192 字节的 stdout/stderr 摘要与截断标记，完整日志保留在候选目录。
测试修改候选、退出非零、启动失败或超时均禁止发布。

只有快速检查与回归都通过后才调用公共保存器，并以未校对状态保存新译文。
正式 JSON 发布使用测试过的字节；`.en`、`.jobs`、JSON 的替换失败会回滚已替换文件，JSON 最后替换。数据库和文件系统尚无跨资源事务：保存中途失败可能留下部分已通过验证的数据库记录，但不会发布候选；阶段 D 的检查点恢复会跳过已完成的 LLM 请求，但不提供数据库与文件系统的跨资源回滚。
失败摘要 `failure.json` 包含文件、阶段、错误码、未完成 UID、Job 路径及 batch ID。处理器的失败批次/Job 文件继续留在候选目录。当前 `entry_path`/`context_key` 用于定位，完整实体双视图任务改造仍未包含在阶段 C 中。

### 真实 schema 探测

```bash
.venv/bin/python scripts/inspect_agent_regression.py \
  --project /path/to/5etools \
  --source /path/to/english/spells/spells-aag.json \
  --node /path/to/node24 \
  --output /tmp/agent-phase-c-real.json
```

该探测使用内存数据库和 mock LLM 跑真实英文文件，通过隔离项目中的 `5etools-utils.JsonTester` 及原生法术 schema 检查候选；不修改原项目、不访问线上数据库或 LLM。这是单文件 schema 集成验证，不代表整站的图片、链接等全量测试通过。

2026-09-15 验证：使用本地 5etools 2.34.1、Node 24.18.1、`data-bak/spells/spells-aag.json`，9 个 Job 经 mock 翻译、快速检查和隔离原生 schema 检查成功发布；发布文件与测试候选逐字节一致。全量站点测试未作为这次单文件探测的结论。
