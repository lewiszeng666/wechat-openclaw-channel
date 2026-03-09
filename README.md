# WeChat OpenClaw Channel

企业微信 - OpenClaw 自动化接入工具，基于 Cookie 复用方案实现半自动化配置。

## 功能特性

- 🔐 **Cookie 预存方案**：运维人员预先扫码保存 Cookie，24 小时内自动操作
- 🚀 **一键配置**：自动安装 OpenClaw 插件、创建企微应用、配置 Webhook
- 🌐 **Web 控制台**：简洁的 Web 界面，实时显示配置进度
- 👤 **新成员自动接入**：监听通讯录变更事件，为新加入成员自动创建专属企微应用
- 📱 **微信二维码**：自动获取企微微信插件二维码

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
│  │  新成员接入：通讯录变更事件 → 自动创建专属应用       │    │
│  └────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌────────────────────────────────────────────────────┐    │
│  │  Cookie 管理器：预存企微后台 Cookie (24h 有效)      │    │
│  └────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

## 部署指南

### 1. 环境要求

- Python 3.8+
- 可访问 OpenClaw 服务器的 SSH 权限
- 企业微信管理员账号（用于预存 Cookie）

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

# 安装 Playwright 浏览器
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
# Flask 配置
SECRET_KEY=your-secret-key-change-in-production
HOST=0.0.0.0
PORT=5000

# OpenClaw 服务器 SSH 配置
OPENCLAW_HOST=192.168.1.100      # OpenClaw 服务器 IP
OPENCLAW_SSH_USER=root           # SSH 用户名
OPENCLAW_SSH_KEY=~/.ssh/id_rsa   # SSH 私钥路径
OPENCLAW_SSH_PORT=22             # SSH 端口

# 企业微信配置
WECOM_CORP_ID=wwxxxxxxxxx        # 企业 ID（在企微后台「我的企业」获取）
```

### 4. 预存企微 Cookie（关键步骤）

**此步骤必须由运维人员在有显示器的环境执行：**

```bash
# 运行 Cookie 预存工具
python cookie_manager.py save YOUR_CORP_ID

# 示例
python cookie_manager.py save ww1234567890abcdef
```

执行后会：
1. 打开浏览器显示企微登录二维码
2. 使用企业微信 App 扫码登录
3. 登录成功后自动保存 Cookie 到 `browser_data/` 目录

**Cookie 有效期约 20-24 小时，过期后需重新执行此命令。**

检查 Cookie 状态：
```bash
python cookie_manager.py check YOUR_CORP_ID
```

### 5. 启动 Web 服务

```bash
# 开发模式
python app.py

# 生产模式（使用 gunicorn）
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 app:app
```

### 6. 使用 Web 控制台

1. 浏览器访问：`http://your-server:5000`
2. 确认 Cookie 状态为"有效"
3. 输入应用名称
4. 点击"开始配置"
5. 等待配置完成，扫描微信二维码关注

---

## 新成员自动接入

### 功能说明

当有新成员加入企业微信时，系统自动：
1. 接收企微通讯录变更事件回调（`change_contact` / `create_user`）
2. 为新成员创建专属企微应用（名称：`{成员名}的openclaw`，Logo：OpenClaw 小龙虾）
3. 设置应用可见范围为该成员本人
4. 配置接收消息 API（随机生成 Token/EncodingAESKey，URL 指向 OpenClaw）
5. 触发 Secret 发送到管理员企微 App（需人工查看一次）

### 快速测试（命令行）

```bash
# 为指定成员创建专属应用（有头模式，可观察过程）
python wecom_member_callback.py create ww95aca10dfcf3d6e2 louis 101.35.102.240 --visible

# 无头模式（生产环境）
python wecom_member_callback.py create ww95aca10dfcf3d6e2 louis 101.35.102.240
```

### 代码集成

