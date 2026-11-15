---
name: bbdc-vocabulary-import
description: 整理用户指定的英文词表并批量加入「不背单词」账号的「我的生词本」。用于从清单、文件或当前材料选词后导入生词本，或生成兼容 WordRadar 的 CSV。仅查释义或讨论实现时不上传。 / Batch-add selected English vocabulary to a user's 不背单词 (BBDC) wordbook or export WordRadar CSV.
---

# 不背单词批量导入

用 [导入脚本](scripts/bbdc_import.py) 读写用户自己的生词本。运行命令时给路径整体加引号，也可用 `python3`。有关安装、输入格式及命令示例，见[安装和使用](references/setup.md)。

1. **准备词表。** 保留用户提供的词形和短语，不擅自扩充。从文章选词时，只把选定的词写入 UTF-8 文本，每行一项；不要直接上传文章。已有词列的 CSV/TSV 可直接传给脚本。运行 `prepare "<词表>"`，读 JSON 中的数量、样例及 `digest`。有错先按行号处理；名单需要用户决定时，给实际词表预览，在决定前不推送。
2. **按原词表推送。** 用户已明确指定范围并要求上传时，运行 `push "<词表>" --expect <digest> --remaining-out "<新的剩余词.csv>"`。只核对账号里是否有这些词时用 `check "<词表>"`。缺依赖时运行 `setup`，后续用它返回的 Python。若返回 `login_required`，告诉用户需要在独立窗口手动登录，等用户可以操作时加 `--login` 继续。续推时换一个输出文件名；若用上次的剩余词表作输入，重新运行 `prepare` 取它的指纹。按可继续轮询的后台命令运行，避免短工具超时关掉登录窗口。不要索要或代填密码、验证码、二维码信息或 Cookie。
3. **解释结果。** 按 JSON 分别汇报新增、已存在、失败、结果待核对、未尝试；只在 `added` 有记录时说已新增。如果查不到释义但写入成功，汇报 `added_without_definition`。鉴权、限流、接口异常和写入待核对时，按 `hint` 说明下一步，并保留剩余词表。只有同一账号才用裁剪后的剩余词表续推；切换账号用原词表。如果响应丢失，先用 `check` 核对或在恢复后重跑词表让脚本逐词查重，不直接补发 POST。

若不能在用户本机运行脚本，或暂时无法登录，运行 `prepare "<词表>" --out "<输出.csv>"`。没有 Python 时也可直接生成 `lemma,flags` CSV，每个待推词一行，`flags=0`。告诉用户在 WordRadar 扩展里「导入 CSV → 确认推送」，并明确尚未写入账号。
