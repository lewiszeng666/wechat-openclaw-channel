"""
OpenClaw WeChat插件安装器
通过SSH远程执行openclaw CLI命令
"""
import os
from typing import Dict, Optional

try:
    import paramiko
    HAS_PARAMIKO = True
except ImportError:
    HAS_PARAMIKO = False


class OpenClawInstaller:
    """OpenClaw插件安装器"""
    
    def __init__(
        self,
        host: str,
        user: str = "root",
        ssh_key: str = "~/.ssh/id_rsa",
        port: int = 22,
        password: str = None
    ):
        self.host = host
        self.user = user
        self.ssh_key = os.path.expanduser(ssh_key)
        self.port = port
        self.password = password
        
        if not HAS_PARAMIKO:
            raise ImportError("paramiko is required. Install with: pip install paramiko")
    
    def _get_ssh_client(self) -> 'paramiko.SSHClient':
        """获取SSH客户端"""
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        connect_kwargs = {
            'hostname': self.host,
            'port': self.port,
            'username': self.user,
        }
        
        if self.password:
            connect_kwargs['password'] = self.password
        elif os.path.exists(self.ssh_key):
            connect_kwargs['key_filename'] = self.ssh_key
        else:
            raise FileNotFoundError(f"SSH key not found: {self.ssh_key}")
        
        client.connect(**connect_kwargs)
        return client
    
    def _exec_command(self, cmd: str, timeout: int = 60) -> Dict:
        """执行SSH命令"""
        try:
            client = self._get_ssh_client()
            stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
            
            exit_code = stdout.channel.recv_exit_status()
            result = {
                "success": exit_code == 0,
                "stdout": stdout.read().decode('utf-8'),
                "stderr": stderr.read().decode('utf-8'),
                "exit_code": exit_code
            }
            client.close()
            return result
        except Exception as e:
            return {
                "success": False,
                "error": str(e),
                "stdout": "",
                "stderr": "",
                "exit_code": -1
            }
    
    def test_connection(self) -> Dict:
        """测试SSH连接"""
        result = self._exec_command("echo 'Connection successful'")
        if result["success"]:
            result["message"] = "SSH连接成功"
        else:
            result["message"] = f"SSH连接失败: {result.get('error', result.get('stderr'))}"
        return result
    
    def install_wecom_plugin(self) -> Dict:
        """安装企业微信插件"""
        # 使用openclaw CLI安装插件
        cmd = "openclaw plugin install @openclaw-china/wecom-app --yes 2>&1"
        result = self._exec_command(cmd, timeout=120)
        
        if result.get("exit_code") == 0:
            return {
                "success": True,
                "message": "WeChat plugin installed successfully",
                "output": result.get("stdout", "")
            }
        return {
            "success": False,
            "error": result.get("stderr") or result.get("error") or result.get("stdout"),
            "output": result.get("stdout", "")
        }
    
    def configure_wecom(
        self,
        corp_id: str,
        agent_id: str,
        secret: str,
        token: str,
        aes_key: str
    ) -> Dict:
        """配置企业微信参数"""
        # 创建配置目录
        self._exec_command("mkdir -p /etc/openclaw")
        
        # 使用环境变量方式配置
        config_content = f"""# WeChat Work (企业微信) Configuration
WECOM_CORP_ID={corp_id}
WECOM_AGENT_ID={agent_id}
WECOM_SECRET={secret}
WECOM_TOKEN={token}
WECOM_AES_KEY={aes_key}
"""
        # 写入配置文件
        cmd = f"cat > /etc/openclaw/wecom.env << 'WECOM_CONFIG_EOF'\n{config_content}WECOM_CONFIG_EOF"
        result = self._exec_command(cmd)
        
        if not result["success"]:
            return result
        
        # 重启服务
        restart_result = self._exec_command("openclaw service restart 2>&1 || systemctl restart openclaw 2>&1")
        
        return {
            "success": True,
            "message": "Configuration saved and service restarted",
            "config_file": "/etc/openclaw/wecom.env"
        }
    
    def get_public_ip(self) -> str:
        """获取OpenClaw服务器公网IP"""
        result = self._exec_command("curl -s --connect-timeout 5 https://api.ipify.org || curl -s --connect-timeout 5 https://ifconfig.me/ip")
        if result.get("success"):
            ip = result.get("stdout", "").strip()
            if ip and '.' in ip:
                return ip
        return ""
    
    def get_webhook_url(self, port: int = 8080, path: str = "/wecom/callback") -> str:
        """获取Webhook URL"""
        ip = self.get_public_ip()
        if ip:
            return f"http://{ip}:{port}{path}"
        return ""
    
    def check_plugin_status(self) -> Dict:
        """检查插件安装状态"""
        cmd = "openclaw plugin list 2>&1 | grep -i wecom"
        result = self._exec_command(cmd)
        return {
            "installed": "wecom" in result.get("stdout", "").lower(),
            "output": result.get("stdout", "")
        }
    
    def get_openclaw_status(self) -> Dict:
        """获取OpenClaw服务状态"""
        cmd = "openclaw status 2>&1 || systemctl status openclaw 2>&1"
        result = self._exec_command(cmd)
        return {
            "running": result.get("exit_code") == 0,
            "output": result.get("stdout", "")
        }


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 3:
        print("用法:")
        print("  python openclaw_installer.py test <host> [user]     # 测试SSH连接")
        print("  python openclaw_installer.py install <host> [user]  # 安装插件")
        print("  python openclaw_installer.py status <host> [user]   # 检查状态")
        print("  python openclaw_installer.py ip <host> [user]       # 获取公网IP")
        sys.exit(1)
    
    action = sys.argv[1]
    host = sys.argv[2]
    user = sys.argv[3] if len(sys.argv) > 3 else "root"
    
    installer = OpenClawInstaller(host=host, user=user)
    
    if action == "test":
        result = installer.test_connection()
        print(result["message"])
    elif action == "install":
        result = installer.install_wecom_plugin()
        print(f"Success: {result['success']}")
        print(result.get("message") or result.get("error"))
    elif action == "status":
        result = installer.check_plugin_status()
        print(f"Plugin installed: {result['installed']}")
        print(result["output"])
    elif action == "ip":
        ip = installer.get_public_ip()
        print(f"Public IP: {ip}")
    else:
        print(f"Unknown action: {action}")
