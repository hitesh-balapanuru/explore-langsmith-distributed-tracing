# Nimbus Notes — Frequently Asked Questions

**Q: How do I move a note between workspaces?**
A: Open the note, click the workspace name in the header, and choose a
destination workspace from the dropdown. Moving a note preserves its tags but
removes it from any collections defined only in the source workspace.

**Q: Can I recover a deleted note?**
A: Yes. Deleted notes go to the Trash and are kept for 30 days before being
permanently purged. Team-tier accounts can extend this to 90 days in
workspace settings.

**Q: Does Nimbus Notes support end-to-end encryption?**
A: Notes are encrypted at rest and in transit. Full end-to-end encryption
(where Nimbus cannot read note contents) is available only on the Team tier
and must be enabled per-workspace before any notes are created in it — it
cannot be turned on retroactively for existing notes.

**Q: What happens if I exceed my storage limit?**
A: Sync pauses for new content, but you can still read and edit existing
notes offline. Nothing is deleted automatically.

**Q: Is there an API?**
A: Yes, a REST API is available on Plus and Team tiers. See
`architecture-notes.md` for the sync protocol the API is built on.
