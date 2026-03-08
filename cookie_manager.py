"""
企业微信浏览器会话管理器
使用持久化浏览器目录 + Cookie文件保持登录状态
Session Cookie 需要手动导出/注入，因为 Chromium 不持久化它们
24小时刷新一次即可
"""
import json
import os
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from playwright.sync_api import sync_playwright, BrowserContext


# 持久化浏览器目录
BROWSER_DATA_DIR = os.path.join(os.path.dirname(__file__), "browser_data")
SESSION_INFO_FILE = os.path.join(BROWSER_DATA_DIR, "session_info.json")


def get_browser_data_dir(corp_id: str) -> str:
    """获取指定企业的浏览器数据目录"""
    return os.path.join(BROWSER_DATA_DIR, corp_id)


def get_cookies_file(corp_id: str) -> str:
    """获取Cookie文件路径"""
    return os.path.join(get_browser_data_dir(corp_id), "session_cookies.json")


def save_cookies(context: BrowserContext, corp_id: str) -> List[dict]:
    """从浏览器Context导出Cookie到文件（包括Session Cookie）"""
    cookies = context.cookies()
    cookies_file = get_cookies_file(corp_id)
    
    # 只保存企微相关的Cookie
    wecom_cookies = [c for c in cookies if 'weixin' in c.get('domain', '')]
    
    with open(cookies_file, 'w', encoding='utf-8') as f:
        json.dump(wecom_cookies, f, indent=2, ensure_ascii=False)
    
    print(f"已导出 {len(wecom_cookies)} 个Cookie到 {cookies_file}")
    return wecom_cookies


def load_cookies(context: BrowserContext, corp_id: str) -> bool:
    """从文件加载Cookie到浏览器Context"""
    cookies_file = get_cookies_file(corp_id)
    
    if not os.path.exists(cookies_file):
        print(f"Cookie文件不存在: {cookies_file}")
        return False
    
    with open(cookies_file, 'r', encoding='utf-8') as f:
        cookies = json.load(f)
    
    if not cookies:
        print("Cookie文件为空")
        return False
    
    # 检查关键Cookie是否存在
    cookie_names = [c['name'] for c in cookies]
    if 'wwrtx.sid' not in cookie_names or 'wwrtx.vst' not in cookie_names:
        print("Cookie文件缺少关键会话Cookie (wwrtx.sid/wwrtx.vst)")
        return False
    
    context.add_cookies(cookies)
    print(f"已加载 {len(cookies)} 个Cookie")
    return True


