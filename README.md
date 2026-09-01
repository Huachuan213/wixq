# wixq

`wixq` 是一个面向知识星球（ZSXQ）群组的专用采集工具，仅用于读取操作者有权访问的群组。
它通过群组主题 API 读取文字和图片 URL，保存每次收到的原始分页响应，并支持中断后安全断点恢复。

本项目只处理文字和图片信息，不下载 PDF、MP3、视频或其他附件，也不读取评论。
登录凭据和采集内容仅保存在本地，并通过 Git 忽略规则排除在公开代码仓库之外。

## 安装

```powershell
cd wixq
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[browser,dev]"
```

## 登录

请使用你自己控制的浏览器完成登录。登录命令会在本地保存会话文件，
请勿提交或分享该文件。

```powershell
wixq login
```

如果普通 Chrome 已经开启远程调试端口，可以不启动新的浏览器，直接保存当前登录态：

```powershell
wixq login --cdp http://127.0.0.1:9222
```

## 读取指定时间范围

```powershell
wixq crawl "https://wx.zsxq.com/group/<group_id>" `
  --after "2026-08-31T15:00:00+08:00" `
  --before "2026-09-01T10:00:00+08:00" `
  --output "reports/zsxq-window"
```

开始时间包含在范围内，结束时间不包含在范围内。
采集器默认每页读取 20 条，并使用 `end_time` 作为分页游标。

## 本地输出

```text
reports/zsxq-window/
├── raw_pages/        # 不可变的请求元数据和完整 API 响应
├── posts/            # 按主题生成的派生 JSON 文件
├── state.json         # 用于断点恢复的进度缓存
├── failed_topics.json
├── topics.jsonl       # 重建得到的派生视图
└── markdown/          # 重建得到的 Markdown 视图
```

`raw_pages/` 是原始事实来源。需要时可以根据它重新生成 `state.json`。
你也可以随时使用以下命令重建派生输出：

```powershell
wixq rebuild-output reports/zsxq-window
```

## 数据使用说明

请只在你有权访问的知识星球群组中使用 wixq。
本项目不会绕过登录、验证码、付费或其他访问控制，也不会将 Cookie 或采集到的群组内容写入源码仓库。
