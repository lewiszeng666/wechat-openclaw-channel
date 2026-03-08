#!/usr/bin/env python3
"""分析企微后台API - 通过浏览器抓包"""

import json
import time
from playwright.sync_api import sync_playwright

LOG_FILE = "/tmp/wecom_api_log.json"

def main():
    all_requests = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        
        def on_request(request):
            url = request.url
            method = request.method
            # 记录所有 cgi-bin 请求
            if 'cgi-bin' in url or 'wework_admin' in url:
                entry = {
                    'time': time.strftime('%H:%M:%S'),
                    'method': method,
                    'url': url,
                }
                if method == 'POST':
                    try:
                        entry['post_data'] = request.post_data
                    except:
                        pass
                all_requests.append(entry)
                print(f"[{method}] {url[:100]}")
        
        def on_response(response):
            url = response.url
            if ('cgi-bin' in url or 'upload' in url.lower()) and response.request.method == 'POST':
                try:
                    body = response.text()
                    # 找到对应的请求并添加响应
                    for req in reversed(all_requests):
                        if req['url'] == url and 'response' not in req:
                            req['response'] = body[:2000]
                            req['status'] = response.status
                            break
                except:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)
        
        print("正在打开企微后台...")
        page.goto("https://work.weixin.qq.com/wework_admin/loginpage_wx")
        
        print("\n" + "="*60)
        print("请完成以下操作：")
        print("1. 扫码登录")
        print("2. 进入 应用管理 -> 创建应用")
        print("3. 上传logo、填名称、选可见范围、创建")
        print("="*60)
        print("\n监控API请求中...")
        print("操作完成后点击聊天中的按钮")
        
        # 持续监控
        try:
            while True:
                time.sleep(2)
                # 每次都保存
                with open(LOG_FILE, 'w') as f:
                    json.dump(all_requests, f, indent=2, ensure_ascii=False)
        except KeyboardInterrupt:
            pass
        
        # 最终保存
        with open(LOG_FILE, 'w') as f:
            json.dump(all_requests, f, indent=2, ensure_ascii=False)
        
        print(f"\n共记录 {len(all_requests)} 条API请求")
        print(f"日志已保存: {LOG_FILE}")
        
        browser.close()

if __name__ == "__main__":
    main()
