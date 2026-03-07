#!/usr/bin/env python3
"""
飞书开放平台 - 自动创建企业自建机器人 (非交互式)

用法:
    python3 feishu_bot_creator.py init     # 启动浏览器，获取二维码内容 (浏览器保持运行)
    python3 feishu_bot_creator.py poll     # 连接同一浏览器，检测扫码 → 自动 create + apply
    python3 feishu_bot_creator.py cleanup  # 关闭残留浏览器进程

流程: init → (前端展示二维码) → poll (扫码成功后自动完成全部创建)

关键: init 不关闭浏览器，poll 通过 CDP 连接同一实例，复用同一页面的扫码会话。
"""

# ============================================================
# 依赖自举
# ============================================================
import importlib
import importlib.util
import os
import subprocess
import sys
import shutil
import platform

# 对于 init 和 poll 命令，立即抑制所有 stderr 输出，避免污染 JSON 响应
if len(sys.argv) > 1 and sys.argv[1] in ("init", "poll"):
    _devnull = open(os.devnull, "w")
    sys.stderr = _devnull
    # 同时重定向底层 fd 2，防止子进程输出
    os.dup2(_devnull.fileno(), 2)

_REQUIRED_PACKAGES = [("playwright", "playwright")]


def _find_system_chrome() -> str:
    """查找系统安装的 Chrome/Chromium 路径"""
    system = platform.system()
    
    if system == "Linux":
        paths = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
        ]
    elif system == "Darwin":
        paths = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    elif system == "Windows":
        paths = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        paths = []
    
    for path in paths:
        if os.path.isfile(path):
            return path
    
    # 尝试 which 查找
    found = shutil.which("google-chrome") or shutil.which("chromium")
    return found or ""


