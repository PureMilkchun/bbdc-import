# 不背单词批量导入

一个 Agent Skill，把英文词表批量加入「不背单词」的「我的生词本」。在用户电脑上运行时，复用独立浏览器里的登录会话，逐词查重和推送；无法访问本机时，可以生成 WordRadar 兼容的 CSV。

## 安装

有 Node.js 时，可以用 [Skills CLI](https://github.com/vercel-labs/skills) 安装到当前项目（以 Codex 为例）：

```sh
npx skills add PureMilkchun/bbdc-import --skill bbdc-vocabulary-import --agent codex
```

把 `codex` 换成 `claude-code`、`cursor` 等目标 Agent；要安装给当前用户的所有项目，加 `--global`。

如果你用的 GitHub CLI 已支持 [`gh skill install`](https://cli.github.com/manual/gh_skill_install)，也可以运行：

```sh
gh skill install PureMilkchun/bbdc-import bbdc-vocabulary-import --agent codex
```

也可以下载 [ZIP 安装包](./bbdc-vocabulary-import.zip)，把解压后的 `bbdc-vocabulary-import` 文件夹放到客户端的 Skill 目录。运行依赖见[安装说明](./skills/bbdc-vocabulary-import/references/setup.md)。

## 开始使用

把词表交给 Agent，说「用 `$bbdc-vocabulary-import` 把这份词表导入不背单词的生词本」。第一次在弹出的独立浏览器里手动登录，Agent 会报告新增、已存在、失败和待核对的数量。

首次使用真实账号时，建议先推送少量指定词，再在网页或 App 里核对。需要重试时，保留生成的剩余词表。

## 验证

在仓库根目录运行 `python -B -m unittest discover -s tests -v`。浏览器集成测试使用本地模拟服务，不会连接你的不背单词账号；运行全部测试需要 Playwright 和 Chromium。

接口流程参考 [WordRadar](https://github.com/seven-steven/word-radar)。
