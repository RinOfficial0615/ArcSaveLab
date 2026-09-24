# ArcSaveLab contributor guide

- Read `CONTEXT.md` for save workspace, draft, verified-file and recovery terminology.

- Read `docs/architecture.md` before changing session lifecycle, UI state, transactions or restoration.
- Read `docs/formats.md` before modifying persisted fields, digests or version profiles.
- Keep edits in memory until an explicit commit. Preserve failed drafts and rollback partial operations.
- Use item IDs, not translated labels or row numbers, for selection and change history.
- Generate test files in temporary directories. `.examples/`, `.old_ver/` and `.local/` remain local-only.
- Runtime paths come from user input or package resources; keep developer installation paths out of code.
- Run the README Development commands through isolated wheel smoke before claiming a release-ready build.
- When available, prefer FastCtx `inspect_local_file`, `grep`, `glob` for file inspection;
  use its `replace` for mechanical replacements and `run` with non-interactive POSIX bash for CLI work.
