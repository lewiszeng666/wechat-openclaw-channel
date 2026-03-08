#!/usr/bin/env python3
"""调试成员职务修改"""
from playwright.sync_api import sync_playwright
import json
import time

corp_id = 'ww95aca10dfcf3d6e2'
member_name = 'louis'
new_position = '已分配openclaw'

with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    
    # 进入通讯录
    print('1. 进入通讯录...')
    page.goto('https://work.weixin.qq.com/wework_admin/frame#contacts')
    time.sleep(5)
    
    # 等待成员列表加载
    print('  等待成员列表加载...')
    try:
        page.wait_for_selector('tr[data-type="member"]', timeout=10000)
        print('  成员列表已加载')
    except:
        print('  成员列表未找到，尝试点击部门展开...')
        # 可能需要点击部门来显示成员
        dept = page.query_selector('.jstree-anchor')
        if dept:
            dept.click()
            time.sleep(3)
    
    # 保存页面
    with open('/tmp/contacts_page.html', 'w') as f:
        f.write(page.content())
    
    # 2. 查找成员 - 使用多种方式
    print(f'2. 查找成员 {member_name}...')
    
    # 方式1: td[title]
    member_cells = page.query_selector_all(f'td[title="{member_name}"]')
    print(f'  方式1 td[title]: 找到 {len(member_cells)} 个')
    
    # 方式2: 使用 locator
    member_rows = page.locator(f'tr:has-text("{member_name}")')
    print(f'  方式2 tr:has-text: 找到 {member_rows.count()} 个')
    
    # 方式3: 使用 JS 查找
    members = page.evaluate(f'''() => {{
        const rows = document.querySelectorAll('tr[data-type="member"]');
        const result = [];
        rows.forEach(row => {{
            const text = row.innerText;
            if (text.includes('{member_name}')) {{
                result.push(text.substring(0, 50));
            }}
        }});
        return result;
    }}''')
    print(f'  方式3 JS查找: 找到 {len(members)} 个')
    
    if member_cells:
        print('  点击成员...')
        member_cells[0].click()
        time.sleep(3)
        
        # 保存详情页
        with open('/tmp/member_detail.html', 'w') as f:
            f.write(page.content())
        
        # 3. 查找编辑按钮
        print('3. 查找编辑按钮...')
        edit_btns = page.query_selector_all('.js_edit, [class*="edit"], button:has-text("编辑")')
        print(f'  找到 {len(edit_btns)} 个可能的编辑按钮')
        
        for i, btn in enumerate(edit_btns):
            try:
                text = btn.inner_text()
                cls = btn.get_attribute('class')
                visible = btn.is_visible()
                print(f'  按钮 {i}: text="{text}", class="{cls}", visible={visible}')
            except:
                pass
        
        # 尝试点击编辑
        edit_btn = page.query_selector('.js_edit')
        if edit_btn and edit_btn.is_visible():
            print('  点击编辑按钮...')
            edit_btn.click()
            time.sleep(2)
        else:
            # 尝试其他方式
            print('  尝试用 locator 查找编辑按钮...')
            try:
                page.locator('text=编辑').first.click()
                time.sleep(2)
            except:
                print('  未能点击编辑按钮')
        
        # 保存编辑页面
        with open('/tmp/member_edit.html', 'w') as f:
            f.write(page.content())
        
        # 4. 查找职务输入框
        print('4. 查找职务输入框...')
        position_inputs = page.query_selector_all('input[name="position"], input[placeholder*="职务"]')
        print(f'  找到 {len(position_inputs)} 个职务输入框')
        
        for i, inp in enumerate(position_inputs):
            value = inp.get_attribute('value')
            placeholder = inp.get_attribute('placeholder')
            visible = inp.is_visible()
            print(f'  输入框 {i}: value="{value}", placeholder="{placeholder}", visible={visible}')
            
            if visible:
                print(f'  修改职务为: {new_position}')
                inp.fill('')
                inp.fill(new_position)
                time.sleep(1)
        
        # 5. 查找保存按钮
        print('5. 查找保存按钮...')
        save_btns = page.query_selector_all('.js_save, button:has-text("保存"), a:has-text("保存")')
        print(f'  找到 {len(save_btns)} 个可能的保存按钮')
        
        for i, btn in enumerate(save_btns):
            try:
                text = btn.inner_text()
                visible = btn.is_visible()
                print(f'  按钮 {i}: text="{text}", visible={visible}')
            except:
                pass
        
        save_btn = page.query_selector('.js_save')
        if save_btn and save_btn.is_visible():
            print('  点击保存...')
            save_btn.click()
            time.sleep(2)
            print('  已保存')
    else:
        print(f'  未找到成员 {member_name}')
    
    input('\n按回车关闭浏览器...')
    browser.close()
