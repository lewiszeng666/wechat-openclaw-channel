#!/usr/bin/env python3
"""调试成员编辑流程"""
from playwright.sync_api import sync_playwright
import json
import time

corp_id = 'ww95aca10dfcf3d6e2'
member_name = 'louis'

with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    
    # 进入通讯录
    page.goto('https://work.weixin.qq.com/wework_admin/frame#contacts')
    time.sleep(4)
    
    # 点击成员名进入详情
    member_cell = page.query_selector(f'td[title="{member_name}"]')
    if member_cell:
        print(f'找到成员 {member_name}，点击进入详情...')
        member_cell.click()
        time.sleep(3)
        
        # 保存详情页
        with open('/tmp/member_detail.html', 'w') as f:
            f.write(page.content())
        print('详情页已保存到 /tmp/member_detail.html')
        
        # 查找编辑按钮
        edit_btn = page.query_selector('.js_edit, .ww_btn:has-text("编辑")')
        if edit_btn:
            print('找到编辑按钮，点击...')
            edit_btn.click()
            time.sleep(2)
            
            # 保存编辑页
            with open('/tmp/member_edit.html', 'w') as f:
                f.write(page.content())
            print('编辑页已保存到 /tmp/member_edit.html')
        else:
            print('未找到编辑按钮')
    else:
        print(f'未找到成员 {member_name}')
    
    browser.close()