```python
from wecom_member_callback import NewMemberAppCreator

creator = NewMemberAppCreator(corp_id="ww95aca10dfcf3d6e2")
result = creator.create_app_for_member(
    member_name="louis",
    member_user_id="louis",
    openclaw_ip="101.35.102.240",
    headless=True,
    keep_page_open=True
)

if result["success"]:
    print(f"AgentId:        {result['agent_id']}")
    print(f"Token:          {result['token']}")
    print(f"EncodingAESKey: {result['aes_key']}")
    print(f"Webhook URL:    {result['webhook_url']}")
    print("Secret:         需在企微 App 查看（View → Send）")

creator.close()
```

### 通讯录变更事件回调

```python
from wecom_member_callback import ContactChangeCallbackHandler

# 初始化（使用通讯录同步应用的 Token/AESKey）
handler = ContactChangeCallbackHandler(
    corp_id="ww95aca10dfcf3d6e2",
    token="your_token",
    encoding_aes_key="your_aes_key"
)

# 在 Flask/FastAPI 路由中处理回调
@app.route("/wecom/contact", methods=["GET", "POST"])
def contact_callback():
    if request.method == "GET":
        # URL 验证
        echostr = handler.verify_url(
            request.args.get("msg_signature"),
            request.args.get("timestamp"),
            request.args.get("nonce"),
            request.args.get("echostr"),
        )
        return echostr or "error"
    else:
        # 处理事件
        event = handler.handle_callback(
            request.args.get("msg_signature"),
            request.args.get("timestamp"),
            request.args.get("nonce"),
            request.data.decode("utf-8"),
        )
        if event and event["event_type"] == "create_user":
            # 异步创建应用
            config = handler.process_pending_member(openclaw_ip="101.35.102.240")
        return "success"
```

---

## Logo 上传机制说明（已实测验证）

> 以下是对企微后台 Logo 上传弹窗的完整逆向分析，供其他 AI/开发者参考。

### 核心机制

企微后台创建应用页面（`#apps/createApiApp`）的 Logo 上传使用 Backbone.js 事件委托，**不是**标准的 `<input type="file">` 直接上传。

| 步骤 | 操作 | 关键说明 |
|------|------|---------|
| 1 | 触发弹窗 | **必须用 `$(input).trigger('click')`**，不能用原生 click（会被 `preventDefault` 拦截） |
| 2 | 等待弹窗 | 等待 `#__dialog__avatarEditor__` 出现 |
| 3 | 注入文件 | 用 `set_input_files()`（CDP 方式），不能用 DataTransfer（`input.value` 不会被设置） |
| 4 | 等待 cropper | 等待 `.cropper-container` 出现，确认 cropper.js 初始化完成 |
| 5 | 检查 Save 按钮 | 用 `$(btn).attr('disabled') === undefined` 检查（jQuery disabled ≠ 原生 disabled） |
| 6 | 点击 Save | 不加 `force=True`，等待弹窗关闭 |

### 图片要求

- 格式：PNG/JPG/GIF
- 尺寸：**≥ 150×150 像素**（低于此尺寸 Save 按钮点击无效）
- 颜色模式：RGB（RGBA 需转换）

### 弹窗状态

弹窗有两种状态，对应不同的 file input 选择器：

| 状态 | 判断条件 | file input 选择器 |
|------|---------|-----------------|
| 初始（无图） | `.js_no_img` 可见 | `.js_no_img .ww_fileInput` |
| 已有图 | `.js_img_container` 可见 | `.js_file_reupload .js_file` |

---

## Secret 获取说明

> **企微 Secret 无法自动获取，这是企微的硬性安全机制。**

企微后台点击「View → Send」后，Secret 通过内部推送发到管理员的企微 App，**不经过任何 HTTP 响应**，网页端无法截获。

### 获取流程

1. 应用创建完成后，系统自动点击「View → Send」触发推送
2. 管理员在**企业微信 App** 中收到「WeCom Team」消息，其中包含 Secret 明文
3. 复制 Secret，填入配置文件或通过 API 传入

### 配置文件示例

