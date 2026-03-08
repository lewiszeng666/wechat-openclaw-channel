#!/usr/bin/env python3
"""调试应用名称修改"""
from playwright.sync_api import sync_playwright
import json
import time

corp_id = 'ww95aca10dfcf3d6e2'
agent_id = '5629502315690924'
new_name = 'louis的openclaw'

with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    
    # 进入应用详情页
    page.goto(f'https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/{agent_id}')
    time.sleep(4)
    
    # 1. 进入编辑模式
    print('1. 进入编辑模式...')
    edit_btn = page.query_selector('.js_enter_editing')
    if edit_btn:
        edit_btn.click()
        time.sleep(2)
        print('  ✓ 已进入编辑模式')
    
    # 2. 查找名称输入框
    print('2. 查找名称输入框...')
    name_inputs = page.query_selector_all('input[name="name"]')
    print(f'  找到 {len(name_inputs)} 个 name 输入框')
    
    for i, inp in enumerate(name_inputs):
        is_visible = inp.is_visible()
        value = inp.get_attribute('value')
        print(f'  输入框 {i}: visible={is_visible}, value={value}')
        
        if is_visible:
            print(f'  正在修改名称为: {new_name}')
            inp.fill('')
            inp.fill(new_name)
            time.sleep(1)
            print('  ✓ 名称已修改')
            break
    
    # 保存页面
    with open('/tmp/name_debug.html', 'w') as f:
        f.write(page.content())
    
    input('\n按回车关闭浏览器...')
    browser.close()
