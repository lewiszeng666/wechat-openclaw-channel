# WeChat OpenClaw Channel

企业微信 - OpenClaw 自动化接入工具，基于Cookie复用方案实现半自动化配置。

## 功能特性

- 🔐 **Cookie预存方案**: 运维人员预先扫码保存Cookie，24小时内自动操作
- 🚀 **一键配置**: 自动安装OpenClaw插件、创建企微应用、配置Webhook
- 🌐 **Web控制台**: 简洁的Web界面，实时显示配置进度
- 📱 **微信二维码**: 自动获取企微微信插件二维码

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                    配置流程                                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────┐    ┌─────────────┐    ┌─────────────┐         │
│  │ Web控制台│ →  │ SSH安装插件  │ →  │ 配置企微后台 │         │
│  └─────────┘    └─────────────┘    └──────┬──────┘         │
│                                           │                 │
│                                    ┌──────▼──────┐         │
│                                    │ 微信二维码   │         │
│                                    └─────────────┘         │
│                                                             │
│  ┌────────────────────────────────────────────────────┐    │
│  │  Cookie管理器: 预存企微后台Cookie (24h有效)         │    │
│  └────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 部署指南

### 1. 环境要求

- Python 3.8+
- 可访问OpenClaw服务器的SSH权限
- 企业微信管理员账号（用于预存Cookie）

### 2. 安装步骤

```bash
# 克隆仓库
git clone https://github.com/lewiszeng666/wechat-openclaw-channel.git
cd wechat-openclaw-channel

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 安装Playwright浏览器
playwright install chromium
```

### 3. 配置环境变量

```bash
# 复制示例配置
cp .env.example .env

# 编辑配置文件
vim .env
```

`.env` 配置说明：

```bash
# Flask配置
SECRET_KEY=your-secret-key-change-in-production
HOST=0.0.0.0
PORT=5000

# OpenClaw服务器SSH配置
OPENCLAW_HOST=192.168.1.100      # OpenClaw服务器IP
OPENCLAW_SSH_USER=root           # SSH用户名
OPENCLAW_SSH_KEY=~/.ssh/id_rsa   # SSH私钥路径
OPENCLAW_SSH_PORT=22             # SSH端口

# 企业微信配置
WECOM_CORP_ID=wwxxxxxxxxx        # 企业ID (在企微后台获取)
```

### 4. 预存企微Cookie（关键步骤）

**此步骤必须由运维人员在有显示器的环境执行：**

```bash
# 运行Cookie预存工具
python cookie_manager.py save YOUR_CORP_ID

# 示例
python cookie_manager.py save ww1234567890abcdef
```

执行后会：
1. 打开浏览器显示企微登录二维码
2. 使用企业微信App扫码登录
3. 登录成功后自动保存Cookie到 `cookies/` 目录

**Cookie有效期约20-24小时，过期后需重新执行此命令。**

检查Cookie状态：
```bash
python cookie_manager.py check YOUR_CORP_ID
```

### 5. 启动Web服务

```bash
# 开发模式
python app.py

# 生产模式 (使用gunicorn)
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 6. 使用Web控制台

1. 浏览器访问: `http://your-server:5000`
2. 确认Cookie状态为"有效"
3. 输入应用名称
4. 点击"开始配置"
5. 等待配置完成，扫描微信二维码关注

## 使用流程

### 流程图

```
运维人员                          客户
    │                              │
    │  1. 预存Cookie (扫码)         │
    │  ──────────────────────────→ │
    │                              │
    │  2. Cookie有效期内...         │
    │                              │
    │                              │  3. 访问Web控制台
    │                              │  ←──────────────────
    │                              │
    │                              │  4. 点击"开始配置"
    │                              │  ←──────────────────
    │                              │
    │  5. 自动执行:                 │
    │     - SSH安装插件             │
    │     - 创建企微应用            │
    │     - 配置Webhook            │
    │  ──────────────────────────→ │
    │                              │
    │                              │  6. 扫描微信二维码
    │                              │  ←──────────────────
    │                              │
    │                              │  7. 开始使用OpenClaw
    │                              │
```

### 扫码次数

| 角色 | 扫码内容 | 次数 |
|------|---------|------|
| 运维人员 | 企微后台登录 | 1次/24小时 |
| 客户 | 微信关注二维码 | 1次 |

## API接口

### 获取Cookie状态
```
GET /api/cookie-status
```

### 测试SSH连接
```
POST /api/test-ssh
```

### 开始配置
```
POST /api/start-setup
Content-Type: application/json

{
    "app_name": "OpenClaw AI助手"
}
```

### 获取任务状态
```
GET /api/task/<task_id>
```

## 文件结构

```
wechat-openclaw-channel/
├── app.py                 # Flask主应用
├── config.py              # 配置管理
├── cookie_manager.py      # Cookie预存工具
├── wecom_automation.py    # 企微后台自动化
├── openclaw_installer.py  # OpenClaw插件安装
├── requirements.txt       # Python依赖
├── .env.example           # 环境变量示例
├── templates/
│   └── index.html        # Web界面
└── cookies/              # Cookie存储目录
```

## 常见问题

### Q: Cookie过期了怎么办？
A: 重新运行 `python cookie_manager.py save YOUR_CORP_ID`，需要管理员扫码。

### Q: SSH连接失败？
A: 检查：
1. OPENCLAW_HOST 是否正确
2. SSH私钥是否有权限访问服务器
3. 防火墙是否开放SSH端口

### Q: 企微应用创建失败？
A: 检查：
1. Cookie是否有效
2. 管理员是否有创建应用权限
3. 查看控制台日志获取详细错误

## License

MIT
