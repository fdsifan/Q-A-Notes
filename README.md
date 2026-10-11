# 问答笔记

本地 ChatGPT Work / Codex 问答记录与摘录插件，当前版本 **0.7.6**。

## 功能

- 按会话查看问题和笔记，进入会话时显示最新一页的底部。
- 从完整回答中选中文字，保存摘录、备注和原文来源。
- 查看原问题、完整回答及高亮摘录，并打开原会话。
- Markdown 排版、图片缩略图与同一面板内的大图缩放。
- 删除与恢复笔记、隐藏与恢复会话、手动刷新反馈。
- 使用客户端原生侧栏面板，不启动独立笔记窗口。

## 范围

读取当前用户本地会话目录中已经保存的 Work / Codex 用户会话及完成后的最终回答。普通 Chat、云端会话和独立归档目录尚未覆盖；思考过程、工具输出和子代理会话不导入。打开原会话不保证定位到具体消息。

## 环境与安装
  
1. 准备环境用户需要安装：

- 支持本地插件与 MCP Apps 面板的 ChatGPT Windows 客户端
- Python 3.12 或更新版本
- Git（用于下载仓库）
2. 打开 PowerShell，运行：

    cd $HOME

    git clone https://github.com/fdsifan/Q-A-Notes.git

    cd Q-A-Notes

    python --version

    python scripts/install_local.py

这里特意将仓库下载到用户主目录，因为你的 install_local.py 使用相对于用户主目录的路径注册插件。

3. 在 ChatGPT 中完成安装
  安装会尝试：
- 注册个人插件来源 personal-chat-notes
- 添加名为 chat-notes 的插件
- 调用本机客户端插件安装器 
如果脚本输出 registered: true、installed: true，说明注册和安装命令都已成功执行。
如果只显示 registered: true，但 installed: false，说明插件来源可能已注册，但尚未完成安装。用户需要重启 ChatGPT，并在插件管理中的个人插件来源里完成安装。
安装成功后重新启动客户端。钩子还需要经过客户端的原生审阅与信任流程，安装本身不等于已经授权钩子运行。

## 数据

Windows 的真实笔记库默认位于 `~/AppData/Local/ChatNotes/notes.sqlite3`，其他平台使用 `~/.local/share/chat-notes/notes.sqlite3`。可通过 `CHAT_NOTES_DATA_DIR` 指定数据目录。

此源码目录不包含个人问答记录、笔记库、临时截图、测试运行数据或开发依赖。单元测试仅在临时目录中使用合成数据。复制的片段仅在用户点击对应按钮时读取，插件代码不上传问答。

## 开发

前端开发需要 Node.js。以下命令在 `web` 目录执行：

```text
npm ci
npm test
npm run build
```

后端测试在仓库根目录执行：

```text
python -m unittest discover -s tests -p "test_*.py"
```

生成安装包：

```text
python scripts/build_package.py
```

安装包输出到仓库上一级目录的 `release` 目录，打包脚本只包含运行所需文件。

## 本地会话适配

客户端本地会话格式属于版本敏感适配。当前适配器为 `codex-jsonl-2026-10`，客户端更新后需要重新验证读取范围及面板显示。
