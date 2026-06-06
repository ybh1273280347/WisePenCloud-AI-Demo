# RAG 权限投影与检索范围对齐设计

## 目标

RAG 的资源可见范围需要和 Java 资源微服务的权限模型保持一致。

这里的一致不是指 Python 在检索时调用 Java 服务，而是：

- Java 资源微服务的权限模型是权威事实。
- Python chat-service 不依赖任何 Java 运行时行为。
- Python 通过本地 ACL 投影、事件同步和本地权限计算，实现与 Java 语义一致的 RAG 检索范围。
- 如果 Java 端 Elasticsearch 的范围实现没有明显错误，RAG 应尽量对齐 Java ES 的资源可见范围。
- RAG 暴露的是资源正文内容，因此最低权限建议使用 `VIEW`，不能只按 `DISCOVER` 判断。

## 当前问题

当前 RAG 检索范围是 owner-only：

- Manifest resolve 基于 `user_id`。
- Mongo / Qdrant / Elasticsearch filter 使用：
  - `user_id`
  - `resource_kind`
  - `resource_id`
  - `index_version`
- 没有表达组资源、指定用户授权、角色授权、覆盖授权、回收站/删除等资源权限状态。

这对个人私有资源是安全的，但和 Java 资源服务的权限模型不一致。

更准确地说，当前 RAG 权限模型最大的问题不是少了一次权限校验，而是完全忽视了一个业务核心概念：**组资源**。

当前 RAG 隐含假设是：

```text
resource belongs to user
-> index by user_id
-> retrieve by user_id
```

但真实业务中资源可能属于或绑定到 group / workspace / organization 语境。用户对资源的可见性可能来自：

- 资源 owner 身份。
- 指定用户授权。
- 组资源绑定。
- 用户在组内的角色。
- 组 ACL 对角色授予的动作。
- 覆盖授权或资源状态约束。

因此 RAG 后续必须拆开以下概念：

```text
资源身份 resource identity
内容版本 content/index version
资源归属 owner / group binding
权限投影 ACL projection
当前用户可检索范围 retrieval scope
```

不能继续把 `user_id` 同时当作资源归属、索引归属和检索可见范围。

## 设计原则

1. **Java 权限模型是事实来源**

   Java resource-service 负责定义资源权限语义，包括 owner、group ACL、指定用户授权、覆盖授权等。

2. **Python 不做运行时 RPC 依赖**

   RAG 检索不能依赖调用 Java 服务做权限判断，否则会引入跨服务运行时耦合、延迟放大和不可用传播。

3. **权限通过本地投影落地**

   Java 资源事件驱动 Python 本地 ACL projection 更新。RAG 检索时只读本地 projection。

4. **检索范围对齐 Java ES**

   如果 Java ES 的资源范围查询没有明显 bug，RAG 的候选资源范围应与其保持一致。

5. **RAG 使用 VIEW 级别**

   Java 搜索可能使用 `DISCOVER` 做资源发现，但 RAG 会把正文提供给模型，因此应至少要求 `VIEW`。

6. **ACL 更新不重建内容索引**

   ACL 变化只更新 manifest / payload / ES doc 中的权限投影，不重新分块、不重新 embedding。

## Java 权限模型摘要

需要对齐的核心字段包括：

- `ownerId`
- `groupBinds`
- `computedGroupAcls`
- `overrideGrantedActionsMask`
- `specifiedUsersGrantedActionsMask`
- 当前用户的 `groupRoleMap`
- 资源状态字段，例如删除、物理销毁、回收站或标签硬约束

Java ES 当前资源搜索范围大致基于：

- owner 匹配
- specified user grant
- computed group ACL + user group role
- 资源状态过滤

RAG 应复用这一套可见范围语义，但权限动作从 `DISCOVER` 提升为 `VIEW`。

## RAG 本地 ACL Projection

建议在 RAG 侧维护一个最小权限投影对象：

