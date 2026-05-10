# Browse Interact 重构 2.0

## 概述
本次重构主要优化了浏览器数据目录检测、提升用户体验，以及代码架构的整洁度。

## 主要改动

### 一、代码风格优化
完全遵循 `code_smell.md` 原则
- 统一使用 `pathlib.Path` 替代 `os.path` 进行路径操作
- 完善类型注解，使用 `typing` 模块（`Union[str, Path]、`List[Tuple[str, Path]]` 等）
- 消除魔法数字，将配置文件路径定义为常量 `_CONFIG_FILE`
- 遵循代码排版符合规范，`if` 条件后换行
- 使用 `log_event` 替代 `print` 进行日志输出

### 二、架构优化
#### 1. 文件职责单一
- `browser_data_detector.py`：纯工具函数，负责检测浏览器数据目录
- `bootstrap.py`：可插拔组件初始化模块，专门处理组件组装
- `main.py`：应用启动入口，保持简洁

#### 2. 架构设计
```
main.py (应用入口)
  ↓
bootstrap.py (初始化组件)
  ↓
browser_data_detector.py (检测数据目录)
  ↓
BrowseInteractTool (浏览器交互)
```

#### 3. 工具参数化重构
为 BrowseInteractTool 添加 `user_data_dir` 参数，支持自定义用户浏览器数据目录，实现持久化登录态：

```python
class BrowseInteractTool(BaseTool):
    def __init__(self, user_data_dir: Optional[Union[str, Path]] = None, timeout: int = 30):
        self._user_data_dir = str(user_data_dir) if user_data_dir else None
        # ... 其余初始化代码
```

架构变更：
- 通过依赖注入容器 `container.py` 动态设置 `user_data_dir`
- `bootstrap.py` 中的 `setup_browser_data_dir` 函数负责组装工具实例
- 保持工具类的可测试性，参数可外部传入

### 三、功能改进

#### 1. 兼容新版 Chrome 锁文件
```python
def is_browser_locked(user_data_dir: Union[str, Path]) -> bool:
    path = Path(user_data_dir)
    if not path.is_dir():
        return False
    for name in path.iterdir():
        if name.name.startswith("SingletonLock"):
            return True
    return False
```
- 使用 `startswith("SingletonLock")` 匹配所有变体
- 兼容 Chrome 132+ 版本的新锁文件命名（如 SingletonLock10、SingletonLock20）

#### 2. 精确的浏览器锁定提示
```python
status, browser_name, detected_path, locked_browsers = detect_browser_data_dir()
...
if status == DataDirStatus.LOCKED:
    browsers_str = "、".join(locked_browsers)
    return None, (
        f"检测到浏览器正在运行：{browsers_str}\n"
        "请关闭浏览器后重试。\n"
        "如需使用其他配置目录，请通过 --data-dir 参数指定。"
    )
```
- 收集所有锁定的浏览器名称
- 提示信息显示具体锁定的浏览器，提高用户体验

#### 3. 持久化用户配置
- 配置文件保存在用户主目录：`~/.agent_browser_config.json`
- 自动保存用户指定的路径，下次无需再次输入
- 支持 CLI 参数、持久化配置、自动检测三级优先级

### 四、用户配置流程
```
1. CLI 参数 --data-dir（本次明确指定，自动持久化）
  ↓
2. 已持久化的路径（上次设定，自动使用）
  ↓
3. 自动检测（Chrome → Edge）
  ↓
4. 临时浏览器会话（无用户登录态）
```

## 文件清单

| 文件名 | 描述 |
|--------|------|
| `browser_data_detector.py` | 浏览器数据目录检测工具 |
| `bootstrap.py` | 应用初始化引导模块 |
| `browse_interact_tool.py` | 浏览器交互工具 |
| `container.py` | 依赖注入容器 |
| `main.py` | 应用启动入口 |

## user_data_dir 参数化重构详解

### 1. 重构原因
之前 BrowseInteractTool 总是创建临时浏览器会话，无法复用用户本地浏览器的登录态、Cookie 等信息。

### 2. 重构方案
将 user_data_dir 作为可选参数注入，通过容器动态设置：

**Bootstrap 层**：
```python
def setup_browser_data_dir(container) -> Optional[str]:
    browser_data_dir, data_dir_source = resolve_user_data_dir()
    if browser_data_dir:
        container.browse_interact_tool.override(providers.Singleton(
            BrowseInteractTool,
            user_data_dir=browser_data_dir,
        ))
        return str(browser_data_dir)
```

**容器层**：
```python
class Container:
    browse_interact_tool = providers.Singleton(BrowseInteractTool)
```

### 3. 核心优势
- **零侵入性**：工具类本身不依赖具体的检测逻辑
- **可测试性**：测试时可传入任意临时目录
- **可扩展性**：未来可通过同样方式注入其他配置项
- **可插拔**：组件可独立替换，不影响整体架构

## 设计原则
- 拒绝过度抽象
- 职责单一
- 消除隐式行为
- 类型注解全面
- 日志分级明确
