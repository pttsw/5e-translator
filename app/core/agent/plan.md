# D&D 翻译 Agent 实施计划

## 1. 目标与边界

在现有翻译流水线外增加一层交互式编排，使用户可以先提供参考资料，再对一个 JSON 文件或目录发起翻译：

```text
/ref <file-or-directory>
/file <json-file-or-directory>
```

第一阶段只支持 5etools 风格 JSON。PDF、TXT、Markdown 等输入格式只预留统一的解析接口，不在第一阶段实现；它们可以作为参考资料，但必须先转换为可检索的文本片段。

Agent 只负责任务理解、参数收集、调用现有流水线、汇报进度和展示失败原因。文件发现、JSON 拆分、译文复用、上下文分组、翻译、TAG 校验、JSON 回填和数据库保存应由确定性的代码完成，不能交给大模型自由决定。

明确不依赖或复用 `app/core/translation_workflow`。本计划只使用 `app/core/agent`、`app/core/translator`、`app/core/database` 和 `app/core/utils` 中的能力。

## 2. 当前已有能力与复用方案

| 需求 | 可复用实现 | 复用方式 | 仍需补充 |
| --- | --- | --- | --- |
| 文件/目录中的 JSON 枚举 | `app/core/utils/loader.py::find_files`、`find_json_files` | 直接复用 | 路径白名单、空目录与非法后缀提示 |
| JSON 解析和最小翻译单元提取 | `JsonAnalyser`、`BaseAnalyser` 及各类型专用 Analyser | 直接复用 `FileWorkInfo` 和 `Job`，不再定义第二套翻译单元 | 给 Agent 暴露稳定的适配入口 |
| 文件位置和上下文身份 | `Job.uid`、`key_path`、`entry_path`、`context_key`、`context_label`、`current_names` | 作为任务分组、回填和日志标识 | 明确字段缺失时的降级规则 |
| 旧译文匹配 | `DBDictionary.get_bunch` 及 analyser 内的文件、UID、路径和 TAG 匹配 | 沿用 analyser 的匹配结果 | 多候选译文冲突不能静默取第一条，需增加冲突判定 |
| 判断是否需要翻译 | `JobNeedTranslateSetter` | 直接复用 `job.need_translate` | Agent 展示待翻译、复用、冲突数量 |
| 参考资料查询 | `term` 表、`BuquanshuSearcher`、processor 的临时术语文件 | 作为内部数据源，不直接暴露原始搜索接口 | 对会话资料和不全书内容统一做实体拆分与中英匹配，只向 LLM 暴露两个带置信度的聚合 Tool |
| 实体级上下文组装 | analyser 生成的占位 JSON，以及 `Job.uid`、`entry_path`、`current_names` | 新建 `EntityContextBuilder`，不直接复用 `BatchChunker.build_units` | 同时生成完整英文实体和完整译文骨架，避免携带整个文件或隐藏已有译文 |
| 实体任务翻译 | `BatchJobProcessor` 的结构化响应解析和单项校验 | 改造成处理实体任务 | 替换现有 `BatchChunker` 载荷，直接提供双视图实体上下文，并绑定参考查询 Tools |
| 单条降级和失败重试 | `JobProcessor`、`LLMFactory`、`BatchChunker.split_retry_unit` 的思路 | 沿用有限重试；只借鉴失败拆分算法 | 实体任务失败时按待译 UID 拆分，但每个子任务仍携带同一完整实体上下文 |
| TAG 和占位符检查 | `JobProcessor`、`BatchJobProcessor` 的响应校验逻辑 | 直接复用 | 抽出公共校验函数，避免 Agent 再实现一遍 |
| JSON 回填 | `JsonGenerator` | 直接复用 | 增加回填后的结构级检查 |
| 缓存及输出文件 | `write_translate_cache`、`write_file_work_infos` | 直接复用其原子写入行为 | Agent 返回明确的输出路径和缓存路径 |
| 数据库保存 | `DatabaseAdapter`、`DBDictionary`，以及 processor 的 `_store_completed_job` | 抽出公共保存器复用现有写库规则 | processor 先以 `update=False` 运行，通过整文件校验后再统一保存 |
| LLM 工具调用 | `DNDTranslatorAgent.chat`、`ToolRegistry` | 复用对话、流式输出和工具协议 | 翻译任务不能依赖无限对话历史，应使用显式运行状态 |
| 术语/实体查询工具 | `ToolRegistry`、现有 `search_terms` 和 `search_buquanshu` 的连接逻辑 | 合并重构为 `lookup_translations`、`lookup_entities` 两个 Tool | 统一来源优先级、置信度、溯源、缓存、结果预算和错误格式 |
| Agent 图 | `app/core/agent/graph.py` | 仅参考文件位置 | 当前是固定返回 `tool` 的示例，需重写，不能视为已有实现 |

