# 不背单词批量导入

一个 Agent Skill，把英文词表批量加入「不背单词」的「我的生词本」。在用户电脑上运行时，复用独立浏览器里的登录会话，逐词查重和推送；无法访问本机时，可以生成 WordRadar 兼容的 CSV。

## 开始使用

1. 下载 [Skill 压缩包](./bbdc-vocabulary-import.zip)，把解压后的 `bbdc-vocabulary-import` 文件夹放到客户端的 Skill 目录。详细步骤见[安装说明](./bbdc-vocabulary-import/references/setup.md)。
2. 把词表交给 Agent，说「用 `$bbdc-vocabulary-import` 把这份词表导入不背单词的生词本」。
3. 首次使用时在弹出的独立浏览器里手动登录；Agent 会给出新增、已存在、失败和待核对的数量。

首次使用真实账号时，建议先推送少量指定词，再在网页或 App 里核对。需要重试时，保留生成的剩余词表。

## 验证

在仓库根目录运行 `python -B -m unittest discover -s tests -v`。浏览器集成测试使用本地模拟服务，不会连接你的不背单词账号；运行全部测试需要 Playwright 和 Chromium。

接口流程参考 [WordRadar](https://github.com/seven-steven/word-radar)。
