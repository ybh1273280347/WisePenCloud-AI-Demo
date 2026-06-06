# Java Resource 权限模型调研报告

## 结论

Java resource-service 的资源权限模型不是 owner-only，而是以「资源 + 组资源绑定 + 标签继承 ACL + 资源级覆盖 + 指定用户授权」为核心。

RAG 当前最大问题是忽略了组资源这个业务核心概念。Python 后续必须对齐 Java 的权限语义，但不能在检索时依赖 Java 运行时服务。正确方向是：Java 权限模型作为事实来源，Python 本地维护 ACL projection，并在 RAG 检索时本地计算 `VIEW` 权限。

项目负责人提到的“组资源涉及硬 tag”，在当前代码里没有直接叫 `hard tag` 的字段，但实现上对应的是路径 tag / 首标约束：资源绑定到 group 时，`groupBinds.tagIds[0]` 是该 group 维度下的主路径标签，`TagEntity.isPath=true` 的路径节点和 `.Trash` / `/` 系统节点决定资源是否仍处于有效业务范围。

## 核心动作模型

`ResourceAction` 是 bitmask：

- `DISCOVER`：列表可见、搜索可发现。
- `VIEW`：在线阅读。
- `EDIT`：编辑。
- `DOWNLOAD_WATERMARK`：下载带水印版本。
- `DOWNLOAD_ORIGINAL`：下载原始版本。

动作存在隐含关系：

- `VIEW` 隐含 `DISCOVER`。
- `EDIT` 隐含 `VIEW` 和 `DISCOVER`。
- `DOWNLOAD_WATERMARK` 隐含 `VIEW` 和 `DISCOVER`。
- `DOWNLOAD_ORIGINAL` 隐含 `DOWNLOAD_WATERMARK`、`VIEW`、`DISCOVER`。

默认普通组成员权限是：

```text
DISCOVER | VIEW | DOWNLOAD_WATERMARK
```

这意味着 Java 的 ES 搜索使用 `DISCOVER` 是合理的资源发现语义，但 RAG 会暴露正文，应使用 `VIEW` 作为最低权限。

## 资源权限字段

`ResourceItemEntity` 中与权限直接相关的字段：

- `ownerId`：资源所有者。
- `groupBinds`：资源绑定到哪些 group/tag。
- `computedGroupAcls`：按 group 预计算后的权限。
- `overrideGrantedActionsMask`：资源级覆盖动作。
- `specifiedUsersGrantedActionsMask`：资源级指定用户动作。
- `deletedAt`：删除状态相关字段。

`groupBinds` 的结构是：

```text
groupId
tagIds
tags
```

其中 `tagIds[0]` 是首标/主路径标签。ACL 计算主要依赖首标，并向上继承祖先 tag 的权限配置。

这个首标就是 RAG 对齐时必须关注的硬边界：它不只是展示分类，也会决定资源属于哪个 group/path 范围、能否被挂载、是否因为路径被删除或进入回收站而从可检索范围移除。

`computedGroupAcls` 的结构是：

```text
groupId -> {
  baseMask,
  userMasks
}
```

含义：

- `baseMask` 是当前 group 下普通成员默认获得的动作。
- `userMasks[userId]` 是当前 group 下某个用户的例外动作。
- group `OWNER` / `ADMIN` 不写进 `computedGroupAcls`，查询时根据用户 `groupRoleMap` 直接短路为 `ALL_ACTIONS`。

## 标签权限继承

Tag 上有三组权限字段：

- `taggedResourceAclGrantScope`：该标签下资源 ACL 下发范围。
- `taggedResourceAclGrantSpecifiedUsers`：白名单/黑名单用户。
- `taggedResourceGrantedActionsMask`：该标签授予的资源动作。
- `tagMountPermissionScope`：成员是否可把资源挂载到这个标签。
- `tagMountSpecifiedUsers`：挂载权限的白名单/黑名单。

`AccessControlScope` 支持：

- `ALL`
- `ONLY_ADMIN`
- `WHITELIST`
- `BLACKLIST`

解析规则是从当前 tag 开始，如果某个维度没有配置，则沿祖先向上找最近的非空配置。资源动作未配置时，回退到 group 默认成员权限；ACL 下发范围未配置时，回退为 `ALL`。

## 组资源与硬 tag 约束