## 3. 命令与会话语义

### 3.1 `/ref <path>`

1. 接受单个文件或目录；目录递归扫描支持的参考文件。
2. 路径必须存在，解析后的真实路径必须位于配置允许的根目录内，不跟随越界符号链接。
3. 同一文件以“真实路径 + 内容摘要”去重；内容变化后视为新版本。
4. 将资料先解析成带来源位置的结构化文本，再执行实体拆分和中英文译文匹配；最终产出实体列表与中英文对照译文列表，而不是只保存无语义的固定长度 chunk。
5. 资料只在当前 Agent 会话/本次运行中生效，默认不写入全局术语库或知识库。
6. 返回成功、跳过、失败文件数，识别出的实体数、译文对数，以及需要 OCR、无法确定实体边界或不支持的文件列表。

建议的统一接口：

```python
class ReferenceLoader(Protocol):
    def supports(self, path: Path) -> bool: ...
    def load(self, path: Path) -> list[ReferenceBlock]: ...
```

首期可先实现纯文本类资料；PDF 等格式通过新的 loader 接入，不影响翻译图。

### 3.2 `/file <path>`

1. 接受一个 `.json` 文件或目录。
2. 目录通过 `find_files(..., file_extension=".json")` 展开，并按规范化相对路径排序，保证重复运行顺序稳定。
3. 启动前输出文件数、预计翻译单元数、已命中数、冲突数和待翻译数。
4. 一个文件失败不应中断整个目录；最终汇总每个文件的 `success`、`partial`、`failed` 或 `skipped` 状态。
5. 同一会话同时只运行一个 `/file` 任务；再次提交时应明确拒绝或排队，不能覆盖正在运行的状态。

### 3.3 辅助命令

建议补充：

- `/refs`：列出本次会话已加载的参考资料。
- `/clear-refs`：清空参考资料，不清空对话。
- `/status`：显示当前文件、实体任务、成功/失败数量和 Token 用量。
- `/retry`：只重试上次运行中可重试的失败单元。
- `/cancel`：在实体任务边界安全停止，不写入未完成结果。
- `/clear`：保留现有语义，清空对话历史；是否同时清空参考资料需在提示中明确，建议默认不清空。

## 4. 编排状态与执行图

不要把完整 `FileWorkInfo`、数据库连接或 LLM Client 放入可序列化状态。状态只保存路径、配置、轻量摘要、阶段结果和错误；运行时对象由节点内部创建并通过受控的运行上下文持有。

建议状态：

```python
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
```

执行图采用固定节点和条件边：

```text
parse_command
  ├─ /ref  → validate_paths → load_references → summarize
  └─ /file → validate_paths → discover_files
                              → analyse_and_reuse
                              → detect_conflicts
                              → prepare_reference_tools
                              → build_entity_tasks
                              → translate
                              → generate
                              → validate
                              → persist_and_write
                              → summarize
```

路由条件必须来自节点返回的结构化状态，例如 `has_conflict`、`has_pending_jobs`、`validation_passed` 和 `cancelled`，不能让 LLM 输出任意节点名。`graph.py` 应只组装节点；具体实现分别放在 `commands.py`、`references.py`、`nodes.py` 和 `state.py`，避免继续堆入 `agent.py`。

当前 `graph.py` 已导入 `langgraph`，但 `requirements.txt` 没有把它声明为直接依赖。阶段 A 应固定一个与现有 LangChain 版本兼容的 `langgraph` 版本并加入依赖；如果不希望增加依赖，则用普通 Python 状态机实现上述同一套固定节点，不保留隐式的传递依赖。

## 5. 各模块详细设计

### 模块一：分析、复用与任务拆分