```text
RagAclProjection
- resource_id
- resource_kind
- owner_id
- acl_version
- is_deleted
- is_trashed
- is_physically_destroyed
- specified_users_granted_actions_mask
- override_granted_actions_mask
- computed_group_acls
  - group_id
  - base_mask
  - user_masks
- group_binds
  - group_id
  - tag_ids
```

其中 `computed_group_acls` 应与 Java resource-service 的 `ComputedGroupAcl` 对齐：

- `base_mask` 表示该 group 下默认下发给普通成员的资源动作掩码。
- `user_masks` 表示该 group 下指定用户的例外掩码。
- 小组 `OWNER` / `ADMIN` 不是写入 `computedGroupAcls` 的角色掩码，而是在查询时根据当前用户 `groupRoleMap` 短路为 `ALL_ACTIONS`。
- 个人空间 group 以 `personal_` 前缀表示，不参与组 ACL 计算，仍按 owner 隔离。

如果 Java 端已有稳定的 ACL projection DTO，Python 应直接按该 DTO 做事件反序列化，避免自行发明字段含义。

## 本地权限计算

RAG 检索时应使用本地 evaluator：

```text
can_view(user_id, user_group_role_map, acl_projection) -> bool
```

判断顺序建议与 Java 保持一致：

1. 资源已删除、物理销毁、不可见状态，直接不可见。
2. owner 拥有 VIEW。
3. specified user grant 命中时，按该用户的 mask 判断是否包含 VIEW。
4. 当前用户在资源绑定的 group 中是 `OWNER` / `ADMIN` 时，拥有 `ALL_ACTIONS`。
5. 当前用户是普通 group member 时，按 `computedGroupAcls[groupId].userMasks[userId]` 或 `baseMask` 累加动作。
6. `overrideGrantedActionsMask` 对普通组授权结果的覆盖规则必须按 Java 端最终确认语义处理。
6. 都不满足则不可见。

这里的关键点是：Python 实现的是 Java 权限语义的本地等价计算，不是自己设计一套新权限。

注意：Java 端当前存在两条权限路径：

- `getResourceInfo` 使用已经预计算的 `computedGroupAcls`。
- `checkPermission` 会重新按 tag 树解析权限。

两者大体一致，但覆盖规则的落点存在细微差异。RAG 落地前应以 Java 端确认后的权威语义为准，并用同一组测试用例锁定。

## 检索范围对齐方式

当前 `RagIndexScope` 是：

```text
user_id
resource_kind
resource_id
index_version
```

后续应改成由本地权限投影解析：

```text
resolve_visible_scopes(current_user, resource_kinds, required_action=VIEW)
```

返回当前用户有 VIEW 权限的资源版本集合。

之后 Qdrant / ES / Mongo 仍然按 scope 过滤，只是 scope 来源从 owner-only 变为 ACL projection。

其中组资源必须是一等公民：

- 用户不拥有该资源，也可能因 group role 获得 VIEW。
- group ACL 变化时，资源内容和 embedding 不变，只更新权限投影。
- 用户离开 group 或角色变化后，应立即从 RAG 检索范围中消失。
- 组资源不能复制成每个用户一份个人索引，否则 ACL 变更和撤权会变得不可控。

组资源还必须保留 Java 端的硬 tag 约束。代码中没有直接使用 `hard tag` 这个字段名，但对应业务语义主要落在：

- `TagEntity.isPath=true` 的路径标签。
- `groupBinds[groupId].tagIds[0]` 首标，即资源在该 group 下的主路径/硬边界。
- `FileOrganizationLogic.FOLDER` 下同一 group 资源至多挂载一个标签。
- `FileOrganizationLogic.TAG` 下允许更自由的标签组织，但仍通过 `groupBinds` 表达资源属于哪些 group/tag。
- `.Trash` 和 `/` 是系统路径节点，影响资源状态和权限剥离。

