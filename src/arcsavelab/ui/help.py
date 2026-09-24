"""Short, task-oriented explanations; persistence keys remain stable identifiers."""

from arcsavelab.i18n import Translator
from arcsavelab.interface import ItemView

from .common import local

SETTING_HELP = {
    "highspeed_int": (
        "Note scrolling speed, stored in tenths: 30 means 3.0. It does not change the music tempo.",
        "音符下落速度，按十分之一存储：30 表示 3.0。不会改变歌曲播放速度。",
    ),
    "offset_int_2": (
        "Audio timing offset in milliseconds. Use the game's calibration to "
        "determine a suitable value.",
        "音频时序偏移，单位为毫秒。建议先在游戏内校准，再填写对应数值。",
    ),
    "bt_offset": (
        "Timing offset for Bluetooth audio, in milliseconds; separate from the "
        "regular audio offset.",
        "蓝牙音频专用时序偏移，单位为毫秒；与普通音频偏移分开保存。",
    ),
    "sfx_int": (
        "Hit-sound volume stored on a 0–20 scale. This is not the device's system volume.",
        "打击音音量，以 0–20 保存；不是设备的系统音量。",
    ),
    "songSort": (
        "Choose the property used to sort songs in the game. Song order "
        "controls the direction separately.",
        "选择游戏内歌曲列表的排序依据；正序或倒序由“歌曲排列顺序”单独控制。",
    ),
    "songOrder": (
        "Ascending or descending order for the selected song-sorting property.",
        "按当前排序依据正向或反向排列歌曲，不会改变歌曲或谱面本身。",
    ),
    "colorblindfriendly": (
        "Enable the game's color-vision assistance option.",
        "启用游戏的色觉辅助选项。",
    ),
    "performancemode_v2": (
        "Enable the game's performance option; this does not change score judgement rules.",
        "启用游戏的性能选项；不会改变成绩判定规则。",
    ),
    "language": (
        "The language saved for the game, not ArcSaveLab's interface language.",
        "游戏内使用的语言；不会改变 ArcSaveLab 的界面语言。",
    ),
}

CATEGORY_HELP = {
    "fragments": (
        "Local Fragment balance. Editing does not synchronize an online account.",
        "本地残片余额。修改不会同步到在线账号。",
    ),
    "partner": (
        "Availability, favorites and current selection are local flags, not "
        "Partner level or awakening. Skill text is reference information.",
        "这里修改本地可用、收藏与当前选用状态，不是搭档等级或觉醒状态。技能说明仅供查看。",
    ),
    "favorite-song": (
        "Add or remove this song from the local favorites list.",
        "将这首歌曲加入或移出本地收藏列表。",
    ),
    "story": (
        "Completed and read are separate local flags. Changing them does not replay the story.",
        "“已完成”和“已阅读”是两个独立的本地标记；修改不会播放故事。",
    ),
    "skill": (
        "A persisted Partner-skill counter, not a skill selector. Keep it "
        "within the displayed range.",
        "搭档技能的存档计数器，不是技能选择器。请按下方范围填写。",
    ),
    "score": (
        "PURE is the total including Shiny Pure. Ordinary Pure = PURE − Shiny "
        "Pure. Scores are recalculated from judgements; Clear type remains a "
        "separate choice.",
        "PURE 总数包含大 Pure 和小 Pure。小 P = PURE 总数 − 大 P。"
        "成绩会按判定数自动重算；Clear 类型需单独选择。",
    ),
    "unlock": (
        "Local progress for this specific chart condition. It requires the "
        "preferences file for its companion digest; it is not an online "
        "entitlement.",
        "该谱面指定条件的本地进度。需同时打开设置文件以更新关联摘要；不代表在线账号权益。",
    ),
    "mission": (
        "Local mission reward state. It does not grant or synchronize server-side rewards.",
        "本地任务奖励状态，不会发放或同步服务器端奖励。",
    ),
    "finale-status": (
        "Axiom of the End has a variable-length progress layout. This view is "
        "diagnostic only; no guessed editing is offered.",
        "Axiom of the End 使用变长进度结构。此项仅供诊断，不提供基于猜测的编辑。",
    ),
    "account": (
        "Account data is read-only. Values appear only in this detail dialog, not in the list. "
        "Authentication tokens are secrets; avoid sharing screenshots of this dialog.",
        "账号信息只读。数值仅在此详情弹窗显示，不在外部列表显示。"
        "认证令牌属于敏感信息，请勿分享包含详情的截图。",
    ),
}


def item_help(item: ItemView, tr: Translator) -> str:
    kind, _, key = item.id.partition(":")
    explanation = (
        SETTING_HELP.get(key)
        if kind == "setting"
        else CATEGORY_HELP.get("account" if kind.startswith("account-") else kind)
    )
    body = (
        local(tr, *explanation)
        if explanation
        else local(
            tr,
            "Local save record. Availability below indicates whether a field can be changed.",
            "本地存档项目。下方字段状态会说明哪些内容可以修改。",
        )
    )
    if item.subtitle:
        body += "\n\n" + item.subtitle
    return body


def identity_help(tr: Translator) -> str:
    return local(
        tr,
        "Use the identity from the device and Android user that wrote this "
        "save—not installId, a device serial or your 9-digit friend code.\n\n"
        "1  Enable USB debugging and authorize your computer.\n"
        "2  Basic query: adb shell settings get secure android_id\n"
        "   Android 8+: this shell value may NOT be the game's app-scoped ID. "
        "Do not assume they match.\n"
        "3  On a rooted device, read the game's entry in settings_ssaid.xml "
        "(guide: docs/device-identity.md), or inspect "
        "Settings.Secure.ANDROID_ID from the running game process.\n"
        "4  Leave User ID blank to use the save's internal uid. Validate "
        "existing nonempty digests before requesting repair. A mismatch is not "
        "proof the save is corrupt.\n\n"
        "Only this session uses these inputs; they are not written into saves "
        "or receipts.",
        "请使用写出这份存档的设备及 Android 用户身份，"
        "不是 installId、设备序列号，也不是九位好友码。\n\n"
        "1  开启 USB 调试，并在手机上授权电脑。\n"
        "2  基础查询：adb shell settings get secure android_id\n"
        "   Android 8+：上述 shell 值不一定等于游戏的应用级 ID，不能直接假定两者相同。\n"
        "3  已 Root：按 docs/device-identity_CN.md 读取 settings_ssaid.xml "
        "中游戏对应条目；或在游戏进程内读取 Settings.Secure.ANDROID_ID。\n"
        "4  用户 ID 留空会使用存档内部 uid。先检查已有非空摘要，再决定是否修复；"
        "摘要不匹配也可能是身份填错。\n\n"
        "输入仅用于当前会话，不会写入存档或回执。",
    )
