"""
企业微信Cookie管理器
包含：预存Cookie方案 + 有效性检测 + 自动刷新提醒
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from playwright.sync_api import sync_playwright


class WeComCookieManager:
    """企业微信Cookie管理器"""
    
    COOKIE_VALID_HOURS = 20  # 保守估计，实际可能24-48小时
    
    def __init__(self, corp_id: str, cookie_dir: str = "./cookies"):
        self.corp_id = corp_id
        self.cookie_dir = cookie_dir
        self.cookie_file = os.path.join(cookie_dir, f"wecom_{corp_id}.json")
        os.makedirs(cookie_dir, exist_ok=True)
    
    def save_cookies(self, cookies: List[Dict], admin_name: str = "unknown") -> Dict:
        """保存Cookie到文件"""
        data = {
            "cookies": cookies,
            "saved_at": datetime.now().isoformat(),
            "corp_id": self.corp_id,
            "admin_name": admin_name,
            "expires_at": (datetime.now() + timedelta(hours=self.COOKIE_VALID_HOURS)).isoformat()
        }
        with open(self.cookie_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return data
    
    def load_cookies(self) -> Optional[Dict]:
        """加载Cookie，返回完整数据（含元信息）"""
        if not os.path.exists(self.cookie_file):
            return None
        
        with open(self.cookie_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        return data
    
    def get_valid_cookies(self) -> Optional[List[Dict]]:
        """获取有效的Cookie列表，过期返回None"""
        data = self.load_cookies()
        if not data:
            return None
        
        saved_at = datetime.fromisoformat(data["saved_at"])
        if datetime.now() - saved_at > timedelta(hours=self.COOKIE_VALID_HOURS):
            return None
        
        return data["cookies"]
    
    def get_status(self) -> Dict:
        """获取Cookie状态"""
        data = self.load_cookies()
        if not data:
            return {
                "valid": False,
                "message": "未找到Cookie，请先运行Cookie预存程序",
                "remaining_hours": 0
            }
        
        saved_at = datetime.fromisoformat(data["saved_at"])
        elapsed = datetime.now() - saved_at
        remaining = timedelta(hours=self.COOKIE_VALID_HOURS) - elapsed
        
        if remaining.total_seconds() <= 0:
            return {
                "valid": False,
                "message": f"Cookie已过期（保存于 {data['saved_at']}），请重新运行预存程序",
                "remaining_hours": 0,
                "admin_name": data.get("admin_name", "unknown")
            }
        
        remaining_hours = remaining.total_seconds() / 3600
        return {
            "valid": True,
            "message": f"Cookie有效，剩余约 {remaining_hours:.1f} 小时",
            "remaining_hours": remaining_hours,
            "admin_name": data.get("admin_name", "unknown"),
            "saved_at": data["saved_at"]
        }


class CookiePreSaver:
    """
    Cookie预存工具
    运维人员使用此工具预先扫码保存Cookie
    """
    
    def __init__(self, corp_id: str, cookie_dir: str = "./cookies"):
        self.corp_id = corp_id
        self.cookie_mgr = WeComCookieManager(corp_id, cookie_dir)
        self.admin_url = "https://work.weixin.qq.com"
    
    def run_interactive(self):
        """交互式运行：显示二维码，等待扫码，保存Cookie"""
        print("=" * 60)
        print("  企业微信Cookie预存工具")
        print("=" * 60)
        print(f"\n企业ID: {self.corp_id}")
        print(f"Cookie存储: {self.cookie_mgr.cookie_file}")
        print("\n即将打开浏览器，请使用企业微信App扫描二维码登录...")
        print("登录成功后Cookie将自动保存\n")
        
        input("按回车键继续...")
        
        with sync_playwright() as p:
            # 启动浏览器（显示界面）
            browser = p.chromium.launch(
                headless=False,
                args=['--window-size=800,600']
            )
            context = browser.new_context(
                viewport={'width': 800, 'height': 600}
            )
            page = context.new_page()
            
            # 访问登录页
            login_url = f"{self.admin_url}/wework_admin/loginpage_wx"
            print(f"\n正在打开: {login_url}")
            page.goto(login_url)
            
            print("\n" + "=" * 60)
            print("  请使用企业微信App扫描浏览器中的二维码")
            print("=" * 60)
            
            try:
                # 等待登录成功（URL变化）
                page.wait_for_url(
                    "**/wework_admin/frame**",
                    timeout=180000  # 3分钟超时
                )
                print("\n✅ 登录成功！")
                
                # 等待页面完全加载
                page.wait_for_load_state("networkidle")
                
                # 尝试获取管理员名称
                admin_name = "unknown"
                try:
                    name_el = page.query_selector(".ww_userName, .member_name, .user-name")
                    if name_el:
                        admin_name = name_el.text_content().strip()
                except:
                    pass
                
                # 保存Cookie
                cookies = context.cookies()
                data = self.cookie_mgr.save_cookies(cookies, admin_name)
                
                print("\n" + "=" * 60)
                print("  Cookie保存成功！")
                print("=" * 60)
                print(f"  管理员: {admin_name}")
                print(f"  保存时间: {data['saved_at']}")
                print(f"  预计过期: {data['expires_at']}")
                print(f"  有效期: 约 {self.cookie_mgr.COOKIE_VALID_HOURS} 小时")
                print(f"  存储文件: {self.cookie_mgr.cookie_file}")
                print("=" * 60)
                
            except Exception as e:
                print(f"\n❌ 登录超时或失败: {e}")
            finally:
                print("\n3秒后关闭浏览器...")
                page.wait_for_timeout(3000)
                browser.close()
    
    def check_status(self) -> bool:
        """检查当前Cookie状态"""
        status = self.cookie_mgr.get_status()
        print("\n" + "=" * 60)
        print("  Cookie状态检查")
        print("=" * 60)
        print(f"  状态: {'✅ 有效' if status['valid'] else '❌ 无效'}")
        print(f"  信息: {status['message']}")
        if status.get('admin_name'):
            print(f"  管理员: {status['admin_name']}")
        if status.get('saved_at'):
            print(f"  保存时间: {status['saved_at']}")
        print("=" * 60)
        return status['valid']


# 命令行入口
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python cookie_manager.py save <corp_id>   # 预存Cookie")
        print("  python cookie_manager.py check <corp_id>  # 检查状态")
        print("")
        print("示例:")
        print("  python cookie_manager.py save ww1234567890")
        print("  python cookie_manager.py check ww1234567890")
        sys.exit(1)
    
    action = sys.argv[1]
    corp_id = sys.argv[2] if len(sys.argv) > 2 else "default"
    
    saver = CookiePreSaver(corp_id)
    
    if action == "save":
        saver.run_interactive()
    elif action == "check":
        saver.check_status()
    else:
        print(f"未知操作: {action}")
        sys.exit(1)
