# ArcSaveLab save editing

Local save workspaces distinguish proposed edits, verified files and a ready editing state.

## Language

**Save workspace**:
A selected set of Arcaea save files, its current device identity and its editing state.
_Avoid_: save directory (files can be selected independently).

**Save draft**:
Proposed changes not yet accepted as the workspace's current saved state. A preserved draft
can remain available for inspection while it is suspended from further editing.
_Avoid_: backup (a draft is not a copy of saved files).

**Journal file set**:
Every save file belonging to one recorded multi-file write. Selecting one member for
interrupted-write recovery does not make the other members independent.
_Avoid_: selected files (the selection may be only a subset).

**Verified files**:
Written save files whose bytes match the recorded output fingerprints. This does not
mean the editing workspace was reloaded or the game accepted those files.
_Avoid_: ready workspace, game-validated save.

**Reload-required workspace**:
A workspace whose files were written but could not be reloaded. Its previous draft is
preserved but suspended, rather than presented as the current saved state.
_Avoid_: failed write (the files were already written).
