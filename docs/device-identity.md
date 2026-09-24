# Device identity

`Ctrl+I` opens a session-only identity form. It retains input when validation fails.
Use the identity of the **device and Android user/profile that wrote the save**.
`installId`, the hardware serial, and the nine-digit friend code are different values.
Leave User ID blank to use the save's internal `uid`.

## Basic query

Enable USB debugging, connect the phone, and authorize the computer:

```console
adb devices
adb shell settings get secure android_id
```

**Do not automatically treat this result as the game's ID on Android 8+.** Android
scopes `ANDROID_ID` by app signing key, Android user, and device; the shell query may
return a different value. See [Android's ANDROID_ID contract](https://developer.android.com/reference/android/provider/Settings.Secure#ANDROID_ID).

## Rooted devices

First identify the Android user that owns the game, then find the package and UID.
Replace `ANDROID_USER` and `PACKAGE` with the actual values; do not mix a work-profile
UID with the primary-user directory.

```console
adb shell pm list users
adb shell pm list packages --user ANDROID_USER -U PACKAGE
```

With a root file manager, open `/data/system/users/ANDROID_USER/settings_ssaid.xml`
read-only. Find the `setting` whose `name` matches the game UID and whose `package`
matches the game package, then read its `value`. Do not choose `userkey`, modify this
file, or upload the whole file: it may contain other applications' identities. Some
systems use binary XML, which ordinary text tools cannot read; use a local viewer that
supports that format.

This follows AOSP's per-UID SSAID lookup; vendor systems may differ. See
[AOSP SettingsProvider](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-11.0.0_r1/packages/SettingsProvider/src/com/android/providers/settings/SettingsProvider.java).

## In-process alternative

In a debuggable game-process context, read the Android API below rather than querying
another ID utility application:

```java
Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID)
```

This requires an actual game-process context; it is not an `adb shell` command. If the
game injects or transforms its identity, verify the value it actually passes to its
native save-integrity code. A generic helper application is not equivalent.

## Before repair

Check whether existing non-empty device-bound digests match. Do not recalculate merely
because a digest mismatches: an incorrect identity also causes a mismatch. Without a
trusted identity, preserve the original digest and edit fields that do not depend on it.

These instructions are documentation, not evidence of a connected-device test.
