#!/usr/bin/env python3
"""调试可见范围修改流程"""
from playwright.sync_api import sync_playwright
import json
import time

corp_id = 'ww95aca10dfcf3d6e2'
agent_id = '5629502315690924'
member_name = 'louis'

with open(f'./browser_data/{corp_id}/session_cookies.json', 'r') as f:
    cookies = json.load(f)

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False, slow_mo=500)  # 慢速模式便于观察
    ctx = browser.new_context()
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    
    # 进入应用详情页
    page.goto(f'https://work.weixin.qq.com/wework_admin/frame#apps/modApiApp/{agent_id}')
    time.sleep(4)
    
    # 1. 进入编辑模式
    print('1. 查找并点击编辑按钮...')
    edit_btn = page.query_selector('.js_enter_editing')
    if edit_btn:
        edit_btn.click()
        time.sleep(2)
        print('  已进入编辑模式')
    else:
        print('  未找到编辑按钮')
    
    # 2. 删除现有可见范围
    print('2. 删除现有可见范围...')
    page.evaluate('''() => {
        const delBtns = document.querySelectorAll('.js_visible_item_del');
        console.log('找到删除按钮数量:', delBtns.length);
        delBtns.forEach(btn => btn.click());
    }''')
    time.sleep(1)
    
    # 3. 点击添加可见范围
    print('3. 点击添加可见范围...')
    add_btn = page.query_selector('.js_show_visible_mod')
    if add_btn:
        # 检查是否可见
        is_visible = add_btn.is_visible()
        print(f'  按钮可见: {is_visible}')
        
        if is_visible:
            add_btn.click()
        else:
            # 使用 JS 点击
            page.evaluate('document.querySelector(".js_show_visible_mod").click()')
        time.sleep(2)
        
        # 保存弹框页面
        with open('/tmp/visible_dialog.html', 'w') as f:
            f.write(page.content())
        print('  弹框页面已保存')
        
        # 4. 直接在 jstree 中选择成员（不搜索）
        print(f'4. 在组织架构树中选择成员 {member_name}...')
        
        # 使用 JS 直接查找并点击成员
        result = page.evaluate(f'''() => {{
            const anchors = document.querySelectorAll('.jstree-anchor');
            for (const anchor of anchors) {{
                const text = anchor.innerText || anchor.textContent;
                if (text && text.includes('{member_name}')) {{
                    // 直接触发点击事件
                    anchor.click();
                    return {{ found: true, text: text }};
                }}
            }}
            // 返回所有可见的成员
            const members = [];
            anchors.forEach(a => members.push(a.innerText || a.textContent));
            return {{ found: false, members: members }};
        }}''')
        
        if result.get('found'):
            print(f'  已选择成员: {result.get("text")}')
            time.sleep(1)
        else:
            print(f'  未找到成员 {member_name}')
            print(f'  可见成员列表: {result.get("members")}')
        
        # 6. 检查右侧已选择列表
        print('6. 检查已选择的成员...')
        selected = page.query_selector('.js_right_col ul')
        if selected:
            html = selected.inner_html()
            print(f'  已选择内容: {html[:200] if html else "(空)"}')
        
        # 保存选择后页面
        with open('/tmp/after_select.html', 'w') as f:
            f.write(page.content())
        
        # 7. 点击确认按钮
        print('7. 点击确认按钮...')
        confirm_btn = page.query_selector('.js_submit')
        if confirm_btn:
            confirm_btn.click()
            time.sleep(2)
            print('  已点击确认')
        
        # 8. 保存应用修改
        print('8. 保存应用修改...')
        # 等待弹框关闭
        time.sleep(1)
        
        # 页面可能有多个保存按钮，找可见的那个
        save_btns = page.query_selector_all('.js_save_editing')
        print(f'  找到 {len(save_btns)} 个保存按钮')
        
        for btn in save_btns:
            if btn.is_visible():
                print('  点击可见的保存按钮')
                btn.click()
                time.sleep(3)
                print('  已保存')
                break
        else:
            # 如果没有可见的，用 JS 点击
            print('  使用 JS 点击保存按钮')
            page.evaluate('''() => {
                const btns = document.querySelectorAll('.js_save_editing');
                for (const btn of btns) {
                    if (btn.offsetParent !== null) {  // 检查是否可见
                        btn.click();
                        return true;
                    }
                }
                // 如果都不可见，点击第一个
                if (btns.length > 0) btns[0].click();
                return btns.length > 0;
            }''')
            time.sleep(3)
        
        with open('/tmp/final_result.html', 'w') as f:
            f.write(page.content())
    else:
        print('  未找到添加按钮')
        # 检查是否有其他添加按钮
        add_btns = page.query_selector_all('[class*="add"], [class*="Add"]')
        print(f'  找到 {len(add_btns)} 个可能的添加按钮')
    
    input('\n按回车关闭浏览器...')
    browser.close()