def _ensure_pip():
    try:
        importlib.import_module("pip")
        return
    except ImportError:
        pass
    print("[准备] 未检测到 pip，正在安装 ...", file=sys.stderr)
    try:
        subprocess.check_call(
            [sys.executable, "-m", "ensurepip", "--upgrade"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    import tempfile, urllib.request
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        urllib.request.urlretrieve("https://bootstrap.pypa.io/get-pip.py", f.name)
        subprocess.check_call(
            [sys.executable, f.name, "--quiet", "--break-system-packages"])


def _ensure_dependencies():
    # 检查是否有系统 Chrome，如果有就不需要安装 Playwright 的 Chromium
    system_chrome = _find_system_chrome()
    # 注意：这里不输出任何内容，避免污染 JSON 响应
    
    missing = [pip for mod, pip in _REQUIRED_PACKAGES
               if not importlib.util.find_spec(mod)]
    if missing:
        _ensure_pip()
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--break-system-packages"] + missing)
        importlib.invalidate_caches()
        import site; site.main()

    # 如果已有系统 Chrome，跳过 Playwright Chromium 检测
    if system_chrome:
        return

    from playwright.sync_api import sync_playwright
    try:
        pw = sync_playwright().start()
        pw.chromium.launch(headless=True).close()
        pw.stop()
    except Exception:
        print("[准备] 正在安装 Chromium ...", file=sys.stderr)
        # 1) 先安装系统依赖（兼容 CentOS/RHEL/Debian/Ubuntu）
        _install_system_deps()
        # 2) 设置环境变量跳过 Playwright 内部的 apt-get 依赖安装
        env = os.environ.copy()
        env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"
        env["DEBIAN_FRONTEND"] = "noninteractive"
        # 3) 安装 Chromium 二进制（不带 --with-deps）
        try:
            subprocess.check_call(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                env=env)
        except subprocess.CalledProcessError:
            # 某些 Playwright 版本 install 命令仍会尝试安装 deps 并报错
            # 但浏览器二进制可能已下载成功，验证一下
            print("[准备] playwright install 返回非零，检查浏览器是否已下载 ...", file=sys.stderr)
            try:
                pw2 = sync_playwright().start()
                path = pw2.chromium.executable_path
                pw2.stop()
                if not os.path.isfile(path):
                    raise FileNotFoundError(path)
                print(f"[准备] Chromium 已就绪: {path}", file=sys.stderr)
            except Exception as e2:
                print(f"[准备] Chromium 安装失败: {e2}", file=sys.stderr)
                sys.exit(1)


def _install_system_deps():
    """尝试用系统包管理器安装 Chromium 运行所需的共享库。"""
    _LIBS_YUM = [
        "nss", "nspr", "atk", "at-spi2-atk", "at-spi2-core",
        "libdrm", "libXcomposite", "libXdamage", "libXrandr",
        "mesa-libgbm", "pango", "cups-libs", "libxkbcommon",
        "alsa-lib", "libXfixes", "libxshmfence",
    ]
    _LIBS_APT = [
        "libnss3", "libnspr4", "libatk1.0-0", "libatk-bridge2.0-0",
        "libdrm2", "libxcomposite1", "libxdamage1", "libxrandr2",
        "libgbm1", "libpango-1.0-0", "libcups2", "libxkbcommon0",
        "libasound2", "libxfixes3", "libxshmfence1",
    ]

    for pkg_mgr, libs in [
        (["yum", "install", "-y"], _LIBS_YUM),
        (["dnf", "install", "-y"], _LIBS_YUM),
        (["apt-get", "install", "-y"], _LIBS_APT),
    ]:
        try:
            subprocess.check_call(
                [pkg_mgr[0], "--version"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
        print(f"[准备] 使用 {pkg_mgr[0]} 安装系统依赖 ...", file=sys.stderr)
        try:
            subprocess.call(
                pkg_mgr + libs,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
        break


_ensure_dependencies()

# ============================================================
# 业务 import
# ============================================================
import io
import json
import os
import random
import signal
import ssl
import time
import uuid
import urllib.request
import urllib.error
from typing import Optional

from playwright.sync_api import sync_playwright, Page

# ============================================================
# 常量
# ============================================================
BASE_URL = "https://open.feishu.cn"
API_BASE = f"{BASE_URL}/developers/v1"
APP_PAGE = f"{BASE_URL}/app"
LOGIN_URL = (
    "https://accounts.feishu.cn/accounts/page/login"
    "?app_id=7&no_trap=1"
    "&redirect_uri=https%3A%2F%2Fopen.feishu.cn%2Fapp"
)
LOGIN_TIMEOUT = 90
POLL_INTERVAL = 2

AVATAR_URL = "https://90-1251810746.cos.ap-guangzhou.myqcloud.com/ico.png"

OPENCLAW_CONFIG = "/root/.openclaw/openclaw.json"
OPENCLAW_ALLOW_FROM = "/root/.openclaw/credentials/feishu-default-allowFrom.json"
WEBSOCKET_POLL_INTERVAL = 3   # 轮询长连接状态的间隔 (秒)
WEBSOCKET_POLL_TIMEOUT = 60   # 等待长连接建立的最大时间 (秒)

STATE_DIR = "/tmp"
STATE_FILE = os.path.join(STATE_DIR, "feishu-bot-creator-state.json")
CDP_PORT = 9222  # Chromium CDP 调试端口

BOT_PERMISSIONS = [
    "im:message", "im:message.p2p_msg:readonly",
    "im:message.group_at_msg:readonly", "im:message:send_as_bot",
    "im:resource", "im:message.group_msg", "im:message:readonly",
    "im:message:update", "im:message:recall", "im:message.reactions:read",
    "contact:user.base:readonly", "contact:contact.base:readonly",
    "docx:document:readonly", "docx:document", "docx:document.block:convert",
    "drive:drive:readonly", "drive:drive",
    "wiki:wiki:readonly", "wiki:wiki",
    "bitable:app:readonly", "bitable:app",
    "task:task:read", "task:task:write",
]


def _gen_bot_name() -> str:
    return f"OpenClaw机器人-{random.randint(1000, 9999)}"


def _find_existing_openclaw_bot(creator: "FeishuBotCreator") -> Optional[dict]:
    """
    检查是否已存在可用的 OpenClaw 机器人。
    
    条件:
    1. 应用名称包含 "OpenClaw" 或 "openclaw"
    2. 应用已启用 (appStatus >= 1)
    3. 应用有机器人能力
    
    返回: {"app_id": str, "name": str} 或 None
    """
    _log("[检查] 查找已存在的 OpenClaw 机器人...")
    
    # 获取应用列表
    body = creator._get(f"{API_BASE}/app/list?page=1&page_size=50")
    if not body or body.get("code") != 0:
        _log("  [跳过] 无法获取应用列表")
        return None
    
    apps = body.get("data", {}).get("apps", [])
    if not apps:
        _log("  [跳过] 没有已创建的应用")
        return None
    
    _log(f"  找到 {len(apps)} 个应用，开始筛选...")
    
    for app in apps:
        app_name = app.get("name", "") or app.get("appName", "")
        app_id = app.get("clientId") or app.get("appId") or app.get("app_id", "")
        app_status = app.get("appStatus", 0)  # 1=已上线, 0=开发中
        
        # 检查名称是否包含 OpenClaw
        if "openclaw" not in app_name.lower():
            continue
        
        _log(f"  检查应用: {app_name} (app_id={app_id}, status={app_status})")
        
        # 检查是否有机器人能力
        detail = creator._get(f"{API_BASE}/app/{app_id}")
        if not detail or detail.get("code") != 0:
            _log(f"    [跳过] 无法获取应用详情")
            continue
        
        app_data = detail.get("data", {})
        abilities = app_data.get("abilities", [])
        has_bot = any(
            ab.get("abilityId") == "robot" or ab.get("ability_id") == "robot" or ab.get("type") == "bot"
            for ab in abilities
        ) if abilities else False
        
        # 也检查 robotEnabled 字段
        robot_enabled = app_data.get("robotEnabled", False) or app_data.get("robot_enabled", False)
        
        if not has_bot and not robot_enabled:
            _log(f"    [跳过] 没有机器人能力 (abilities={abilities})")
            continue
        
        # 检查应用状态
        actual_status = app_data.get("appStatus", app_status)
        if actual_status < 1:
            _log(f"    [跳过] 应用未上线 (status={actual_status})")
            continue
        
        _log(f"    [匹配] 找到可用的 OpenClaw 机器人!")
        return {
            "app_id": app_id,
            "name": app_name,
        }
    
    _log("  [结果] 未找到可复用的 OpenClaw 机器人")
    return None


# ============================================================
# 状态文件 & 工具
# ============================================================
def _save_state(data: dict) -> None:
    with open(STATE_FILE, "w") as f:
        json.dump(data, f, ensure_ascii=False)


def _output_json(data: dict) -> None:
    """输出 JSON 到 stdout 并 flush，确保不被缓冲"""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


def _load_state() -> dict:
    if not os.path.isfile(STATE_FILE):
        _output_json({"status": "error", "message": "状态文件不存在，请先运行 init"})
        sys.exit(1)
    with open(STATE_FILE) as f:
        return json.load(f)


# 全局日志收集器
_log_buffer = []

def _log(msg: str) -> None:
    _log_buffer.append(msg)
    print(msg, file=sys.stderr)


def _download_avatar() -> str:
    avatar_path = os.path.join(STATE_DIR, "feishu-bot-avatar.png")
    if os.path.isfile(avatar_path) and os.path.getsize(avatar_path) > 0:
        _log(f"[头像] 使用已缓存: {avatar_path}")
        return avatar_path

    _log(f"[头像] 正在下载: {AVATAR_URL}")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(AVATAR_URL, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, context=ctx) as resp:
            data = resp.read()
        with open(avatar_path, "wb") as f:
            f.write(data)
        _log(f"[头像] 下载完成: {len(data)} bytes → {avatar_path}")
        return avatar_path
    except Exception as e:
        _log(f"[头像] 下载失败: {e}")
        return ""


def _write_openclaw_config(app_id: str, app_secret: str) -> bool:
    """将飞书 app_id/app_secret 写入 openclaw.json 配置文件。"""
    _log(f"[配置] 写入 openclaw 配置: {OPENCLAW_CONFIG}")

    # 确保目录存在
    config_dir = os.path.dirname(OPENCLAW_CONFIG)
    os.makedirs(config_dir, exist_ok=True)

    # 读取现有配置（如果存在）
    config = {}
    if os.path.isfile(OPENCLAW_CONFIG):
        try:
            with open(OPENCLAW_CONFIG) as f:
                config = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            _log(f"  [警告] 读取现有配置失败: {e}, 将创建新配置")

    # 写入 feishu 频道配置
    if "channels" not in config:
        config["channels"] = {}
    config["channels"]["feishu"] = {
        "enabled": True,
        "appId": app_id,
        "appSecret": app_secret,
        "domain": "feishu",
        "groupPolicy": "open",
    }

    try:
        with open(OPENCLAW_CONFIG, "w") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _log(f"  [成功] 已写入 feishu 配置 (appId={app_id})")
        return True
    except IOError as e:
        _log(f"  [失败] 写入配置文件: {e}")
        return False


def _write_allow_from(open_id: str) -> bool:
    """将 owner open_id 写入 feishu-default-allowFrom.json。"""
    _log(f"[配置] 写入 allowFrom: {OPENCLAW_ALLOW_FROM}")

    config_dir = os.path.dirname(OPENCLAW_ALLOW_FROM)
    os.makedirs(config_dir, exist_ok=True)

    data = {
        "version": 1,
        "allowFrom": [open_id],
    }

    try:
        with open(OPENCLAW_ALLOW_FROM, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        _log(f"  [成功] 已写入 allowFrom (open_id={open_id})")
        return True
    except IOError as e:
        _log(f"  [失败] 写入 allowFrom: {e}")
        return False


def _kill_cdp_browser():
    """杀掉占用 CDP_PORT 的残留 Chromium 进程。"""
    # 先尝试用保存的 PID
    if os.path.isfile(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            pid = data.get("chrome_pid")
            if pid:
                _log(f"[cleanup] 杀掉保存的 Chromium PID={pid}")
                os.kill(int(pid), signal.SIGKILL)
        except (json.JSONDecodeError, OSError, ProcessLookupError):
            pass

    # 再用 lsof/fuser 兜底
    try:
        out = subprocess.check_output(
            ["lsof", "-ti", f":{CDP_PORT}"], stderr=subprocess.DEVNULL
        ).decode().strip()
        if out:
            for pid in out.split("\n"):
                pid = pid.strip()
                if pid:
                    _log(f"[cleanup] 杀掉端口占用进程 PID={pid}")
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except (OSError, ProcessLookupError):
                        pass
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 清理 profile lock 文件
    profile_dir = os.path.join(STATE_DIR, "feishu-bot-chrome-profile")
    for lock_file in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        p = os.path.join(profile_dir, lock_file)
        if os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass


# ============================================================
# FeishuBotCreator (不变)
# ============================================================
class FeishuBotCreator:

    def __init__(self, page: Page):
        self.page = page
        self.csrf_token: Optional[str] = None
        self.app_id: Optional[str] = None
        self.app_secret: Optional[str] = None
        self.version_id: Optional[str] = None

    def install_network_capture(self) -> None:
        def _on_request(req):
            if "open.feishu.cn" not in req.url:
                return
            token = req.headers.get("x-csrf-token") or req.headers.get("X-CSRF-Token")
            if token:
                self.csrf_token = token
        self.page.on("request", _on_request)

    def _csrf(self) -> Optional[str]:
        if self.csrf_token:
            return self.csrf_token
        try:
            token = self.page.evaluate("window.csrfToken || ''")
            if token:
                self.csrf_token = token
                return token
        except Exception:
            pass
        try:
            cookies = {c["name"]: c["value"]
                       for c in self.page.context.cookies([BASE_URL])}
            token = (cookies.get("lark_oapi_csrf_token")
                     or cookies.get("lgw_csrf_token")
                     or cookies.get("swp_csrf_token"))
            if token:
                self.csrf_token = token
            return token
        except Exception:
            return None

    def _headers(self, *, with_body: bool = False) -> dict:
        h = {"accept": "*/*", "x-timezone-offset": "-480"}
        if with_body:
            h.update({"content-type": "application/json",
                       "origin": BASE_URL, "referer": APP_PAGE})
        csrf = self._csrf()
        if csrf:
            h["x-csrf-token"] = csrf
        return h

    def _post(self, url: str, payload: dict) -> Optional[dict]:
        try:
            resp = self.page.request.post(
                url, data=payload, headers=self._headers(with_body=True))
            return resp.json()
        except Exception as e:
            _log(f"  [失败] POST {url}: {e}")
            return None

    def _get(self, url: str) -> Optional[dict]:
        try:
            return self.page.request.get(url, headers=self._headers()).json()
        except Exception as e:
            _log(f"  [失败] GET {url}: {e}")
            return None

    def _ok(self, body: Optional[dict], step: str) -> Optional[dict]:
        if body is None:
            return None
        if body.get("code") != 0:
            _log(f"  [失败] {step}: code={body.get('code')}, msg={body.get('msg')}")
            return None
        _log(f"  [成功] {step}")
        return body

    @staticmethod
    def _build_multipart(fields: dict, files: dict):
        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex[:16]}"
        parts = []
        for key, value in fields.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            parts.append(f"{value}\r\n".encode())
        for key, (filename, data, content_type) in files.items():
            parts.append(f"--{boundary}\r\n".encode())
            parts.append(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode())
            parts.append(f"Content-Type: {content_type}\r\n\r\n".encode())
            parts.append(data)
            parts.append(b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        return b"".join(parts), f"multipart/form-data; boundary={boundary}"

    def _upload_avatar(self, avatar_path: str) -> Optional[str]:
        with open(avatar_path, "rb") as f:
            img_data = f.read()

        csrf = self._csrf()
        if not csrf:
            _log("  [失败] 未获取到 CSRF token")
            return None

        browser_cookies = self.page.context.cookies([BASE_URL])
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in browser_cookies)

        body, content_type = self._build_multipart(
            fields={
                "uploadType": "4",
                "isIsv": "false",
                "scale": '{"width":240,"height":240}',
            },
            files={
                "file": (str(uuid.uuid4()), img_data, "image/png"),
            },
        )

        headers = {
            "Accept": "*/*",
            "Content-Type": content_type,
            "Cookie": cookie_str,
            "Origin": BASE_URL,
            "Referer": APP_PAGE,
            "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/145.0.0.0 Safari/537.36"),
            "x-csrf-token": csrf,
            "x-timezone-offset": "-480",
        }

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            f"{API_BASE}/app/upload/image",
            data=body, headers=headers, method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ssl_ctx) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode()
            _log(f"  [失败] 上传图片 HTTP {e.code}: {error_body[:300]}")
            return None
        except Exception as e:
            _log(f"  [失败] 上传图片异常: {e}")
            return None

        if result.get("code") != 0:
            _log(f"  [失败] 上传图片: code={result.get('code')}, msg={result.get('msg')}")
            return None

        url = result["data"].get("url", "")
        _log(f"  [成功] 上传图片: {url}")
        return url

    def step1_create_app(self, name: str, desc: str, avatar_path: str) -> bool:
        _log(f"[步骤 1] 创建企业自建应用: {name}")
        if avatar_path and os.path.isfile(avatar_path):
            _log(f"  上传图标: {os.path.basename(avatar_path)}")
            avatar_url = self._upload_avatar(avatar_path)
            if not avatar_url:
                _log("  [警告] 头像上传失败，尝试用空头像创建")
                avatar_url = ""
        else:
            _log("  [警告] 无头像文件，用空头像创建")
            avatar_url = ""

        body = self._post(f"{API_BASE}/app/create", {
            "appSceneType": 0, "name": name, "desc": desc,
            "avatar": avatar_url,
            "i18n": {"zh_cn": {"name": name, "description": desc}},
            "primaryLang": "zh_cn",
        })
        if not self._ok(body, "创建应用"):
            return False
        self.app_id = body["data"]["ClientID"]
        _log(f"  App ID: {self.app_id}")
        return True

    def step2_get_credentials(self) -> bool:
        _log("[步骤 2] 获取应用凭证")
        body = self._get(f"{API_BASE}/secret/{self.app_id}")
        if not self._ok(body, "获取 App Secret"):
            return False
        d = body.get("data", {})
        self.app_secret = (d.get("appSecret") or d.get("app_secret")
                           or d.get("secret") or d.get("AppSecret"))
        if not self.app_secret:
            _log(f"  [失败] 未找到 App Secret, keys={list(d.keys())}")
            return False
        _log(f"  App ID:     {self.app_id}")
        _log(f"  App Secret: {self.app_secret}")
        return True

    def step3_add_bot(self) -> bool:
        _log("[步骤 3] 添加机器人能力")
        return self._ok(
            self._post(f"{API_BASE}/robot/switch/{self.app_id}", {"enable": True}),
            "开启机器人能力") is not None

    def step4_event_mode(self) -> bool:
        """切换事件模式为长连接 (WebSocket)，轮询等待 openclaw 建立连接。"""
        _log("[步骤 4] 切换事件模式为长连接 (WebSocket)")
        deadline = time.time() + WEBSOCKET_POLL_TIMEOUT
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            body = self._post(f"{API_BASE}/event/switch/{self.app_id}", {"eventMode": 4})
            if body and body.get("code") == 10068:
                _log(f"  [等待] 尚未建立 WebSocket 连接 (第{attempt}次, code=10068)，"
                     f"{WEBSOCKET_POLL_INTERVAL}s 后重试...")
                time.sleep(WEBSOCKET_POLL_INTERVAL)
                continue
            if self._ok(body, "切换事件模式 → WebSocket(4)") is not None:
                return True
            # 其他错误直接失败
            return False
        _log(f"  [失败] 等待 WebSocket 连接超时 ({WEBSOCKET_POLL_TIMEOUT}s)")
        return False

    def step5_add_event(self) -> bool:
        _log("[步骤 5] 添加「接收消息」事件")
        ev = self._get(f"{API_BASE}/event/{self.app_id}")
        mode = ev.get("data", {}).get("eventMode", 1) if ev and ev.get("code") == 0 else 1
        body = self._post(f"{API_BASE}/event/update/{self.app_id}", {
            "operation": "add",
            "events": ["im.message.receive_v1"],
            "eventMode": mode,
        })
        if not self._ok(body, "添加 im.message.receive_v1"):
            return False
        verify = self._get(f"{API_BASE}/event/{self.app_id}")
        if verify and verify.get("code") == 0:
            events = verify["data"].get("events", [])
            tag = "✓" if "im.message.receive_v1" in events else "⚠"
            _log(f"  {tag} 当前事件列表: {events}")
        return True

    def step6_callback_mode(self) -> bool:
        """配置长连接接收回调，轮询等待 WebSocket 连接就绪。"""
        _log("[步骤 6] 配置长连接接收回调")
        deadline = time.time() + WEBSOCKET_POLL_TIMEOUT
        attempt = 0
        while time.time() < deadline:
            attempt += 1
            body = self._post(f"{API_BASE}/callback/switch/{self.app_id}", {"callbackMode": 4})
            if body and body.get("code") == 10068:
                _log(f"  [等待] 尚未建立 WebSocket 连接 (第{attempt}次, code=10068)，"
                     f"{WEBSOCKET_POLL_INTERVAL}s 后重试...")
                time.sleep(WEBSOCKET_POLL_INTERVAL)
                continue
            if self._ok(body, "切换回调模式 → 长连接(4)") is not None:
                return True
            return False
        _log(f"  [失败] 等待 WebSocket 连接超时 ({WEBSOCKET_POLL_TIMEOUT}s)")
        return False

    def step7_permissions(self) -> bool:
        _log("[步骤 7] 批量导入权限")
        body = self._get(f"{API_BASE}/scope/all/{self.app_id}")
        if not self._ok(body, "获取权限列表"):
            return False
        name_to_id = {}
        for s in body.get("data", {}).get("scopes", []):
            name = s.get("name") or s.get("scopeName", "")
            sid = s.get("id", "")
            if name and sid:
                name_to_id[name] = str(sid)
        ids = [name_to_id[n] for n in BOT_PERMISSIONS if n in name_to_id]
        missing = [n for n in BOT_PERMISSIONS if n not in name_to_id]
        if missing:
            _log(f"  ⚠ 未匹配: {missing}")
        _log(f"  匹配 {len(ids)}/{len(BOT_PERMISSIONS)} 个权限")
        if not ids:
            _log("  [失败] 无可用权限 ID")
            return False
        body = self._post(f"{API_BASE}/scope/update/{self.app_id}", {
            "clientId": self.app_id,
            "appScopeIDs": ids, "userScopeIDs": [], "scopeIds": [],
            "operation": "add",
        })
        return self._ok(body, "批量添加权限") is not None

    def step8_publish(self, version: str = "1.0.0") -> bool:
        _log(f"[步骤 8] 创建版本 v{version} 并发布")
        body = self._post(f"{API_BASE}/app_version/create/{self.app_id}", {
            "clientId": self.app_id, "appVersion": version,
            "changeLog": "初始版本", "autoPublish": False,
            "pcDefaultAbility": "bot", "mobileDefaultAbility": "bot",
        })
        if not self._ok(body, "创建版本"):
            return False
        self.version_id = body.get("data", {}).get("versionId") or body["data"].get("version_id")
        if not self.version_id:
            _log("  [失败] 未获取到版本 ID")
            return False
        _log(f"  版本 ID: {self.version_id}")

        time.sleep(1)
        body = self._post(f"{API_BASE}/publish/commit/{self.app_id}/{self.version_id}", {})
        if not self._ok(body, "提交审核"):
            return False

        time.sleep(1)
        body = self._post(
            f"{API_BASE}/publish/release/{self.app_id}/{self.version_id}",
            {"clientId": self.app_id, "versionId": self.version_id})
        if body and body.get("code") == 0:
            _log("  [成功] 发布完成")
        elif body:
            _log(f"  [跳过] 发布: code={body.get('code')}, msg={body.get('msg')}")

        time.sleep(1)
        info = self._get(f"{API_BASE}/app/{self.app_id}")
        if info and info.get("code") == 0:
            d = info["data"]
            _log(f"  应用状态: appStatus={d.get('appStatus')}, auditStatus={d.get('auditStatus')}")
        return True

    def step9_get_owner_open_id(self) -> Optional[str]:
        _log("[步骤 9] 获取应用 Owner 的 open_id")
        if not self.app_id or not self.app_secret:
            _log("  [跳过] 缺少 app_id 或 app_secret")
            return None

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        _log("  获取 tenant_access_token ...")
        payload = json.dumps({
            "app_id": self.app_id, "app_secret": self.app_secret,
        }).encode()
        req = urllib.request.Request(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            data=payload,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, context=ctx) as resp:
                token_data = json.loads(resp.read())
        except Exception as e:
            _log(f"  [失败] 获取 token: {e}")
            return None

        token = token_data.get("tenant_access_token")
        if not token:
            _log(f"  [失败] 未获取到 token: {token_data}")
            return None
        _log(f"  [成功] tenant_access_token: {token[:20]}...")

        _log("  查询用户列表获取 open_id ...")
        req2 = urllib.request.Request(
            "https://open.feishu.cn/open-apis/contact/v3/users?page_size=50&user_id_type=open_id",
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {token}",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req2, context=ctx) as resp:
                user_data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            _log(f"  [失败] HTTP {e.code}: {body[:300]}")
            return None
        except Exception as e:
            _log(f"  [失败] 查询用户: {e}")
            return None

        items = user_data.get("data", {}).get("items", [])
        if not items:
            _log(f"  [失败] 用户列表为空: {json.dumps(user_data, ensure_ascii=False)[:300]}")
            return None

        owner = items[0]
        open_id = owner.get("open_id", "")
        name = owner.get("name", "未知")
        _log(f"  [成功] 用户: {name}, open_id: {open_id}")
        return open_id


# ============================================================
# 命令: init
# 直接启动独立 Chromium 进程 (CDP 端口)，用 Playwright 连接获取二维码 token。
# Chromium 作为 detached 进程运行，Python 退出后浏览器不会关闭。
# ============================================================
def _get_chromium_path() -> str:
    """获取 Chrome/Chromium 路径，优先使用系统安装的 Chrome。"""
    # 优先使用系统 Chrome
    system_chrome = _find_system_chrome()
    if system_chrome:
        _log(f"[浏览器] 使用系统 Chrome: {system_chrome}")
        return system_chrome
    
    # 回退到 Playwright 自带的 Chromium
    _log("[浏览器] 未找到系统 Chrome，使用 Playwright Chromium")
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    path = pw.chromium.executable_path
    pw.stop()
    return path


def _launch_detached_chromium() -> int:
    """启动独立的 Chromium 进程 (headless, CDP 端口)，返回 PID。"""
    chrome_path = _get_chromium_path()
    if not os.path.isfile(chrome_path):
        raise FileNotFoundError(f"Chromium 不存在: {chrome_path}")

    user_data_dir = os.path.join(STATE_DIR, "feishu-bot-chrome-profile")
    os.makedirs(user_data_dir, exist_ok=True)

    args = [
        chrome_path,
        "--headless=new",
        f"--remote-debugging-port={CDP_PORT}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--disable-extensions",
        "--disable-background-networking",
        "--no-sandbox",
        "about:blank",
    ]

    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,  # detach: 父进程退出后子进程继续运行
    )
    _log(f"[init] Chrome 已启动, PID={proc.pid}")
    return proc.pid


def _wait_for_cdp_ready(timeout: int = 15) -> bool:
    """等待 CDP 端口可用。"""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", CDP_PORT), timeout=1)
            s.close()
            return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def cmd_init():
    # 抑制所有 stderr 输出（包括 _log 和 node DeprecationWarning）
    _orig_stderr = sys.stderr
    sys.stderr = open(os.devnull, "w")
    os.dup2(os.open(os.devnull, os.O_WRONLY), 2)

    _kill_cdp_browser()  # 清理残留

    # 删除整个 Chrome profile 目录，确保无缓存/cookie 干扰
    profile_dir = os.path.join(STATE_DIR, "feishu-bot-chrome-profile")
    if os.path.isdir(profile_dir):
        _log("[init] 清理旧 Chrome profile 目录...")
        shutil.rmtree(profile_dir, ignore_errors=True)

    # 启动独立 Chromium 进程
    chrome_pid = _launch_detached_chromium()

    # 等待 CDP 端口就绪
    _log("[init] 等待 CDP 端口就绪...")
    if not _wait_for_cdp_ready(timeout=20):
        _log("[init] CDP 端口超时，杀掉 Chromium")
        try:
            os.kill(chrome_pid, signal.SIGKILL)
        except OSError:
            pass
        _output_json({"status": "error", "message": "Chromium 启动超时"})
        sys.exit(1)

    _log("[init] CDP 端口就绪，通过 Playwright 连接获取二维码 token...")

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
    except Exception as e:
        pw.stop()
        try:
            os.kill(chrome_pid, signal.SIGKILL)
        except OSError:
            pass
        _output_json({"status": "error", "message": f"连接 Chromium 失败: {e}"})
        sys.exit(1)

    # 获取页面 (Chromium 启动时加载的是 about:blank)
    contexts = browser.contexts
    if not contexts or not contexts[0].pages:
        page = browser.new_context().new_page()
    else:
        page = contexts[0].pages[0]

    # 先注册 listener，再导航，确保能捕获 qrlogin/init 响应
    state = {"qr_token": None}

    def _on_response(resp):
        try:
            if "qrlogin/init" in resp.url:
                body = resp.json()
                if body.get("code") == 0:
                    state["qr_token"] = body["data"]["step_info"]["token"]
        except Exception:
            pass

    page.on("response", _on_response)

    _log("[init] 导航到飞书登录页...")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    # 轮询等待 qrlogin/init XHR 返回 token，每 200ms 检查一次，最多 5s
    for _ in range(25):
        if state["qr_token"]:
            break
        page.wait_for_timeout(200)

    if not state["qr_token"]:
        _log("[init] 未获取到 token，刷新重试...")
        page.reload(wait_until="domcontentloaded", timeout=30000)
        for _ in range(25):
            if state["qr_token"]:
                break
            page.wait_for_timeout(200)

    if not state["qr_token"]:
        pw.stop()  # 断开 CDP，不杀 Chromium
        try:
            os.kill(chrome_pid, signal.SIGKILL)
        except OSError:
            pass
        _output_json({"status": "error", "message": "未能获取二维码 token"})
        sys.exit(1)

    qr_content = json.dumps({"qrlogin": {"token": state["qr_token"]}})
    deadline = int(time.time()) + LOGIN_TIMEOUT

    cdp_url = f"http://127.0.0.1:{CDP_PORT}"

    _save_state({
        "phase": "init",
        "qr_token": state["qr_token"],
        "qr_content": qr_content,
        "deadline": deadline,
        "cdp_url": cdp_url,
        "chrome_pid": chrome_pid,
    })

    # 输出结果到 stdout（只输出 qr_content 原始 JSON，不包装）
    print(qr_content)

    # 断开 Playwright CDP 连接，但 Chromium 独立进程继续运行
    _log(f"[init] 浏览器独立运行 (PID={chrome_pid}, CDP port={CDP_PORT})，等待 poll 连接...")
    pw.stop()
    sys.exit(0)


# ============================================================
# 命令: poll
# 通过 CDP 连接 init 启动的浏览器，检测扫码 → 自动 create + apply
# ============================================================
def cmd_poll():
    data = _load_state()

    if data.get("phase") not in ("init",):
        _output_json({"status": "error", "message": f"当前阶段为 {data.get('phase')}，请先运行 init"})
        sys.exit(1)

    deadline = data.get("deadline", 0)
    if time.time() > deadline:
        _kill_cdp_browser()
        _output_json({"status": "expired", "message": "二维码已过期，请重新 init"})
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()

    # 连接到 init 启动的浏览器
    cdp_url = data.get("cdp_url", f"http://127.0.0.1:{CDP_PORT}")
    _log(f"[poll] 连接到浏览器: {cdp_url}")

    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
    except Exception as e:
        pw.stop()
        _output_json({"status": "error", "message": f"无法连接浏览器 (是否已运行 init?): {e}"})
        sys.exit(1)

    # 获取 init 创建的页面
    contexts = browser.contexts
    if not contexts or not contexts[0].pages:
        browser.close()
        pw.stop()
        _output_json({"status": "error", "message": "浏览器中没有页面，请重新 init"})
        sys.exit(1)

    page = contexts[0].pages[0]
    _log(f"[poll] 当前页面 URL: {page.url}")

    # 监听扫码轮询响应
    state = {"login_ok": False, "scanned": False}

    def _on_response(resp):
        try:
            if "qrlogin/polling" in resp.url:
                body = resp.json()
                info = body.get("data", {}).get("step_info", {})
                status = info.get("status")
                if status == 2:
                    state["scanned"] = True
                if status not in (None, 1, 2) or body.get("data", {}).get("redirect_url"):
                    state["login_ok"] = True
        except Exception:
            pass

    page.on("response", _on_response)

    # 检查是否已跳转到开放平台 (说明已登录)
    current_url = page.url
    if "open.feishu.cn" in current_url and "accounts.feishu.cn" not in current_url:
        state["login_ok"] = True

    if not state["login_ok"]:
        # 最多等待 2s 重试 1 次
        for _ in range(1):
            page.wait_for_timeout(2000)
            if state["login_ok"]:
                break
            current_url = page.url
            if "open.feishu.cn" in current_url and "accounts.feishu.cn" not in current_url:
                state["login_ok"] = True
                break

    if not state["login_ok"]:
        # 不关闭浏览器，让用户可以继续 poll
        pw.stop()  # 断开 CDP 连接但不杀浏览器
        if state["scanned"]:
            _output_json({"status": "scanned", "message": "已扫码，等待确认"})
            sys.exit(2)
        _output_json({"status": "pending", "message": "等待扫码"})
        sys.exit(2)

    # ---- 登录成功！确保在开放平台 ----
    _log("[poll] 扫码登录成功!")
    if "accounts.feishu.cn" in page.url:
        _log("[poll] 跳转到开放平台...")
        page.goto(APP_PAGE, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    creator = FeishuBotCreator(page)
    creator.install_network_capture()

    page.goto(APP_PAGE, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    csrf = creator._csrf()
    _log(f"  CSRF token: {'获取成功' if csrf else '未获取，继续尝试'}")

    # ---- 检查是否已存在可用的 OpenClaw 机器人 ----
    existing_bot = _find_existing_openclaw_bot(creator)
    if existing_bot:
        _log(f"[复用] 找到已存在的 OpenClaw 机器人: {existing_bot['name']} (app_id={existing_bot['app_id']})")
        creator.app_id = existing_bot["app_id"]
        
        # 获取 app_secret
        if not creator.step2_get_credentials():
            _log("[警告] 无法获取已有应用的凭证，尝试创建新应用")
            existing_bot = None
        else:
            _log(f"[复用] 已获取凭证: app_id={creator.app_id}")

    if not existing_bot:
        # ---- 自动执行 create ----
        _log("[poll] 未找到可复用的机器人，创建新应用 ...")

        bot_name = _gen_bot_name()
        bot_desc = bot_name
        avatar_path = _download_avatar()

        if not creator.step1_create_app(bot_name, bot_desc, avatar_path):
            _kill_cdp_browser(); pw.stop()
            _output_json({"status": "error", "message": "创建应用失败", "logs": _log_buffer[-20:]})
            sys.exit(1)

        if not creator.step2_get_credentials():
            _kill_cdp_browser(); pw.stop()
            _output_json({"status": "error", "message": "获取凭证失败", "logs": _log_buffer[-20:]})
            sys.exit(1)

        _log(f"[create] 完成: app_id={creator.app_id}")
    else:
        bot_name = existing_bot["name"]

    # ---- 写入 openclaw 配置，让服务建立长连接 ----
    _log("[配置] 写入 openclaw.json，等待服务建立长连接 ...")
    if not _write_openclaw_config(creator.app_id, creator.app_secret):
        _kill_cdp_browser(); pw.stop()
        _output_json({"status": "error", "message": "写入 openclaw 配置失败"})
        sys.exit(1)

    # 等待 2 秒让 openclaw 服务读取新配置并建立 WebSocket 连接
    _log("[配置] 等待 2s 让 openclaw 服务读取配置 ...")
    time.sleep(2)

    # ---- 自动执行 apply (仅新创建的应用需要) ----
    if not existing_bot:
        _log("[apply] 开始添加能力/事件/权限/发布 ...")

        steps = [
            creator.step3_add_bot,
            creator.step4_event_mode,
            creator.step5_add_event,
            creator.step6_callback_mode,
            creator.step7_permissions,
            creator.step8_publish,
        ]
        for fn in steps:
            if not fn():
                _kill_cdp_browser(); pw.stop()
                _output_json({"status": "error", "message": f"{fn.__name__} 失败"})
                sys.exit(1)
    else:
        _log("[复用] 已有应用无需重新配置，跳过 apply 步骤")

    open_id = creator.step9_get_owner_open_id()

    # ---- 写入 allowFrom 配置 ----
    if open_id:
        _write_allow_from(open_id)
    else:
        _log("[警告] 未获取到 open_id，跳过 allowFrom 写入")

    # 全部完成，关闭浏览器
    _kill_cdp_browser()
    pw.stop()

    result = {
        "status": "ok",
        "app_id": creator.app_id,
        "app_secret": creator.app_secret,
        "bot_name": bot_name,
        "version_id": creator.version_id,
        "open_id": open_id,
        "manage_url": f"{BASE_URL}/app/{creator.app_id}",
        "reused": existing_bot is not None,  # 标识是复用还是新创建
    }

    _save_state({"phase": "done", **result})
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ============================================================
# 命令: cleanup
# ============================================================
def cmd_cleanup():
    _kill_cdp_browser()
    if os.path.isfile(STATE_FILE):
        os.remove(STATE_FILE)
    _output_json({"status": "ok", "message": "已清理"})


# ============================================================
# 入口
# ============================================================
def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        print("用法:")
        print(f"  {sys.argv[0]} init       启动浏览器，获取二维码内容 (JSON)")
        print(f"  {sys.argv[0]} poll       检测扫码状态，成功后自动 create + apply")
        print(f"  {sys.argv[0]} cleanup    关闭残留浏览器，清理状态")
        print()
        print("流程: init → (前端展示二维码) → poll (循环直到成功)")
        print("exit code: 0=成功, 1=错误, 2=等待中(poll)")
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "init":
        cmd_init()
    elif cmd == "poll":
        cmd_poll()
    elif cmd == "cleanup":
        cmd_cleanup()
    else:
        _output_json({"status": "error", "message": f"未知命令: {cmd}"})
        sys.exit(1)


if __name__ == "__main__":
    main()
