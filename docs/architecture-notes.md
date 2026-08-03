# Nimbus Notes — Sync Architecture Notes

This document describes the (fictional) internal sync protocol, included so
the sample agent has some "engineering-flavored" content to retrieve in
addition to product/FAQ content.

## Operation Log

Every client mutation (create, edit, delete, move, tag) is appended to a
local operation log before being applied to the local note store. The sync
engine ships this log to the server in batches every 15 seconds.

Each operation has:
- `op_id` — a client-generated ULID, used for idempotent replay
- `workspace_id`
- `vector_clock` — per-device logical clock used for conflict resolution
- `payload` — the actual diff (a JSON patch against the note's markdown)

## Conflict Resolution

Nimbus Notes uses last-writer-wins at the paragraph level, not the whole-note
level. When two devices edit different paragraphs of the same note offline,
both edits are kept. When two devices edit the *same* paragraph, the edit
with the higher vector clock timestamp wins, and the losing edit is stored in
a shadow history accessible from "Note History" in the client.

## Why This Matters for Distributed Tracing (meta note)

This repository is not really about Nimbus Notes — it's a vehicle for
exploring distributed tracing with LangSmith. The op-log / vector-clock
design above is a deliberate parallel to how this repo's own tracing works:
a request that starts in the TypeScript frontend and continues in a Python
agent is conceptually the same problem as a note edit that starts on one
device and continues on another — you need a shared identifier (a trace ID,
or a vector clock) to know the two events belong to the same causal chain.