1. 使用 `JsonAnalyser` 生成 `FileWorkInfo` 和 `Job`，保留原有专用 analyser 的行为。
2. 使用 `JobNeedTranslateSetter` 标记待翻译项。
3. 对数据库命中执行显式分级：
   - 同文件、同 `uid` 或 `key_path` 的精确命中可直接复用；
   - 同英文、同类别且所有高可信候选译文一致时可复用；
   - 同英文存在多个不同译文且缺少位置级证据时标记 `conflict`，不直接选择第一条；
   - 冲突项可交给 LLM 结合上下文选择，但结果仍视为新候选，不覆盖原候选。
4. 不直接复用 `BatchChunker.build_units`。它可能把整个文件或较大的结构块放进 `context_text`，同时又只在 `known_translations` 中挑选部分已有译文，容易造成 Token 浪费和实体上下文不连续。
5. 新建 `EntityContextBuilder`，根据 analyser 识别出的实体边界，将同一实体内的待译 Job 合为一个 `EntityTranslationTask`。建议给 `Job` 增加明确的 `entity_path`；各专用 analyser 在创建 Job 时写入它，通用 analyser 则从顶层实体数组和最近的带 `name`/`source` 对象确定。`entry_path` 和 `key_path` 只用于辅助定位，不能仅按相邻 Job、共同父路径或字符窗口猜测实体边界。
6. 每个实体任务生成两个结构完全对应的视图：
   - `source_entity`：该实体的完整英文 JSON，内容不得裁剪或改写；
   - `translation_skeleton`：该实体的完整译文骨架。已有译文的 Job 填入当前中文，待译 Job 保留 `{!@ <uid>}`，非翻译字段保持原值。
7. analyser 当前已经在 `json_obj` 中使用 `{!@ <uid>}`，因此译文骨架应沿用这一内部标识，不再设计第二套定位符。发送给模型时需同时附带 `pending_items`，明确列出 `uid`、英文原文和 TAG，避免模型仅凭占位符猜测目标。
8. 模型只返回 `pending_items` 对应的 `{uid: cn_text}`，不得返回或覆盖整个实体。已有译文仅作为连贯上下文，回填时保持不变。
9. 不再向每个任务附带文件级 `known_translations`。实体内部已有译文已经体现在 `translation_skeleton` 中；实体外只补充命中的术语和参考片段，并分别设置独立的数量、字符数和 Token 上限。
10. 预算应在调用模型前按最终消息计算 Token。一个正常实体作为一个任务，即使只有一个字段待译，也必须携带上述两个完整视图；多个实体不得为了凑批而合并。
11. 如果单个完整实体超过模型硬上下文上限，应标记为 `entity_too_large` 并进入显式降级流程或等待人工处理，不能静默截断实体、删除已有译文或退回携带整个文件。可选降级方案需要单独设计和测试后才能启用。
12. 失败重试可以将 `pending_items` 拆成更小集合，但所有子任务继续携带相同的完整 `source_entity` 和 `translation_skeleton`，并在骨架中填入此前已经成功返回且通过校验的候选译文。
13. 每个实体任务必须记录 `entity_id`、`source_hash`、实体路径、待译 UID 列表、引用片段 ID 和实际 Token 估算，便于失败定位、去重和重试。

任务载荷示意：

```json
{
  "entity_id": "monster:Goblin|MM",
  "source_entity": {
    "name": "Goblin",
    "entries": ["Nimble Escape.", "The goblin can take the Disengage action."]
  },
  "translation_skeleton": {
    "name": "哥布林",
    "entries": ["{!@ $.monster[0].entries[0]}", "哥布林可以执行撤离动作。"]
  },
  "pending_items": [
    {"uid": "$.monster[0].entries[0]", "en_str": "Nimble Escape.", "tag": "monster"}
  ]
}
```

这里第二条已有译文不会被隐藏，模型可以从完整实体的中英文对应关系理解语境；同时，模型只需输出第一个 UID 的译文。

### 模块二：参考资料与大模型翻译

每次模型调用只直接提供 `source_entity`、`translation_skeleton`、`pending_items`，以及两个参考查询 Tool 的名称和使用说明。除实体内已有译文外，不预先把术语、会话资料或不全书内容塞进提示词。

#### 2.1 统一的参考资料处理管线

