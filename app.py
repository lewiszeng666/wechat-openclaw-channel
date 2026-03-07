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
from datetime import datetime
from flask import Flask, render_template, jsonify, request
from config import Config
from cookie_manager import WeComCookieManager
from wecom_automation import WeComAutomation
from openclaw_plugin import (
    send_test_message_via_bot,
    send_command_via_bot,
    install_wecom_plugin_via_feishu,
    generate_wecom_install_commands
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


def get_corp_id_from_cookies() -> str:
    """从cookies目录中获取已保存的corp_id"""
    cookie_dir = Config.COOKIE_DIR
    if not os.path.exists(cookie_dir):
        return ""
    
    for f in os.listdir(cookie_dir):
        if f.startswith("wecom_") and f.endswith(".json"):
            return f.replace("wecom_", "").replace(".json", "")
    return Config.WECOM_CORP_ID


@app.route('/')
def index():
    """首页"""
    corp_id = get_corp_id_from_cookies()
    cookie_status = {"valid": False, "message": "未找到Cookie"}
    
    if corp_id:
        cookie_mgr = WeComCookieManager(corp_id, Config.COOKIE_DIR)
        cookie_status = cookie_mgr.get_status()
    
    return render_template('index.html', 
                          cookie_status=cookie_status,
                          corp_id=corp_id)


@app.route('/api/cookie-status')
def api_cookie_status():
    """获取Cookie状态"""
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"valid": False, "message": "未找到Cookie文件"})
    
    cookie_mgr = WeComCookieManager(corp_id, Config.COOKIE_DIR)
    return jsonify(cookie_mgr.get_status())


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
    
    if not app_id or not app_secret or not open_id:
        return jsonify({
            "available": False,
            "message": "缺少必要参数: app_id, app_secret, open_id"
        })
    
    logger.info(f"[feishu_verify] 验证飞书通道: app_id={app_id}, open_id={open_id[:20]}...")
    
    # 通过机器人发送测试消息
    result = send_test_message_via_bot(app_id, app_secret, open_id)
    
    if result["success"]:
        logger.info(f"[feishu_verify] 测试消息发送成功，通道验证通过")
        return jsonify({
            "available": True,
            "message": "测试消息已发送到飞书，请在飞书中查看确认",
            "app_id": app_id,
            "open_id": open_id
        })
    else:
        logger.warning(f"[feishu_verify] 测试消息发送失败: {result['message']}")
        return jsonify({
            "available": False,
            "message": f"通道验证失败: {result['message']}"
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
    """
    data = request.json or {}
    session_id = data.get("session_id", "")
    logger.debug(f"[feishu_poll] 开始轮询, session_id={session_id}")
    
    try:
        cwd_path = os.path.dirname(__file__) or '.'
        result = subprocess.run(
            ['python3', 'feishu_bot.py', 'poll'],
            capture_output=True,
            text=True,
            timeout=30,
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
            logger.info(f"[feishu_poll] 扫码成功! app_id={poll_result.get('app_id')}, bot_name={poll_result.get('bot_name')}")
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
                "manage_url": poll_result.get("manage_url")
            })
        
        elif status == "scanned":
            logger.info("[feishu_poll] 已扫码，等待确认")
            return jsonify({
                "success": True,
                "status": "scanned",
                "message": "已扫码，等待确认"
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


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({"status": "ok"})


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
