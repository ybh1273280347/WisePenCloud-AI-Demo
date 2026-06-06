# Model Architecture Principles

This document defines where dataclasses, DTOs, enums, and helper types should live.
The goal is to keep model placement tied to ownership and flow boundaries, not just
to the number of import sites.

## Core Rules

1. Put a model where its semantic owner first creates or owns it.
   If a pipeline stage creates a value and later stages only carry it as input, the
   model belongs to that stage or that stage's local `models.py`.

2. Do not promote a model only because multiple files import it.
   Cross-file usage may be only type hints or input/output plumbing. Promotion is
   justified only when the model is a stable protocol shared by multiple owners.

3. Keep interaction protocols at service boundaries.
   Request/result models used by a public service facade, API service, or tool
   entrypoint can live in the package-level `models.py`.

4. Keep runtime behavior under `runtime`.
   Runtime-only inputs, diagnostics, execution plans, provider calls, cache entries,
   and stage outputs should live near the runtime component that owns them.

5. Split enums from models.
   Enums belong in `enums.py` at the narrowest directory level where their semantic
   scope is shared. Do not mix `StrEnum` definitions into `models.py`.

6. Do not put helper functions in `models.py`.
   Builders, serializers, render helpers, validators, and factory functions belong
   next to the workflow that calls them. If a helper is used by one file, keep it
   private in that file.

## Placement Guide

- Single-file model:
  Define it inside that file.

- Stage-local cross-file model:
  Put it under that stage directory, for example `runtime/retrieval/stages/...`.

- Runtime subdomain protocol:
  Put it under that runtime subdomain, for example `runtime/retrieval/channels/models.py`
  or `runtime/indexing/models.py`.

- Package service protocol:
  Put it in package-level `models.py`, but only when it is part of the external
  service/tool interaction contract.

- Enum:
  Put it in the closest relevant `enums.py`.

- Provider/vendor schema:
  Put it under `schemas/` or the provider implementation that owns the mapping.

## Smells

- A `models.py` imports `StrEnum`.
- A `models.py` contains `build_*`, `parse_*`, `render_*`, or `serialize_*`.
- A model has only one real owner but is placed at package root.
- A field uses a raw `str` even though a local enum already represents that concept.
- A "final output" model exists but no service/tool/API returns it.
