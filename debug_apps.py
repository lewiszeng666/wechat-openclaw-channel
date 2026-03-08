#!/usr/bin/env python3
from playwright.sync_api import sync_playwright
import json
import time
import re

corp_id = 'ww95aca10dfcf3d6e2'
with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    
    # 直接进入自建应用页面
    page.goto('https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp')
    time.sleep(5)
    
    # 保存完整 HTML
    html = page.content()
    with open('/tmp/apps_full.html', 'w') as f:
        f.write(html)
    print('HTML 已保存到 /tmp/apps_full.html')
    
    # 查找所有包含 openclaw 的文本
    print('\n包含 openclaw 的元素:')
    els = page.query_selector_all('[class*="name"], [class*="title"], span, div')
    for el in els:
        try:
            text = el.inner_text().strip()
            if 'openclaw' in text.lower() and len(text) < 50:
                print(f'  - {text}')
        except:
            pass
    
    # 查找应用卡片
    print('\n应用卡片:')
    cards = page.query_selector_all('.app_index_item, [class*="appCard"], [class*="app_item"]')
    for card in cards[:20]:
        try:
            text = card.inner_text().replace('\n', ' ')[:60]
            # 检查是否有链接
            link = card.query_selector('a')
            href = link.get_attribute('href') if link else ''
            print(f'  - {text} | {href}')
        except:
            pass
    
    # 点击第一个 "我的openclaw" 应用看看会跳转到哪里
    print('\n尝试点击应用...')
    try:
        # 找到包含 "我的openclaw" 的可点击元素
        app_el = page.locator('text=我的openclaw').first
        app_el.click()
        time.sleep(3)
        print(f'点击后 URL: {page.url}')
        
        # 从 URL 提取 agent_id
        import re
        match = re.search(r'modApiApp/(\d+)', page.url)
        if match:
            print(f'Agent ID: {match.group(1)}')
    except Exception as e:
        print(f'点击失败: {e}')
    
    browser.close()
