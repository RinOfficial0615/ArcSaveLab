# Architecture and rewrite decisions

## Keep the proven seams; replace the interaction model

The 0.2 rewrite retains the lossless XML token adapters, pure integrity algorithms,
schema-aware SQLite adapter, catalog domain model, and the tested session operations.
These are format knowledge, not UI design. They were revalidated and hardened rather
than reimplemented from guesses.

The old TUI was replaced entirely. The new implementation has separate workspace,
edit, open/browse, review, and backup screens. It does not import the old checkout.
The unused standalone capability matrix was removed; `SaveSession` is the single
authority for available operations. The catalog builder excludes the complete game message
catalog, retaining only official Partner names and skill descriptions as entity metadata.

| Module | Responsibility |
| --- | --- |
| `interface.py` | Typed public commands, immutable views and receipts |
| `session.py` | Domain operations, revisions, history, integrity dependencies and preview |
| `workspace.py` | Active session replacement, current identity, verified receipts and suspended drafts |
| `formats/` | Lossless XML and schema-preserving SQLite snapshots |
| `integrity.py`, `verification.py` | Pure digest calculations, shared policy, repair planning and verification |
| `transaction.py`, `_journal.py` | Complete write-set locks, journal contract, writes and restoration |
| `backups.py` | Compatible imports for backup operations owned by the transaction module |
| `catalog.py`, `catalog_builder.py`, `domain.py` | Versioned normalized game metadata |
| `sources.py` | Portable filename hints and content detection |
| `ui/` | Textual screens, formatting, styling and keyboard/mouse interaction |
| `adapters/` | Console argument parsing and entry points |

## Interaction state

```text
Welcome → Open/browse → Workspace → Edit draft → Stage → Review → Save
                           ↑          │                  │
                           └── Esc ───┘                  └── Cancel
                           ├── Undo / Redo
                           └── Backup history → Confirm → Restore
```

The cursor stores an item ID, never a DataTable row number. Searches and category
changes clear batch marks. A redraw retains a selected ID only if it still exists.
The list queries the actual category size, so searches and selection work past row 100.
Each form has private widgets and an expected revision. A failed operation restores
the whole snapshot; the form remains open with the input and an error message.
Multi-row forms default every field to disabled, preventing unrelated values from
the first item from being applied to the rest.

Single-click selects; double-click and Enter open an explained detail/edit form. Read-only
records remain inspectable without enabling mutation. Long descriptions and the device-identity
guide scroll independently of the dialog actions, including at 80×24. Identity forms prefill
the current session inputs (masked device ID), retaining them when the operation fails.
Mouse selection does not pre-scroll the old cursor. Header separators resize columns without
rebuilding rows; widths are retained for the current app session, separately for general and
score tables. `ItemTable` isolates Textual's internal width/cache invalidation behind mouse tests.
Boolean values retain green/red text even under selection; missing context/source uses amber.
Account browse views remain redacted. Only explicit account detail inspection returns a local,
read-only value; closing that dialog does not put the value into the list or change the draft.
Finale details show raw `fin_v` and zero-based split entries without assuming gameplay semantics.
The compact overview shows connected components, draft state and diagnostic counts rather
than interpreting a successful file check as game-runtime validation.

Review changes are merged by stable item ID plus field, not translated labels. Distinct
unlock records and score control types remain distinguishable. The preview token binds
the revision and rendered file hashes. Saving recomputes it and rejects a stale token.
Successful saves increment the revision, clear history and refresh diagnostics.

Workspace lifecycle owns open, save and restoration outcomes; Textual presents them.
Opening a replacement first validates a candidate session before discarding the old one.
Current source paths and device identity come from the active session, including after export.
If a verified write is followed by a reload failure, `ReloadRequired` retains the verified
receipt. The old draft is preserved but suspended: borrowed session references also reject
edits and previews. The UI disables saving and requests reopening instead of announcing
workspace success. A successful explicit reopen retires the suspended draft; discarding
uncommitted changes still requires confirmation.

The verifier, session diagnostics and repair preview share pure integrity evaluation.
Only explicitly selected map documents participate in session evaluation; CLI path discovery
remains separate. A missing computable digest blocks ordinary editing until explicit repair.
Unchanged sources retain their original digest bytes, including uppercase spelling. Empty
device-bound values retain the empty sentinel without requiring identity; nonempty values
still require identity when recalculation is requested.

## Transactions

Edits are in-memory. Ordinary no-op previews preserve input bytes, including valid
uppercase digests. Only changed digest sources (or explicit repair) are regenerated.
An in-place commit requires all files to share a directory and requires backups.
Export reserves a new directory; it never silently overwrites an existing export.

1. Validate unique destinations and compare source fingerprints.
2. Acquire exclusive per-source ArcSaveLab lock files.
3. Write backups, stage all outputs, flush file contents, persist the journal.
4. Recheck sources; replace hash-bearing preferences last.
5. Verify output hashes and persist `committed`; clean staging files and locks.
6. On an ordinary failure, restore every replaced destination and retain the journal.

SQLite output is built from a shadow snapshot and must pass `PRAGMA quick_check`.
Non-empty WAL or rollback journal sidecars cause rejection, including if they appear
between opening and saving. Database URI paths are escaped, and duplicate chart keys
are rejected rather than collapsed in a dictionary.

Opening a directory with an **unlocked**, unfinished in-place transaction attempts
rollback before loading it. Recovery validates every path and backup hash before writing;
paths must remain inside the original save/backup directories. If another writer changed
a recovery target to an unrecognized hash, recovery stops without overwriting that data.
Backup restore uses the same coordinator and first backs up the current files.

All three write paths acquire exclusive locks for the **complete journal file set** and
hold them through the final journal update. Recovery first discovers the complete set,
then acquires its locks and checks the manifest has not changed before writing.
The shared journal contract rejects empty/malformed artifacts, duplicate identities,
invalid hashes and redirected paths. Backup browsing omits malformed manifests; write
and automatic recovery paths fail closed. Corrupted manifests in a save's backup directory
must be inspected rather than silently skipped during opening.

These are recoverable multi-file transactions, not a filesystem-wide atomic operation.
The lock files coordinate ArcSaveLab processes, not the game or every external program.
Use closed, quiescent save copies. Power-loss behavior depends on the filesystem and OS;
individual file `fsync` does not promise atomic durability of an entire directory.

### A stale lock after a crash

A lock is named `.<save-filename>.arcsavelab-lock` next to its source. Once every writer
is stopped, preserve a copy of the directory (including its backups), then remove only
those stale lock files. Reopening the save runs journal recovery. Do not remove a live
writer's lock. If a journal reports `manual_required`, keep the journal and backup files
and inspect them rather than starting another write against uncertain state.

### Export and restore behavior

After export, the workspace points at the output, not the original input. Subsequent
in-place saves therefore affect the output. Backup history is local to the active save
directory. Restoration acts on all files listed in that backup and discards pending
in-memory edits only after the user confirms and restoration succeeds.

## Validation

Tests synthesize preferences/plists/SQLite databases at runtime. No private save sample
is a fixture. The suite covers all 15 nonempty component combinations, parser byte
preservation, digest vectors, source races, fault injection, rollback, backup restoration,
and keyboard/mouse workflows. UI tests use Textual's official
[headless Pilot API](https://textual.textualize.io/guide/testing/), with terminal sizes
including 80×24. Distribution checks inspect both wheel and source archive, then install
and smoke-test a wheel outside the checkout using isolated Python.
