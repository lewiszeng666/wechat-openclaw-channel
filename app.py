"""
OpenClaw企微接入自动化Web控制台

前提：客户已经建立了飞书和OpenClaw通信通道

流程：
1. 验证飞书↔OpenClaw通道是否可用
2. 通过企微后台创建应用，获取 corpId、corpSecret、agentId
3. 通过飞书通道发送命令安装配置 wecom-app 插件
4. 获取微信插件二维码供客户扫码绑定
"""
import os
import json
import secrets
import subprocess
import threading
import time
import logging
from typing import Dict, Optional
from datetime import datetime
from flask import Flask, render_template, jsonify, request, redirect, url_for
from config import Config
from cookie_manager import get_session_status
from wecom_automation import WeComAutomation
from openclaw_plugin import (
    send_test_message_via_bot,
    send_command_via_bot,
    install_wecom_plugin_via_feishu,
    generate_wecom_install_commands
)
from wecom_member_callback import (
    ContactChangeCallbackHandler,
    NewMemberAppCreator,
    parse_contact_change_event,
    WXBizMsgCrypt
)

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config.from_object(Config)

# 存储进行中的任务
tasks = {}
# 飞书扫码状态
feishu_sessions = {}
# 飞书 poll 锁，防止并发请求导致浏览器被关闭
feishu_poll_lock = threading.Lock()
# 通讯录变更回调处理器（按 corp_id 存储）
callback_handlers: Dict[str, ContactChangeCallbackHandler] = {}
# 新成员应用创建器（按 corp_id 存储，保持页面打开）
member_app_creators: Dict[str, NewMemberAppCreator] = {}

# OpenClaw 配置文件路径
OPENCLAW_CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'openclaw_config.json')


def load_openclaw_config() -> Dict:
    """加载 OpenClaw 配置"""
    if os.path.exists(OPENCLAW_CONFIG_FILE):
        try:
            with open(OPENCLAW_CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"加载 OpenClaw 配置失败: {e}")
    return {}


def save_openclaw_config(config: Dict):
    """保存 OpenClaw 配置"""
    try:
        with open(OPENCLAW_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info(f"OpenClaw 配置已保存: {OPENCLAW_CONFIG_FILE}")
    except Exception as e:
        logger.error(f"保存 OpenClaw 配置失败: {e}")


def get_openclaw_host() -> Optional[str]:
    """获取 OpenClaw 服务器地址（优先从配置文件，其次从环境变量）"""
    config = load_openclaw_config()
    host = config.get("host") or Config.OPENCLAW_HOST
    return host if host else None


def get_openclaw_callback_url() -> Optional[str]:
    """获取 OpenClaw 回调 URL"""
    host = get_openclaw_host()
    if not host:
        return None
    port = load_openclaw_config().get("callback_port", Config.OPENCLAW_CALLBACK_PORT)
    return f"http://{host}:{port}/wecom"


def get_corp_id_from_cookies() -> str:
    """从browser_data目录中获取已保存的corp_id"""
    # 新方式：从 browser_data 目录获取
    browser_data_dir = os.path.join(os.path.dirname(__file__), "browser_data")
    if os.path.exists(browser_data_dir):
        for d in os.listdir(browser_data_dir):
            if d.startswith("ww") and os.path.isdir(os.path.join(browser_data_dir, d)):
                return d
    
    # 兼容旧方式：从 cookies 目录获取
    cookie_dir = Config.COOKIE_DIR
    if os.path.exists(cookie_dir):
        for f in os.listdir(cookie_dir):
            if f.startswith("wecom_") and f.endswith(".json"):
                return f.replace("wecom_", "").replace(".json", "")
    
    return Config.WECOM_CORP_ID


@app.route('/')
def index():
    """首页：默认跳转到飞书流程页"""
    return redirect(url_for('feishu_flow'))


@app.route('/feishu-flow')
def feishu_flow():
    """飞书扫码 + OpenClaw IP 获取流程页"""
    return render_template('feishu_flow.html')


@app.route('/api/cookie-status')
def api_cookie_status():
    """获取Cookie状态"""
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"valid": False, "message": "未找到Cookie文件"})
    
    return jsonify(get_session_status(corp_id))


# ============================================================
# OpenClaw 配置 API
# ============================================================

