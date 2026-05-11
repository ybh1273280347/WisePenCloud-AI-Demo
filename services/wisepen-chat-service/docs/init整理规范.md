你正在维护一个 Python 仓库。现在需要做一次“全仓库所有 __init__.py 的公共 API 导出规范排查与治理”。

注意：这不是只排查 content_store，也不是只排查某个文件夹。请从仓库根目录开始，扫描整个项目里的所有 __init__.py，并审查每个包级导出的合理性。

# 总目标

建立并执行统一规范：

1. 所有 __init__.py 只导出该包的稳定公共 API；
2. 不把内部 helper、实现细节、private 模块、临时兼容符号随意暴露到包级入口；
3. 测试如果需要内部函数，应从具体模块导入，而不是从包级 __init__.py 导入；
4. 兼容旧 API 的导出应明确放在兼容层，而不是污染 core/domain 等基础层；
5. 全仓库不能出现 import break；
6. 文档、脚本、测试、示例里的导入也要同步检查。

# 排查范围

从仓库根目录开始，覆盖整个仓库，包括但不限于：

- src/
- test/
- tests/
- scripts/
- docs/
- examples/
- dev_fixtures/
- migrations/
- notebooks/
- README / markdown 文档
- CLI / 调试脚本
- 配置中的示例代码

可以排除：

- .git/
- .venv/
- venv/
- __pycache__/
- .pytest_cache/
- .ruff_cache/
- node_modules/
- dist/
- build/
- 其他构建产物或缓存目录

# 第一阶段：列出所有 __init__.py

请从仓库根目录搜索所有：

__init__.py

输出表格：

| 序号 | 文件路径 | 所属包 | 当前是否有导出逻辑 | 是否包含 __all__ | 初步风险等级 |
|---:|---|---|---|---|---|

风险等级建议：

- P0：包级导出了明显内部 helper / private 符号，且被业务代码依赖；
- P1：包级导出过宽，没有 __all__，或混入旧兼容语义；
- P2：只有少量 re-export，但边界需要确认；
- P3：空文件或仅包标记，无需处理。

# 第二阶段：审查每个 __init__.py 的导出内容

对每个非空 __init__.py，分析：

1. import 了哪些符号；
2. __all__ 里列了哪些符号；
3. 是否存在 wildcard import；
4. 是否导出了以下类型的内部实现：
   - helper 函数；
   - private 函数或 private 类；
   - 具体实现类但外部应该依赖抽象；
   - 临时兼容别名；
   - 测试专用符号；
   - vendor / third-party 细节；
   - 运行时单例；
   - debug 工具；
   - CLI 内部函数；
5. 是否造成循环 import 风险；
6. 是否和分层架构冲突，例如：
   - core 包导出 application/tool 语义；
   - domain 包导出 infrastructure 实现；
   - api 包导出 application 内部工具；
   - application 包导出具体 provider 或 persistence 细节。

输出表格：

| __init__.py | 当前导出符号 | 建议保留 | 建议移除 | 需要迁移导入的位置 | 理由 |
|---|---|---|---|---|---|

# 第三阶段：全仓库搜索导入依赖

对每个 __init__.py 当前导出的符号，必须搜索全仓库使用情况。

重点搜索：

1. 从包级导入：

from some.package import Symbol
import some.package

2. 从具体模块导入：

from some.package.module import Symbol

3. wildcard import：

from some.package import *

4. 文档和示例代码中的导入。

请输出：

| 符号 | 当前包级导出位置 | 全仓库使用位置 | 使用类型 | 是否可迁移 | 建议 |
|---|---|---|---|---|---|

使用类型：

- production
- test
- script
- docs
- example
- config
- unknown

# 第四阶段：统一导出规范

请按以下规范审查和修改所有 __init__.py。

## 1. 默认只导出稳定公共 API

允许包级导出的符号：

- 该包对外主入口类；
- 该包对外主入口函数；
- 明确稳定的数据模型；
- 明确稳定的异常类型；
- 明确稳定的配置对象；
- 文档中承诺的公共 API。

不应包级导出的符号：

- 内部 helper；
- private 函数或类；
- 只被本包内部使用的实现细节；
- 只被测试使用的函数；
- 临时调试函数；
- 具体工具函数，除非它本身就是包级 public API；
- third-party 实现细节；
- 仅为兼容迁移临时存在的旧名字，除非该包就是兼容层。

## 2. 测试可以导入具体模块

如果测试需要内部 helper，例如：

from package import internal_helper

应改成：

from package.internal_module import internal_helper

不要为了测试方便把 helper 放到包级 __init__.py。

## 3. 兼容 API 放到兼容层

如果某个旧符号是为了兼容历史调用，应放在原来的旧入口模块或明确的 compatibility 模块。

例如：

- 旧 tool 语义放在 application/tool_content_store.py；
- core 包尽量只暴露新语义；
- 不要让 core.__init__ 长期导出 application/tool 专用旧别名。

