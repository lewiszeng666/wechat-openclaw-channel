#!/usr/bin/env python3
"""调试应用修改流程"""
from playwright.sync_api import sync_playwright
import json
import time

corp_id = 'ww95aca10dfcf3d6e2'
agent_id = '5629502315690924'
new_name = 'louisopenclaw'
member_name = 'louis'

with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    
    # 进入应用详情页
    page.goto(f'https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/{agent_id}')
    time.sleep(4)
    
    print('=== 修改应用名称 ===')
    # 1. 点击编辑应用名称按钮（铅笔图标）
    edit_name_btn = page.query_selector('.js_edit_app_name')
    if edit_name_btn:
        print('找到编辑名称按钮，点击...')
        edit_name_btn.click()
        time.sleep(2)
        
        # 查找输入框
        name_input = page.query_selector('input.js_app_name, input[name="name"]')
        if name_input:
            print(f'找到输入框，填写 {new_name}')
            name_input.fill('')
            name_input.fill(new_name)
            time.sleep(1)
        else:
            # 可能是个 contenteditable 的元素
            editable = page.query_selector('[contenteditable="true"]')
            if editable:
                print('找到 contenteditable 元素')
                editable.fill('')
                editable.fill(new_name)
    else:
        print('未找到编辑名称按钮')
    
    print('\n=== 修改可见范围 ===')
    # 2. 点击进入编辑模式（可见范围区域）
    edit_btn = page.query_selector('.js_enter_editing')
    if edit_btn:
        print('找到进入编辑按钮，点击...')
        edit_btn.click()
        time.sleep(2)
        
        # 保存页面用于分析
        with open('/tmp/edit_mode.html', 'w') as f:
            f.write(page.content())
        print('编辑模式页面已保存到 /tmp/edit_mode.html')
    else:
        print('未找到进入编辑按钮')
    
    input('\n按回车关闭浏览器...')
    browser.close()