def save_session_info(corp_id: str, admin_name: str = "unknown"):
    """保存会话信息（登录时间等元数据，不保存Cookie）"""
    os.makedirs(BROWSER_DATA_DIR, exist_ok=True)
    info_file = os.path.join(get_browser_data_dir(corp_id), "session_info.json")
    os.makedirs(os.path.dirname(info_file), exist_ok=True)
    
    data = {
        "corp_id": corp_id,
        "admin_name": admin_name,
        "login_at": datetime.now().isoformat(),
        "expires_at": (datetime.now() + timedelta(hours=24)).isoformat()
    }
    with open(info_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def get_session_status(corp_id: str) -> Dict:
    """检查会话状态"""
    data_dir = get_browser_data_dir(corp_id)
    info_file = os.path.join(data_dir, "session_info.json")
    
    if not os.path.exists(info_file):
        return {
            "valid": False,
            "message": "未找到登录会话，请先运行 login 命令",
            "remaining_hours": 0
        }
    
    with open(info_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    login_at = datetime.fromisoformat(data["login_at"])
    elapsed = datetime.now() - login_at
    remaining = timedelta(hours=24) - elapsed
    
    if remaining.total_seconds() <= 0:
        return {
            "valid": False,
            "message": f"会话已过期（登录于 {data['login_at']}），请重新登录",
            "remaining_hours": 0,
            "admin_name": data.get("admin_name", "unknown")
        }
    
    remaining_hours = remaining.total_seconds() / 3600
    return {
        "valid": True,
        "message": f"会话有效，剩余约 {remaining_hours:.1f} 小时",
        "remaining_hours": remaining_hours,
        "admin_name": data.get("admin_name", "unknown"),
        "login_at": data["login_at"]
    }


def create_persistent_context(corp_id: str, headless: bool = False, auto_load_cookies: bool = True) -> tuple:
    """
    创建持久化浏览器 Context
    返回 (playwright, context)，调用者需要自己管理生命周期
    
    Args:
        corp_id: 企业ID
        headless: 是否无头模式
        auto_load_cookies: 是否自动加载之前保存的Cookie
    """
    from playwright.sync_api import sync_playwright
    
    data_dir = get_browser_data_dir(corp_id)
    os.makedirs(data_dir, exist_ok=True)
    
    p = sync_playwright().start()
    context = p.chromium.launch_persistent_context(
        data_dir,
        headless=headless,
        viewport={'width': 1280, 'height': 800},
        args=['--window-size=1280,800'] if not headless else []
    )
    
    # 自动加载之前保存的Cookie（包括Session Cookie）
    if auto_load_cookies:
        try:
            load_cookies(context, corp_id)
        except Exception as e:
            print(f"加载Cookie失败: {e}")
    
    return p, context


def login_interactive(corp_id: str, skip_confirm: bool = False):
    """
    交互式登录：打开浏览器扫码登录，登录后保持浏览器运行
    """
    print("=" * 60)
    print("  企业微信登录")
    print("=" * 60)
    print(f"\n企业ID: {corp_id}")
    print(f"浏览器数据目录: {get_browser_data_dir(corp_id)}")
    print("\n即将打开浏览器，请使用企业微信App扫描二维码登录...")
    print("登录成功后浏览器将保持运行，按 Ctrl+C 退出\n")
    
    if not skip_confirm:
        input("按回车键继续...")
    
    p, context = create_persistent_context(corp_id, headless=False)
    
    try:
        page = context.pages[0] if context.pages else context.new_page()
        
        # 检查是否已经登录
        page.goto("https://work.weixin.qq.com/wework_admin/frame")
        page.wait_for_load_state("domcontentloaded")
        
        import time
        time.sleep(2)
        
        current_url = page.url
        if "loginpage" in current_url:
            print("\n需要扫码登录...")
            print("=" * 60)
            print("  请使用企业微信App扫描浏览器中的二维码")
            print("=" * 60)
            
            # 等待登录成功
            page.wait_for_url("**/wework_admin/frame**", timeout=180000)
            print("\n✅ 登录成功！")
        else:
            print("\n✅ 已经是登录状态！")
        
        # 获取管理员名称
        admin_name = "unknown"
        try:
            name_el = page.query_selector(".ww_userName, .member_name, .user-name")
            if name_el:
                admin_name = name_el.text_content().strip()
        except:
            pass
        
        # 保存会话信息
        data = save_session_info(corp_id, admin_name)
        
        print("\n" + "=" * 60)
        print("  登录成功！浏览器将保持运行")
        print("=" * 60)
        print(f"  管理员: {admin_name}")
        print(f"  登录时间: {data['login_at']}")
        print(f"  建议刷新: 24小时内")
        print(f"  浏览器数据: {get_browser_data_dir(corp_id)}")
        print("=" * 60)
        print("\n按 Ctrl+C 退出（浏览器会话将保持）")
        
        # 保持运行，等待用户退出
        while True:
            time.sleep(60)
            # 每分钟刷新一下页面保持活跃
            try:
                page.reload()
            except:
                pass
                
    except KeyboardInterrupt:
        print("\n\n用户退出，正在保存Cookie...")
        # 关闭前导出Cookie（包括Session Cookie）
        try:
            save_cookies(context, corp_id)
        except Exception as e:
            print(f"保存Cookie失败: {e}")
        print("浏览器数据已保存")
    finally:
        try:
            context.close()
        except Exception:
            pass  # 忽略关闭时的连接错误
        try:
            p.stop()
        except Exception:
            pass


def check_status(corp_id: str) -> bool:
    """检查会话状态"""
    status = get_session_status(corp_id)
    print("\n" + "=" * 60)
    print("  会话状态检查")
    print("=" * 60)
    print(f"  状态: {'✅ 有效' if status['valid'] else '❌ 无效'}")
    print(f"  信息: {status['message']}")
    if status.get('admin_name'):
        print(f"  管理员: {status['admin_name']}")
    if status.get('login_at'):
        print(f"  登录时间: {status['login_at']}")
    print("=" * 60)
    return status['valid']


# 命令行入口
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python cookie_manager.py login <corp_id>  # 登录并保持浏览器运行")
        print("  python cookie_manager.py check <corp_id>  # 检查会话状态")
        print("")
        print("示例:")
        print("  python cookie_manager.py login ww1234567890")
        print("  python cookie_manager.py check ww1234567890")
        print("")
        print("说明:")
        print("  - 登录后浏览器数据保存在 browser_data/<corp_id>/ 目录")
        print("  - 后续脚本直接使用该目录启动浏览器，无需重新登录")
        print("  - 建议每24小时重新登录一次刷新会话")
        sys.exit(1)
    
    action = sys.argv[1]
    corp_id = sys.argv[2] if len(sys.argv) > 2 else "default"
    
    # 兼容旧命令
    if action == "save":
        action = "login"
    
    if action == "login":
        skip_confirm = "--no-confirm" in sys.argv
        login_interactive(corp_id, skip_confirm=skip_confirm)
    elif action == "check":
        check_status(corp_id)
    else:
        print(f"未知操作: {action}")
        sys.exit(1)
