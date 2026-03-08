#!/usr/bin/env python3
"""企微后台登录 - 保存Cookie以便后续复用"""

import json
import time
import os
from playwright.sync_api import sync_playwright

COOKIE_DIR = "/Users/lewiszeng/MyProject/lewiscode/wechat-openclaw-channel/cookies"

def main():
    os.makedirs(COOKIE_DIR, exist_ok=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        print("正在打开企微后台登录页...")
        page.goto("https://work.weixin.qq.com/wework_admin/loginpage_wx")
        
        print("\n" + "="*60)
        print("请扫码登录企微后台")
        print("登录成功后会自动保存Cookie")
        print("="*60)
        
        # 等待登录成功 - 检测URL变化
        while True:
            time.sleep(2)
            current_url = page.url
            print(f"当前URL: {current_url[:60]}...")
            
            # 登录成功后会跳转到 frame 页面
            if "frame" in current_url or "index" in current_url:
                print("\n✓ 检测到登录成功！")
                break
            
            # 超时检查
            if "loginpage" not in current_url and "login" not in current_url:
                print("\n✓ 页面已跳转，可能已登录")
                break
        
        # 等待页面完全加载
        time.sleep(3)
        
        # 获取Cookie
        cookies = context.cookies()
        
        # 从URL中提取corp_id
        current_url = page.url
        corp_id = None
        
        # 尝试从cookie中获取corp_id
        for cookie in cookies:
            if cookie['name'] == 'wwrtx.corpid':
                corp_id = cookie['value']
                break
        
        if not corp_id:
            # 从页面获取
            try:
                corp_id = page.evaluate("() => window.wx && window.wx.corpid")
            except:
                pass
        
        if not corp_id:
            # 从URL获取
            import re
            match = re.search(r'corpid=([^&]+)', current_url)
            if match:
                corp_id = match.group(1)
        
        if not corp_id:
            corp_id = "unknown"
            print("⚠ 未能获取corp_id，使用默认值")
        
        # 保存Cookie
        cookie_file = os.path.join(COOKIE_DIR, f"wecom_{corp_id}.json")
        with open(cookie_file, 'w') as f:
            json.dump(cookies, f, indent=2, ensure_ascii=False)
        
        print(f"\n✓ Cookie已保存到: {cookie_file}")
        print(f"  Corp ID: {corp_id}")
        print(f"  Cookie数量: {len(cookies)}")
        
        # 截图确认
        page.screenshot(path="/tmp/wecom_logged_in.png")
        print(f"  截图已保存: /tmp/wecom_logged_in.png")
        
        # 保持浏览器打开，让用户确认
        print("\n浏览器将在10秒后关闭...")
        time.sleep(10)
        
        browser.close()
        print("完成！")

if __name__ == "__main__":
    main()
