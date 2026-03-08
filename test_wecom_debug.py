#!/usr/bin/env python3
"""监控企微操作 - 持续监控直到手动停止"""

import json
import time
import signal
import sys
from playwright.sync_api import sync_playwright

LOG_FILE = "/tmp/wecom_operations.json"

def main():
    operations = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        # 监控所有请求
        def on_request(request):
            if request.method == "POST":
                url = request.url
                if any(k in url.lower() for k in ['upload', 'create', 'app', 'cgi-bin', 'image', 'logo']):
                    op = {
                        'type': 'request',
                        'method': 'POST',
                        'url': url,
                        'time': time.strftime('%H:%M:%S')
                    }
                    try:
                        op['post_data'] = request.post_data[:1000] if request.post_data else None
                    except:
                        pass
                    operations.append(op)
                    print(f"[POST] {url}")
        
        def on_response(response):
            url = response.url
            if response.request.method == "POST" and any(k in url.lower() for k in ['upload', 'create', 'app', 'cgi-bin', 'image', 'logo']):
                try:
                    body = response.text()[:500]
                    operations.append({
                        'type': 'response',
                        'url': url,
                        'status': response.status,
                        'body': body,
                        'time': time.strftime('%H:%M:%S')
                    })
                    print(f"[RESP {response.status}] {url[:80]}")
                except:
                    pass
        
        page.on("request", on_request)
        page.on("response", on_response)
        
        print("正在打开企微后台登录页...")
        page.goto("https://work.weixin.qq.com/wework_admin/loginpage_wx")
        
        print("\n" + "="*60)
        print("请完成以下操作：")
        print("1. 扫码登录")
        print("2. 进入 应用管理 -> 创建应用")
        print("3. 上传logo、填名称、选可见范围、创建")
        print("="*60)
        print("\n监控中... 操作完成后点击聊天中的按钮")
        
        # 持续监控，每2秒保存一次日志
        try:
            while True:
                time.sleep(2)
                with open(LOG_FILE, 'w') as f:
                    json.dump(operations, f, indent=2, ensure_ascii=False)
                
                # 检查是否创建成功
                if "modApiApp" in page.url:
                    print(f"\n✓ 检测到创建成功！URL: {page.url}")
                    page.screenshot(path="/tmp/wecom_created.png")
                    # 继续监控一会儿抓取更多信息
                    time.sleep(3)
                    break
        except KeyboardInterrupt:
            pass
        
        # 最终保存
        page.screenshot(path="/tmp/wecom_final.png")
        with open(LOG_FILE, 'w') as f:
            json.dump(operations, f, indent=2, ensure_ascii=False)
        
        print(f"\n日志已保存: {LOG_FILE}")
        print(f"截图已保存: /tmp/wecom_final.png")
        print(f"共记录 {len(operations)} 条操作")
        
        browser.close()

if __name__ == "__main__":
    main()
