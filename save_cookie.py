#!/usr/bin/env python3
"""保存企微Cookie - 简化版"""
import json, time, os
from playwright.sync_api import sync_playwright

COOKIE_DIR = '/Users/lewiszeng/MyProject/lewiscode/wechat-openclaw-channel/cookies'
os.makedirs(COOKIE_DIR, exist_ok=True)

print('启动浏览器...')
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    page = context.new_page()
    
    page.goto('https://work.weixin.qq.com/wework_admin/loginpage_wx')
    print('请扫码登录企微后台...')
    
    # 等待登录成功（最多90秒）
    logged_in = False
    for i in range(45):
        time.sleep(2)
        url = page.url
        if 'frame' in url:
            logged_in = True
            print(f'\n✓ 登录成功!')
            break
        if i % 5 == 0:
            print(f'等待中... ({i*2}秒)')
    
    if not logged_in:
        print('等待超时，尝试保存当前状态...')
    
    time.sleep(3)  # 等待页面完全加载
    
    # 获取并保存cookies
    cookies = context.cookies()
    
    # 提取corp_id
    corp_id = 'unknown'
    for c in cookies:
        if c['name'] == 'wwrtx.corpid':
            corp_id = c['value']
            break
    
    cookie_file = os.path.join(COOKIE_DIR, f'wecom_{corp_id}.json')
    with open(cookie_file, 'w') as f:
        json.dump(cookies, f, indent=2)
    
    print(f'\n✓ Cookie已保存!')
    print(f'  文件: {cookie_file}')
    print(f'  Corp ID: {corp_id}')
    print(f'  Cookie数量: {len(cookies)}')
    
    # 截图
    page.screenshot(path='/tmp/wecom_login_done.png')
    
    browser.close()
    print('\n完成!')
