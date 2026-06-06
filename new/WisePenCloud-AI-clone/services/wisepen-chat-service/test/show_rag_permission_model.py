from chat.application.rag.permissions import (
    RagAclProjection,
    RagComputedGroupAclProjection,
    RagGroupBindProjection,
    RagGroupRole,
    RagResourceAction,
    build_owner_acl_projection,
    can_view,
)
from chat.application.rag.runtime.models import RagIndexManifest
from chat.application.rag.runtime.persistence.entities.manifest_documents import (
    build_manifest_acl_index_fields,
)
from chat.application.rag.enums import ResourceKind


def mask(*actions: RagResourceAction) -> int:
    value = 0
    for action in actions:
        value |= action.value
    return value


def show(name: str, allowed: bool) -> None:
    print(f"{name}: {'可读' if allowed else '不可读'}")


owner_projection = build_owner_acl_projection("u_owner")
show(
    "owner 默认可读",
    can_view(user_id="u_owner", group_role_map={}, projection=owner_projection),
)
show(
    "非 owner 默认不可读",
    can_view(user_id="u_other", group_role_map={}, projection=owner_projection),
)

group_projection = RagAclProjection(
    owner_id="u_owner",
    group_binds=[
        RagGroupBindProjection(
            group_id="g_1",
            tag_ids=["path_tag", "normal_tag"],
            primary_tag_id="path_tag",
            primary_tag_is_path=True,
        )
    ],
    computed_group_acls={
        "g_1": RagComputedGroupAclProjection(
            base_mask=mask(RagResourceAction.DISCOVER, RagResourceAction.VIEW),
            user_masks={
                "u_blocked": mask(RagResourceAction.DISCOVER),
                "u_special": mask(RagResourceAction.VIEW),
            },
        )
    },
)

show(
    "组 member 继承 baseMask VIEW",
    can_view(
        user_id="u_member",
        group_role_map={"g_1": RagGroupRole.MEMBER},
        projection=group_projection,
    ),
)
show(
    "组 member userMasks 黑名单只有 DISCOVER",
    can_view(
        user_id="u_blocked",
        group_role_map={"g_1": RagGroupRole.MEMBER},
        projection=group_projection,
    ),
)
show(
    "组 admin 短路全权限",
    can_view(
        user_id="u_admin",
        group_role_map={"g_1": RagGroupRole.ADMIN},
        projection=group_projection,
    ),
)

specified_projection = RagAclProjection(
    owner_id="u_owner",
    specified_users_granted_actions_mask={
        "u_reader": mask(RagResourceAction.VIEW),
        "u_discover_only": mask(RagResourceAction.DISCOVER),
    },
)
show(
    "资源级指定用户有 VIEW",
    can_view(user_id="u_reader", group_role_map={}, projection=specified_projection),
)
show(
    "资源级指定用户只有 DISCOVER",
    can_view(
        user_id="u_discover_only",
        group_role_map={},
        projection=specified_projection,
    ),
)

trash_projection = RagAclProjection(
    owner_id="u_owner",
    is_trashed=True,
)
show(
    "进入 trash 后 owner 也不可被 RAG 检索",
    can_view(user_id="u_owner", group_role_map={}, projection=trash_projection),
)

manifest = RagIndexManifest(
    user_id="u_owner",
    resource_kind=ResourceKind.DOCUMENT,
    resource_id="res_1",
    resource_version=1,
    material_hash="material",
    pipeline_version="pipeline",
    current_index_version="index",
    acl_projection=group_projection,
)
index_fields = build_manifest_acl_index_fields(manifest)
print("manifest owner_id:", index_fields["owner_id"])
print("manifest group_ids:", index_fields["group_ids"])
print("manifest admin_group_ids:", index_fields["admin_group_ids"])
print("manifest member_view_group_ids:", index_fields["member_view_group_ids"])
print("manifest member_view_user_ids:", index_fields["member_view_user_ids"])
print("manifest denied_user_ids:", index_fields["denied_user_ids"])
