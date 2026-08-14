# 招标商机快报

每日自动搜索江西洗涤相关招标信息，通过邮件推送到 QQ 邮箱。

## 功能
- 自动搜索中国政府采购网和江西公共资源交易网
- 关键词：洗涤、布草洗涤、织物洗涤、洗涤服务
- 每天北京时间9点自动运行（GitHub Actions 定时任务）
- 搜索结果通过邮件推送到指定邮箱

## 配置
在仓库 Settings > Secrets 中设置：
- SENDER_EMAIL: 发件人邮箱
- SENDER_AUTH_CODE: SMTP授权码
- RECEIVER_EMAIL: 收件人邮箱