```json
{
  "corp_id": "ww95aca10dfcf3d6e2",
  "agent_id": "1000010",
  "secret": "YOUR_SECRET_FROM_WECOM_APP",
  "token": "EoWgXD3utpLOLifS",
  "aes_key": "HeJvvgnX1GA1zcSyzpu0yz895De3qjiEPcBWwsWgHfQ",
  "webhook_url": "http://101.35.102.240:3000/wecom"
}
```

---

## 使用流程

### 流程图

```
运维人员                          新成员加入
    │                              │
    │  1. 预存 Cookie（扫码）        │
    │                              │
    │                              │  2. 企微通讯录变更事件
    │                              │  ──────────────────→
    │                              │
    │  3. 自动执行:                 │
    │     - 创建专属企微应用         │
    │     - 上传 OpenClaw Logo      │
    │     - 设置可见范围为新成员     │
    │     - 配置接收消息 API        │
    │     - 触发 Secret 发送        │
    │                              │
    │  4. ⚠️ 人工操作（唯一）:       │
    │     在企微 App 查看 Secret    │
    │     写入配置文件              │
    │                              │
    │  5. 功能4 保存 API 配置        │
    │     （OpenClaw 服务响应验证）  │
    │                              │
```

### 扫码次数

| 角色 | 操作 | 次数 |
|------|------|------|
| 运维人员 | 企微后台登录（预存 Cookie） | 1 次/24 小时 |
| 运维人员 | 企微 App 查看 Secret | 每个新应用 1 次 |
| 新成员 | 无需操作 | 0 次 |

---

## API 接口

### 获取 Cookie 状态
```
GET /api/cookie-status
```

### 测试 SSH 连接
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

---

## 文件结构

```
wechat-openclaw-channel/
├── app.py                    # Flask 主应用
├── config.py                 # 配置管理
├── cookie_manager.py         # Cookie 预存工具
├── wecom_automation.py       # 企微后台自动化（基础）
├── wecom_member_callback.py  # 新成员接入模块（核心）
│                             #   - NewMemberAppCreator: 创建应用
│                             #   - ContactChangeCallbackHandler: 回调处理
│                             #   - WXBizMsgCrypt: 消息加解密
├── member_app_manager.py     # 应用管理工具
├── openclaw_plugin.py        # OpenClaw 插件集成
├── openclaw_logo.png         # OpenClaw 小龙虾 Logo（≥150×150）
├── requirements.txt          # Python 依赖
├── .env.example              # 环境变量示例
├── templates/
│   └── index.html            # Web 界面
└── browser_data/             # Cookie/会话存储目录（gitignore）
```

---

## 常见问题

### Q: Cookie 过期了怎么办？
A: 重新运行 `python cookie_manager.py save YOUR_CORP_ID`，需要管理员扫码。

### Q: Logo 上传失败，提示"请上传应用logo"？
A: 检查以下几点：
1. `openclaw_logo.png` 尺寸是否 ≥ 150×150（运行 `python -c "from PIL import Image; print(Image.open('openclaw_logo.png').size)"`）
2. 图片颜色模式是否为 RGB（RGBA 需转换：`img.convert('RGB').save('openclaw_logo.png')`）
3. 确认使用 `$(input).trigger('click')` 触发弹窗，而非原生 click

### Q: SSH 连接失败？
A: 检查：
1. `OPENCLAW_HOST` 是否正确
2. SSH 私钥是否有权限访问服务器
3. 防火墙是否开放 SSH 端口

### Q: 企微应用创建失败？
A: 检查：
1. Cookie 是否有效（`python cookie_manager.py check YOUR_CORP_ID`）
2. 管理员是否有创建应用权限
3. 查看控制台日志获取详细错误

### Q: Secret 能自动获取吗？
A: 不能。企微出于安全机制，Secret 只推送到管理员企微 App，不经过任何 HTTP 响应，无法自动截获。每个新应用需人工在企微 App 查看一次（约 30 秒），之后存入配置文件永久复用。

## License

MIT