会话级参考和不全书页面共用 `ReferenceContentProcessor`。输入通常以中文为主，标题或实体名称可能是“中文 英文”“中文（英文）”“英文 中文”等双语形态，而且一份内容可能包含多个实体，因此不能按固定字符数直接切块。

处理步骤：

1. **规范化文档**：保留标题层级、段落、列表、表格、页码、DOM 路径或原始偏移；清理重复导航和空白，但不能改写正文。
2. **识别实体边界**：优先使用 HTML 标题、文档标题层级、分页标题和不全书分类；其次识别连续的双语实体标题。一个文档允许拆出多个实体，实体正文从当前标题延伸到下一个同级或更高级实体标题。
3. **解析实体名称**：支持 `中文 英文`、`中文（英文）`、`英文（中文）`、`中文 | 英文` 等显式格式，输出 `name_en`、`name_cn` 和别名。英文或中文未在原文中明确出现时保持为空，不能由模型猜造。
4. **识别实体类型**：结合来源分类、标题和正文结构判断 `item`、`monster`、`spell` 等类型；无法可靠判断时使用 `unknown`，不能为了提高命中率错误分类。
5. **抽取中英文译文对**：从双语实体标题、显式术语表、同一结构字段中的中英对应内容提取。任意相邻的中英文文本不能直接视为翻译对；每个译文对必须保存原文证据位置。
6. **处理非结构化内容**：确定性规则无法拆分时，可调用一次结构化 LLM 提取作为降级，但输出的中英文名称、译文和证据都必须能在输入原文中逐字定位；无法定位的结果丢弃。
7. **去重但不消除冲突**：相同 `en + cn + entity_type` 合并来源；同一英文对应不同中文时全部保留为独立候选，由查询 Tool 返回，不能在建索引阶段选一个覆盖其他值。
8. **建立两个只读索引**：实体索引同时按规范化中英文名称、别名和类型检索；译文索引按规范化英文、类别及所属实体检索。索引项保留来源、内容摘要、解析方式和证据位置。

会话 `/ref` 在加载时立即执行上述处理。不全书数据在 Tool 首次查询时搜索并抓取页面全文，再走相同处理管线，并按页面内容摘要缓存本次会话的拆分结果。当前 `BuquanshuSearcher.search()` 只返回标题、预览和 URL，不能满足完整说明查询，因此需新增安全的页面全文获取能力；全文抓取失败时不得把截断预览伪装成完整实体。

处理结果的数据结构：

```python
class ReferenceEntity(TypedDict):
    entity_id: str
    entity_type: str
    name_en: str
    name_cn: str
    aliases: list[str]
    description_cn: str
    source_type: str       # session_reference | buquanshu
    source_id: str
    evidence_location: dict
    content_hash: str
    parse_method: str      # structured | rule | llm_fallback
    source_rank: int
    match_type: str
    confidence: float
    confidence_reasons: list[str]
    complete: bool

class TranslationCandidate(TypedDict):
    en: str
    cn: str
    category: str
    entity_id: str | None
    source_type: str       # term_table | session_reference | buquanshu | temporary_term
    source_id: str
    evidence_location: dict
    source_rank: int
    match_type: str
    confidence: float
    confidence_reasons: list[str]
    conflict: bool
```

每次处理最终输出 `entities: list[ReferenceEntity]` 和 `translations: list[TranslationCandidate]`。原始正文保存在会话参考存储中，Agent 状态只保存索引 ID 和统计信息。

#### 2.2 置信度与排序规则

`confidence` 是用于候选排序的工程分数，不宣称是统计概率。先计算名称/译文的匹配质量，再映射进互不重叠的来源区间，从而严格保证指定的数据源优先级；不能让低优先级来源因模糊搜索分数较高而越级。

匹配质量至少考虑：规范化后完全相等、别名完全相等、实体类型/类别一致、英文大小写差异、模糊相似度、解析方式和证据是否完整。建议返回 `confidence` 的同时返回 `source_rank`、`match_type` 和 `confidence_reasons`，方便模型理解分数来源。

直接译文候选的分数区间：

| `source_rank` | 来源 | `confidence` 区间 |
| --- | --- | --- |
| 1 | 数据库 `term` 表 | `[0.95, 1.00]` |
| 2 | 会话级参考拆分出的译文 | `[0.85, 0.94]` |
| 3 | 不全书拆分出的译文 | `[0.70, 0.84]` |
| 4 | 当前翻译流程保存的临时术语 | `[0.55, 0.69]` |