从代码看，组资源不是“资源上挂了一个 groupId”这么简单，而是由 `groupBinds` 和 tag 树共同定义：

- 每个 `GroupTagBind` 表示资源在一个 group 下的绑定。
- `tagIds[0]` 是首标，ACL 计算取它作为主入口。
- `TagEntity.isPath=true` 表示 FOLDER/路径节点，路径节点承载资源的硬位置。
- `TagEntity.isPath=false` 表示普通标签。
- `TagServiceImpl` 禁止路径 tag 和普通 tag 跨类型移动。
- 个人空间会自动初始化 `/` 和 `.Trash` 两个系统路径节点。
- `.Trash` 内节点禁止继续操作；资源进入回收站或路径节点删除会影响资源有效性。

几个关键业务规则：

- 个人资源必须绑定且只能绑定一个路径节点。
- 个人资源的路径 tag 必须放在 `tagIds[0]`。
- group 的 `FOLDER` 模式下，同一 group 内每个资源至多挂载一个标签。
- group 的 `TAG` 模式更自由，但资源归属仍通过 `groupBinds` 表达。
- 普通成员挂载资源到 tag 前，需要通过 `tagMountPermissionScope` 校验。

删除和回收站规则：

- path tag 被彻底删除时，挂在其上的资源会被软删除并移入 `wisepen_resource_trash`。
- tag 被移入个人 `.Trash` 时，会触发 `TagTrashedEvent`，资源侧执行 `stripGroupPermission`，剥离非个人 group 绑定、override、specified user 和 computed ACL。
- 非 path 普通 tag 删除时，会从资源 `groupBinds.tagIds` 中移除该 tag；如果某个 group 下 tag 清空，则移除该 group bind，并触发 ACL 重算。

因此，对 RAG 来说，硬 tag 是检索范围的一部分。只同步 ACL mask 不够，还必须同步 `groupBinds`、首标、路径/回收站状态，否则会出现“权限 mask 看似可读，但资源已经不在有效 group/path 范围内”的问题。

## ACL 重算机制

资源 ACL 重算入口是 `calculateResourceGroupAcl(resourceId)`：

1. 读取资源的 `groupBinds`。
2. 跳过个人空间 group。
3. 对每个 group 取首标。
4. 从首标向上解析 ACL 下发范围和动作 mask。
5. 如果资源有 `overrideGrantedActionsMask`，用它作为有效下发 mask。
6. 根据 `ALL` / `ONLY_ADMIN` / `WHITELIST` / `BLACKLIST` 生成 `ComputedGroupAcl`。
7. 写回 Mongo 的 `computedGroupAcls`。

触发 ACL 重算的事件包括：

- 资源 group tags 变化：`RESOURCE_TAGS_CHANGED`。
- 资源动作权限变化：`RESOURCE_ACTION_PERMISSION_CHANGED`。
- tag 权限或结构变化：`TAG_CHANGED`。
- tag 删除：`TAG_DELETED`。
- group 默认权限变化：`GROUP_DEFAULT_MASK_CHANGED`。
- group 权限被剥离：`STRIP_GROUP_PERMISSION`。

Kafka topic：

- `wisepen-resource-acl-recalc-topic`
- 消费组：`wisepen-resource-acl-recalc-group`

消费后会先重算 Mongo 的 `computedGroupAcls`，再同步 ES ACL 字段。

## 资源详情权限判断

`getResourceInfo` 的权限判断流程：

1. owner 直接拥有 `ALL_ACTIONS`。
2. 非 owner 若命中 `specifiedUsersGrantedActionsMask[userId]`，先取该 mask。
3. 否则遍历 `computedGroupAcls`：
   - 当前用户不在该 group，跳过。
   - group `OWNER` / `ADMIN` 直接 `ALL_ACTIONS`。
   - 普通 member 使用 `userMasks[userId]`，没有则使用 `baseMask`，多 group 按位或。
4. 如果当前 mask 非 0 且存在 `overrideGrantedActionsMask`，将 mask 覆盖为 override。
5. 最终必须包含 `VIEW`，否则拒绝。

这里有一个需要 Java 端确认的语义点：字段注释说 `specifiedUsersGrantedActionsMask` 是“直接返回、忽略其他规则”，但 `getResourceInfo` 代码在指定用户 mask 之后仍可能被 `overrideGrantedActionsMask` 覆盖。另一个 `checkPermission` 路径则在最后让指定用户 mask 覆盖组策略和 override。两条路径存在细微不一致，RAG 对齐前应确定一个权威语义。

