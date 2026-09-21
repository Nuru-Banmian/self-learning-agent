# 外部 API 配置

核对日期：2026-09-21。三个值都填入项目根目录已有的 `.env`，保留其中的百炼配置；不需要重新复制 `.env.example`。密钥只在本地后端使用。

## 阿里云 IQS：搜索与正文读取

1. 登录 [IQS 控制台](https://iqs.console.aliyun.com/)，按页面指引开通信息查询服务。
2. 进入 **API Key → 创建 API Key**。
3. 将所得值填入 `IQS_API_KEY`。使用 IQS 的独立 API Key，不填百炼密钥，也不填阿里云 AccessKey ID/Secret。

[官方开通与凭据说明](https://help.aliyun.com/zh/document_detail/2870227.html)。项目默认使用 `Generic` 搜索引擎，增强摘要关闭；可用额度、引擎权限与计费以自己的控制台为准。

## 和风天气：城市与预报

1. 登录 [和风控制台](https://console.qweather.com/)，进入 **项目管理 → 创建项目**。
2. 打开项目，在凭据区域选择 **添加凭据**，认证方式选 **API KEY**，填写名称并保存。
3. 将生成的密钥填入 `QWEATHER_API_KEY`。项目当前使用 HTTP API Key 认证。
4. 进入 **设置 → API Host**，将系统分配的完整域名填入 `QWEATHER_API_HOST`。只填域名，不含 `https://`、端口或路径；保留全部子域，例如 `h2a9cf3mhs.xy.qweatherapi.com`。示例不能代替自己账户的域名。

[官方项目和凭据说明](https://dev.qweather.com/docs/configuration/project-and-key/)；[官方 API Host 说明](https://dev.qweather.com/docs/configuration/api-host/)。

```dotenv
IQS_API_KEY=你的IQS密钥
QWEATHER_API_KEY=你的和风密钥
QWEATHER_API_HOST=你的完整专属域名
```

## 配置后验证

保存 `.env` 并重启运行中的应用。独立真实检查仅写临时隔离数据库，组合检查写入指定输出目录，不改个人日常数据：

```powershell
.\.venv\Scripts\python -m tests.live_research
.\.venv\Scripts\python -m tests.live_weather
.\.venv\Scripts\python -m tests.live_acceptance --output output/issue1/live
```

缺少配置会明确跳过；跳过不是通过。`--mock-information` 只用于真实百炼与模拟信息服务的组合验证，不能证明 IQS 或和风可用。

大陆验收还须记录机器实际所在网络和代理/VPN情况。客户端禁用环境代理不能证明机器位于大陆，也不能排除系统隧道。只有在已确认的大陆无代理环境完成实际调用，才可将 AC-12 记为通过。
