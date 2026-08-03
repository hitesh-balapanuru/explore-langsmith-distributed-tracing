# Nimbus Notes — Product Overview

Nimbus Notes is a fictional note-taking application used as the sample knowledge
base for this repository. It exists only so the RAG agent has something
non-trivial to retrieve and answer questions about.

## Core Concepts

- **Workspace**: the top-level container for a user's notes. Every account
  starts with one default workspace called "Personal".
- **Note**: a single markdown document. Notes belong to exactly one workspace
  and can be tagged with any number of labels.
- **Collection**: a saved filter (e.g. "tag:project-x") that behaves like a
  virtual folder. Collections do not move notes, they only filter the view.
- **Sync Engine**: Nimbus Notes syncs changes every 15 seconds when online,
  and queues changes locally when offline using an append-only operation log.

## Pricing Tiers

| Tier | Workspaces | Storage | Price |
|------|-----------|---------|-------|
| Free | 1 | 500 MB | $0 |
| Plus | 5 | 10 GB | $6/month |
| Team | Unlimited | 100 GB pooled | $12/user/month |

## Supported Platforms

Nimbus Notes ships clients for macOS, Windows, Linux, iOS, Android, and a web
app. All clients share the same sync protocol described in
`architecture-notes.md`.
