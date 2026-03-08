#!/usr/bin/env python3
"""打开带Cookie的浏览器"""
from playwright.sync_api import sync_playwright
import json
import time

corp_id = 'ww95aca10dfcf3d6e2'
with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

p = sync_playwright().start()
browser = p.chromium.launch(headless=False)
ctx = browser.new_context()
ctx.add_cookies(cookies)
page = ctx.new_page()
page.goto('https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/5629502315690924')
print('浏览器已打开，你有5分钟时间操作，改完后按 Ctrl+C 关闭')
try:
    time.sleep(300)
except KeyboardInterrupt:
    pass
browser.close()
p.stop()