实体说明候选的分数区间：

| `source_rank` | 来源 | `confidence` 区间 |
| --- | --- | --- |
| 1 | 会话级参考拆分出的实体 | `[0.85, 1.00]` |
| 2 | 不全书拆分出的实体 | `[0.65, 0.84]` |

同一来源区间内再按匹配质量映射分数。完全匹配高于别名匹配，别名匹配高于模糊匹配；类型一致可提高区间内得分，但不能改变来源等级。排序键固定为 `confidence DESC, exact_match DESC, source_id ASC`，确保重复执行稳定。低于配置阈值的候选不返回；同英文多译文即使置信度不同也需保留，并在结果中标记 `conflict=true`。

数据库 `term` 表是第一优先级的事实来源，Redis 只能作为它的读取缓存，返回时仍标记 `source_type=term_table`。项目记忆显示 `term` 表可能存在通用词和分类脏数据，因此置信度区间只表示来源优先级，仍需返回类别和来源字段供模型判断，不能把所有 term 结果描述成“已确认唯一译法”。

临时术语复用 `JobProcessor` 当前每个输出文件的 `.terms.csv` 数据，但应抽出为运行级 `TemporaryTermStore`，作用域限制为当前 `/file` 运行；其候选优先级最低，运行结束后不能自动写入 `term` 表。

#### 2.3 对外只提供两个 LLM Tools

1. `lookup_translations`

   - 输入：`en_list`，可选 `category`、`entity_id`、`limit_per_term`。
   - 行为：依次查询 `term` 表、会话译文索引、不全书译文索引和当前运行临时术语；内部可以并行查询，但输出必须按上述置信度规则统一排序。
   - 输出：每个英文对应零到多个 `TranslationCandidate`，包含 `cn`、`confidence`、`source_rank`、`source_type`、`match_type`、冲突标记和证据来源。
   - 约束：输入短语按完整字符串查询，不再像当前 `search_terms` 一样默认拆成单词；只有模型明确提交多个 `en_list` 项时才分别查询。

2. `lookup_entities`

   - 输入：`names`（允许英文、中文或混合名称），可选 `entity_type`、`limit_per_name`。
   - 行为：先查询会话实体索引；需要更多结果时查询不全书、获取全文并完成拆分。英文名和中文名使用同一个别名索引。
   - 输出：每个名称对应零到多个完整 `ReferenceEntity`，包含中英文名称、实体类型、完整中文说明、`confidence`、`source_rank`、`match_type` 和证据来源。
   - 约束：只有完整正文才可标记 `complete=true`；只有预览时必须标记 `complete=false`，默认不作为“实体完整说明”返回。

现有 `search_terms` 和 `search_buquanshu` 不再作为 Agent 可见 Tool，而是降为两个聚合 Tool 的内部 provider。`KnowledgeSetter` 不在这一参考查询面中单独暴露；若以后需要其数据，必须先规范化为译文候选或实体记录，再接入上述两个 Tool，不能增加第三种无统一置信度的返回格式。

两个 Tool 使用统一响应外壳：

```json
{
  "query": ["Fireball"],
  "results": [{"confidence": 0.98, "source_rank": 1, "source_type": "term_table"}],
  "truncated": false,
  "errors": []
}
```

Tool 返回只进入当前实体任务的对话上下文，不写入 `translation_skeleton`，也不直接修改 Job。Tool 调用、查询参数和返回的来源 ID 由运行日志记录。模型最终仍只返回并回填通过本地校验的 `{uid: cn_text}`。

翻译处理器沿用 `BatchJobProcessor` 的结构化响应解析、TAG 检查和单条降级，但不沿用其现有 `BatchChunker` 请求载荷。处理器应接收 `EntityTranslationTask`，并按 UID 校验返回集合：不得缺项、增加未知 UID、重复 UID 或修改已有译文。Agent 的通用聊天模型不直接替代 processor；后续若统一模型客户端，应先抽象 `TranslatorModel` 接口，再迁移调用方。

模型应先检查实体双视图，仅在术语、背景或既有译法不明确时调用 Tool。调用需要设置单个实体任务的最大轮数、单次结果条数/字符数、网络超时、去重规则和总 Token 预算；相同参数在同一实体任务内直接复用缓存结果。Tool 失败只影响对应参考来源，不应直接导致整个文件失败；模型仍无法给出合法结果时，该 Job 进入失败清单。

