---
name: paper-core-innovation-extraction
display_name: 论文核心创新点抽取（PDF/RAG链路）
description: 当用户给出学术论文URL（尤其是PDF，如 arXiv）并要求“概括/讲解核心创新点、贡献、方法亮点、关键结果”时，触发本技能。该技能以最小可用工具链条完成：获取PDF→解析为可检索文本→证据排序→定点读取→结构化总结，并严格基于检索到的正文证据作答。典型触发语包括：“讲解这篇论文的核心创新”“总结贡献点”“解释方法的新颖性”“这篇paper的关键点是什么”等。
version: 1.0.0
metadata:
  schema_version: '1.0'
  target_runtime: wisepen
  generated_by: agent
  created_by:
    user_id: 111cbc3191344134
  source:
    service: chat-service
    session_id: 6a1fcb7a7dbd360bdf3f5e23
  created_at: '2026-06-03T06:39:37.667628+00:00'
---
# 论文核心创新点抽取（PDF/RAG链路）

## Purpose

为学术论文（尤其是PDF）构建一条稳定、可复用的工具链路，快速、可追溯地抽取“核心创新/贡献/关键结果”，并确保答案只基于已检索的论文文本证据。

## Input requirements

- 至少需要：论文的PDF链接或已上传的PDF文件。
- 可选：用户关注点（如仅核心创新/是否包含结果与消融）、是否需要引用章节/表格、输出语言偏好。

## Workflow

1. 识别输入与目标
   - 确认用户是否提供了论文URL（优先PDF直链）或本地已上传文档。
   - 澄清用户要点（例如：仅要核心创新点？是否需要关键结果与少量消融支持？是否需要页码/表格引用）。
2. 获取与解析文档
   - 若是网络PDF链接：调用 functions.web_fetch(urls=[...]) 获取 file_ref。
   - 一次性将所有 file_ref 传入 functions.document_parse(file_refs=[...], objective=...)，objective 中明确抽取目标（如：title、abstract、novelty、architecture、key results、ablations）。
   - 记录返回的 parsed_content_id（cnt_*）以备检索。
3. 证据检索与排序（RAG二次排序）
   - 调用 functions.evidence_rank(content_ids=[parsed_content_id], query=...) 对整篇解析内容做二次排序。
   - query 模板：围绕“核心创新/新颖点/方法模块/相对RNN/CNN优势/并行与复杂度/位置编码/多头注意力/关键结果/消融”等关键词组织。
4. 定点阅读与扩展上下文
   - 若需要上下文：调用 functions.tool_content_read(content_id=..., offset or chunk_index, before/after)。
   - 需要同时查看多个关键片段：调用 functions.tool_content_batch_read，成批读取相关 chunk，并合理设置 before/after。
5. 结构化摘要与溯源
   - 按固定大纲输出：- 架构级创新 - 关键模块 - 训练与效率优势 - 结果与成本 - 消融与发现 - 任务泛化/限制。
   - 确保每个要点均来自已读证据；必要时在括号中标注来源（如“见表2/表3/‘Positional Encoding’小节”）。
   - 若证据不足，明确说明“检索文本不足以支持某点”。
6. 质量与安全检查
   - 不引入外部信息或常识性回忆，所有事实只来自检索到的正文内容。
   - 语言与格式：与用户语种保持一致；使用清晰小标题与要点列表。
   - 若PDF解析质量下降（有解析警告），提醒用户可能存在表格/版面还原缺陷。

## Output requirements

- 以层次化要点列出“核心创新/新颖性/关键模块/效率优势/结果/消融与发现”。
- 保持证据可追溯：在要点后标注来源线索（如“Abstract”“Section 3.2.2 Multi-Head Attention”“Table 2/3”等）。
- 语言简洁、结论明确；不输出与证据不符或无法证实的信息。

## Tool guidance

- functions.web_fetch：用于直连PDF与URL获取；拿到 file_ref 后立刻转交 document_parse。
- functions.document_parse：一次性传入全部 file_ref；设置 objective 明确抽取目标。
- functions.evidence_rank：对 parsed_content_id 执行二次排序，快速定位“Abstract/Introduction/Method/Results/ Ablations”等关键段。
- functions.tool_content_read：读取单窗口或指定 chunk 的上下文。
- functions.tool_content_batch_read：并行获取多个关键片段，便于对照分析与交叉引用。

## Resource guidance

- 解析成功后会返回 parsed_content_id（cnt_*）；evidence_rank 与 content_read 系列均基于该ID操作。
- 如 evidence_rank 返回的片段不够集中，可增加 max_evidence 或迭代优化 query 关键词。
- 当工具提示内容被截断时，用 tool_content_read 的 offset/limit 或 chunk_index 模式继续读取。

## Bundled files

- `assets/templates/answer-outline.md` — 面向论文核心创新点抽取的固定大纲与占位符模板

## Constraints

- 仅基于已检索的论文正文与表格/图注作答，禁止臆测或补充外部事实。
- 当证据不足以回答某一子问题时，必须显式声明信息不足。
- 尽量引用论文中的章节名、表格名或关键术语，以增强可追溯性。
- 遵循最小工具集：优先 web_fetch→document_parse→evidence_rank→content_read/batch_read 的顺序；避免不必要的网络搜索。

## Examples

### 示例1：arXiv PDF核心创新点抽取

**User input**

https://arxiv.org/pdf/xxxx.xxxxx，讲解这篇论文的核心创新点

**Expected behavior**

- 使用 web_fetch 获取PDF，document_parse 解析。
- 用 evidence_rank 查询“核心创新/novelty/architecture/positional encoding/efficiency/results/ablations”等。
- 用 content_read/batch_read 拉取关键窗口。
- 按固定大纲输出要点，并在每点后用“见 Abstract/Section X/Table Y”等标注来源。

### 示例2：用户要求只看‘方法新颖点’

**User input**

请只讲方法的新颖性，不需要结果

**Expected behavior**

- 在 evidence_rank 的 query 中强调 Method/Architecture/Attention/Encoding。
- 输出仅保留‘架构级创新’与‘关键模块’两部分，并注明相应章节来源。
