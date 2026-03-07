"""
OpenClaw企微接入自动化Web控制台
流程：飞书扫码 → 自动创建飞书机器人 → 配置企微后台 → 生成微信二维码
"""
import os
import json
import secrets
import subprocess
import threading
import time
from flask import Flask, render_template, jsonify, request
from config import Config
from cookie_manager import WeComCookieManager
from wecom_automation import WeComAutomation

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
# 飞书扫码相关 API
# ============================================================

@app.route('/api/feishu/init', methods=['POST'])
def feishu_init():
    """
    初始化飞书扫码
    调用 feishu_bot.py init 获取二维码内容
    """
    session_id = secrets.token_hex(8)
    
    try:
        # 调用 feishu_bot.py init
        # 首次运行可能需要安装 Chromium，超时设为 180 秒
        result = subprocess.run(
            ['python3', 'feishu_bot.py', 'init'],
            capture_output=True,
            text=True,
            timeout=180,
            cwd=os.path.dirname(__file__) or '.'
        )
        
        if result.returncode != 0:
            error_msg = result.stderr or result.stdout or "初始化失败"
            return jsonify({"success": False, "error": error_msg})
        
        # 解析二维码内容
        qr_content = result.stdout.strip()
        
        feishu_sessions[session_id] = {
            "status": "pending",
            "qr_content": qr_content,
            "created_at": time.time()
        }
        
        return jsonify({
            "success": True,
            "session_id": session_id,
            "qr_content": qr_content
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "初始化超时"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


@app.route('/api/feishu/poll', methods=['POST'])
def feishu_poll():
    """
    轮询飞书扫码状态
    调用 feishu_bot.py poll 检测扫码结果
    """
    data = request.json or {}
    session_id = data.get("session_id", "")
    
    try:
        result = subprocess.run(
            ['python3', 'feishu_bot.py', 'poll'],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=os.path.dirname(__file__)
        )
        
        output = result.stdout.strip()
        
        try:
            poll_result = json.loads(output)
        except json.JSONDecodeError:
            poll_result = {"status": "error", "message": output or "解析响应失败"}
        
        status = poll_result.get("status", "error")
        
        if status == "ok":
            # 扫码成功，获取到飞书机器人信息
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
            return jsonify({
                "success": False,
                "status": "expired",
                "message": "二维码已过期，请重新获取"
            })
        
        else:
            return jsonify({
                "success": False,
                "status": "error",
                "message": poll_result.get("message", "未知错误")
            })
        
    except subprocess.TimeoutExpired:
        return jsonify({"success": True, "status": "pending", "message": "等待扫码"})
    except Exception as e:
        return jsonify({"success": False, "status": "error", "message": str(e)})


@app.route('/api/feishu/cleanup', methods=['POST'])
def feishu_cleanup():
    """清理飞书扫码会话"""
    try:
        subprocess.run(
            ['python3', 'feishu_bot.py', 'cleanup'],
            capture_output=True,
            timeout=10,
            cwd=os.path.dirname(__file__)
        )
        feishu_sessions.clear()
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})


# ============================================================
# 企微配置相关 API
# ============================================================

@app.route('/api/start-wecom-setup', methods=['POST'])
def start_wecom_setup():
    """
    开始企微配置流程
    飞书扫码完成后调用，自动配置企微后台
    """
    data = request.json or {}
    app_name = data.get("app_name", "OpenClaw AI助手")
    
    corp_id = get_corp_id_from_cookies()
    if not corp_id:
        return jsonify({"success": False, "error": "未找到企微Cookie，请先运行预存程序"})
    
    task_id = secrets.token_hex(8)
    
    tasks[task_id] = {
        "task_id": task_id,
        "status": "started",
        "step": 0,
        "total_steps": 2,
        "steps": [
            {"name": "配置企业微信后台", "status": "pending"},
            {"name": "生成微信关注二维码", "status": "pending"}
        ],
        "result": {},
        "error": ""
    }
    
    def run_setup():
        try:
            _execute_wecom_setup(task_id, corp_id, app_name)
        except Exception as e:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
    
    thread = threading.Thread(target=run_setup)
    thread.start()
    
    return jsonify({"task_id": task_id, "status": "started"})


def _execute_wecom_setup(task_id: str, corp_id: str, app_name: str):
    """执行企微配置流程"""
    task = tasks[task_id]
    
    try:
        # 获取服务器公网IP
        public_ip = _get_public_ip()
        webhook_url = f"http://{public_ip}:8080/webhook/wecom"  # OpenClaw 默认 webhook 地址
        
        # Step 1: 配置企微后台
        task["step"] = 1
        task["steps"][0]["status"] = "running"
        task["status"] = "configuring_wecom"
        
        automation = WeComAutomation(corp_id, Config.COOKIE_DIR)
        
        wecom_result = automation.create_app_and_configure(
            app_name=app_name,
            webhook_url=webhook_url,
            trusted_ip=public_ip
        )
        
        if not wecom_result.get("success"):
            task["steps"][0]["status"] = "error"
            task["status"] = "failed"
            task["error"] = f"企微配置失败: {wecom_result.get('error')}"
            return
        
        task["steps"][0]["status"] = "done"
        
        # Step 2: 获取微信插件二维码
        task["step"] = 2
        task["steps"][1]["status"] = "running"
        task["status"] = "generating_qrcode"
        
        task["steps"][1]["status"] = "done"
        
        # 完成
        task["status"] = "completed"
        task["result"] = {
            "corp_id": corp_id,
            "agent_id": wecom_result.get("agent_id"),
            "secret": wecom_result.get("secret"),
            "token": wecom_result.get("token"),
            "aes_key": wecom_result.get("aes_key"),
            "wechat_qrcode_url": wecom_result.get("wechat_qrcode_url", ""),
            "webhook_url": webhook_url,
            "public_ip": public_ip
        }
        
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        current_step = task.get("step", 1) - 1
        if 0 <= current_step < len(task["steps"]):
            task["steps"][current_step]["status"] = "error"


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


@app.route('/api/openclaw-config')
def get_openclaw_config():
    """获取 OpenClaw 配置信息（飞书扫码后自动生成）"""
    config_path = Config.OPENCLAW_CONFIG_PATH
    
    if not os.path.exists(config_path):
        return jsonify({
            "success": False,
            "message": "OpenClaw 配置不存在，请先完成飞书扫码"
        })
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
        
        return jsonify({
            "success": True,
            "config": {
                "feishu_app_id": config.get("feishu", {}).get("app_id", ""),
                "feishu_configured": bool(config.get("feishu", {}).get("app_id"))
            }
        })
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    os.makedirs(Config.COOKIE_DIR, exist_ok=True)
    
    corp_id = get_corp_id_from_cookies()
    
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