### 模块三：生成与回归检查

1. 使用 `JsonGenerator` 基于 UID 回填译文，不通过英文全文替换来定位。
2. 生成后执行快速检查：
   - 输出可被 `json.loads` 解析；
   - 根节点和非翻译字段类型不变；
   - 所有预期 UID 已回填，且不存在 `{!@ ...}` 占位符；
   - 被跳过字段保持原值；
   - 每个翻译单元的 TAG、花括号、数字和骰子表达式满足现有校验规则；
   - 没有 `cn_str is None` 的未终结 Job 被当作成功。
3. 快速检查通过后，在隔离的 5etools 工作副本中写入候选文件并运行项目测试。具体命令做成配置项，默认优先使用仓库实际的数据测试命令。
4. 只有测试通过才能替换正式输出。失败候选保留为 `.staging` 或诊断产物，正式文件不得被覆盖。
5. 记录命令、退出码、截断后的 stdout/stderr 和候选文件位置，供 `/status` 和最终摘要展示。

### 模块四：保存与输出

1. 翻译阶段以 `update=False` 调用 `JobProcessor`/`BatchJobProcessor`，避免整文件校验前提前写库。
2. 将 processor 当前私有的 `_store_completed_job` 写库规则抽成公共保存器；整文件快速检查和 5etools 回归都通过后，再由它通过 `DatabaseAdapter` 一次性保存成功 Job，Agent 不直接拼 SQL。
3. 数据库键至少保留 `rel_path`、`uid`、`key_path`、`context_key` 和 `tag`，确保同英文多译文可以按出现位置区分。
4. 未经人工校对的 LLM 译文保持未校对状态；不能因整文件测试通过而自动标记 `proofread`。
5. 冲突、翻译失败或回归失败的条目不更新已有高可信译文。
6. 使用 `write_file_work_infos` 写出 `.en`、`.jobs` 和最终 JSON；最终 JSON 仅在全部必要检查通过后发布。
7. 最终汇报输出路径、复用/新译/冲突/失败数量、测试结果、Token 用量及可执行的重试命令。

## 6. 错误处理与安全约束

- 所有用户路径先规范化并检查允许根目录，拒绝 `..` 越界、设备文件和不支持的后缀。
- 源文件只读；输出只能写入配置的输出目录，不能原地覆盖英文源文件。
- 不把 API Key、数据库连接信息或完整异常环境写入提示词和日志。
- LLM 返回内容必须经过结构化解析和本地校验后才能进入 Job。
- 重试只针对明确的暂时性错误或格式错误，保持现有有限次数；认证失败、路径错误和确定性校验失败不盲目重试。
- 目录任务隔离每个文件的运行对象，避免 `JsonAnalyser`、词典缓存或 Agent 对话历史污染下一个文件。
- 流式输出只用于展示进度；状态更新和数据库写入以完整响应为准。

## 7. 实施阶段与验收标准

### 阶段 A：命令与确定性编排

2026-09-11：阶段 A 的命令与现有批量流水线接入已实现，采用普通 Python 状态机。入口、配置、提示词和验收结果见 [README.md](README.md)。本阶段保留原批次上下文协议；完整实体双视图改造尚未实施，原生 schema 的 `ENG_name` 兼容差异见 README。

- 实现命令解析、路径校验、显式状态和新的 `graph.py`。
- `/file` 能调用现有 analyser → `JobNeedTranslateSetter` → processor(`update=False`) → generator → validator → persister/writer 流水线；不调用 `TermSetter` 或 `KnowledgeSetter` 自动注入参考内容。
- 不加载参考资料时，Job 覆盖范围、UID 回填和输出结构与现有 `main.py translate --batch` 兼容；由于提示上下文发生变化，不要求 LLM 译文逐字一致。
- 设计各阶段或各种情况下的llm的提示词

### 阶段 B：参考资料

2026-09-15：已实现会话参考命令、共享实体/译名解析、两个聚合查询 Tool、不全书安全全文读取及 processor 按需 Tool 调用。已使用 `forum_posts2129.txt`、真实 `term` 行和不全书全文只读验证；用法、预算、解析边界与探测脚本见 [README.md](README.md)。完整实体双视图载荷仍属于待完成的处理器改造，不由本阶段的参考索引替代。

