# GitHub Actions 定时配置说明

这个 workflow 已经对应当前仓库里的脚本，可以做到：

- GitHub 云端每天定时运行
- 电脑关机也继续执行
- 生成日报
- 通过 Gmail 自动发到指定邮箱

## 文件

- Workflow: [`.github/workflows/polymarket-daily.yml`](/C:/Users/fangz/Documents/Codex/2026-06-27/new-chat-3/.github/workflows/polymarket-daily.yml)

## 当前定时

- `30 0 * * *`
- 这是 `UTC 00:30`
- 对应 `Asia/Shanghai 08:30`

如果你想改成北京时间每天 `09:00`，就把 cron 改成：

```yaml
- cron: "0 1 * * *"
```

## 需要配置的 GitHub Secrets

到仓库的 `Settings -> Secrets and variables -> Actions` 里新增：

- `OPENAI_API_KEY`
- `GMAIL_CLIENT_ID`
- `GMAIL_CLIENT_SECRET`
- `GMAIL_REFRESH_TOKEN`
- `GMAIL_SENDER`

其中：

- `GMAIL_SENDER` 是发件 Gmail 地址
- `GMAIL_REFRESH_TOKEN` 建议走 Gmail API OAuth2，不建议长期用普通密码
- `OPENAI_API_KEY` 是可选增强项：配置后会用模型生成完整中文日报；不配也能跑，但会降级为模板化报告

## Workflow 依赖的默认脚本入口

当前 workflow 会执行：

```bash
python scripts/polymarket_daily.py
```

脚本还会自动调用 `scripts/gmail_sender.py` 完成 Gmail OAuth2 发信。

## 推荐目录结构

```text
.github/workflows/polymarket-daily.yml
scripts/polymarket_daily.py
scripts/gmail_sender.py
requirements.txt
outputs/
```

## 运行结果

workflow 结束后会上传：

- `outputs/` 下的产物
- 根目录下的 `*.md`

这样即使发信失败，也能在 Actions 的 artifact 里拿到报告。

## 下一步

当前仓库已经补上：

1. `scripts/polymarket_daily.py`
2. `scripts/gmail_sender.py`
3. `requirements.txt`

现在还需要你在 GitHub 上完成：

1. 推送这几个文件到仓库
2. 在 `Settings -> Secrets and variables -> Actions` 里填好凭据
3. 手动触发一次 `workflow_dispatch` 验证发信

补充说明：

- 脚本优先调用 OpenAI 生成完整中文报告。
- 如果 OpenAI 生成失败，会自动降级为模板化 Markdown 报告。
- Gmail 发送不依赖本地桌面连接器，电脑关机也不影响执行。
- 如果你想先不发邮件验证抓取链路，可以临时在 workflow 里加 `POLYMARKET_DRY_RUN: "1"`。
