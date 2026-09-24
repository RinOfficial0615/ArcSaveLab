# Save format contracts and version evidence

## Validation layers

- **Catalog:** rebuilt from the local 7.0.255c APK. APK SHA-256:
  `5aeb5edc425808605cc8a682cd9269aecae671fdab9573376b100d08ee3577c6`.
  The manifest records the exact hashes of 35 metadata assets plus the Chinese localization asset and both generated outputs.
- **File behavior:** synthetic tests exercise the inherited format contracts. Local reference
  files were separately opened, no-op checked, edited on copies, exported and reopened.
  Reference files are not published, copied into tests or required in CI.
- **Game runtime:** actual re-import into a running 7.0.255c game has not been validated by
  these host tests. The sample file dates alone do not establish which native build wrote them.
  The digest implementations are inherited format knowledge, not newly recovered native offsets.

To reproduce the public catalog (the output directory is explicit):

```console
arcsavelab catalog build --apk ./game.apk --version 7.0.255c --out ./catalog-output
```

Inspect its manifest, compare normalized data, and run `verify_catalog_directory` before
replacing bundled files. A version label alone does not authorize assuming new field semantics.

## Preferences

`Cocos2dxPrefsFile.xml` is Android SharedPreferences XML. Scalar `string`, `int`, `long`,
`float` and `boolean` values are supported. The editor patches token spans rather than
reserializing the whole document: declarations, comments, DOCTYPE, whitespace, quoting,
unknown nodes, and unedited bytes are preserved.

Known digest preimages are UTF-8 and use lowercase MD5 on generation:

| Digest | Source / preimage |
| --- | --- |
| `st_k`, `cs_k`, `fin_k`, `fc_k`, `fs_k` | Raw matching `*_v` string |
| `fr_k` and known integer companions | `str(int32(v)) + "ok" + str(int32(v))` |
| `p_k`, `s_k`, `wu_k`, `ac_k` | `device_id + str(int32(uid)) + raw_value + str(int32(uid * 2))` |
| `un_k`, `ms_k` | Serialized corresponding plist map |

Direct and device-bound empty sources use the empty-string sentinel, not MD5 of empty bytes.
Integer arithmetic follows signed 32-bit wraparound. Device identity is session-only;
account tokens are masked in browse views and revealed only in explicitly opened local read-only
details. Receipts contain file fingerprints, not identity input.
MD5 is reproduced for format compatibility, not used as cryptographic authentication.

Profile detection requires the known source-field signature; a missing digest for a present
source is an integrity failure, not evidence of a different version. Explicit repair may
regenerate a computable missing digest. Missing source fields still retain version-profile
protection, and unknown digest layouts are not guessed.

## Plist maps

`un` and `ms` are XML plists with a dictionary root. Supported values are integers, reals,
strings and booleans. The map digest uses last-duplicate-key wins, descending UTF-8 byte
key order, then `key + "&" + canonical_value + "$"` for each pair. Integers use decimal,
reals use 16 significant digits, booleans use `true` / `false`, strings remain raw.
Nested unknown values are retained by the adapter; their unsupported digest serialization
is not guessed. Map editing needs preferences to store its companion digest.

Type 102 unlock keys can identify challenges rather than songs. The UI resolves a recognized
`SONG_challenge` or `SONGChallengeN_challenge` naming pattern against the song catalog, retaining
the full target ID and raw key. Unmapped challenges are explicitly labeled; this name matching
does not establish the challenge's task conditions or completion threshold.

`fin_v` details preserve the raw string and each `|`-separated entry, including empty entries.
The displayed trailing `1337` marker check is not full layout or in-game progress validation.
No finale editing is enabled.

## SQLite scores

The known schema version is 4. `scores` and `cleartypes` identify charts by song ID,
difficulty and control type (`ct`); control types are not merged. Unknown tables,
indexes and columns remain in the shadow database. Purchase/receipt records are not edited.

```text
score = floor(10,000,000 × (2 × PURE + FAR) / (2 × total_notes)) + shiny_PURE
```

`total_notes = PURE + FAR + LOST`; zero notes give a score of zero. Shiny PURE must not
exceed PURE. Normal edits regenerate the score and known grade cache rather than accepting
an inconsistent independent score number. Difficulty codes are PST=0, PRS=1, FTR=2,
BYD=3 and ETR=4. Unsupported structures are read-only or rejected with diagnostics.
