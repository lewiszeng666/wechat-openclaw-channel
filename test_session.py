#!/usr/bin/env python3
"""测试 Cookie 会话是否有效"""
from playwright.sync_api import sync_playwright
import json
import time
import re

corp_id = 'ww95aca10dfcf3d6e2'
user_data_dir = f'./browser_data/{corp_id}'

# 加载 Cookie
with open(f'{user_data_dir}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

print(f"已加载 {len(cookies)} 个 Cookie")

with sync_playwright() as p:
    # 用新的 context（不用持久化目录）避免 Singleton 锁冲突
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    
    # 添加 Cookie
    context.add_cookies(cookies)
    
    page = context.new_page()
    page.goto('https://work.weixin.qq.com/wework_admin/frame#contacts')
    
    time.sleep(5)
    print('当前URL:', page.url)
    
    content = page.content()
    if 'loginpage' in page.url:
        print('❌ 需要登录')
    elif '其他页面登录' in content or '其他页面登陆' in content:
        print('❌ 会话冲突：您已在其他页面登录')
    else:
        print('✓ 登录成功!')
        # 等待页面完全加载
        time.sleep(3)
        content = page.content()
        
        # 保存页面用于分析
        with open('/tmp/contacts_debug.html', 'w') as f:
            f.write(content)
        print('页面已保存到 /tmp/contacts_debug.html')
        
        # 尝试多种正则
        members = re.findall(r'"member_name":"([^"]+)"', content)
        if not members:
            members = re.findall(r'title="([^"]+)"[^>]*class="[^"]*member', content)
        if not members:
            members = re.findall(r'<td[^>]*title="([^"]+)"[^>]*><span>', content)
        print(f'成员: {members}')
    
    input('按回车关闭浏览器...')
    browser.close()
