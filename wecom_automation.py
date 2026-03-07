"""
企业微信后台自动化操作
使用预存Cookie自动登录，创建应用，配置webhook
"""
import json
import re
import time
import secrets
from typing import Optional, Dict, Tuple
from playwright.sync_api import sync_playwright, Page
from cookie_manager import WeComCookieManager


class WeComAutomation:
    """企微后台自动化"""
    
    def __init__(self, corp_id: str, cookie_dir: str = "./cookies"):
        self.corp_id = corp_id
        self.cookie_mgr = WeComCookieManager(corp_id, cookie_dir)
        self.base_url = "https://work.weixin.qq.com"
    
    def _check_login_status(self, page: Page) -> bool:
        """检查是否登录成功"""
        return "loginpage" not in page.url and "frame" in page.url
    
    def create_app_and_configure(
        self,
        app_name: str,
        webhook_url: str,
        trusted_ip: str,
        app_description: str = "OpenClaw AI Assistant"
    ) -> Dict:
        """
        创建企微应用并配置
        
        Returns:
            {
                "success": bool,
                "agent_id": str,
                "secret": str,
                "token": str,
                "aes_key": str,
                "wechat_qrcode_url": str,
                "error": str
            }
        """
        cookies = self.cookie_mgr.get_valid_cookies()
        if not cookies:
            return {
                "success": False,
                "error": "Cookie无效或已过期，请运行: python cookie_manager.py save " + self.corp_id
            }
        
        result = {
            "success": False,
            "agent_id": "",
            "secret": "",
            "token": "",
            "aes_key": "",
            "wechat_qrcode_url": "",
            "error": ""
        }
        
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            
            # 加载Cookie
            context.add_cookies(cookies)
            page = context.new_page()
            
            try:
                # 1. 访问后台首页验证登录
                page.goto(f"{self.base_url}/wework_admin/frame")
                page.wait_for_load_state("networkidle")
                
                if not self._check_login_status(page):
                    result["error"] = "Cookie失效，请重新运行预存程序"
                    return result
                
                print("✅ Cookie登录成功")
                
                # 2. 创建自建应用
                app_info = self._create_application(page, app_name, app_description)
                if not app_info:
                    result["error"] = "创建应用失败"
                    return result
                
                result["agent_id"] = app_info["agent_id"]
                print(f"✅ 应用创建成功, AgentId: {app_info['agent_id']}")
                
                # 3. 获取Secret
                secret = self._get_app_secret(page, app_info["agent_id"])
                if secret:
                    result["secret"] = secret
                    print("✅ 获取Secret成功")
                
                # 4. 配置可信IP
                self._configure_trusted_ip(page, trusted_ip)
                print(f"✅ 可信IP配置成功: {trusted_ip}")
                
                # 5. 生成Token和AESKey
                token, aes_key = self._generate_token_and_aes_key()
                result["token"] = token
                result["aes_key"] = aes_key
                
                # 6. 配置接收消息API
                self._configure_webhook(page, webhook_url, token, aes_key)
                print(f"✅ Webhook配置成功: {webhook_url}")
                
                # 7. 获取微信插件二维码
                qrcode_url = self._get_wechat_plugin_qrcode(page)
                if qrcode_url:
                    result["wechat_qrcode_url"] = qrcode_url
                    print("✅ 微信插件二维码获取成功")
                
                result["success"] = True
                
            except Exception as e:
                result["error"] = str(e)
                print(f"❌ 操作失败: {e}")
            finally:
                browser.close()
        
        return result
    
    def _generate_token_and_aes_key(self) -> Tuple[str, str]:
        """生成Token和AESKey"""
        token = secrets.token_urlsafe(16)[:32]
        aes_key = secrets.token_urlsafe(32)[:43]  # AESKey必须是43位
        return token, aes_key
    
    def _create_application(self, page: Page, name: str, description: str) -> Optional[Dict]:
        """创建自建应用"""
        try:
            # 导航到应用管理页面
            page.goto(f"{self.base_url}/wework_admin/frame#apps/createSelfApp")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            
            # 填写应用信息
            name_input = page.query_selector('input[name="name"], input[placeholder*="应用名称"], .app_name_input input')
            if name_input:
                name_input.fill(name)
            
            # 选择可见范围（全员可见）
            try:
                page.click('text=选择成员')
                page.wait_for_timeout(500)
                page.click('text=全部成员')
                page.click('button:has-text("确定")')
            except:
                pass
            
            # 填写描述
            desc_input = page.query_selector('textarea[name="description"], textarea[placeholder*="描述"]')
            if desc_input:
                desc_input.fill(description)
            
            # 提交创建
            page.click('button:has-text("创建应用"), a:has-text("创建应用")')
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            
            # 从URL或页面中提取AgentId
            match = re.search(r'modApiApp/(\d+)|selfApp/(\d+)', page.url)
            if match:
                agent_id = match.group(1) or match.group(2)
                return {"agent_id": agent_id}
            
            # 尝试从页面提取
            agent_el = page.query_selector('[data-agent-id], .agent-id, .app_id')
            if agent_el:
                agent_id = agent_el.get_attribute('data-agent-id') or agent_el.text_content()
                return {"agent_id": agent_id.strip()}
            
            return None
        except Exception as e:
            print(f"创建应用异常: {e}")
            return None
    
    def _get_app_secret(self, page: Page, agent_id: str) -> Optional[str]:
        """获取应用Secret"""
        try:
            page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp/{agent_id}")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            
            # 点击查看Secret
            view_btn = page.query_selector('a:has-text("查看"), span:has-text("查看")')
            if view_btn:
                view_btn.click()
                page.wait_for_timeout(1000)
            
            # 获取Secret值
            secret_el = page.query_selector('.secret_value, .js_secret, [data-secret]')
            if secret_el:
                return secret_el.text_content().strip()
            
            return None
        except Exception as e:
            print(f"获取Secret异常: {e}")
            return None
    
    def _configure_trusted_ip(self, page: Page, ip: str):
        """配置企业可信IP"""
        try:
            # 导航到安全设置
            page.goto(f"{self.base_url}/wework_admin/frame#security/apiAuth")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            
            # 点击配置可信IP
            page.click('text=配置, a:has-text("配置")')
            page.wait_for_timeout(500)
            
            # 输入IP
            ip_input = page.query_selector('input[placeholder*="IP"], textarea[placeholder*="IP"]')
            if ip_input:
                ip_input.fill(ip)
            
            # 保存
            page.click('button:has-text("确定"), button:has-text("保存")')
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"配置可信IP异常: {e}")
    
    def _configure_webhook(self, page: Page, url: str, token: str, aes_key: str):
        """配置消息接收URL"""
        try:
            # 导航到应用的API设置页面
            page.click('text=API接收消息, a:has-text("接收消息")')
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1000)
            
            # 点击设置
            page.click('text=设置API接收, a:has-text("设置")')
            page.wait_for_timeout(500)
            
            # 填写URL
            url_input = page.query_selector('input[placeholder*="URL"], input[name="url"]')
            if url_input:
                url_input.fill(url)
            
            # 填写Token
            token_input = page.query_selector('input[placeholder*="Token"], input[name="token"]')
            if token_input:
                token_input.fill(token)
            
            # 填写AESKey
            aes_input = page.query_selector('input[placeholder*="EncodingAESKey"], input[name="encodingAESKey"]')
            if aes_input:
                aes_input.fill(aes_key)
            
            # 保存
            page.click('button:has-text("保存")')
            page.wait_for_load_state("networkidle")
        except Exception as e:
            print(f"配置Webhook异常: {e}")
    
    def _get_wechat_plugin_qrcode(self, page: Page) -> Optional[str]:
        """获取微信插件二维码URL"""
        try:
            # 导航到微信插件页面
            page.goto(f"{self.base_url}/wework_admin/frame#customer/wechatPlugin")
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(2000)
            
            # 获取二维码图片URL
            qr_img = page.query_selector('img.qrcode, img[alt*="二维码"], .qrcode-img img, .wechat_qrcode img')
            if qr_img:
                src = qr_img.get_attribute('src')
                if src and src.startswith('http'):
                    return src
                elif src and src.startswith('data:'):
                    return src
            
            # 尝试截图二维码区域
            qr_container = page.query_selector('.qrcode-container, .wechat_plugin_qrcode')
            if qr_container:
                screenshot = qr_container.screenshot()
                # 可以将截图保存或转为base64
                import base64
                return f"data:image/png;base64,{base64.b64encode(screenshot).decode()}"
            
            return None
        except Exception as e:
            print(f"获取微信插件二维码异常: {e}")
            return None


def get_public_ip() -> str:
    """获取本机公网IP"""
    import urllib.request
    try:
        return urllib.request.urlopen('https://api.ipify.org', timeout=10).read().decode('utf8')
    except:
        try:
            return urllib.request.urlopen('https://ifconfig.me/ip', timeout=10).read().decode('utf8')
        except:
            return ""


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python wecom_automation.py test <corp_id>  # 测试Cookie登录")
        sys.exit(1)
    
    action = sys.argv[1]
    corp_id = sys.argv[2] if len(sys.argv) > 2 else "default"
    
    if action == "test":
        automation = WeComAutomation(corp_id)
        result = automation.create_app_and_configure(
            app_name="Test App",
            webhook_url="https://example.com/webhook",
            trusted_ip="127.0.0.1"
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
