# ◈ ArcSaveLab

**让存档管理井然有序。** 面向 Arcaea 的本地终端存档管理器。

[English](README.md) · 简体中文

![使用合成数据的新版工作区](docs/images/workbench.svg)

## 启动

需要 Python **3.12+** 和 UTF-8 终端。最低 80×24，建议 120×36 或更大。

```console
uv sync --locked
uv run arcsavelab --language zh-Hans
```

也可以直接传入存档文件夹：

```console
uv run arcsavelab edit ./saves --language zh-Hans
```

长期使用时，在项目根目录运行 `uv tool install .`，之后直接启动 `arcsavelab`。
运行时不依赖游戏安装路径、APK、ADB、相邻项目或开发者本机目录。

## 操作方式

1. **打开**：`Ctrl+O`。识别文件夹内的标准文件名，或点击「浏览」使用目录树；也可分别输入四种文件的路径。
2. **浏览**：左侧分类导航，`Ctrl+F` 搜索名称、ID、难度。列表不再只显示前 100 项。
3. **详情与编辑**：单击选中，双击或 `Enter` 打开说明和独立表单；只读项目也可查看说明。校验失败保留草稿；`Esc` 取消草稿；暂存不会写入文件。
4. **批量**：在列表中用 `Space` 选择一项，`Ctrl+A` 选择全部筛选结果，`Ctrl+B` 编辑。多项编辑必须逐字段启用，避免把第一行的其他值复制给所有行。
5. **审阅保存**：`Ctrl+S` 查看修改前后值和文件指纹。默认导出到新目录；覆盖原文件需要再次确认并强制备份。
6. **恢复**：总览中的「备份历史」或 `Ctrl+H`。恢复时先验证备份，并为当前文件再创建一份备份，因此恢复本身也能撤回。

`Ctrl+Z` / `Ctrl+Y` 撤销 / 重做；`Ctrl+I` 设置仅本次会话使用的设备身份；
`Ctrl+R` 暂存摘要修复；`F1` 查看快捷键；`Ctrl+Q` 退出。文本输入框内优先遵循文本编辑快捷键。

拖动表头分隔线 `│` 可调整各列宽度，本次运行内保留（成绩表独立保存列宽）。
“是／否”使用绿／红色，缺少设备身份或文件使用黄色，数值使用浅蓝色。
账号数值不在列表显示，双击或 Enter 打开只读详情后才显示明文；请勿分享含认证令牌的截图。
Axiom 详情提供完整 `fin_v` 原文和从 0 开始的逐项索引；末尾标记正常不代表完整进度验证通过。

保存成功后，工作区会切换到刚保存的文件；切换文件、恢复备份或退出时会确认未保存的修改。
设备身份的 ADB 查询、Android 8+ 应用级 ID 区别及获取步骤见 [设备身份指南](docs/device-identity_CN.md)；英文版见 [Device identity](docs/device-identity.md)。

## 文件与功能

| 文件 | 用途 |
| --- | --- |
| `Cocos2dxPrefsFile.xml` | 游玩设置、残片、拥有内容、搭档、收藏、故事及已知技能计数 |
| `st3` | 成绩、判定数、通关类型，以及 Full Recall / Pure Memory 预设 |
| `un` | 解锁进度；修改时需要同时选择 preferences |
| `ms` | 任务领取状态；修改时需要同时选择 preferences |

四种文件支持任意非空组合。账号和 Axiom of the End 诊断保持只读；设备绑定数据需要匹配的设备身份。
程序只处理本地文件，不进行网络登录或云端同步。

最新支持版本：**7.0.260c**。内置目录包含 554 条歌曲记录（553 首有效歌曲）、1,837 张谱面、62 个曲包、100 位搭档。
仅分发相关结构化元数据与搭档说明，不包含游戏音频、图片、故事正文或完整翻译消息库。
最新 APK 的目录校验、存档格式测试与游戏内实际回导是不同的验证层次，详见 [格式与验证状态](docs/formats.md)。

编辑前关闭游戏或其他存档写入程序。存在非空 SQLite WAL / 回滚日志的数据库会被拒绝打开，避免丢失日志中的数据。
重要存档请另存独立副本；事务恢复和残留锁处理见 [架构文档](docs/architecture.md#transactions)。

## 校验、开发与开源准备

```console
uv run arcsavelab verify ./saves/Cocos2dxPrefsFile.xml --un ./saves/un --ms ./saves/ms --format json
uv run arcsavelab catalog verify
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
uv run python scripts/check_distribution.py dist
uv run python scripts/smoke_wheel.py dist
```

项目独立于 lowiro，采用 [MIT 代码许可证](LICENSE)。游戏名称和相关元数据属于各自权利人。项目开发使用了 AI 辅助。
