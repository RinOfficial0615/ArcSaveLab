# 设备身份

`Ctrl+I` 会打开仅限当前会话的设备身份表单。校验失败时，已填写内容会保留。
请使用**写出这份存档的设备及 Android 用户/配置文件身份**。
`installId`、硬件序列号和九位好友码都不是同一个值。
用户 ID 留空时，会使用存档内部的 `uid`。

## 基础查询

开启 USB 调试，连接手机并在手机上授权电脑：

```console
adb devices
adb shell settings get secure android_id
```

**Android 8 及更高版本不要直接把这个结果当作游戏 ID。** Android 会根据应用
签名密钥、Android 用户和设备对 `ANDROID_ID` 做作用域隔离，因此 shell 查询结果
可能不同。详见 [Android 的 ANDROID_ID 说明](https://developer.android.com/reference/android/provider/Settings.Secure#ANDROID_ID)。

## 已 Root 的设备

先确定游戏所属的 Android 用户，再查找游戏包名和 UID。将 `ANDROID_USER` 与
`PACKAGE` 替换为实际值；不要把工作资料用户的 UID 与主用户目录混用。

```console
adb shell pm list users
adb shell pm list packages --user ANDROID_USER -U PACKAGE
```

使用 Root 文件管理器，以只读方式打开
`/data/system/users/ANDROID_USER/settings_ssaid.xml`。找到 `name` 与游戏 UID
一致、`package` 与游戏包名一致的 `setting`，读取它的 `value`。不要选择
`userkey`，不要修改此文件，也不要上传整份文件；其中可能包含其他应用的身份信息。
部分系统使用二进制 XML，普通文本工具无法读取，应使用支持该格式的本地查看器。

以上方法遵循 AOSP 按 UID 查询 SSAID 的方式，但不同厂商系统可能不同。详见
[AOSP SettingsProvider](https://android.googlesource.com/platform/frameworks/base/+/refs/tags/android-11.0.0_r1/packages/SettingsProvider/src/com/android/providers/settings/SettingsProvider.java)。

## 在游戏进程内读取

在可以调试的游戏进程上下文中读取下面的 Android API，不要使用另一个 ID 查询工具：

```java
Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ANDROID_ID)
```

这要求是真实的游戏进程上下文，不是 `adb shell` 命令。如果游戏注入或转换了设备
身份，请确认它实际传给原生存档完整性代码的值。普通辅助应用读取的值不等价。

## 修复摘要前

先检查现有的非空设备绑定摘要是否匹配。不要因为摘要不匹配就直接重算：身份填错
也会导致不匹配。没有可信身份时，请保留原摘要，先编辑不依赖设备身份的字段。

本文档只是操作说明，不代表已经连接真机完成验证。
