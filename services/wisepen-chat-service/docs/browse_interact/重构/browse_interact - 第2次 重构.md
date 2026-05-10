# BrowseInteract 1.0

***

## 一、重构背景

原实现存在以下问题，亟需重构：

- **职责混淆**：`BrowseInteract` 与 `BrowseUrl` 职责边界模糊，反爬对抗被错误耦合到操作工具中
- **执行器冗余**：存在 Steel 云浏览器 → 本地 Playwright 的双执行器降级链，引入不必要复杂度
- **操作模式过时**：保留大量像素级坐标操作，Agent 决策空间被污染，操作准确率低
- **感知层缺陷**：快照注入脚本存在交互元素感知不全（缺 disabled/可见性检测、无 `<label for>` 查询、不穿透 Shadow DOM）
- **稳定性隐患**：资源清理不彻底、`fill_ref` 在现代前端框架下易失败、会话复用缺少有效性校验

***

## 二、重构成果

### 2.1 架构层面

- **操作与抓取彻底分离**：`BrowseInteract` 只负责本地浏览器操作，反爬对抗移交 `BrowseUrl`
- **单一执行引擎**：移除 Steel 执行器及相关代码，仅保留本地 Playwright Chromium
- **职责清晰**：工具类通过依赖注入容器组装，支持 `user_data_dir` 参数化配置

### 2.2 功能层面

- **操作清单精简**：从约 20 个动作缩减至 10 个核心动作，仅保留 `snapshot + ref` 范式
- **快照脚本重写**：新增真实可交互性检测、`<label for>` 查询、Shadow DOM 穿透、防反爬保护
- **`fill_ref`** **优化**：使用 Playwright 原生 `fill()`，正确处理 React/Vue 受控组件，增加联想菜单关闭逻辑
- **资源管理加固**：独立的 `_cleanup_local()` 方法，每次重建前强制调用
- **会话状态校验**：心跳检测后附加登录重定向判断，防止在已登出页面盲操

### 2.3 用户体验提升

- **浏览器数据目录支持**：通过 `user_data_dir` 参数复用用户本地浏览器登录态
- **精确锁定提示**：显示具体锁定的浏览器名称
- **持久化配置**：用户指定路径自动保存，下次无需再次输入

***

## 三、架构变更

### 3.1 职责划分

| 工具             | 职责                | 反爬对抗 | 引擎            |
| -------------- | ----------------- | ---- | ------------- |
| BrowseInteract | 页面操作（导航、输入、点击、滚动） | 无    | 本地 Playwright |
| WebFetch       | 内容抓取（HTML、截图）     | 有    | 三级降级链         |

### 3.2 初始化流程

```
main.py (应用入口)
  ↓
bootstrap.py (初始化组件)
  ↓
browser_data_detector.py (检测数据目录)
  ↓
BrowseInteractTool (浏览器交互)
```

***

## 四、代码优化要点

### 4.1 工具参数化

```python
class BrowseInteractTool(BaseTool):
    def __init__(self, user_data_dir: Optional[Union[str, Path]] = None, timeout: int = 30):
        self._user_data_dir = str(user_data_dir) if user_data_dir else None
```

### 4.2 锁文件检测兼容

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

***

## 五、删除的组件

| 被删除项                      | 原因                   |
| ------------------------- | -------------------- |
| Steel 执行器、`AsyncSteel` 依赖 | 反爬职责移交 WebFetch      |
| 浏览器遍历逻辑                   | 单引擎锁定 Chromium       |
| 所有坐标操作动作                  | `snapshot+ref` 范式已覆盖 |
| `type` 动作                 | `fill_ref` 已替代       |
| `find_and_click` 动作       | ref 方案解耦更优           |
| 双引擎降级逻辑                   | 单一引擎，无降级需要           |

***

## 六、设计原则遵循

- ✅ 拒绝过度抽象
- ✅ 职责单一
- ✅ 消除隐式行为
- ✅ 类型注解全面
- ✅ 统一使用 `pathlib`
- ✅ 依赖注入解耦

***

## 七、文件清单

| 文件名                        | 描述          |
| -------------------------- | ----------- |
| `browser_data_detector.py` | 浏览器数据目录检测工具 |
| `bootstrap.py`             | 应用初始化引导模块   |
| `browse_interact_tool.py`  | 浏览器交互工具     |
| `container.py`             | 依赖注入容器      |
| `main.py`                  | 应用启动入口      |

