# 安装和使用

把整个 `bbdc-vocabulary-import` 文件夹放进支持 [Agent Skills](https://agentskills.io/specification) 的客户端，例如 Codex 的 `~/.codex/skills/`。只支持读文件的客户端也可以打开 `SKILL.md`。直接推送要求在用户电脑上执行脚本，第一次登录还需要桌面浏览器。没有这些能力时，交付 WordRadar CSV。

## 第一次使用

用 Python 3.10 或更新版本。下面用 `bbdc_import.py` 表示 Skill 目录里的 `scripts/bbdc_import.py`，运行时传入实际路径。路径和文件名有空格时加引号。

```sh
python "<Skill目录>/scripts/bbdc_import.py" setup
python "<Skill目录>/scripts/bbdc_import.py" prepare "words.txt"
python "<Skill目录>/scripts/bbdc_import.py" push "words.txt" --login --remaining-out "remaining.csv"
```

- `setup` 会先检查现有环境。缺依赖时，在本机用户的数据目录建独立 Python 环境，再准备所需浏览器。使用 JSON 返回的 `python` 路径执行后续命令。只想检查环境用 `doctor`，它不登录也不安装。
- 首次推送时在打开的窗口登录目标账号，脚本会继续推送并在结束后自动关闭窗口。默认等待 5 分钟，可用 `--login-timeout 600` 调整。后续通常省略 `--login`；如果网站没有保存会话，就在同一次推送中用 `--login`。
- 如果下载 Chromium 不方便，可在 `setup` 以及后续需要浏览器的命令里加 `--browser edge` 或 `--browser chrome`，使用已安装的浏览器。每种浏览器有独立的会话，均不读取它们的日常资料。在 Windows，默认会话目录为 `%LOCALAPPDATA%/bbdc-vocabulary-import/chromium`。同一个会话只允许打开一个导入进程。
- `status` 只检查登录；`login` 只完成登录；`account` 打开网站供你查看或切换账号，关闭窗口后结束，不会推送。交互命令请在用户本机运行，不要把登录资料传给远程 Agent。

若需要手动配置，使用同一个 Python 执行：

```sh
python -m pip install -r "<Skill目录>/scripts/requirements.txt"
python -m playwright install --no-shell chromium
```

Linux 如提示缺少 Chromium 的系统依赖，按 [Playwright 浏览器安装说明](https://playwright.dev/python/docs/browsers#install-system-dependencies)安装，或在允许操作系统软件包的环境下执行 `python -m playwright install --with-deps --no-shell chromium`。

## 准备和核对词表

- 文本每行一个词或短语，也可以带项目符号或编号。大小写、空格及常用连字符和撇号会归一。不做词形还原，释义和例句请另存。
- CSV/TSV 要有一个名为 `word`、`lemma` 或「单词」的列，可保留其他说明列。支持 UTF-8（含 BOM）及带 BOM 的 UTF-16。WordRadar 的 `lemma,flags` 中，非零标记的词不上传；同一个词有多行时，以已推送标记为准。若想把备份中的词加入另一个账号，先选定目标词并按纯文本导入。
- `prepare` 离线校验并显示前几项、去重数、已标记项和指纹。可以用 `--out "wordradar.csv"` 导出所有待推词。加 `--expect <digest>` 的推送命令会在文件与预览不一致时停下；使用新的输入文件时重新运行 `prepare`。
- `check "words.txt"` 只向不背单词查这些词是否已存在，不查释义、不上传。未找到的词不会自动加入。第一次实际验证时，可以先推送一小份明确指定的词，再用 `check` 或网页「我的生词本」核对。短语或词典未收录的拼写可能查不到释义，脚本会按原词尝试写入并单独统计成功但无释义的词。

## 中断后继续

指定 `--remaining-out` 后，脚本在任何远端写入之前用新文件保存完整待推词表，正常退出时会裁剪为还没确认完成的词。文件已经存在时会停下，避免覆盖。如果进程意外关闭，完整备份也可以重新推送，脚本会先查远端。剩余词表只适合同一账号；切换账号时用原词表。

写入响应丢失、响应不明或中途取消时，该词会标记为「待核对」，不会自动补发。先等服务恢复并用 `check` 核对，或重新推送剩余词，让脚本先查重。限流会直接暂停；如果返回了等待时间，按提示稍后再推。

标准输出是单个 JSON（`--help` 等命令行用法除外）；进度、登录提示在标准错误。退出码：`0` 完成或没有待处理词，`1` 停止或部分失败，`2` 输入错误，`3` 需要登录、登录超时或窗口关闭。判断结果看 `status` 和计数；`uncertain` 不是已新增。

推送协议依据 [WordRadar 对接说明](https://github.com/seven-steven/word-radar/blob/1a0e2dee7c5e092f61c110586dc82beee6ee3007/docs/spec.md#L91-L101)。网页接口发生变动时，可以先交付 WordRadar CSV；兼容性以服务当前实际表现为准。
