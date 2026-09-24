# Contributing

Use Python 3.12+ and `uv sync --locked`. Run the commands in README's Development section
before submitting a change. CI exercises Windows, Linux and macOS; local results should name
the platform actually tested rather than claiming the whole matrix passed.

## Code boundaries

UI modules translate user input to public session operations. Keep XML, SQLite and backup
write logic in the core. A bug fix should add a synthetic regression test at the smallest
relevant seam; interaction fixes also need a Pilot test driven through actual widgets/keys.

Use stable IDs for cursor/history state, immutable DTOs at the public interface, parameterized
SQL and path objects. Validate coupled fields as one undoable operation. Keep UI error drafts
open, preserve a working session when opening new files fails, and reset batch marks on search.

## Data and publication hygiene

- Tests generate data in temporary directories. The optional `.examples/`, `.old_ver/` and
  `.local/` folders are local references only; never copy them into fixtures or packages.
- Runtime paths come from user arguments, selected files or package resources. Avoid drive
  letters, home directories, APK paths, or references to a sibling checkout in runtime code.
- Rebuild catalogs deterministically. Include metadata manifests; keep game media, story scripts
  and extracted translation messages out of the repository.
- Run the distribution allowlist check and isolated wheel smoke, not just editable-install tests.
- Do not publish logs/screenshots containing personal save values or account identifiers.

## UI validation

Use `App.run_test` and `Pilot`. Pause after events that mount screens. Textual buttons have
a short active animation; a keyboard submission can test an immediate validation retry.
Exercise compact layouts, modal cancellation, multi-field editing and failed commits.
Screenshot scripts must use synthetic data, not a user's save.