因此 RAG ACL projection 不能只保存“用户可见列表”，还要保存 `group_binds.tag_ids`，尤其是首标。首标变化、路径 tag 移动、路径 tag 删除、回收站移动，都可能改变 RAG 的可检索范围。

## 索引 payload 对齐

建议写入以下位置：

1. Manifest
   - 保存资源当前 index_version 和 ACL projection 摘要。

2. Mongo chunk records
   - 保存 chunk 原文与 ACL projection 摘要，便于最终 evidence assembly 前做防御性校验。

3. Qdrant payload
   - 保存可过滤 ACL 字段或 scope 标识。
   - 用于向量召回阶段减少越权候选。

4. Elasticsearch document
   - 保存可过滤 ACL 字段或 scope 标识。
   - 对齐 Java ES 查询范围。

## 事件监听

建议监听 Java 资源侧事件：

1. ACL recalculation event
   - 更新本地 ACL projection。
   - 更新 manifest / Mongo / Qdrant / ES 的权限 metadata。
   - 不重建 chunk / embedding。

2. Physical destroy event
   - 删除 RAG manifest。
   - 删除 Mongo chunks。
   - 删除 Qdrant points。
   - 删除 ES docs。

3. Resource state event
   - 例如 trash、delete、restore、tag hard constraint。
   - 更新本地 projection 或资源状态字段。

4. Resource content update event
   - 内容变化才触发重新索引。

## 与 Java ES 的关系

如果 Java ES 范围实现没有明显问题：

- RAG 的 scope resolver 应复刻 Java ES 的资源范围逻辑。
- RAG 使用 `VIEW` 动作，而不是 `DISCOVER`。
- 如果 Java ES 当前写入 projection 存在字段结构不一致，例如 map/list projection 不匹配，需要先记录并确认，不能盲目复制 bug。

当前 Java ES 的 `ESIndexEntity` 投影是为 `DISCOVER` 检索设计的：

```text
ownerId
specifiedDiscoverUsers
computedGroupAcls[]
  - groupId
  - isDiscover
  - specifiedUsers
```

RAG 需要同构但面向 `VIEW` 的投影，不能直接复用 `specifiedDiscoverUsers` / `isDiscover` 作为正文可读权限。

## 测试矩阵

至少覆盖：

- owner 有 VIEW，可以检索。
- 非 owner 无授权，不可检索。
- specified user 有 VIEW，可以检索。
- specified user revoke 后不可检索。
- group role 有 VIEW，可以检索。
- group role 只有 DISCOVER，无 VIEW，不可检索。
- group ACL revoke 后不可检索。
- resource trashed / deleted 后不可检索。
- physical destroy 后所有 RAG 索引被删除。
- ACL 更新不触发 embedding 重建。
- 内容更新触发新 index_version。

## 分阶段落地建议

### 第一阶段：设计与投影

- 确认 Java ACL projection DTO。
- 定义 Python 本地 projection schema。
- 实现本地 `can_view` evaluator。
- 加 evaluator 单元测试。

### 第二阶段：索引侧写入

- Manifest 写入 ACL projection。
- Mongo / Qdrant / ES 写入 ACL metadata。
- 支持 ACL metadata 局部更新。

### 第三阶段：检索范围对齐

- 将 owner-only manifest resolver 改为 ACL projection scope resolver。
- Qdrant / ES / Mongo 使用新的 scope filter。
- evidence assembly 前增加本地权限防线。

### 第四阶段：事件与回填

- 接 ACL recalculation event。
- 接 physical destroy event。
- 对已有 RAG 索引做 ACL projection backfill。

## 明确不做

- 不在 RAG 检索时调用 Java resource-service。
- 不用 DISCOVER 代替 VIEW。
- 不因为权限投影更新而重新 embedding。
- 不在 Python 侧重新发明一套和 Java 不一致的权限规则。