@app.route('/api/openclaw/config', methods=['GET', 'POST'])
def openclaw_config_api():
    """
    获取或设置 OpenClaw 配置
    
    GET: 获取当前配置
    POST: 设置配置 {host: "OpenClaw公网IP", callback_port: 3000}
    """
    if request.method == 'GET':
        config = load_openclaw_config()
        return jsonify({
            "success": True,
            "config": {
                "host": config.get("host", ""),
                "callback_port": config.get("callback_port", 3000),
                "callback_url": get_openclaw_callback_url(),
                "feishu": config.get("feishu", {})  # 保存的飞书凭证
            },
            "configured": bool(config.get("host"))
        })
    
    else:  # POST
        data = request.json or {}
        host = data.get("host", "").strip()
        
        if not host:
            return jsonify({"success": False, "error": "缺少 host 参数"})
        
        config = load_openclaw_config()
        config["host"] = host
        config["callback_port"] = data.get("callback_port", 3000)
        config["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_openclaw_config(config)
        
        logger.info(f"[openclaw-config] OpenClaw 配置已更新: host={host}")
        
        return jsonify({
            "success": True,
            "config": {
                "host": host,
                "callback_port": config["callback_port"],
                "callback_url": get_openclaw_callback_url()
            }
        })


# ============================================================
# 飞书通道验证 API（新流程）
# ============================================================

@app.route('/api/feishu/verify', methods=['POST'])
def feishu_verify():
    """
    验证飞书↔OpenClaw通信通道是否可用
    
    【正确的验证方式】
    1. 用户飞书扫码登录后，从 poll 结果中获取已有 OpenClaw 机器人信息
    2. 通过该机器人向用户发送测试消息
    3. 如果消息发送成功，说明机器人凭证有效
    4. 用户在飞书中收到消息并回复，说明通道完全通畅
    
    请求体: {app_id, app_secret, open_id}
    - app_id: 已有 OpenClaw 机器人的 app_id（从 poll 结果获取）
    - app_secret: 机器人的 app_secret
    - open_id: 用户的 open_id（扫码用户）
    """
    data = request.json or {}
    app_id = data.get("app_id")
    app_secret = data.get("app_secret")
    open_id = data.get("open_id")
    
    if not app_id or not app_secret:
        return jsonify({
            "available": False,
            "message": "缺少必要参数: app_id, app_secret"
        })
    
    # 如果没有 open_id，跳过验证直接返回成功
    # open_id 获取需要通讯录权限，但实际使用中不是必需的
    if not open_id:
        logger.info(f"[feishu_verify] 无 open_id，跳过通道验证（凭证已获取）")
        config = load_openclaw_config()
        config["feishu"] = {
            "app_id": app_id,
            "app_secret": app_secret,
            "open_id": None,
            "verified_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "skip_verify": True
        }
        save_openclaw_config(config)
        openclaw_configured = bool(config.get("host"))
        return jsonify({
            "available": True,
            "message": "飞书凭证已保存（跳过通道验证）",
            "app_id": app_id,
            "open_id": None,
            "openclaw_configured": openclaw_configured,
            "need_openclaw_host": not openclaw_configured,
            "skipped": True
        })
    
    logger.info(f"[feishu_verify] 验证飞书通道: app_id={app_id}, open_id={open_id[:20]}...")
    
    # 通过机器人发送测试消息
    result = send_test_message_via_bot(app_id, app_secret, open_id)
    
    if result["success"]:
        logger.info(f"[feishu_verify] 测试消息发送成功，通道验证通过")
        
        # 保存飞书凭证到配置文件
        config = load_openclaw_config()
        config["feishu"] = {
            "app_id": app_id,
            "app_secret": app_secret,
            "open_id": open_id,
            "verified_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        save_openclaw_config(config)
        
        # 检查是否已配置 OpenClaw Host
        openclaw_configured = bool(config.get("host"))
        
        return jsonify({
            "available": True,
            "message": "测试消息已发送到飞书，请在飞书中查看确认",
            "app_id": app_id,
            "open_id": open_id,
            "openclaw_configured": openclaw_configured,
            "need_openclaw_host": not openclaw_configured,
            "hint": "请配置 OpenClaw 服务器地址" if not openclaw_configured else None
        })
    else:
        logger.warning(f"[feishu_verify] 测试消息发送失败: {result['message']}")
        return jsonify({
            "available": False,
            "message": f"通道验证失败: {result['message']}"
        })


@app.route('/api/openclaw/get-ip', methods=['POST'])
def openclaw_get_ip():
    """
    通过飞书通道获取 OpenClaw 服务器的公网 IP
    
    流程：
    1. 通过飞书机器人发送命令给 OpenClaw
    2. 命令内容：执行 curl ifconfig.me 获取公网IP
    3. 等待 OpenClaw 返回结果
    
    请求体: {app_id, app_secret, open_id}
    """
    data = request.json or {}
    app_id = data.get("app_id")
    app_secret = data.get("app_secret")
    open_id = data.get("open_id")
    
    if not app_id or not app_secret or not open_id:
        return jsonify({
            "success": False,
            "error": "缺少必要参数: app_id, app_secret, open_id"
        })
    
    logger.info(f"[openclaw_get_ip] 开始获取 OpenClaw 公网IP...")
    
    # 发送获取IP的命令
    # 注意：这个命令需要 OpenClaw 能够理解并执行
    command = "执行 curl ifconfig.me 这个命令然后将结果返回给我，只需返回IP地址即可"
    
    result = send_command_via_bot(app_id, app_secret, open_id, command)
    
    if not result["success"]:
        logger.warning(f"[openclaw_get_ip] 发送命令失败: {result['message']}")
        return jsonify({
            "success": False,
            "error": f"发送命令失败: {result['message']}"
        })
    
    logger.info("[openclaw_get_ip] 命令已发送，等待 OpenClaw 返回IP...")
    
    # 由于飞书消息是异步的，我们需要轮询获取结果
    # 但目前架构下，OpenClaw 返回的消息无法直接获取
    # 所以这里采用另一种方式：让用户在 OpenClaw 配置文件中指定，或者使用 webhook 回调
    
    # 临时方案：从配置文件读取（如果已经配置过）
    config = load_openclaw_config()
    existing_host = config.get("host")
    
    if existing_host:
        logger.info(f"[openclaw_get_ip] 使用已配置的 IP: {existing_host}")
        return jsonify({
            "success": True,
            "ip": existing_host,
            "source": "config",
            "message": "使用已保存的配置"
        })
    
    # 如果没有配置，返回需要手动输入的提示
    # 但我们可以尝试通过另一种方式：让前端轮询一个专门接收 OpenClaw 回调的接口
    return jsonify({
        "success": False,
        "error": "命令已发送，但无法自动获取返回结果。请手动配置 OpenClaw 公网IP",
        "hint": "调用 POST /api/openclaw/config 设置 host",
        "command_sent": True
    })


@app.route('/api/openclaw/set-ip', methods=['POST'])
def openclaw_set_ip():
    """
    手动设置 OpenClaw 公网 IP（备用方案）
    """
    data = request.json or {}
    ip = data.get("ip", "").strip()
    
    if not ip:
        return jsonify({"success": False, "error": "缺少 ip 参数"})
    
    # 验证 IP 格式
    import re
    if not re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', ip):
        return jsonify({"success": False, "error": "IP 格式无效"})
    
    config = load_openclaw_config()
    config["host"] = ip
    config["callback_port"] = data.get("port", 3000)
    config["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    save_openclaw_config(config)
    
    logger.info(f"[openclaw_set_ip] OpenClaw IP 已设置: {ip}")
    
    return jsonify({
        "success": True,
        "ip": ip,
        "callback_url": f"http://{ip}:{config['callback_port']}/wecom"
    })


# ============================================================
# 飞书扫码相关 API（保留旧接口兼容）
# ============================================================

@app.route('/api/feishu/init', methods=['POST'])
def feishu_init():
    """
    初始化飞书扫码
    调用 feishu_bot.py init 获取二维码内容
    注意：新流程下此接口仅用于验证身份，不创建新机器人
    """
    session_id = secrets.token_hex(8)
    logger.info(f"[feishu_init] 开始初始化飞书扫码, session_id={session_id}")
    
    try:
        # 调用 feishu_bot.py init
        # 首次运行可能需要安装 Chromium，超时设为 180 秒
        cwd_path = os.path.dirname(__file__) or '.'
        logger.info(f"[feishu_init] 执行命令: python3 feishu_bot.py init, cwd={cwd_path}")
        
        result = subprocess.run(
            ['python3', 'feishu_bot.py', 'init'],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=cwd_path
        )
        
        logger.info(f"[feishu_init] 命令返回码: {result.returncode}")
        logger.info(f"[feishu_init] stdout: {result.stdout[:500] if result.stdout else '(空)'}")
        if result.stderr:
            logger.warning(f"[feishu_init] stderr: {result.stderr[:500]}")
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "初始化失败"
            logger.error(f"[feishu_init] 初始化失败: {error_msg}")
            return jsonify({"success": False, "error": error_msg})
        
        # 解析二维码内容
        qr_content = result.stdout.strip()
        logger.info(f"[feishu_init] 获取到二维码内容: {qr_content[:100]}...")
        
        feishu_sessions[session_id] = {
            "status": "pending",
            "qr_content": qr_content,
            "created_at": time.time()
        }
        
        logger.info(f"[feishu_init] 初始化成功, session_id={session_id}")
        return jsonify({
            "success": True,
            "session_id": session_id,
            "qr_content": qr_content
        })
        
    except subprocess.TimeoutExpired:
        logger.error("[feishu_init] 初始化超时 (180s)")
        return jsonify({"success": False, "error": "初始化超时"})
    except Exception as e:
        logger.exception(f"[feishu_init] 初始化异常: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/feishu/poll', methods=['POST'])
def feishu_poll():
    """
    轮询飞书扫码状态
    调用 feishu_bot.py poll 检测扫码结果
    
    注意：使用锁防止并发请求导致浏览器被关闭
    """
    data = request.json or {}
    session_id = data.get("session_id", "")
    logger.debug(f"[feishu_poll] 开始轮询, session_id={session_id}")
    
    # 尝试获取锁，如果已有 poll 在执行，直接返回等待状态
    if not feishu_poll_lock.acquire(blocking=False):
        logger.debug("[feishu_poll] 已有 poll 在执行，返回等待状态")
        return jsonify({"success": True, "status": "pending", "message": "等待扫码"})
    
    try:
        cwd_path = os.path.dirname(__file__) or '.'
        result = subprocess.run(
            ['python3', 'feishu_bot.py', 'poll'],
            capture_output=True,
            text=True,
            timeout=120,  # 增加 timeout，因为获取 IP 需要时间
            cwd=cwd_path
        )
        
        output = result.stdout.strip()
        logger.info(f"[feishu_poll] 返回码={result.returncode}, stdout={output[:200] if output else '(空)'}")
        if result.stderr:
            logger.warning(f"[feishu_poll] stderr: {result.stderr[:200]}")
        
        try:
            poll_result = json.loads(output)
        except json.JSONDecodeError as e:
            logger.error(f"[feishu_poll] JSON解析失败: {e}, 原始输出: {output[:300]}")
            poll_result = {"status": "error", "message": output or "解析响应失败"}
        
        status = poll_result.get("status", "error")
        logger.info(f"[feishu_poll] 状态: {status}")
        
        if status == "ok":
            # 扫码成功，获取到飞书机器人信息
            logger.info(f"[feishu_poll] 扫码成功! app_id={poll_result.get('app_id')}, bot_name={poll_result.get('bot_name')}, openclaw_ip={poll_result.get('openclaw_ip')}")
            if session_id in feishu_sessions:
                feishu_sessions[session_id]["status"] = "completed"
                feishu_sessions[session_id]["result"] = poll_result
            
            return jsonify({
                "success": True,
                "status": "completed",
                "app_id": poll_result.get("app_id"),
                "app_secret": poll_result.get("app_secret"),
                "bot_name": poll_result.get("bot_name"),
                "open_id": poll_result.get("open_id"),
                "openclaw_ip": poll_result.get("openclaw_ip"),
                "manage_url": poll_result.get("manage_url")
            })
        
        elif status == "scanned":
            logger.info("[feishu_poll] 已扫码，等待确认")
            return jsonify({
                "success": True,
                "status": "scanned",
                "message": "已扫码，等待确认"
            })

        elif status == "login_ok":
            logger.info("[feishu_poll] 登录飞书成功，继续查找机器人")
            return jsonify({
                "success": True,
                "status": "login_ok",
                "message": "登录飞书成功，正在查找机器人"
            })
        
        elif status == "pending":
            return jsonify({
                "success": True,
                "status": "pending",
                "message": "等待扫码"
            })
        
        elif status == "expired":
            logger.warning("[feishu_poll] 二维码已过期")
            return jsonify({
                "success": False,
                "status": "expired",
                "message": "二维码已过期，请重新获取"
            })
        
        else:
            # 如果有详细日志，也记录下来
            if poll_result.get("logs"):
                logger.error(f"[feishu_poll] 详细日志: {poll_result.get('logs')}")
            logger.error(f"[feishu_poll] 未知状态或错误: {poll_result}")
            return jsonify({
                "success": False,
                "status": "error",
                "message": poll_result.get("message", "未知错误"),
                "logs": poll_result.get("logs", [])
            })
        
    except subprocess.TimeoutExpired:
        logger.warning("[feishu_poll] 轮询超时")
        return jsonify({"success": True, "status": "pending", "message": "等待扫码"})
    except Exception as e:
        logger.exception(f"[feishu_poll] 轮询异常: {e}")
        return jsonify({"success": False, "status": "error", "message": str(e)})
    finally:
        # 确保释放锁
        feishu_poll_lock.release()


@app.route('/api/feishu/cleanup', methods=['POST'])
def feishu_cleanup():
    """清理飞书扫码会话"""
    logger.info("[feishu_cleanup] 开始清理飞书会话")
    try:
        result = subprocess.run(
            ['python3', 'feishu_bot.py', 'cleanup'],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=os.path.dirname(__file__) or '.'
        )
        logger.info(f"[feishu_cleanup] 清理完成, stdout: {result.stdout[:100] if result.stdout else '(空)'}")
        feishu_sessions.clear()
        return jsonify({"success": True})
    except Exception as e:
        logger.exception(f"[feishu_cleanup] 清理异常: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/feishu/get-openclaw-ip', methods=['POST'])
def get_openclaw_ip():
    """
    通过飞书网页版发送命令获取 OpenClaw 部署机 IP

    前提：用户已完成飞书扫码登录

    流程：
    1. 连接到已登录的浏览器
    2. 导航到飞书消息页面
    3. 给 OpenClaw 机器人发送 "执行 curl ifconfig.me" 命令
    4. 等待机器人返回 IP 地址
    """
    logger.info("[get_openclaw_ip] 开始获取 OpenClaw IP...")

    cwd_path = os.path.dirname(__file__) or '.'

    def _run_feishu_bot(cmd: str, timeout_sec: int = 120):
        result = subprocess.run(
            ['python3', 'feishu_bot.py', cmd],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=cwd_path
        )
        output = (result.stdout or '').strip()
        logger.info(f"[get_openclaw_ip] feishu_bot {cmd} 返回码={result.returncode}, stdout={output[:200] if output else '(空)'}")
        if result.stderr:
            logger.debug(f"[get_openclaw_ip] feishu_bot {cmd} stderr: {result.stderr[:500]}")
        try:
            return json.loads(output) if output else {}
        except json.JSONDecodeError:
            return {"status": "error", "message": "解析响应失败", "raw": output[:300]}

    try:
        ip_result = {}
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            logger.info(f"[get_openclaw_ip] 第 {attempt}/{max_attempts} 次执行 get_ip")
            ip_result = _run_feishu_bot('get_ip', 180)

            if ip_result.get("status") == "ok" and ip_result.get("ip"):
                break

            msg = str(ip_result.get("message", ""))
            retryable = (
                "请先完成扫码登录" in msg
                or "connect ECONNREFUSED" in msg
                or "获取 IP 失败" in msg
            )

            if retryable and attempt < max_attempts:
                logger.info(f"[get_openclaw_ip] 第 {attempt} 次失败，先 poll 一次后重试")
                _run_feishu_bot('poll', 120)
                time.sleep(1)

        if ip_result.get("status") == "ok" and ip_result.get("ip"):
            ip = ip_result.get("ip")
            logger.info(f"[get_openclaw_ip] 成功获取 IP: {ip}")

            config = load_openclaw_config()
            config["openclaw_ip"] = ip
            config["ip_obtained_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            save_openclaw_config(config)

            return jsonify({
                "success": True,
                "ip": ip,
                "message": "成功获取 OpenClaw 部署机 IP"
            })

        return jsonify({
            "success": False,
            "error": ip_result.get("message", "获取 IP 失败"),
            "logs": ip_result.get("logs", []),
            "status": ip_result.get("status")
        })

    except subprocess.TimeoutExpired:
        logger.warning("[get_openclaw_ip] 获取 IP 超时")
        return jsonify({"success": False, "error": "获取 IP 超时，请稍后重试"})
    except Exception as e:
        logger.exception(f"[get_openclaw_ip] 异常: {e}")
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# 企微配置相关 API
# ============================================================

@app.route('/api/start-wecom-setup', methods=['POST'])
def start_wecom_setup():
    """
    开始企微配置流程（新流程）
    
    前提：客户已有飞书↔OpenClaw通道，需要在请求中传递飞书机器人凭证
    
    请求体: {
        app_name: 企微应用名称,
        feishu_app_id: 飞书机器人 app_id（扫码后获取的已有 OpenClaw 机器人）,
        feishu_app_secret: 飞书机器人 app_secret,
        feishu_open_id: 用户 open_id
    }
    
    步骤：
    1. 验证飞书通道可用（发送测试消息）
    2. 企微后台创建应用，获取凭证
    3. 通过飞书通道发送命令安装配置 wecom-app 插件
    4. 获取微信插件二维码
    """
    data = request.json or {}
    app_name = data.get("app_name", "OpenClaw AI助手")
    
    # 获取飞书机器人凭证（从扫码结果传入）
    feishu_app_id = data.get("feishu_app_id")
    feishu_app_secret = data.get("feishu_app_secret")
    feishu_open_id = data.get("feishu_open_id")
    
    if not feishu_app_id or not feishu_app_secret:
        return jsonify({
            "success": False,
            "error": "缺少飞书机器人凭证，请先完成飞书扫码验证",
            "hint": "需要传入 feishu_app_id, feishu_app_secret"
        })
    
    # 验证飞书通道（发送测试消息）- 如果有 open_id 才验证
    if feishu_open_id:
        logger.info(f"[start_wecom_setup] 验证飞书通道: app_id={feishu_app_id}")
        test_result = send_test_message_via_bot(feishu_app_id, feishu_app_secret, feishu_open_id)
        if not test_result["success"]:
            return jsonify({
                "success": False, 
                "error": f"飞书通道验证失败: {test_result['message']}",
                "hint": "请确认飞书机器人凭证正确，且已与 OpenClaw 建立通道"
            })
    else:
        logger.warning("[start_wecom_setup] 未提供 open_id，跳过飞书通道验证")
    
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie，请先运行预存程序"})
    
    task_id = secrets.token_hex(8)
    
    tasks[task_id] = {
        "task_id": task_id,
        "status": "started",
        "step": 0,
        "total_steps": 5,
        "steps": [
            {"name": "验证飞书通道", "status": "done"},  # 已在上面验证
            {"name": "创建企微应用", "status": "pending"},
            {"name": "安装配置wecom-app插件", "status": "pending"},
            {"name": "验证配置并重启", "status": "pending"},
            {"name": "获取微信二维码", "status": "pending"}
        ],
        "result": {},
        "commands": [],  # 记录执行的命令，用于页面展示
        "error": "",
        # 保存飞书凭证供后续步骤使用
        "feishu": {
            "app_id": feishu_app_id,
            "app_secret": feishu_app_secret,
            "open_id": feishu_open_id
        }
    }
    
    def run_setup():
        try:
            _execute_wecom_setup(task_id, corp_id, app_name)
        except Exception as e:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
            logger.exception(f"企微配置异常: {e}")
    
    thread = threading.Thread(target=run_setup)
    thread.start()
    
    return jsonify({"task_id": task_id, "status": "started"})


def _execute_wecom_setup(task_id: str, corp_id: str, app_name: str):
    """
    执行企微配置流程（新流程）
    
    步骤：
    1. 企微后台创建应用
    2. 通过飞书通道安装 wecom-app 插件
    3. 配置插件参数
    4. 验证配置正确性
    5. 重启 OpenClaw 服务
    6. 获取微信插件二维码
    """
    task = tasks[task_id]
    
    # 获取飞书凭证（在 start_wecom_setup 中已保存）
    feishu_creds = task.get("feishu", {})
    feishu_app_id = feishu_creds.get("app_id")
    feishu_app_secret = feishu_creds.get("app_secret")
    feishu_open_id = feishu_creds.get("open_id")
    
    if not feishu_app_id or not feishu_app_secret:
        task["status"] = "failed"
        task["error"] = "飞书凭证丢失，请重新开始"
        return
    
    try:
        # 获取服务器公网IP
        public_ip = _get_public_ip()
        webhook_path = "/wecom-app"
        
        # ============================================================
        # Step 2: 企微后台创建应用
        # ============================================================
        task["step"] = 2
        task["steps"][1]["status"] = "running"
        task["status"] = "creating_wecom_app"
        logger.info(f"[Step 2] 企微后台创建应用...")
        
        automation = WeComAutomation(corp_id, Config.COOKIE_DIR)
        
        # 注意：这里不传 webhook_url，因为企微应用的 webhook 需要配置为 OpenClaw 的地址
        wecom_result = automation.create_app_and_configure(
            app_name=app_name,
            webhook_url=f"http://{public_ip}:18789{webhook_path}",  # OpenClaw gateway 默认端口
            trusted_ip=public_ip
        )
        
        if not wecom_result.get("success"):
            task["steps"][1]["status"] = "error"
            task["status"] = "failed"
            task["error"] = f"企微应用创建失败: {wecom_result.get('error')}"
            return
        
        task["steps"][1]["status"] = "done"
        agent_id = wecom_result.get("agent_id", "")
        corp_secret = wecom_result.get("secret", "")
        msg_token = wecom_result.get("token", "")
        aes_key = wecom_result.get("aes_key", "")
        
        logger.info(f"[Step 2] 企微应用创建成功: corp_id={corp_id}, agent_id={agent_id}")
        
        # ============================================================
        # Step 3: 通过飞书通道安装配置 wecom-app 插件
        # ============================================================
        task["step"] = 3
        task["steps"][2]["status"] = "running"
        task["status"] = "installing_wecom_plugin"
        logger.info(f"[Step 3] 通过飞书通道安装配置 wecom-app 插件...")
        
        # 生成安装命令（用于页面展示）
        install_commands = generate_wecom_install_commands(
            corp_id=corp_id,
            corp_secret=corp_secret,
            agent_id=agent_id,
            token=msg_token,
            aes_key=aes_key,
            webhook_path=webhook_path
        )
        task["commands"] = install_commands
        
        # 通过飞书通道发送命令（使用扫码获取的机器人凭证）
        plugin_result = install_wecom_plugin_via_feishu(
            app_id=feishu_app_id,
            app_secret=feishu_app_secret,
            user_open_id=feishu_open_id,
            corp_id=corp_id,
            corp_secret=corp_secret,
            agent_id=agent_id,
            msg_token=msg_token,
            aes_key=aes_key,
            webhook_path=webhook_path
        )
        
        if not plugin_result.get("success"):
            task["steps"][2]["status"] = "warning"
            task["steps"][2]["message"] = plugin_result.get("message")
            logger.warning(f"[Step 3] 插件安装可能失败: {plugin_result.get('message')}")
            logger.warning(f"[Step 3] 请在 OpenClaw 控制台手动执行以下命令:")
            for cmd in install_commands:
                if cmd and not cmd.startswith("#"):
                    logger.warning(f"  {cmd}")
        else:
            task["steps"][2]["status"] = "done"
            logger.info(f"[Step 3] wecom-app 插件安装配置命令已发送")
        
        # ============================================================
        # Step 4: 验证配置并重启（已在 install_wecom_plugin_via_feishu 中完成）
        # ============================================================
        task["step"] = 4
        task["steps"][3]["status"] = "running"
        task["status"] = "verifying_and_restarting"
        logger.info(f"[Step 4] 等待配置命令执行...")
        
        # 等待配置命令执行完成
        time.sleep(3)
        
        # 由于服务不在同一机器，无法本地验证配置
        # 重启命令已在 install_wecom_plugin_via_feishu 中发送
        task["steps"][3]["status"] = "done"
        task["steps"][3]["message"] = "配置命令已发送，请在飞书中确认 OpenClaw 执行结果"
        logger.info(f"[Step 4] 配置命令已发送，请在飞书中确认")
        
        # ============================================================
        # Step 5: 获取微信插件二维码
        # ============================================================
        task["step"] = 5
        task["steps"][4]["status"] = "running"
        task["status"] = "getting_qrcode"
        logger.info(f"[Step 5] 获取微信插件二维码...")
        
        # 二维码在 wecom_result 中
        qrcode_url = wecom_result.get("wechat_qrcode_url", "")
        
        task["steps"][4]["status"] = "done"
        
        # ============================================================
        # 完成
        # ============================================================
        task["status"] = "completed"
        task["result"] = {
            "corp_id": corp_id,
            "agent_id": agent_id,
            "secret": corp_secret,
            "token": msg_token,
            "aes_key": aes_key,
            "webhook_url": f"http://{public_ip}:18789{webhook_path}",
            "public_ip": public_ip,
            "wechat_qrcode_url": qrcode_url,
            "commands": install_commands,
        }
        logger.info(f"[完成] 企微配置流程完成")
        
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        current_step = task.get("step", 1) - 1
        if 0 <= current_step < len(task["steps"]):
            task["steps"][current_step]["status"] = "error"
        logger.error(f"企微配置失败: {e}")


def _get_public_ip() -> str:
    """获取本机公网IP"""
    import urllib.request
    try:
        return urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode('utf8')
    except:
        try:
            return urllib.request.urlopen('https://ifconfig.me/ip', timeout=10).read().decode('utf8')
        except:
            return "127.0.0.1"


@app.route('/api/task/<task_id>')
def get_task_status(task_id):
    """获取任务状态"""
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(tasks[task_id])


@app.route('/api/wecom-plugin/commands', methods=['POST'])
def get_wecom_plugin_commands():
    """
    生成 wecom-app 插件安装命令（仅生成不执行）
    用于页面展示给用户
    
    请求体: {corp_id, corp_secret, agent_id, token, aes_key}
    """
    data = request.json or {}
    
    required_fields = ['corp_id', 'corp_secret', 'agent_id', 'token', 'aes_key']
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return jsonify({"success": False, "error": f"缺少必填字段: {missing}"})
    
    commands = generate_wecom_install_commands(
        corp_id=data['corp_id'],
        corp_secret=data['corp_secret'],
        agent_id=data['agent_id'],
        token=data['token'],
        aes_key=data['aes_key'],
        webhook_path=data.get('webhook_path', '/wecom-app')
    )
    
    return jsonify({
        "success": True,
        "commands": commands,
        "hint": "请在 OpenClaw 部署机上依次执行以上命令"
    })


@app.route('/api/wecom-plugin/install', methods=['POST'])
def install_wecom_plugin_api():
    """
    通过飞书通道安装配置 wecom-app 插件
    
    请求体: {
        feishu_app_id: 飞书机器人 app_id,
        feishu_app_secret: 飞书机器人 app_secret,
        feishu_open_id: 用户 open_id,
        corp_id, corp_secret, agent_id, token, aes_key
    }
    """
    data = request.json or {}
    
    required_fields = [
        'feishu_app_id', 'feishu_app_secret', 'feishu_open_id',
        'corp_id', 'corp_secret', 'agent_id', 'token', 'aes_key'
    ]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return jsonify({"success": False, "error": f"缺少必填字段: {missing}"})
    
    result = install_wecom_plugin_via_feishu(
        app_id=data['feishu_app_id'],
        app_secret=data['feishu_app_secret'],
        user_open_id=data['feishu_open_id'],
        corp_id=data['corp_id'],
        corp_secret=data['corp_secret'],
        agent_id=data['agent_id'],
        msg_token=data['token'],
        aes_key=data['aes_key'],
        webhook_path=data.get('webhook_path', '/wecom-app')
    )
    
    return jsonify(result)


@app.route('/api/wecom-plugin/configure', methods=['POST'])
def configure_wecom_plugin_api():
    """
    配置 wecom-app 插件
    请求体: {
        feishu_app_id, feishu_app_secret, feishu_open_id,
        corp_id, agent_id, secret, token, aes_key
    }
    """
    data = request.json or {}
    
    required_fields = [
        'feishu_app_id', 'feishu_app_secret', 'feishu_open_id',
        'corp_id', 'agent_id', 'secret', 'token', 'aes_key'
    ]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return jsonify({"success": False, "error": f"缺少必填字段: {missing}"})
    
    result = install_wecom_plugin_via_feishu(
        app_id=data['feishu_app_id'],
        app_secret=data['feishu_app_secret'],
        user_open_id=data['feishu_open_id'],
        corp_id=data['corp_id'],
        corp_secret=data['secret'],
        agent_id=data['agent_id'],
        msg_token=data['token'],
        aes_key=data['aes_key'],
        webhook_path=data.get('webhook_path', '/wecom-app')
    )
    return jsonify(result)


@app.route('/api/openclaw-config')
def get_openclaw_config():
    """
    获取 OpenClaw 配置状态
    注意：本服务与 OpenClaw 不在同一机器，无法直接读取 OpenClaw 配置文件。
    飞书通道的验证通过发送测试消息完成，而不是读取本地文件。
    """
    return jsonify({
        "success": True,
        "message": "OpenClaw 配置需要通过飞书通道验证，请使用 /api/feishu/verify 接口",
        "note": "本服务与 OpenClaw 部署在不同机器"
    })


@app.route('/api/wecom/invite-qrcode')
def get_invite_qrcode_api():
    """
    获取企微邀请同事加入的二维码
    """
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie"})
    
    # 检查会话状态
    status = get_session_status(corp_id)
    if not status["valid"]:
        return jsonify({
            "success": False, 
            "error": status["message"],
            "need_relogin": True
        })
    
    # 获取二维码
    from wecom_invite import get_invite_qrcode
    result = get_invite_qrcode(corp_id)
    return jsonify(result)


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({"status": "ok"})


# ============================================================
# 新成员自动接入相关 API
# ============================================================

@app.route('/api/contact-callback/setup', methods=['POST'])
def setup_contact_callback():
    """
    配置通讯录变更事件回调
    
    请求体: {
        token: 回调 Token（可选，不传则自动生成）,
        encoding_aes_key: EncodingAESKey（可选，不传则自动生成）
    }
    
    返回: 需要在企微后台配置的 URL、Token、EncodingAESKey
    """
    data = request.json or {}
    corp_id = get_corp_id_from_cookies()
    
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie"})
    
    # 生成或使用传入的 Token 和 EncodingAESKey
    token = data.get("token") or secrets.token_urlsafe(24)[:32]
    aes_key = data.get("encoding_aes_key") or secrets.token_urlsafe(32)[:43]
    
    # 获取本机 IP
    public_ip = _get_public_ip()
    callback_url = f"http://{public_ip}:{Config.PORT}/api/contact-callback/receive"
    
    # 创建回调处理器
    handler = ContactChangeCallbackHandler(corp_id, token, aes_key)
    callback_handlers[corp_id] = handler
    
    logger.info(f"[contact-callback] 已配置回调处理器: corp_id={corp_id}")
    
    return jsonify({
        "success": True,
        "config": {
            "url": callback_url,
            "token": token,
            "encoding_aes_key": aes_key,
            "corp_id": corp_id
        },
        "instructions": [
            "1. 登录企业微信管理后台",
            "2. 进入「管理工具」→「通讯录同步」",
            "3. 点击「API接收」→「设置接收事件服务器」",
            "4. 填入以上 URL、Token、EncodingAESKey",
            "5. 勾选「通讯录变更」事件",
            "6. 点击保存"
        ]
    })


@app.route('/api/contact-callback/receive', methods=['GET', 'POST'])
def receive_contact_callback():
    """
    接收企微通讯录变更事件回调
    
    GET 请求：验证 URL 有效性
    POST 请求：接收事件通知
    """
    corp_id = get_corp_id_from_cookies()
    
    if not corp_id or corp_id not in callback_handlers:
        # 返回 success 避免企微重试
        return "success"
    
    handler = callback_handlers[corp_id]
    
    # 获取签名参数
    msg_signature = request.args.get("msg_signature", "")
    timestamp = request.args.get("timestamp", "")
    nonce = request.args.get("nonce", "")
    
    if request.method == 'GET':
        # URL 验证请求
        echostr = request.args.get("echostr", "")
        logger.info(f"[contact-callback] 收到 URL 验证请求")
        
        result = handler.verify_url(msg_signature, timestamp, nonce, echostr)
        if result:
            logger.info(f"[contact-callback] URL 验证成功")
            return result
        else:
            logger.error(f"[contact-callback] URL 验证失败")
            return "fail"
    
    else:
        # POST: 事件通知
        request_body = request.data.decode('utf-8')
        logger.info(f"[contact-callback] 收到事件通知")
        
        event = handler.handle_callback(msg_signature, timestamp, nonce, request_body)
        
        if event and event.get("event_type") == "create_user":
            # 新成员加入，触发后台创建应用任务
            member_info = event.get("member_info", {})
            member_name = member_info.get("name", "")
            member_user_id = member_info.get("user_id", "")
            
            logger.info(f"[contact-callback] 检测到新成员: {member_name} ({member_user_id})")
            
            # 启动异步任务创建应用
            task_id = secrets.token_hex(8)
            tasks[task_id] = {
                "task_id": task_id,
                "type": "new_member_app",
                "status": "pending",
                "member_name": member_name,
                "member_user_id": member_user_id,
                "created_at": time.time()
            }
            
            def create_app_async():
                try:
                    _create_app_for_new_member(task_id, corp_id, member_name, member_user_id)
                except Exception as e:
                    tasks[task_id]["status"] = "failed"
                    tasks[task_id]["error"] = str(e)
                    logger.exception(f"创建应用失败: {e}")
            
            thread = threading.Thread(target=create_app_async)
            thread.start()
        
        return "success"


def _create_app_for_new_member(task_id: str, corp_id: str, member_name: str, member_user_id: str):
    """
    为新成员创建专属应用（后台任务）
    
    自动化流程：
    1. 创建应用并配置 Token/EncodingAESKey
    2. 自动填入 OpenClaw 回调 URL: http://{OPENCLAW_HOST}:3000/wecom
    3. 提取所有配置参数
    """
    task = tasks[task_id]
    task["status"] = "running"
    
    try:
        logger.info(f"[new-member-app] 开始为 {member_name} 创建应用...")
        
        # 从配置文件获取 OpenClaw 回调 URL
        callback_url = get_openclaw_callback_url()
        if not callback_url:
            task["status"] = "failed"
            task["error"] = "未配置 OpenClaw 服务器地址，请先调用 /api/openclaw/config 设置"
            logger.error("[new-member-app] 未配置 OpenClaw Host，无法自动填入回调 URL")
            return
        
        logger.info(f"[new-member-app] 回调 URL: {callback_url}")
        
        creator = NewMemberAppCreator(corp_id)
        result = creator.create_app_for_member(
            member_name=member_name,
            member_user_id=member_user_id,
            headless=True,
            keep_page_open=True  # 保持页面打开，用于填入 URL
        )
        
        if result["success"]:
            # 自动填入回调 URL
            logger.info(f"[new-member-app] 应用创建成功，自动填入回调 URL...")
            url_updated = creator.update_callback_url(callback_url)
            
            if url_updated:
                result["callback_url"] = callback_url
                task["status"] = "completed"
                task["config"] = result
                logger.info(f"[new-member-app] ✓ 应用配置完成")
                logger.info(f"  CORP_ID: {result['corp_id']}")
                logger.info(f"  AgentId: {result['agent_id']}")
                logger.info(f"  Secret: {result['secret'][:10] if result['secret'] else '(待获取)'}...")
                logger.info(f"  Token: {result['token']}")
                logger.info(f"  EncodingAESKey: {result['aes_key']}")
                logger.info(f"  Callback URL: {callback_url}")
            else:
                # URL 更新失败，保留页面让用户手动处理
                task["status"] = "waiting_url"
                task["config"] = result
                member_app_creators[f"{corp_id}_{member_user_id}"] = creator
                logger.warning(f"[new-member-app] 回调 URL 自动填入失败，等待手动更新")
                return  # 不关闭浏览器
            
            creator.close()
        else:
            task["status"] = "failed"
            task["error"] = result.get("error", "创建失败")
            creator.close()
            
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        logger.exception(f"创建应用失败: {e}")


@app.route('/api/new-member/create-app', methods=['POST'])
def create_app_for_new_member_api():
    """
    手动为新成员创建专属应用
    
    请求体: {
        member_name: 成员名称,
        member_user_id: 成员 UserID（可选）
    }
    """
    data = request.json or {}
    member_name = data.get("member_name")
    member_user_id = data.get("member_user_id", "")
    
    if not member_name:
        return jsonify({"success": False, "error": "缺少 member_name 参数"})
    
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie"})
    
    # 检查会话状态
    status = get_session_status(corp_id)
    if not status["valid"]:
        return jsonify({
            "success": False, 
            "error": status["message"],
            "need_relogin": True
        })
    
    # 创建任务
    task_id = secrets.token_hex(8)
    tasks[task_id] = {
        "task_id": task_id,
        "type": "new_member_app",
        "status": "pending",
        "member_name": member_name,
        "member_user_id": member_user_id or f"manual_{int(time.time())}",
        "created_at": time.time()
    }
    
    def create_app_async():
        try:
            _create_app_for_new_member(
                task_id, corp_id, member_name, 
                member_user_id or f"manual_{int(time.time())}"
            )
        except Exception as e:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
    
    thread = threading.Thread(target=create_app_async)
    thread.start()
    
    return jsonify({
        "success": True,
        "task_id": task_id,
        "message": f"正在为 {member_name} 创建专属应用..."
    })


@app.route('/api/new-member/update-url', methods=['POST'])
def update_member_app_url():
    """
    更新新成员应用的回调 URL
    
    请求体: {
        task_id: 创建任务ID,
        callback_url: 回调 URL
    }
    """
    data = request.json or {}
    task_id = data.get("task_id")
    callback_url = data.get("callback_url")
    
    if not task_id or not callback_url:
        return jsonify({"success": False, "error": "缺少 task_id 或 callback_url"})
    
    if task_id not in tasks:
        return jsonify({"success": False, "error": "任务不存在"})
    
    task = tasks[task_id]
    if task.get("status") != "waiting_url":
        return jsonify({
            "success": False, 
            "error": f"任务状态不正确: {task.get('status')}"
        })
    
    # 获取创建器
    corp_id = get_corp_id_from_cookies()
    creator_key = f"{corp_id}_{task.get('member_user_id')}"
    creator = member_app_creators.get(creator_key)
    
    if not creator:
        return jsonify({"success": False, "error": "创建器不存在，页面可能已关闭"})
    
    try:
        success = creator.update_callback_url(callback_url)
        
        if success:
            task["status"] = "completed"
            task["config"]["callback_url"] = callback_url
            
            # 清理创建器
            creator.close()
            del member_app_creators[creator_key]
            
            logger.info(f"[new-member-app] URL 已更新: {callback_url}")
            
            return jsonify({
                "success": True,
                "config": task["config"]
            })
        else:
            return jsonify({"success": False, "error": "更新 URL 失败"})
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/new-member/task/<task_id>')
def get_new_member_task(task_id):
    """获取新成员应用创建任务状态"""
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
    
    task = tasks[task_id]
    
    # 如果状态是 waiting_url，返回配置参数
    if task.get("status") == "waiting_url":
        return jsonify({
            "task_id": task_id,
            "status": "waiting_url",
            "message": "应用已创建，等待配置回调 URL",
            "config": task.get("config", {}),
            "member_name": task.get("member_name"),
        })
    
    return jsonify(task)


@app.route('/api/new-member/pending')
def get_pending_members():
    """获取待处理的新成员列表"""
    corp_id = get_corp_id_from_cookies()
    
    if not corp_id or corp_id not in callback_handlers:
        return jsonify({"pending": [], "count": 0})
    
    handler = callback_handlers[corp_id]
    
    return jsonify({
        "pending": handler.pending_members,
        "count": handler.get_pending_count()
    })


@app.route('/api/new-member/poll')
def poll_new_member():
    """
    轮询是否有新成员加入
    前端定期调用此接口检查是否有新成员
    """
    corp_id = get_corp_id_from_cookies()
    
    if not corp_id or corp_id not in callback_handlers:
        return jsonify({"new_member": None})
    
    handler = callback_handlers[corp_id]
    pending = handler.pending_members
    
    if pending:
        # 取出第一个待处理的新成员
        member = pending[0]
        return jsonify({
            "new_member": {
                "name": member.get("name", "未知"),
                "user_id": member.get("user_id", ""),
                "department": member.get("department", []),
                "detected_at": member.get("detected_at", "")
            }
        })
    
    return jsonify({"new_member": None})


@app.route('/api/openclaw/install-wecom-plugin', methods=['POST'])
def openclaw_install_wecom_plugin():
    """
    通过飞书通道安装和配置 wecom-app 插件（新版 API）
    
    请求体: {
        app_id: 飞书机器人 app_id,
        app_secret: 飞书机器人 app_secret,
        open_id: 用户 open_id,
        corp_id: 企微 corp_id,
        corp_secret: 企微应用 secret,
        agent_id: 企微应用 agent_id,
        token: 消息回调 token,
        aes_key: 消息加密 AES Key
    }
    """
    data = request.json or {}
    
    required_fields = [
        'app_id', 'app_secret', 'open_id',
        'corp_id', 'corp_secret', 'agent_id', 'token', 'aes_key'
    ]
    missing = [f for f in required_fields if not data.get(f)]
    if missing:
        return jsonify({"success": False, "error": f"缺少必填字段: {missing}"})
    
    logger.info(f"[openclaw_install_wecom_plugin] 开始安装 wecom-app 插件...")
    logger.info(f"  corp_id={data['corp_id']}, agent_id={data['agent_id']}")
    
    result = install_wecom_plugin_via_feishu(
        app_id=data['app_id'],
        app_secret=data['app_secret'],
        user_open_id=data['open_id'],
        corp_id=data['corp_id'],
        corp_secret=data['corp_secret'],
        agent_id=data['agent_id'],
        msg_token=data['token'],
        aes_key=data['aes_key'],
        webhook_path="/wecom"  # 使用 /wecom 而不是 /wecom-app
    )
    
    return jsonify(result)


@app.route('/api/new-member/save-api-config', methods=['POST'])
def save_api_config():
    """
    保存 API 接收配置（点击企微后台的保存按钮）
    
    请求体: {task_id: 任务ID}
    """
    data = request.json or {}
    task_id = data.get("task_id")
    
    if not task_id:
        return jsonify({"success": False, "error": "缺少 task_id"})
    
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie"})
    
    # 查找对应的创建器
    key = None
    for k, v in member_app_creators.items():
        if k.startswith(corp_id):
            key = k
            break
    
    if not key or key not in member_app_creators:
        return jsonify({"success": False, "error": "未找到应用创建器，可能页面已关闭"})
    
    creator = member_app_creators[key]
    
    try:
        logger.info(f"[save_api_config] 保存 API 配置...")
        success = creator.save_api_config()
        
        if success:
            logger.info("[save_api_config] 保存成功")
            # 关闭浏览器
            creator.close()
            del member_app_creators[key]
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "保存失败，请手动在企微后台保存"})
            
    except Exception as e:
        logger.exception(f"[save_api_config] 保存异常: {e}")
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/wecom/wechat-qrcode')
def get_wechat_qrcode():
    """
    获取微信插件二维码
    
    参数: agent_id - 应用 ID
    """
    agent_id = request.args.get("agent_id")
    
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie"})
    
    # 这里需要实现获取微信插件二维码的逻辑
    # 通常在企微后台 -> 应用管理 -> 应用 -> 微信插件
    # 暂时返回提示
    return jsonify({
        "success": False,
        "error": "请手动从企微后台获取微信插件二维码",
        "hint": f"路径: 应用管理 -> 应用 (AgentId: {agent_id}) -> 微信插件"
    })


# ============================================================
# 飞书网页版登录 - 用于获取用户飞书登录态（后台线程+队列通信）
# ============================================================
import threading
import queue
import base64
import uuid

feishu_messenger_sessions = {}  # session_id -> {cmd_queue, result_queue, thread, last_screenshot}

def feishu_worker(session_id, cmd_queue, result_queue):
    """后台线程，持续运行 Playwright 并处理命令"""
    from playwright.sync_api import sync_playwright
    import time
    
    pw = None
    browser = None
    page = None
    
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={'width': 1280, 'height': 800},
            locale='zh-CN'
        )
        page = context.new_page()
        
        # 打开飞书网页版
        logger.info(f"[feishu-messenger] 打开飞书登录页面...")
        page.goto('https://www.feishu.cn/messenger/', wait_until='domcontentloaded', timeout=60000)
        try:
            page.wait_for_selector('img[class*="qrcode"], canvas, .qr-code, [class*="QRCode"]', timeout=15000)
        except:
            pass
        time.sleep(3)
        
        # 初始截图
        screenshot = page.screenshot()
        qr_base64 = base64.b64encode(screenshot).decode('utf-8')
        result_queue.put({'type': 'init', 'success': True, 'qr_image': qr_base64})
        
        # 持续处理命令
        while True:
            try:
                cmd = cmd_queue.get(timeout=0.5)
                
                if cmd['action'] == 'stop':
                    break
                elif cmd['action'] == 'screenshot':
                    screenshot = page.screenshot()
                    result_queue.put({
                        'type': 'screenshot',
                        'success': True,
                        'screenshot': base64.b64encode(screenshot).decode('utf-8')
                    })
                elif cmd['action'] == 'click':
                    page.mouse.click(cmd['x'], cmd['y'])
                    time.sleep(0.5)
                    screenshot = page.screenshot()
                    result_queue.put({
                        'type': 'click',
                        'success': True,
                        'screenshot': base64.b64encode(screenshot).decode('utf-8')
                    })
                elif cmd['action'] == 'type':
                    page.keyboard.type(cmd['text'])
                    time.sleep(0.5)
                    screenshot = page.screenshot()
                    result_queue.put({
                        'type': 'type',
                        'success': True,
                        'screenshot': base64.b64encode(screenshot).decode('utf-8')
                    })
                elif cmd['action'] == 'press':
                    page.keyboard.press(cmd['key'])
                    time.sleep(0.5)
                    screenshot = page.screenshot()
                    result_queue.put({
                        'type': 'press',
                        'success': True,
                        'screenshot': base64.b64encode(screenshot).decode('utf-8')
                    })
                elif cmd['action'] == 'open_bot_chat':
                    # 自动搜索并打开机器人聊天窗口
                    bot_name = cmd.get('bot_name', 'openclaw')
                    preset_msg = cmd.get('preset_msg', '执行 curl ifconfig.me')
                    
                    try:
                        # 等待页面加载完成
                        time.sleep(1)
                        
                        # 点击搜索框
                        search_box = page.query_selector('input[placeholder*="搜索"], .search-input, [class*="search"] input')
                        if search_box:
                            search_box.click()
                            time.sleep(0.3)
                        else:
                            # 尝试点击搜索区域
                            page.mouse.click(200, 80)
                            time.sleep(0.3)
                        
                        # 输入机器人名称
                        page.keyboard.type(bot_name)
                        time.sleep(1.5)
                        
                        # 点击搜索结果（第一个结果）
                        # 尝试找到搜索结果列表中的项目
                        result_item = page.query_selector('[class*="search-result"] [class*="item"], [class*="SearchResult"], [class*="result-item"]')
                        if result_item:
                            result_item.click()
                        else:
                            # 按下回车或点击大约位置
                            page.keyboard.press('Enter')
                        time.sleep(1)
                        
                        # 等待聊天窗口加载
                        time.sleep(1)
                        
                        # 在输入框中输入预设消息
                        input_box = page.query_selector('[class*="editor"], [class*="input-box"], [contenteditable="true"], textarea')
                        if input_box:
                            input_box.click()
                            time.sleep(0.3)
                        else:
                            # 点击聊天输入区域（通常在底部）
                            page.mouse.click(800, 700)
                            time.sleep(0.3)
                        
                        # 输入预设命令
                        page.keyboard.type(preset_msg)
                        time.sleep(0.5)
                        
                        screenshot = page.screenshot()
                        result_queue.put({
                            'type': 'open_bot_chat',
                            'success': True,
                            'screenshot': base64.b64encode(screenshot).decode('utf-8'),
                            'message': '已打开机器人聊天，预设命令已输入'
                        })
                    except Exception as e:
                        screenshot = page.screenshot()
                        result_queue.put({
                            'type': 'open_bot_chat',
                            'success': False,
                            'error': str(e),
                            'screenshot': base64.b64encode(screenshot).decode('utf-8')
                        })
                elif cmd['action'] == 'send_message':
                    # 发送消息（按回车或点击发送按钮）
                    try:
                        page.keyboard.press('Enter')
                        time.sleep(2)  # 等待消息发送和回复
                        screenshot = page.screenshot()
                        result_queue.put({
                            'type': 'send_message',
                            'success': True,
                            'screenshot': base64.b64encode(screenshot).decode('utf-8')
                        })
                    except Exception as e:
                        result_queue.put({
                            'type': 'send_message',
                            'success': False,
                            'error': str(e)
                        })
                    
            except queue.Empty:
                # 没有命令，自动刷新截图并检测登录状态
                try:
                    screenshot = page.screenshot()
                    screenshot_b64 = base64.b64encode(screenshot).decode('utf-8')
                    
                    # 更新最新截图到 session
                    if session_id in feishu_messenger_sessions:
                        feishu_messenger_sessions[session_id]['last_screenshot'] = screenshot_b64
                        
                        # 检测是否已登录（检查URL或页面元素）
                        current_url = page.url
                        if 'passport' not in current_url and 'login' not in current_url:
                            # 检查是否有搜索框（登录后的标志）
                            search = page.query_selector('input[placeholder*="搜索"], .search-input')
                            if search and not feishu_messenger_sessions[session_id].get('logged_in'):
                                feishu_messenger_sessions[session_id]['logged_in'] = True
                                logger.info(f"[feishu-messenger] 检测到登录成功")
                except:
                    pass
                    
    except Exception as e:
        logger.error(f"[feishu-messenger] Worker 错误: {e}")
        result_queue.put({'type': 'init', 'success': False, 'error': str(e)})
    finally:
        try:
            if browser:
                browser.close()
            if pw:
                pw.stop()
        except:
            pass
        logger.info(f"[feishu-messenger] Worker 退出: {session_id}")


@app.route('/api/feishu-messenger/init', methods=['POST'])
def feishu_messenger_init():
    """初始化飞书网页版登录，返回二维码"""
    session_id = str(uuid.uuid4())
    cmd_queue = queue.Queue()
    result_queue = queue.Queue()
    
    # 启动后台线程
    thread = threading.Thread(target=feishu_worker, args=(session_id, cmd_queue, result_queue), daemon=True)
    thread.start()
    
    # 等待初始化结果
    try:
        result = result_queue.get(timeout=90)
        if result.get('success'):
            feishu_messenger_sessions[session_id] = {
                'cmd_queue': cmd_queue,
                'result_queue': result_queue,
                'thread': thread,
                'last_screenshot': result.get('qr_image', '')
            }
            return jsonify({'success': True, 'session_id': session_id, 'qr_image': result.get('qr_image', '')})
        else:
            return jsonify({'success': False, 'error': result.get('error', '未知错误')})
    except queue.Empty:
        cmd_queue.put({'action': 'stop'})
        return jsonify({'success': False, 'error': '初始化超时'})


@app.route('/api/feishu-messenger/poll', methods=['POST'])
def feishu_messenger_poll():
    """轮询飞书登录状态，返回最新截图"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    
    # 直接返回后台线程更新的最新截图
    last_screenshot = session.get('last_screenshot', '')
    logged_in = session.get('logged_in', False)
    
    if last_screenshot:
        return jsonify({
            'success': True, 
            'status': 'logged_in' if logged_in else 'waiting_scan', 
            'screenshot': last_screenshot,
            'logged_in': logged_in
        })
    else:
        return jsonify({'success': False, 'error': '截图未就绪'})


@app.route('/api/feishu-messenger/open-bot', methods=['POST'])
def feishu_messenger_open_bot():
    """打开机器人聊天窗口并预设命令"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    bot_name = data.get('bot_name', 'openclaw')
    preset_msg = data.get('preset_msg', '执行 curl ifconfig.me')
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    cmd_queue = session['cmd_queue']
    result_queue = session['result_queue']
    
    # 发送打开机器人聊天的命令
    cmd_queue.put({
        'action': 'open_bot_chat',
        'bot_name': bot_name,
        'preset_msg': preset_msg
    })
    
    try:
        result = result_queue.get(timeout=30)
        return jsonify(result)
    except queue.Empty:
        return jsonify({'success': False, 'error': '操作超时'})


@app.route('/api/feishu-messenger/send', methods=['POST'])
def feishu_messenger_send():
    """发送当前输入框中的消息"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    cmd_queue = session['cmd_queue']
    result_queue = session['result_queue']
    
    # 发送消息命令
    cmd_queue.put({'action': 'send_message'})
    
    try:
        result = result_queue.get(timeout=15)
        return jsonify(result)
    except queue.Empty:
        return jsonify({'success': False, 'error': '发送超时'})


@app.route('/api/feishu-messenger/screenshot', methods=['POST'])
def feishu_messenger_screenshot():
    """获取当前飞书页面截图"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    cmd_queue = session['cmd_queue']
    result_queue = session['result_queue']
    
    # 发送截图命令
    cmd_queue.put({'action': 'screenshot'})
    
    try:
        result = result_queue.get(timeout=10)
        return jsonify(result)
    except queue.Empty:
        return jsonify({'success': False, 'error': '截图超时'})


@app.route('/api/feishu-messenger/click', methods=['POST'])
def feishu_messenger_click():
    """在飞书页面上点击指定位置"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    x = data.get('x', 0)
    y = data.get('y', 0)
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    cmd_queue = session['cmd_queue']
    result_queue = session['result_queue']
    
    # 发送点击命令
    cmd_queue.put({'action': 'click', 'x': x, 'y': y})
    
    try:
        result = result_queue.get(timeout=10)
        return jsonify(result)
    except queue.Empty:
        return jsonify({'success': False, 'error': '点击超时'})


@app.route('/api/feishu-messenger/type', methods=['POST'])
def feishu_messenger_type():
    """在飞书页面上输入文字"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    text = data.get('text', '')
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    cmd_queue = session['cmd_queue']
    result_queue = session['result_queue']
    
    # 发送输入命令
    cmd_queue.put({'action': 'type', 'text': text})
    
    try:
        result = result_queue.get(timeout=10)
        return jsonify(result)
    except queue.Empty:
        return jsonify({'success': False, 'error': '输入超时'})


@app.route('/api/feishu-messenger/press', methods=['POST'])
def feishu_messenger_press():
    """按下键盘按键"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    key = data.get('key', 'Enter')
    
    if not session_id or session_id not in feishu_messenger_sessions:
        return jsonify({'success': False, 'error': '会话不存在'})
    
    session = feishu_messenger_sessions[session_id]
    cmd_queue = session['cmd_queue']
    result_queue = session['result_queue']
    
    # 发送按键命令
    cmd_queue.put({'action': 'press', 'key': key})
    
    try:
        result = result_queue.get(timeout=10)
        return jsonify(result)
    except queue.Empty:
        return jsonify({'success': False, 'error': '按键超时'})


@app.route('/api/feishu-messenger/cleanup', methods=['POST'])
def feishu_messenger_cleanup():
    """清理飞书会话"""
    data = request.get_json() or {}
    session_id = data.get('session_id')
    
    if session_id and session_id in feishu_messenger_sessions:
        session = feishu_messenger_sessions[session_id]
        try:
            session['cmd_queue'].put({'action': 'stop'})
        except:
            pass
        del feishu_messenger_sessions[session_id]
    
    return jsonify({'success': True})


if __name__ == '__main__':
    os.makedirs(Config.COOKIE_DIR, exist_ok=True)
    
    corp_id = get_corp_id_from_cookies()
    
    logger.info("=" * 60)
    logger.info("OpenClaw 企业微信接入自动化控制台启动")
    logger.info(f"访问地址: http://{Config.HOST}:{Config.PORT}")
    logger.info(f"企业ID: {corp_id or '未配置（请先运行cookie预存）'}")
    logger.info(f"工作目录: {os.path.dirname(__file__) or os.getcwd()}")
    logger.info(f"DEBUG模式: {os.environ.get('DEBUG', 'false')}")
    logger.info("=" * 60)
    
    print(f"""
╔════════════════════════════════════════════════════════════╗
║       OpenClaw 企业微信接入自动化控制台                      ║
╠════════════════════════════════════════════════════════════╣
║  访问地址: http://{Config.HOST}:{Config.PORT}                          
║  企业ID: {corp_id or '未配置（请先运行cookie预存）'}                              
╚════════════════════════════════════════════════════════════╝
""")
    
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=os.environ.get('DEBUG', 'false').lower() == 'true'
    )
