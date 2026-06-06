# RAG Permission Alignment TODO

## Current Gap

RAG retrieval is currently owner-scoped:

- Manifest resolution uses `user_id`.
- Mongo/Qdrant/Elasticsearch filters use `user_id/resource_kind/resource_id/index_version`.
- Group resources, tag ACL, resource-level overrides, and specified-user grants from `wisepen-resource-service` are not represented.

This is safe for personal-only resources, but it does not match the Java resource service permission model.

## Java Resource Permission Model To Align With

Resource service computes access from:

- `ownerId`
- `groupBinds`
- `computedGroupAcls`
- `overrideGrantedActionsMask`
- `specifiedUsersGrantedActionsMask`
- current user's `groupRoleMap`

Resource search currently uses `DISCOVER` visibility. RAG should use `VIEW` as the minimum permission because RAG exposes source content to the model.

## Architecture Constraint

Python chat-service must align with the Java resource permission semantics, but it must not depend on Java resource-service at retrieval runtime. In a microservice architecture, RAG authorization needs a local, queryable permission projection that is fed by resource lifecycle and ACL events.

## Short-Term Plan

1. Define the local RAG ACL projection fields from the Java resource model.
2. Backfill ACL projection into RAG manifests and physical index payloads.
3. Use the local projection to resolve `RagIndexScope` and enforce `VIEW` visibility during retrieval.
4. Add a final local permission gate before evidence assembly as defense in depth.
5. Log dropped evidence counts by reason.
6. If all evidence is dropped, return the existing insufficient evidence response.

This avoids runtime coupling to Java resource-service while keeping RAG behavior aligned with the Java permission model.

## Long-Term Plan

1. Store ACL projection in RAG manifest and physical index payloads.
2. Use a `VIEW`-based projection, not Java search's current `DISCOVER` projection.
3. Resolve `RagIndexScope` from owner resources plus group/shared resources visible to the current user.
4. Listen to `wisepen-resource-acl-recalc-topic` and update only ACL metadata when permissions change.
5. Listen to `wisepen-resource-physical-destroy-topic` and remove RAG manifests/chunks/points/docs.
6. Add cross-user, group-share, revoke, tag-trash, and resource-delete regression tests.

## Open Decisions

- Confirm RAG minimum permission: recommended `VIEW`.
- Confirm exact ACL projection schema and event payload contract.
- Confirm backfill strategy for existing indexed resources.
- Confirm content ingestion path for group-shared note/document resources.
