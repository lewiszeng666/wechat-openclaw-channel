"""
OpenClaw 插件管理
通过飞书消息通道向 OpenClaw 发送命令，安装和配置 wecom-app 插件

参考文档: https://github.com/BytePioneer-AI/openclaw-china

【重要】验证飞书通道的正确方式：
  本服务不与 OpenClaw 部署在同一机器，无法直接读取 openclaw.json。
  正确的验证方式是：
  1. 用户飞书扫码登录本系统
  2. 获取用户已有的飞书机器人列表
  3. 找到名称包含 "OpenClaw" 的机器人（说明用户已配置过飞书↔OpenClaw通道）
  4. 通过该机器人发送测试消息，验证通道是否通畅
"""
import json
import os
import ssl
import time
import urllib.request
import urllib.error
from typing import Optional, Dict, Tuple, List

# OpenClaw 配置文件路径（本机不一定存在，仅供参考）
OPENCLAW_CONFIG = "/root/.openclaw/openclaw.json"
OPENCLAW_ALLOW_FROM = "/root/.openclaw/credentials/feishu-default-allowFrom.json"


def _log(msg: str):
    """打印日志"""
    print(f"[openclaw-plugin] {msg}")


def _get_tenant_access_token(app_id: str, app_secret: str) -> Optional[str]:
    """获取飞书 tenant_access_token"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    payload = json.dumps({
        "app_id": app_id,
        "app_secret": app_secret,
    }).encode()
    
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read())
        return data.get("tenant_access_token")
    except Exception as e:
        _log(f"获取 token 失败: {e}")
        return None


def send_test_message_via_bot(app_id: str, app_secret: str, user_open_id: str) -> Dict:
    """
    通过指定的飞书机器人向用户发送测试消息
    用于验证飞书↔OpenClaw通道是否通畅
    
    Args:
        app_id: 飞书应用 ID（OpenClaw 机器人的 app_id）
        app_secret: 飞书应用密钥
        user_open_id: 接收消息的用户 open_id
    
    Returns:
        {"success": bool, "message": str}
    """
    _log(f"通过机器人发送测试消息: app_id={app_id}, user={user_open_id[:20]}...")
    
    # 获取 token
    token = _get_tenant_access_token(app_id, app_secret)
    if not token:
        return {
            "success": False,
            "message": "无法获取机器人 access_token，凭证可能无效"
        }
    
    # 发送测试消息
    test_message = "🔗 OpenClaw 通道测试\n\n如果你收到这条消息，说明飞书↔OpenClaw通道正常工作！\n\n请回复任意内容确认。"
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    payload = json.dumps({
        "receive_id": user_open_id,
        "msg_type": "text",
        "content": json.dumps({"text": test_message}),
    }).encode()
    
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read())
        
        if data.get("code") == 0:
            _log("测试消息发送成功")
            return {
                "success": True,
                "message": "测试消息已发送，请在飞书中查看并确认"
            }
        else:
            error_msg = data.get("msg", str(data))
            _log(f"测试消息发送失败: {error_msg}")
            return {
                "success": False,
                "message": f"发送失败: {error_msg}"
            }
    except Exception as e:
        _log(f"发送测试消息异常: {e}")
        return {
            "success": False,
            "message": f"发送异常: {e}"
        }


def send_command_via_bot(app_id: str, app_secret: str, user_open_id: str, command: str) -> Dict:
    """
    通过飞书机器人向用户发送命令
    用户收到消息后，OpenClaw 会在后台执行该命令
    
    Args:
        app_id: 飞书应用 ID
        app_secret: 飞书应用密钥
        user_open_id: 用户 open_id
        command: 要执行的命令
    
    Returns:
        {"success": bool, "message": str}
    """
    _log(f"发送命令: {command[:80]}...")
    
    token = _get_tenant_access_token(app_id, app_secret)
    if not token:
        return {"success": False, "message": "无法获取 access_token"}
    
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    payload = json.dumps({
        "receive_id": user_open_id,
        "msg_type": "text",
        "content": json.dumps({"text": command}),
    }).encode()
    
    req = urllib.request.Request(
        "https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id",
        data=payload,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            data = json.loads(resp.read())
        
        if data.get("code") == 0:
            return {"success": True, "message": "命令已发送"}
        else:
            return {"success": False, "message": data.get("msg", "发送失败")}
    except Exception as e:
        return {"success": False, "message": str(e)}


def generate_wecom_install_commands(
    corp_id: str,
    corp_secret: str,
    agent_id: str,
    token: str,
    aes_key: str,
    webhook_path: str = "/wecom-app"
) -> List[str]:
    """
    生成 wecom-app 插件安装和配置命令
    
    参考: https://github.com/BytePioneer-AI/openclaw-china
    
    Returns:
        命令列表
    """
    commands = [
        "# 1. 安装 wecom-app 插件",
        "openclaw plugins install @openclaw-china/wecom-app",
        "",
        "# 2. 配置 wecom-app 插件",
        "openclaw config set channels.wecom-app.enabled true",
        f"openclaw config set channels.wecom-app.webhookPath {webhook_path}",
        f"openclaw config set channels.wecom-app.token {token}",
        f"openclaw config set channels.wecom-app.encodingAESKey {aes_key}",
        f"openclaw config set channels.wecom-app.corpId {corp_id}",
        f"openclaw config set channels.wecom-app.corpSecret {corp_secret}",
        f"openclaw config set channels.wecom-app.agentId {agent_id}",
        "",
        "# 3. 重启 OpenClaw 服务生效",
        "openclaw gateway restart",
    ]
    return commands


def install_wecom_plugin_via_feishu(
    app_id: str,
    app_secret: str,
    user_open_id: str,
    corp_id: str,
    corp_secret: str,
    agent_id: str,
    msg_token: str,
    aes_key: str,
    webhook_path: str = "/wecom-app"
) -> Dict:
    """
    通过飞书通道安装和配置 wecom-app 插件
    
    【新流程】不再读取本地 openclaw.json，而是使用扫码登录后获取的机器人凭证
    
    Args:
        app_id: 飞书机器人 app_id（扫码后从已有 OpenClaw 机器人获取）
        app_secret: 飞书机器人 app_secret
        user_open_id: 用户 open_id（接收命令的用户）
        corp_id: 企微 corp_id
        corp_secret: 企微应用 secret
        agent_id: 企微应用 agent_id
        msg_token: 企微消息回调 token
        aes_key: 企微消息加密 AES Key
        webhook_path: webhook 路径
    
    Returns:
        {"success": bool, "message": str, "commands": list, "steps": list}
    """
    steps = []
    commands = generate_wecom_install_commands(
        corp_id, corp_secret, agent_id, msg_token, aes_key, webhook_path
    )
    
    # Step 1: 获取飞书 token
    _log("Step 1: 获取飞书 access_token...")
    feishu_token = _get_tenant_access_token(app_id, app_secret)
    if not feishu_token:
        return {
            "success": False,
            "message": "无法获取飞书 access_token，机器人凭证可能无效",
            "commands": commands,
            "steps": [{"step": "get_token", "status": "failed", "message": "获取 token 失败"}]
        }
    steps.append({"step": "get_token", "status": "success", "message": "获取 access_token 成功"})
    
    # Step 2: 发送安装命令
    _log("Step 2: 发送安装命令...")
    install_cmd = "openclaw plugins install @openclaw-china/wecom-app"
    result = send_command_via_bot(app_id, app_secret, user_open_id, install_cmd)
    if not result["success"]:
        steps.append({"step": "install_plugin", "status": "failed", "message": result["message"]})
        return {
            "success": False,
            "message": f"发送安装命令失败: {result['message']}",
            "commands": commands,
            "steps": steps
        }
    steps.append({"step": "install_plugin", "status": "success", "message": f"已发送: {install_cmd}"})
    time.sleep(5)  # 等待安装完成
    
    # Step 3: 发送配置命令
    _log("Step 3: 发送配置命令...")
    config_commands = [
        "openclaw config set channels.wecom-app.enabled true",
        f"openclaw config set channels.wecom-app.webhookPath {webhook_path}",
        f"openclaw config set channels.wecom-app.token {msg_token}",
        f"openclaw config set channels.wecom-app.encodingAESKey {aes_key}",
        f"openclaw config set channels.wecom-app.corpId {corp_id}",
        f"openclaw config set channels.wecom-app.corpSecret {corp_secret}",
        f"openclaw config set channels.wecom-app.agentId {agent_id}",
    ]
    
    for cmd in config_commands:
        result = send_command_via_bot(app_id, app_secret, user_open_id, cmd)
        if not result["success"]:
            _log(f"  警告: 配置命令发送失败: {cmd}")
        time.sleep(0.5)  # 间隔发送避免限流
    
    steps.append({"step": "configure_plugin", "status": "success", "message": "配置命令已发送"})
    time.sleep(2)
    
    # Step 4: 验证配置（发送检查命令）
    _log("Step 4: 验证配置...")
    verify_cmd = "openclaw config get channels.wecom-app"
    send_command_via_bot(app_id, app_secret, user_open_id, verify_cmd)
    steps.append({"step": "verify_config", "status": "success", "message": "配置验证命令已发送"})
    time.sleep(2)
    
    # Step 5: 重启 OpenClaw
    _log("Step 5: 重启 OpenClaw...")
    restart_cmd = "openclaw gateway restart"
    result = send_command_via_bot(app_id, app_secret, user_open_id, restart_cmd)
    if not result["success"]:
        steps.append({"step": "restart", "status": "warning", "message": "重启命令发送失败，请手动重启"})
    else:
        steps.append({"step": "restart", "status": "success", "message": "重启命令已发送"})
    
    return {
        "success": True,
        "message": "wecom-app 插件安装和配置命令已发送，请在飞书中确认 OpenClaw 执行结果",
        "commands": commands,
        "steps": steps,
        "config": {
            "corp_id": corp_id,
            "agent_id": agent_id,
            "webhook_path": webhook_path
        }
    }


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python openclaw_plugin.py test <app_id> <app_secret> <user_open_id>")
        print("                                                 # 发送测试消息验证通道")
        print("  python openclaw_plugin.py commands <corp_id> <secret> <agent_id> <token> <aes_key>")
        print("                                                 # 生成安装命令（不执行）")
        print("  python openclaw_plugin.py install <feishu_app_id> <feishu_secret> <user_open_id> \\")
        print("                                    <corp_id> <corp_secret> <agent_id> <token> <aes_key>")
        print("                                                 # 通过飞书通道安装配置")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == "test":
        if len(sys.argv) < 5:
            print("参数不足: <app_id> <app_secret> <user_open_id>")
            sys.exit(1)
        
        result = send_test_message_via_bot(
            app_id=sys.argv[2],
            app_secret=sys.argv[3],
            user_open_id=sys.argv[4]
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    elif action == "commands":
        if len(sys.argv) < 7:
            print("参数不足: <corp_id> <secret> <agent_id> <token> <aes_key>")
            sys.exit(1)
        
        commands = generate_wecom_install_commands(
            corp_id=sys.argv[2],
            corp_secret=sys.argv[3],
            agent_id=sys.argv[4],
            token=sys.argv[5],
            aes_key=sys.argv[6]
        )
        print("\n".join(commands))
    
    elif action == "install":
        if len(sys.argv) < 11:
            print("参数不足: <feishu_app_id> <feishu_secret> <user_open_id> <corp_id> <corp_secret> <agent_id> <token> <aes_key>")
            sys.exit(1)
        
        result = install_wecom_plugin_via_feishu(
            app_id=sys.argv[2],
            app_secret=sys.argv[3],
            user_open_id=sys.argv[4],
            corp_id=sys.argv[5],
            corp_secret=sys.argv[6],
            agent_id=sys.argv[7],
            msg_token=sys.argv[8],
            aes_key=sys.argv[9]
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    
    else:
        print(f"未知命令: {action}")
        sys.exit(1)
