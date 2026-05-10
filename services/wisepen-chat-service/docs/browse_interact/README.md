# browse_interact 文档索引

## 近期修复与优化

| 文档 | 主题 |
|---|---|
| [修复/browse_interact - 第1次 修复.md](修复/browse_interact%20-%20第1次%20修复.md) | Playwright 启动合同、snapshot/ref 生命周期、失败恢复、新 tab、代理继承等运行问题修复。 |
| [优化/browse_interact - 第8次 优化.md](优化/browse_interact%20-%20第8次%20优化.md) | snapshot label/focused 质量增强、短 tree 格式、select_ref/check_ref 表单能力。 |

## 功能范围

`browse_interact` 负责浏览器交互能力，核心线索包括本地 Playwright 执行、单 action 调用、snapshot/ref 交互范式、错误协议、命名收敛和后续清理。

## 当前可用结论

- 交互入口倾向单 action，而不是批量 actions。
- Agent 面向元素操作时优先使用 snapshot/ref，避免像素坐标。
- 错误协议需要收敛，区分 agent-facing 信息和内部诊断信息。
- 会话字段、owner_id、防御性类型检查、简单错误类、伪抽象等都经历过多轮清理。

## 推荐阅读顺序

| 顺序 | 文档 | 用途 |
|---|---|---|
| 1 | [重构/browse_interact - 第1次 重构.md](重构/browse_interact%20-%20第1次%20重构.md) | 从浏览器工具总重构中拆出的 browse_interact 总览。 |
| 2 | [重构/browse_interact - 第2次 重构.md](重构/browse_interact%20-%20第2次%20重构.md) | browse_interact 第一轮独立重构记录。 |
| 3 | [重构/browse_interact - 第3次 重构.md](重构/browse_interact%20-%20第3次%20重构.md) | 第二轮重构与代码风格优化。 |
| 4 | [重构/browse_interact - 第4次 重构.md](重构/browse_interact%20-%20第4次%20重构.md) | 模块瘦身和职责拆分。 |
| 5 | [重构/browse_interact - 第5次 重构.md](重构/browse_interact%20-%20第5次%20重构.md) | Snapshot + Ref 架构文档。 |
| 6 | [重构/browse_interact - 第6次 重构.md](重构/browse_interact%20-%20第6次%20重构.md) | 错误协议重构。 |
| 7 | [重构/browse_interact - 第7次 重构.md](重构/browse_interact%20-%20第7次%20重构.md) | 第二轮协议扩展。 |
| 8 | [重构/browse_interact - 第8次 重构.md](重构/browse_interact%20-%20第8次%20重构.md) | 命名收敛。 |
| 9 | [重构/browse_interact - 第9次 重构.md](重构/browse_interact%20-%20第9次%20重构.md) | 最终结构合并建议。 |

## 优化与清理线索

| 文档 | 主题 |
|---|---|
| [优化/browse_interact - 第1次 优化.md](优化/browse_interact%20-%20第1次%20优化.md) | 工程成熟度补强。 |
| [优化/browse_interact - 第2次 优化.md](优化/browse_interact%20-%20第2次%20优化.md) | session owner_id 清理。 |
| [优化/browse_interact - 第3次 优化.md](优化/browse_interact%20-%20第3次%20优化.md) | session 字段简化。 |
| [优化/browse_interact - 第4次 优化.md](优化/browse_interact%20-%20第4次%20优化.md) | isinstance 防御性检查清理。 |
| [优化/browse_interact - 第5次 优化.md](优化/browse_interact%20-%20第5次%20优化.md) | 模块级下划线清理。 |
| [优化/browse_interact - 第6次 优化.md](优化/browse_interact%20-%20第6次%20优化.md) | pass 错误类清理。 |
| [优化/browse_interact - 第7次 优化.md](优化/browse_interact%20-%20第7次%20优化.md) | 无意义 dataclass / 伪抽象清理。 |

## 追溯提示

若只想了解当前形态，优先读第 5 次到第 9 次重构，再读优化第 4 次到第 7 次。更早文档主要用于理解为什么从原始浏览器工具逐步收敛到当前结构。