## 内部硬鉴权接口

`/internal/resource/checkResPermission` 会调用 `checkPermission`。

它不依赖预计算的 `computedGroupAcls`，而是重新根据资源 `groupBinds`、当前用户 `groupRoles`、tag 继承配置和 group 默认权限计算权限。

这条路径更适合下游服务做强鉴权，但 RAG 不能在检索时调用它。RAG 应把它当成 Java 权威语义的参考，用本地 projection 和测试复刻结果。

## Java ES 检索范围

Java ES 索引字段中与 ACL 相关的是：

```text
ownerId
specifiedDiscoverUsers
computedGroupAcls[]
  - groupId
  - isDiscover
  - specifiedUsers
```

`globalSearch` 的 ACL filter：

1. owner 命中。
2. `specifiedDiscoverUsers` 命中。
3. 当前用户在 group 中：
   - group `OWNER` / `ADMIN` 只要 groupId 命中即可。
   - 普通 member 且 `isDiscover=true`，并且不在 `specifiedUsers` 黑名单中。
   - 普通 member 且 `isDiscover=false`，但在 `specifiedUsers` 白名单中。

这个范围是 `DISCOVER` 范围，适合“资源可发现/搜索列表”。RAG 应对齐其结构，但动作要改成 `VIEW`。

## 已发现的实现风险

1. `getResourceInfo` 与 `checkPermission` 在 `specifiedUsersGrantedActionsMask` 和 `overrideGrantedActionsMask` 的优先级上疑似不一致。

2. `SearchSyncServiceImpl` 写 ES ACL 时传入的是 `new ESIndexEntity(entity)` 后的 `entity.getComputedGroupAcls()`，从类型上看应该是 `List<ComputedGroupAclProjection>`。这一点目前与 ESIndexEntity 的 projection 结构一致，但字段名与 Mongo 原字段同名，容易误读为直接写 Mongo map。

3. Java ES 只投影 `DISCOVER`，不能被 RAG 直接复用为正文可读范围。

4. 当前 RAG owner-only 会漏召回合法组资源，也可能在未来错误扩展时绕过撤权逻辑。必须引入组资源 ACL projection。

## 对 RAG 的对齐建议

RAG 本地投影应至少包含：

```text
resource_id
owner_id
group_binds[]
  - group_id
  - tag_ids
  - primary_tag_id
  - primary_tag_is_path
  - in_trash
computed_group_acls
  group_id -> {
    base_mask,
    user_masks
  }
override_granted_actions_mask
specified_users_granted_actions_mask
deleted / trashed / physically_destroyed 状态
acl_version / update_time
```

RAG 本地 evaluator：

```text
can_view(user_id, group_role_map, projection) -> bool
```

判断必须使用 Java 的 bitmask 和角色语义：

- owner 可读。
- 指定用户 mask 包含 `VIEW` 可读。
- group `OWNER` / `ADMIN` 可读。
- 普通 member 按 `userMasks` 或 `baseMask` 判断 `VIEW`。
- override 优先级按 Java 最终确认语义实现。
- 删除、回收站、物理销毁状态必须拒绝。

事件侧：

- ACL 变化只更新 manifest / Mongo chunk / Qdrant payload / ES doc 的 ACL metadata，不重建 embedding。
- 内容变化才重新分块和 embedding。
- 物理销毁事件删除所有 RAG 索引。

## 下一步

1. 与 Java 端确认 `specifiedUsersGrantedActionsMask` 与 `overrideGrantedActionsMask` 的最终优先级。
2. 定义 Python RAG ACL projection schema，字段名尽量贴近 Java。
3. 实现本地 `can_view` evaluator 和测试矩阵。
4. 将当前 owner-only `RagIndexScope` 改成基于本地 projection 的可见资源解析。
5. 为 Qdrant / ES / Mongo payload 增加 `VIEW` 维度的 ACL filter。
6. 接入 ACL recalculation / physical destroy 事件或等价本地同步事件。
7. 把硬 tag 相关事件纳入 projection 更新：path tag 移动、path tag 删除、普通 tag 删除、进入回收站、group FOLDER/TAG 模式变化。
