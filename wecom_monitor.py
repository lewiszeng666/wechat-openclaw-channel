#!/usr/bin/env python3
"""企微API监控 - 使用保存的Cookie"""
import json
import time
from playwright.sync_api import sync_playwright

COOKIE_FILE = "/Users/lewiszeng/MyProject/lewiscode/wechat-openclaw-channel/cookies/wecom_ww95aca10dfcf3d6e2.json"
LOG_FILE = "/tmp/wecom_api.json"

def main():
    # 加载Cookie
    with open(COOKIE_FILE, 'r') as f:
        data = json.load(f)
    cookies = data.get('cookies', data)  # 兼容两种格式
    
    api_logs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()
        
        def on_request(request):
            if request.method == "POST" and 'cgi-bin' in request.url:
                entry = {
                    'time': time.strftime('%H:%M:%S'),
                    'method': 'POST',
                    'url': request.url,
                }
                try:
                    entry['body'] = request.post_data
                except:
                    pass
                api_logs.append(entry)
                print(f"[POST] {request.url}")
        
        def on_response(response):
            if response.request.method == "POST" and 'cgi-bin' in response.url:
                try:
                    text = response.text()
                    for log in reversed(api_logs):
                        if log['url'] == response.url and 'response' not in log:
                            log['response'] = text[:2000]
                            log['status'] = response.status
                            break
                except:
                    pass
        
        page.on("request", on_request)
        page.on("response", on_response)
        
        print("打开创建应用页面...")
        page.goto("https://work.weixin.qq.com/wework_admin/frame#/apps/createApiApp")
        page.wait_for_load_state("networkidle")
        
        print("\n" + "="*50)
        print("请在浏览器中完成创建应用操作")
        print("操作完成后在聊天中点击确认按钮")
        print("="*50 + "\n")
        
        # 持续监控
        try:
            while True:
                time.sleep(2)
                with open(LOG_FILE, 'w') as f:
                    json.dump(api_logs, f, indent=2, ensure_ascii=False)
        except KeyboardInterrupt:
            pass
        
        with open(LOG_FILE, 'w') as f:
            json.dump(api_logs, f, indent=2, ensure_ascii=False)
        
        print(f"\n记录了 {len(api_logs)} 条API请求")
        print(f"日志: {LOG_FILE}")
        
        browser.close()

if __name__ == "__main__":
    main()
