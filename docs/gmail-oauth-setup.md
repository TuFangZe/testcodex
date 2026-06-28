# Gmail OAuth2 Setup

这份项目在 GitHub Actions 上发邮件，使用的是 Gmail API OAuth2，不依赖本地桌面连接器。

## 你需要准备的 Secrets

- `GMAIL_SENDER`
- `GMAIL_CLIENT_ID`
- `GMAIL_CLIENT_SECRET`
- `GMAIL_REFRESH_TOKEN`

如果你准备继续用当前 Gmail 账号发信，`GMAIL_SENDER` 可以填：

```text
iamxioat@gmail.com
```

## 一次性准备步骤

1. 打开 [Google Cloud Console](https://console.cloud.google.com/)
2. 新建一个项目，或者选已有项目
3. 启用 `Gmail API`
4. 到 `APIs & Services -> OAuth consent screen`
5. 配置 OAuth consent screen
6. 到 `APIs & Services -> Credentials`
7. 创建 `OAuth client ID`
8. 应用类型选 `Desktop app`
9. 记下：
   - `Client ID`
   - `Client Secret`

## 获取 Refresh Token

你需要用一次授权流程换到 refresh token。最简单的方式是用 Google OAuth Playground：

1. 打开 [OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
2. 右上角点齿轮
3. 勾选 `Use your own OAuth credentials`
4. 填入你刚才的 `Client ID` 和 `Client Secret`
5. 在左侧 scope 输入：

```text
https://www.googleapis.com/auth/gmail.send
```

6. 点 `Authorize APIs`
7. 用你要发信的 Gmail 账号登录并授权
8. 点 `Exchange authorization code for tokens`
9. 复制返回里的 `refresh_token`

## GitHub Actions Secrets 填写位置

仓库地址：

- [TuFangZe/testcodex](https://github.com/TuFangZe/testcodex)

填写路径：

- `Settings -> Secrets and variables -> Actions -> New repository secret`

需要新增：

- `GMAIL_SENDER`
- `GMAIL_CLIENT_ID`
- `GMAIL_CLIENT_SECRET`
- `GMAIL_REFRESH_TOKEN`
- `OPENAI_API_KEY`（可选，但建议配）

## 首次测试建议

为了先验证抓取链路，再验证发信，你可以先做两轮：

1. 第一轮：
   先把 workflow 里的 `POLYMARKET_DRY_RUN` 临时设为 `true`
2. 第二轮：
   去掉 `POLYMARKET_DRY_RUN`，验证 Gmail 真实发送

如果你不想改 workflow，也可以直接先把 Gmail secrets 配好，然后手动触发一次运行。