- 实现 `ReferenceLoader` 和共享的 `ReferenceContentProcessor`，能从单份中文为主的内容中拆出多个实体，并提取有原文证据的中英文译文对。
- 会话资料和不全书正文使用相同的 `ReferenceEntity`、`TranslationCandidate` 结构及两个只读索引；处理结果明确输出实体列表和中英文对照译文列表。
- 实现 `lookup_translations`，按 `term` 表 → 会话译文 → 不全书译文 → 临时术语的固定优先级返回带置信度候选。
- 实现 `lookup_entities`，支持中英文名称查询，并按会话实体 → 不全书实体的固定优先级返回完整中文说明。
- 为不全书补充安全的全文读取；只有搜索预览时必须标记不完整，不能作为完整实体说明返回。
- `/refs`、`/clear-refs` 可用；Agent 状态只保存索引 ID 和统计信息，原始资料不会预先进入实体任务提示。
- 参考文件或不全书页面处理失败不会污染其他文件、全局知识库或数据库 `term` 表。

### 阶段 C：冲突与验证

- 增加同英文多译文冲突识别，不再静默选第一条。
- 增加 JSON 快速检查、5etools 隔离测试和通过后发布。
- 失败结果可定位到文件、实体任务和 Job UID，且不会覆盖成功产物。

### 阶段 D：可恢复性与体验

- 实现 `/status`、`/retry`、`/cancel` 和目录级汇总。
- 中断后能从 `.jobs` 和失败清单恢复，已成功 Job 不重复请求 LLM。
- 展示模型输入/输出/缓存 Token；供应商未返回某类统计时明确显示“未提供”。

## 8. 测试清单

单元测试至少覆盖：

- 命令含空格路径、引号、缺少参数、非法命令和越界路径；
- 文件/目录展开、排序、空目录、坏 JSON 和混合后缀；
- 中文为主、双语标题、多个实体、嵌套标题、无结构正文和错误实体边界的拆分；
- 中英文名称解析、实体类型识别、译文证据定位、相同译文去重和冲突译文保留；
- 规则拆分失败后的结构化 LLM 降级，以及无法在原文定位的提取结果被拒绝；
- `lookup_translations` 的四级来源排序、置信度区间、类别过滤、完整短语查询和临时术语运行隔离；
- `lookup_entities` 的中英文/别名查询、两级来源排序、完整正文返回和预览拒绝；
- 同 UID 精确复用、唯一通用译文复用、同英文多译文冲突；
- 同实体分组、完整英文实体、带 UID 占位符的完整译文骨架，以及已有译文不会被模型输出覆盖；
- 不携带无关实体或文件级 `known_translations`，单实体超出硬上限时不静默裁剪；
- 重复 Job 合并，以及失败任务按 UID 拆小后仍保留完整实体双视图；
- 两个聚合 Tool 的成功、缓存、截断、超时、错误、超轮数以及恶意/超长返回；
- 未调用 Tool 时，术语、`/ref` 原文和不全书内容不会出现在模型请求中；
- TAG、占位符、数字、骰子表达式和非翻译字段一致性；
- 数据库只写成功项，失败/冲突项不覆盖旧数据；
- 多文件部分失败、取消、重试和会话隔离；
- 同步与流式聊天的 Token 统计。

集成测试使用 `MemoryDBDictionary` 和 mock LLM 跑完整 `/file` 流程；再选择一个小型真实 5etools JSON 做隔离回归测试。外部 MySQL、Redis、Chroma、网络 LLM 和不全书搜索不应成为默认测试的前置条件。

## 9. 完成定义

满足以下条件才算第一版完成：

1. `/ref` 和 `/file` 的成功、失败及安全边界有明确且经过测试的行为。
2. Agent 通过确定性图编排现有翻译组件，没有复制 analyser、processor 或 generator 的核心逻辑。
3. 同英文多译文不会被无条件复用。
4. 每个输出都能追溯到源文件、Job UID、使用的参考片段和验证结果。
5. JSON 快速检查及 5etools 回归测试通过后才写入最终文件。
6. 失败不会破坏英文源文件、已有正式输出或已校对数据库记录。
