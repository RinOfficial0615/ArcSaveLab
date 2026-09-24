# Targeted official localization

The catalog stores only partner display names and skill descriptions in English and
Simplified Chinese. It does **not** distribute the game's full translation catalog.
The existing `arcsavelab catalog build` command reproducibly selects these fields from
`assets/tl/zh-Hans.mo`, whose hash is included among the 36 manifest source assets.
No APK, native library, MO parser or device connection is needed at application runtime.

`_catalog_labels.py` maps stable metadata identities to official English message keys.
These keys were checked against the native display functions in the supported APK:

- Native SHA-256: `72e42cb4925655ecfef98bf2dbf92a5ac05145eb005a531c96c11b927a46e6dc`.
- Base name switch: `0x10C5ED8`, covering all 100 partner IDs.
- Variant switch: `0x14BE9F0`, dispatch chunk `0x174AE88`; 38 unconditional variants
  are included. Five runtime-conditional suffixes are not inferred from save metadata.
- Name formatting at `0x14B9160` removes the `[ANS]` message disambiguation marker.
- Skill description switch: `0x1770D90`; 108 message alternatives for 100 skill IDs.
  Every nonempty base/awakened skill in the character metadata is covered.
- Text lookup: `0x101A7E4` delegates to the translation lookup implementation `0xAE7EC0`.

All selected name and skill message keys exist in the APK's Simplified Chinese MO.
An official translation may intentionally equal its English key; those names remain
unchanged, as do untranslated variant names. Skill alternatives are labeled as states,
not claimed to be simultaneously active. Official dynamic placeholders remain intact in
catalog data; the UI identifies them as dynamic parameters instead of guessing values
that depend on the character's current level or game state.

The raw `Partner.name` and skill identities remain stable. `display_name`, `description`
and `uncapped_description` are additive localized domain fields. Old catalogs without
these fields continue loading with fallbacks. Tests use only bundled normalized metadata
and freshly synthesized save files; the original APK is not a test fixture.
