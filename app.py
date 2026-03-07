"""
OpenClaw企微接入自动化Web控制台
"""
import os
import secrets
import threading
from flask import Flask, render_template, jsonify, request, redirect, url_for
from config import Config
from cookie_manager import WeComCookieManager
from wecom_automation import WeComAutomation, get_public_ip
from openclaw_installer import OpenClawInstaller

app = Flask(__name__)
app.config.from_object(Config)

# 存储进行中的任务
tasks = {}


@app.route('/')
def index():
    """首页"""
    cookie_mgr = WeComCookieManager(Config.WECOM_CORP_ID, Config.COOKIE_DIR)
    cookie_status = cookie_mgr.get_status()
    
    return render_template('index.html', 
                          cookie_status=cookie_status,
                          config=Config)


@app.route('/api/cookie-status')
def api_cookie_status():
    """获取Cookie状态"""
    cookie_mgr = WeComCookieManager(Config.WECOM_CORP_ID, Config.COOKIE_DIR)
    return jsonify(cookie_mgr.get_status())


@app.route('/api/test-ssh', methods=['POST'])
def test_ssh():
    """测试SSH连接"""
    try:
        installer = OpenClawInstaller(
            host=Config.OPENCLAW_HOST,
            user=Config.OPENCLAW_SSH_USER,
            ssh_key=Config.OPENCLAW_SSH_KEY,
            port=Config.OPENCLAW_SSH_PORT
        )
        result = installer.test_connection()
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


@app.route('/api/start-setup', methods=['POST'])
def start_setup():
    """
    开始配置流程
    1. 安装OpenClaw WeChat插件
    2. 自动配置企微后台
    3. 返回微信二维码
    """
    data = request.json or {}
    task_id = secrets.token_hex(8)
    
    # 初始化任务状态
    tasks[task_id] = {
        "task_id": task_id,
        "status": "started",
        "step": 0,
        "total_steps": 4,
        "steps": [
            {"name": "测试SSH连接", "status": "pending"},
            {"name": "安装WeChat插件", "status": "pending"},
            {"name": "配置企业微信后台", "status": "pending"},
            {"name": "生成微信二维码", "status": "pending"}
        ],
        "result": {},
        "error": ""
    }
    
    # 在后台线程执行
    def run_setup():
        try:
            _execute_setup(task_id, data)
        except Exception as e:
            tasks[task_id]["status"] = "failed"
            tasks[task_id]["error"] = str(e)
    
    thread = threading.Thread(target=run_setup)
    thread.start()
    
    return jsonify({"task_id": task_id, "status": "started"})