## 4. 所有非空 __init__.py 建议显式 __all__

如果 __init__.py 有 re-export，建议提供 __all__。

空 __init__.py 可以保持空，不必强行写 __all__。

## 5. 不使用 wildcard import

禁止在 __init__.py 中使用：

from .module import *

除非已有强历史原因，并且无法安全移除。若保留，必须说明理由。

## 6. 避免运行时副作用

__init__.py 不应做：

- 创建连接；
- 初始化外部服务；
- 读取大型资源；
- 注册全局状态；
- 执行网络请求；
- 运行耗时逻辑。

只允许轻量导入和 re-export。

# 第五阶段：修改策略

不要一开始就大规模改。请按风险分批：

## P0：立即修复

- 会暴露内部实现并被 production 误用的导出；
- wildcard import；
- 导入引发副作用；
- 循环 import 风险；
- 已经导致 import break 或测试不稳定的导出。

## P1：建议修复

- helper 被包级导出；
- 测试从包级导入内部 helper；
- __all__ 缺失；
- 兼容别名在错误层级导出。

## P2：记录 TODO

- 暂时保留的迁移兼容导出；
- 文档中仍提到旧路径但不影响代码；
- 后续需要单独设计 public API 的模块。

# 第六阶段：content_store 作为具体案例

请特别检查：

src/chat/core/content_store/__init__.py

原则：

- 不从包级导出 create_content_chunks；
- 不从包级导出 find_chunk_by_offset；
- 如果测试需要它们，应从 chat.core.content_store.chunking 导入；
- StoredToolContent / WindowedContent 是否保留，取决于全仓库依赖；
- format_tool_content_window 是否保留，取决于 application 兼容层导入；
- core 包级入口不应长期承载 tool-only 语义。

但这只是一个案例。请用同样标准检查全仓库所有 __init__.py。

# 第七阶段：导入迁移规则

如果发现某个符号不应从包级导出，但当前有使用：

## production 代码

优先判断该依赖是否合理：

- 合理：改为从具体模块导入；
- 不合理：改为使用更高层稳定 API；
- 涉及分层违规：记录并给出迁移方案，不要盲改。

## test 代码

测试可以从具体模块导入内部 helper。

例如：

from chat.core.content_store import create_content_chunks

改成：

from chat.core.content_store.chunking import create_content_chunks

## docs / examples

同步更新示例，避免文档继续传播错误导入方式。

## scripts

脚本如果是调试脚本，可以从具体模块导入。
如果是用户-facing CLI，应该只用公共 API。

# 第八阶段：测试与验证

修改后至少运行：

1. 导入检查：

python -m compileall src test

或项目等价命令。

2. 相关测试：

uv run python -m unittest test.test_content_store_unit -v
uv run python -m unittest test.test_tool_content_store_offsets -v
uv run python test/test_tool_content_read_unit.py

3. 全量测试：

uv run python -m pytest

如果全量测试太慢或环境不满足，请说明原因，并列出已运行的替代测试。

4. 如果项目有 lint/type check，也运行：

uv run ruff check .
uv run mypy .

如果项目未配置，则说明未运行。

# 第九阶段：输出格式

请按以下结构输出。

## 当前必须优化的点，为什么

列出全仓库所有 __init__.py 排查后发现的 P0 / P1 问题。

每条说明：

- 文件；
- 当前导出；
- 为什么不合理；
- 修改方案。

## 需要确认一些东西再决定如何处理的点：需要确认什么，造成什么分歧

列出仍有争议的导出，例如：

- 某个旧兼容别名是否继续包级导出；
- 某个 formatter 是否属于 core 公共 API；
- 某个 singleton 是否该暴露；
- 某个 docs 示例是否代表 public API。

## 接下来 review 哪个文件

给出下一个最值得 review 的文件，通常是：

- 风险最高的 __init__.py；
- 或被最多导入的包级入口；
- 或分层边界最模糊的模块。

## 本次排查结果

必须包含：

1. 扫描了多少个 __init__.py；
2. 哪些 __init__.py 是空文件；
3. 哪些 __init__.py 有 re-export；
4. 哪些有 __all__；
5. 哪些没有 __all__；
6. 哪些存在 wildcard import；
7. 哪些导出内部 helper；
8. 哪些导出兼容别名；
9. 哪些导入路径被修改；
10. 哪些文档 / 示例被修改；
11. 测试结果。

## 修改清单

列出具体修改文件和修改内容。

## TODO

列出暂时不动但后续需要处理的问题。

# 验收标准

完成后必须满足：

- 已从仓库根目录扫描所有 __init__.py；
- 所有有 re-export 的 __init__.py 都经过审查；
- 不合理的包级内部 helper 导出已移除或记录明确理由；
- 测试中对内部 helper 的使用改为具体模块导入；
- 兼容 API 放在兼容层，而不是污染基础层；
- 文档和示例导入路径同步；
- 没有 wildcard import，除非有明确保留理由；
- 没有 import break；
- 相关测试通过。