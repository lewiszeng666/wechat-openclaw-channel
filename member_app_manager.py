#!/usr/bin/env python3
"""
成员应用管理器
功能：为新成员分配专属 OpenClaw 应用

流程：
1. 查询所有成员，找到职务为"未设置"的新成员
2. 找到应用名以"openclaw"结尾且可见范围仅为"openclaw聊天窗"的应用
3. 修改应用：可见范围改为该成员，名称改为"成员名+openclaw"
4. 修改成员职务为"已设置openclaw"
"""
import os
import re
import time
import json
from typing import List, Dict, Optional
from playwright.sync_api import sync_playwright, Page, BrowserContext


class MemberAppManager:
    """成员应用管理器"""
    
    def __init__(self, corp_id: str):
        self.corp_id = corp_id
        self.base_url = "https://work.weixin.qq.com"
        self.user_data_dir = f'./browser_data/{corp_id}'
        self.p = None
        self.browser = None
        self.context = None
        self.page = None
    
    def _load_cookies(self) -> List[Dict]:
        """加载保存的 Cookie"""
        cookie_file = f'{self.user_data_dir}/session_cookies.json'
        if os.path.exists(cookie_file):
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
                print(f"已加载 {len(cookies)} 个 Cookie")
                return cookies
        return []
    
    def _init_browser(self, headless: bool = False):
        """初始化浏览器（使用独立 context + Cookie，避免会话冲突）"""
        if self.context:
            return
        
        cookies = self._load_cookies()
        if not cookies:
            raise Exception(f"未找到 Cookie，请先运行: python cookie_manager.py login {self.corp_id}")
        
        self.p = sync_playwright().start()
        self.browser = self.p.chromium.launch(headless=headless)
        self.context = self.browser.new_context()
        self.context.add_cookies(cookies)
        self.page = self.context.new_page()
    
    def _close_browser(self):
        """关闭浏览器"""
        for obj in [self.context, self.browser, self.p]:
            if obj:
                try:
                    obj.close() if hasattr(obj, 'close') else obj.stop()
                except:
                    pass
        self.context = self.browser = self.p = self.page = None
    
    def _check_login(self) -> bool:
        """检查登录状态"""
        if not self.page:
            return False
        url = self.page.url
        content = self.page.content()
        if "loginpage" in url:
            return False
        if "其他页面登录" in content or "其他页面登陆" in content:
            print("ERROR: 会话冲突")
            return False
        return "frame" in url or "wework_admin" in url
    
    def get_new_members(self) -> List[Dict]:
        """
        获取新成员（职务为空/未设置的成员）
        Returns: [{"name": "成员名", "department": "部门", "phone": "手机号"}, ...]
        """
        print("\n[步骤1] 获取成员列表，筛选职务未设置的新成员...")
        self.page.goto(f"{self.base_url}/wework_admin/frame#contacts")
        time.sleep(4)
        
        if not self._check_login():
            print("ERROR: 需要重新登录")
            return []
        
        html = self.page.content()
        new_members = []
        
        # 解析成员表格，每行有：成员名、职务、部门、手机号
        # 表格结构：多个 <td title="xxx"> 依次排列
        # 使用 data-id 识别每一行
        rows = re.findall(
            r'<tr[^>]*data-id="(\d+)"[^>]*data-type="member"[^>]*>(.*?)</tr>',
            html, re.DOTALL
        )
        
        for userid, row_html in rows:
            # 提取所有 title 属性
            titles = re.findall(r'<td[^>]*title="([^"]*)"', row_html)
            if len(titles) >= 4:
                name, position, department, phone = titles[0], titles[1], titles[2], titles[3]
                # 职务为空或未设置的是新成员
                if not position or position in ['未设置', '']:
                    new_members.append({
                        "userid": userid,
                        "name": name,
                        "position": position,
                        "department": department,
                        "phone": phone
                    })
                    print(f"  新成员: {name} (部门: {department})")
        
        print(f"  共找到 {len(new_members)} 个新成员")
        return new_members
    
    def get_available_apps(self) -> List[Dict]:
        """
        获取可用的 OpenClaw 应用
        条件：名称以"openclaw"结尾，可见范围仅为"openclaw聊天窗"
        Returns: [{"agent_id": "ID", "name": "应用名"}, ...]
        """
        print("\n[步骤2] 查找可用的 OpenClaw 应用...")
        self.page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp")
        time.sleep(4)
        
        available_apps = []
        
        # 从页面中查找所有以 openclaw 结尾的应用名
        html = self.page.content()
        
        # 查找所有包含 openclaw 的应用名（去重）
        app_names = set()
        els = self.page.query_selector_all('[class*="name"], [class*="title"], span')
        for el in els:
            try:
                text = el.inner_text().strip()
                # 以 openclaw 结尾，但不是 "我的openclaw" 开头的已分配应用
                if text.endswith('openclaw') and len(text) < 50:
                    app_names.add(text)
            except:
                pass
        
        # 筛选符合条件的应用名（以 openclaw 结尾，待分配的是 "我的openclawX" 格式）
        candidate_names = [n for n in app_names if n.startswith('我的openclaw')]
        print(f"  找到候选应用: {candidate_names}")
        
        # 依次点击每个应用，获取 agent_id 和检查可见范围
        for app_name in candidate_names:
            try:
                # 回到应用列表页
                self.page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp")
                time.sleep(3)
                
                # 点击应用名
                app_el = self.page.locator(f'text={app_name}').first
                app_el.click()
                time.sleep(3)
                
                # 从 URL 获取 agent_id
                url = self.page.url
                match = re.search(r'modApiApp/(\d+)', url)
                if not match:
                    continue
                agent_id = match.group(1)
                
                # 获取可见范围
                visible_range = self._get_app_visible_range_from_page()
                print(f"  应用: {app_name} (ID: {agent_id}), 可见范围: {visible_range}")
                
                # 只选择可见范围仅为 "openclaw聊天窗" 的应用
                if visible_range == "openclaw聊天窗":
                    available_apps.append({
                        "agent_id": agent_id,
                        "name": app_name
                    })
                    print(f"    ✓ 符合条件")
                else:
                    print(f"    ✗ 不符合（可见范围不是 openclaw聊天窗）")
                    
            except Exception as e:
                print(f"  检查应用 {app_name} 失败: {e}")
                continue
        
        print(f"  共 {len(available_apps)} 个可用应用")
        return available_apps
    
    def _get_app_visible_range_from_page(self) -> str:
        """从当前应用详情页获取可见范围"""
        html = self.page.content()
        
        # 查找可见范围文本（在 ww_groupSelBtn_item_text 类中）
        match = re.search(r'ww_groupSelBtn_item_text[^>]*>([^<]+)', html)
        if match:
            return match.group(1).strip()
        
        # 尝试 js_all_party_name
        match = re.search(r'js_all_party_name[^>]*>([^<]+)', html)
        if match:
            return match.group(1).strip()
        
        # 尝试从页面元素获取
        try:
            el = self.page.query_selector('.ww_groupSelBtn_item_text, .js_all_party_name')
            if el:
                return el.inner_text().strip()
        except:
            pass
        
        return ""
    
    
    def modify_app_for_member(self, agent_id: str, member_name: str) -> bool:
        """
        修改应用：可见范围改为指定成员，名称改为"成员名的openclaw"
        """
        new_name = f"{member_name}的openclaw"
        print(f"\n[步骤3] 修改应用 {agent_id}...")
        print(f"  新名称: {new_name}")
        print(f"  新可见范围: {member_name}")
        
        # 进入应用设置页面
        self.page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp/{agent_id}")
        time.sleep(4)
        
        try:
            # === 1. 进入编辑模式 ===
            edit_btn = self.page.query_selector('.js_enter_editing')
            if edit_btn:
                edit_btn.click()
                time.sleep(2)
                print("  已进入编辑模式")
            
            # === 2. 修改应用名称 ===
            # 编辑模式下名称输入框可能不可见，用 JS 设置值
            result = self.page.evaluate(f'''() => {{
                const inputs = document.querySelectorAll('input[name="name"]');
                for (const input of inputs) {{
                    if (input.value && input.value.includes('openclaw')) {{
                        input.value = '{new_name}';
                        input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        return true;
                    }}
                }}
                return false;
            }}''')
            if result:
                print(f"  ✓ 名称已填写: {new_name}")
            else:
                print(f"  ✗ 未找到名称输入框")
            
            # === 3. 修改可见范围 ===
            # 先删除现有的可见范围（使用 JS 点击，因为按钮可能在 hover 时才显示）
            self.page.evaluate('''() => {
                const delBtns = document.querySelectorAll('.js_visible_item_del');
                delBtns.forEach(btn => btn.click());
            }''')
            time.sleep(1)
            print("  已删除现有可见范围")
            
            # 点击添加可见范围按钮（使用 JS 点击，因为按钮可能不可见）
            self.page.evaluate('document.querySelector(".js_show_visible_mod").click()')
            time.sleep(2)
            print("  已打开成员选择对话框")
            
            # 在 jstree 组织架构中选择成员
            result = self.page.evaluate(f'''() => {{
                const anchors = document.querySelectorAll('.jstree-anchor');
                for (const anchor of anchors) {{
                    const text = anchor.innerText || anchor.textContent;
                    if (text && text.includes('{member_name}')) {{
                        anchor.click();
                        return {{ found: true, text: text }};
                    }}
                }}
                const members = [];
                anchors.forEach(a => members.push(a.innerText || a.textContent));
                return {{ found: false, members: members }};
            }}''')
            
            if result.get('found'):
                print(f"  ✓ 已选择成员: {result.get('text')}")
                time.sleep(1)
            else:
                print(f"  ✗ 未找到成员 {member_name}，可选: {result.get('members')}")
                return False
            
            # 点击确认按钮
            confirm_btn = self.page.query_selector('.js_submit')
            if confirm_btn:
                confirm_btn.click()
                time.sleep(2)
                print("  ✓ 已确认选择")
            
            # === 4. 保存应用修改 ===
            time.sleep(1)  # 等待弹框关闭
            
            # 找可见的保存按钮
            save_btns = self.page.query_selector_all('.js_save_editing')
            saved = False
            for btn in save_btns:
                if btn.is_visible():
                    btn.click()
                    saved = True
                    break
            
            if not saved:
                # 使用 JS 点击
                self.page.evaluate('''() => {
                    const btns = document.querySelectorAll('.js_save_editing');
                    for (const btn of btns) {
                        if (btn.offsetParent !== null) {
                            btn.click();
                            return;
                        }
                    }
                    if (btns.length > 0) btns[0].click();
                }''')
            
            time.sleep(3)
            print(f"  ✓ 应用修改已保存")
            
            return True
            
        except Exception as e:
            print(f"  ✗ 修改失败: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def update_member_position(self, member_name: str, new_position: str = "已设置openclaw") -> bool:
        """
        修改成员职务
        """
        print(f"\n[步骤4] 修改成员 {member_name} 的职务为 '{new_position}'...")
        
        # 进入通讯录
        self.page.goto(f"{self.base_url}/wework_admin/frame#contacts")
        time.sleep(4)
        
        try:
            # 点击成员进入详情
            member_cell = self.page.query_selector(f'td[title="{member_name}"]')
            if member_cell:
                member_cell.click()
                time.sleep(3)
            else:
                print(f"  未找到成员 {member_name}")
                return False
            
            # 点击编辑按钮
            edit_btn = self.page.query_selector('.js_edit')
            if edit_btn:
                edit_btn.click()
                time.sleep(2)
            
            # 找到职务输入框并修改
            position_input = self.page.query_selector('input[name="position"]')
            if position_input:
                position_input.fill('')
                position_input.fill(new_position)
                time.sleep(0.5)
            
            # 保存
            save_btn = self.page.query_selector('.js_save')
            if save_btn:
                save_btn.click()
                time.sleep(2)
            
            print(f"  ✓ 职务已修改为: {new_position}")
            return True
            
        except Exception as e:
            print(f"  ✗ 修改职务失败: {e}")
            return False
    
    def process_new_members(self, headless: bool = False) -> Dict:
        """
        主流程：处理所有新成员
        
        Returns:
            {
                "success": bool,
                "new_members": [str],
                "available_apps": [str],
                "processed": [{"member": str, "app": str, "success": bool}],
                "errors": [str]
            }
        """
        result = {
            "success": False,
            "new_members": [],
            "available_apps": [],
            "processed": [],
            "errors": []
        }
        
        try:
            print("=" * 60)
            print("  企微成员应用分配流程")
            print("=" * 60)
            
            self._init_browser(headless=headless)
            
            # 验证登录
            self.page.goto(f"{self.base_url}/wework_admin/frame")
            time.sleep(3)
            if not self._check_login():
                result["errors"].append("登录失效，请重新登录")
                return result
            
            # 步骤1：获取新成员
            new_members = self.get_new_members()
            result["new_members"] = [m["name"] for m in new_members]
            
            if not new_members:
                print("\n✓ 没有需要处理的新成员")
                result["success"] = True
                return result
            
            # 步骤2：获取可用应用
            available_apps = self.get_available_apps()
            result["available_apps"] = [a["name"] for a in available_apps]
            
            if not available_apps:
                result["errors"].append("没有可用的 OpenClaw 应用")
                return result
            
            # 步骤3&4：为每个新成员分配应用
            for member in new_members:
                if not available_apps:
                    result["errors"].append(f"应用不足，无法为 {member['name']} 分配")
                    break
                
                app = available_apps.pop(0)  # 取出一个应用
                member_name = member["name"]
                
                print(f"\n{'='*40}")
                print(f"处理成员: {member_name}")
                print(f"分配应用: {app['name']} -> {member_name}的openclaw")
                
                # 修改应用
                app_success = self.modify_app_for_member(app["agent_id"], member_name)
                
                # 修改成员职务
                pos_success = self.update_member_position(member_name)
                
                result["processed"].append({
                    "member": member_name,
                    "app": f"{member_name}的openclaw",
                    "agent_id": app["agent_id"],
                    "app_modified": app_success,
                    "position_modified": pos_success,
                    "success": app_success and pos_success
                })
                
                if not (app_success and pos_success):
                    result["errors"].append(f"{member_name}: 部分操作失败")
            
            result["success"] = len(result["errors"]) == 0
            
            print("\n" + "=" * 60)
            print("  处理完成")
            print("=" * 60)
            print(f"  新成员数: {len(result['new_members'])}")
            print(f"  可用应用数: {len(result['available_apps']) + len(result['processed'])}")
            print(f"  成功处理: {sum(1 for p in result['processed'] if p['success'])}")
            print(f"  失败: {sum(1 for p in result['processed'] if not p['success'])}")
            
            return result
            
        except Exception as e:
            result["errors"].append(str(e))
            print(f"✗ 处理异常: {e}")
            import traceback
            traceback.print_exc()
            return result
        finally:
            self._close_browser()
    
    def list_status(self, headless: bool = False):
        """列出当前状态（成员和应用）"""
        try:
            self._init_browser(headless=headless)
            
            self.page.goto(f"{self.base_url}/wework_admin/frame")
            time.sleep(3)
            
            if not self._check_login():
                print("ERROR: 需要重新登录")
                return
            
            # 获取新成员
            new_members = self.get_new_members()
            
            # 获取可用应用
            available_apps = self.get_available_apps()
            
            print("\n" + "=" * 60)
            print("  状态总结")
            print("=" * 60)
            print(f"\n待处理的新成员 ({len(new_members)}):")
            for m in new_members:
                print(f"  - {m['name']} (部门: {m['department']})")
            
            print(f"\n可用的 OpenClaw 应用 ({len(available_apps)}):")
            for a in available_apps:
                print(f"  - {a['name']} (ID: {a['agent_id']})")
            
            if new_members and available_apps:
                print(f"\n✓ 可以为 {min(len(new_members), len(available_apps))} 个成员分配应用")
            elif new_members and not available_apps:
                print(f"\n✗ 没有可用应用，无法为 {len(new_members)} 个新成员分配")
            else:
                print("\n✓ 没有需要处理的新成员")
                
        finally:
            self._close_browser()


def main():
    """命令行入口"""
    import sys
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python member_app_manager.py list <corp_id>      # 查看当前状态")
        print("  python member_app_manager.py process <corp_id>   # 处理所有新成员")
        print("")
        print("示例:")
        print("  python member_app_manager.py list ww95aca10dfcf3d6e2")
        print("  python member_app_manager.py process ww95aca10dfcf3d6e2")
        sys.exit(1)
    
    action = sys.argv[1]
    corp_id = sys.argv[2] if len(sys.argv) > 2 else "ww95aca10dfcf3d6e2"
    
    manager = MemberAppManager(corp_id)
    
    if action == "list":
        manager.list_status(headless=False)
        
    elif action == "process":
        result = manager.process_new_members(headless=False)
        print("\n结果JSON:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
    else:
        print(f"未知操作: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