def _execute_setup(task_id: str, data: dict):
    """执行配置流程"""
    task = tasks[task_id]
    app_name = data.get("app_name", "OpenClaw AI助手")
    
    try:
        # Step 1: 测试SSH连接
        task["step"] = 1
        task["steps"][0]["status"] = "running"
        task["status"] = "testing_ssh"
        
        installer = OpenClawInstaller(
            host=Config.OPENCLAW_HOST,
            user=Config.OPENCLAW_SSH_USER,
            ssh_key=Config.OPENCLAW_SSH_KEY,
            port=Config.OPENCLAW_SSH_PORT
        )
        
        ssh_result = installer.test_connection()
        if not ssh_result.get("success"):
            task["steps"][0]["status"] = "error"
            task["status"] = "failed"
            task["error"] = f"SSH连接失败: {ssh_result.get('message')}"
            return
        
        task["steps"][0]["status"] = "done"
        
        # 获取OpenClaw服务器公网IP
        public_ip = installer.get_public_ip()
        webhook_url = installer.get_webhook_url()
        
        # Step 2: 安装OpenClaw插件
        task["step"] = 2
        task["steps"][1]["status"] = "running"
        task["status"] = "installing_plugin"
        
        install_result = installer.install_wecom_plugin()
        if not install_result.get("success"):
            task["steps"][1]["status"] = "error"
            task["status"] = "failed"
            task["error"] = f"插件安装失败: {install_result.get('error')}"
            return
        
        task["steps"][1]["status"] = "done"
        
        # Step 3: 配置企微后台
        task["step"] = 3
        task["steps"][2]["status"] = "running"
        task["status"] = "configuring_wecom"
        
        automation = WeComAutomation(Config.WECOM_CORP_ID, Config.COOKIE_DIR)
        
        wecom_result = automation.create_app_and_configure(
            app_name=app_name,
            webhook_url=webhook_url,
            trusted_ip=public_ip
        )
        
        if not wecom_result.get("success"):
            task["steps"][2]["status"] = "error"
            task["status"] = "failed"
            task["error"] = f"企微配置失败: {wecom_result.get('error')}"
            return
        
        task["steps"][2]["status"] = "done"
        
        # Step 4: 配置OpenClaw并获取二维码
        task["step"] = 4
        task["steps"][3]["status"] = "running"
        task["status"] = "finalizing"
        
        config_result = installer.configure_wecom(
            corp_id=Config.WECOM_CORP_ID,
            agent_id=wecom_result["agent_id"],
            secret=wecom_result["secret"],
            token=wecom_result["token"],
            aes_key=wecom_result["aes_key"]
        )
        
        task["steps"][3]["status"] = "done"
        
        # 完成
        task["status"] = "completed"
        task["result"] = {
            "agent_id": wecom_result["agent_id"],
            "wechat_qrcode_url": wecom_result.get("wechat_qrcode_url", ""),
            "webhook_url": webhook_url,
            "public_ip": public_ip,
            "corp_id": Config.WECOM_CORP_ID
        }
        
    except Exception as e:
        task["status"] = "failed"
        task["error"] = str(e)
        # 标记当前步骤为错误
        current_step = task.get("step", 1) - 1
        if 0 <= current_step < len(task["steps"]):
            task["steps"][current_step]["status"] = "error"


@app.route('/api/task/<task_id>')
def get_task_status(task_id):
    """获取任务状态"""
    if task_id not in tasks:
        return jsonify({"error": "Task not found"}), 404
    return jsonify(tasks[task_id])


@app.route('/api/server-info')
def get_server_info():
    """获取服务器信息"""
    try:
        installer = OpenClawInstaller(
            host=Config.OPENCLAW_HOST,
            user=Config.OPENCLAW_SSH_USER,
            ssh_key=Config.OPENCLAW_SSH_KEY,
            port=Config.OPENCLAW_SSH_PORT
        )
        
        public_ip = installer.get_public_ip()
        plugin_status = installer.check_plugin_status()
        openclaw_status = installer.get_openclaw_status()
        
        return jsonify({
            "success": True,
            "public_ip": public_ip,
            "plugin_installed": plugin_status.get("installed", False),
            "openclaw_running": openclaw_status.get("running", False),
            "webhook_url": installer.get_webhook_url()
        })
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        })


@app.route('/health')
def health():
    """健康检查"""
    return jsonify({"status": "ok"})


if __name__ == '__main__':
    # 确保Cookie目录存在
    os.makedirs(Config.COOKIE_DIR, exist_ok=True)
    
    print(f"""
╔════════════════════════════════════════════════════════════╗
║       OpenClaw 企业微信接入自动化控制台                      ║
╠════════════════════════════════════════════════════════════╣
║  访问地址: http://{Config.HOST}:{Config.PORT}                          
║  企业ID: {Config.WECOM_CORP_ID or '未配置'}                              
║  OpenClaw: {Config.OPENCLAW_HOST or '未配置'}                            
╚════════════════════════════════════════════════════════════╝
""")
    
    app.run(
        host=Config.HOST,
        port=Config.PORT,
        debug=os.environ.get('DEBUG', 'false').lower() == 'true'
    )
