#!/usr/bin/env python3
"""
企业微信新成员自动接入模块

功能：
1. 监听企微后台配置的"通讯录变更事件"回调，捕获新成员加入事件
2. 为新成员创建专属企业应用
3. 配置接收消息API参数
4. 提取配置参数供后续模块使用

企微通讯录变更事件文档：
https://developer.work.weixin.qq.com/document/path/90970
"""
import os
import re
import time
import json
import secrets
import hashlib
import base64
import logging
from typing import Dict, Optional, Tuple, List
from xml.etree import ElementTree as ET
from Crypto.Cipher import AES

from playwright.sync_api import sync_playwright, Page, BrowserContext

logger = logging.getLogger(__name__)

# OpenClaw 小龙虾 logo 路径
OPENCLAW_LOGO_PATH = os.path.join(os.path.dirname(__file__), "openclaw_logo.png")


class WXBizMsgCrypt:
    """
    企业微信消息加解密工具类
    用于验证回调请求签名和解密消息内容
    """
    
    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        # EncodingAESKey 需要 base64 解码
        self.aes_key = base64.b64decode(encoding_aes_key + "=")
    
    def verify_signature(self, signature: str, timestamp: str, nonce: str, echostr: str = "") -> bool:
        """验证消息签名"""
        sort_list = sorted([self.token, timestamp, nonce, echostr])
        sha1 = hashlib.sha1("".join(sort_list).encode()).hexdigest()
        return sha1 == signature
    
    def decrypt_msg(self, encrypted_msg: str) -> str:
        """解密消息"""
        try:
            cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16])
            decrypted = cipher.decrypt(base64.b64decode(encrypted_msg))
            # PKCS7 去除填充
            pad = decrypted[-1]
            decrypted = decrypted[:-pad]
            # 前16字节是随机字符串，接下来4字节是消息长度
            msg_len = int.from_bytes(decrypted[16:20], 'big')
            msg_content = decrypted[20:20+msg_len].decode('utf-8')
            return msg_content
        except Exception as e:
            logger.error(f"消息解密失败: {e}")
            return ""
    
    def encrypt_msg(self, reply_msg: str) -> str:
        """加密回复消息"""
        # 随机字符串 + 消息长度 + 消息 + CorpID
        rand_str = secrets.token_bytes(16)
        msg_bytes = reply_msg.encode('utf-8')
        msg_len = len(msg_bytes).to_bytes(4, 'big')
        corp_bytes = self.corp_id.encode('utf-8')
        
        full_msg = rand_str + msg_len + msg_bytes + corp_bytes
        # PKCS7 填充
        pad_len = 32 - (len(full_msg) % 32)
        full_msg += bytes([pad_len] * pad_len)
        
        cipher = AES.new(self.aes_key, AES.MODE_CBC, self.aes_key[:16])
        encrypted = cipher.encrypt(full_msg)
        return base64.b64encode(encrypted).decode()


def parse_contact_change_event(xml_content: str) -> Optional[Dict]:
    """
    解析通讯录变更事件
    
    事件类型：
    - create_user: 新增成员
    - update_user: 更新成员
    - delete_user: 删除成员
    - create_party: 新增部门
    - update_party: 更新部门
    - delete_party: 删除部门
    - update_tag: 标签变更
    
    Returns:
        {
            "msg_type": "event",
            "event": "change_contact",
            "change_type": "create_user" | "update_user" | ...,
            "user_id": "成员UserID",
            "name": "成员名称",
            "department": [部门ID列表],
            ...
        }
    """
    try:
        root = ET.fromstring(xml_content)
        
        msg_type = root.findtext("MsgType", "")
        event = root.findtext("Event", "")
        change_type = root.findtext("ChangeType", "")
        
        if msg_type != "event" or event != "change_contact":
            return None
        
        result = {
            "msg_type": msg_type,
            "event": event,
            "change_type": change_type,
            "to_user_name": root.findtext("ToUserName", ""),
            "from_user_name": root.findtext("FromUserName", ""),
            "create_time": root.findtext("CreateTime", ""),
        }
        
        # 成员相关事件
        if change_type in ("create_user", "update_user", "delete_user"):
            result["user_id"] = root.findtext("UserID", "")
            result["name"] = root.findtext("Name", "")
            result["department"] = root.findtext("Department", "")
            result["mobile"] = root.findtext("Mobile", "")
            result["position"] = root.findtext("Position", "")
            result["email"] = root.findtext("Email", "")
            result["status"] = root.findtext("Status", "")
            result["avatar"] = root.findtext("Avatar", "")
            result["alias"] = root.findtext("Alias", "")
            result["telephone"] = root.findtext("Telephone", "")
            result["address"] = root.findtext("Address", "")
        
        # 部门相关事件
        elif change_type in ("create_party", "update_party", "delete_party"):
            result["party_id"] = root.findtext("Id", "")
            result["party_name"] = root.findtext("Name", "")
            result["parent_id"] = root.findtext("ParentId", "")
        
        return result
        
    except ET.ParseError as e:
        logger.error(f"XML解析失败: {e}")
        return None


class NewMemberAppCreator:
    """
    新成员专属应用创建器
    使用浏览器自动化在企微后台创建应用
    """
    
    def __init__(self, corp_id: str):
        self.corp_id = corp_id
        self.base_url = "https://work.weixin.qq.com"
        self.user_data_dir = f'./browser_data/{corp_id}'
        self.p = None
        self.browser = None
        self.context = None
        self.page = None
        # 保存创建的应用配置
        self.created_config: Optional[Dict] = None
    
    def _load_cookies(self) -> List[Dict]:
        """加载保存的 Cookie"""
        cookie_file = f'./browser_data/{self.corp_id}/session_cookies.json'
        if os.path.exists(cookie_file):
            with open(cookie_file, 'r') as f:
                cookies = json.load(f)
                logger.info(f"已加载 {len(cookies)} 个 Cookie")
                return cookies
        return []
    
    def _init_browser(self, headless: bool = True):
        """初始化浏览器（优先 Cookie 注入；无 Cookie 时回退持久化目录）"""
        if self.context:
            return

        self.p = sync_playwright().start()

        # 方案1：Cookie 注入（当前环境更稳定）
        cookies = self._load_cookies()
        if cookies:
            self.browser = self.p.chromium.launch(headless=headless)
            self.context = self.browser.new_context()
            self.context.add_cookies(cookies)
            self.page = self.context.new_page()
            logger.info("浏览器已使用 Cookie 注入模式启动")
            return

        # 方案2：回退到持久化目录
        if not os.path.isdir(self.user_data_dir):
            raise Exception(f"未找到 Cookie/会话目录，请先运行: python cookie_manager.py login {self.corp_id}")

        self.context = self.p.chromium.launch_persistent_context(
            self.user_data_dir,
            headless=headless,
            viewport={'width': 1280, 'height': 800},
            args=['--window-size=1280,800'] if not headless else []
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        logger.info("浏览器已回退为持久化会话目录模式")
    
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
            logger.error("会话冲突")
            return False
        return "frame" in url or "wework_admin" in url
    
    def _generate_token_and_aes_key(self) -> Tuple[str, str]:
        """生成 Token 和 EncodingAESKey"""
        # Token: 3-32 个字符
        token = secrets.token_urlsafe(24)[:32]
        # EncodingAESKey: 43 个字符 (Base64 编码后)
        aes_key = secrets.token_urlsafe(32)[:43]
        return token, aes_key
    
    def _upload_logo(self) -> Optional[str]:
        """上传 OpenClaw logo 图片（含“编辑logo”弹窗保存），返回图片路径"""
        if not os.path.exists(OPENCLAW_LOGO_PATH):
            logger.warning(f"Logo 文件不存在: {OPENCLAW_LOGO_PATH}")
            return None

        def _has_bound_file_input() -> bool:
            try:
                return bool(self.page.evaluate('''() => {
                    return Array.from(document.querySelectorAll('input[type="file"]'))
                        .some(i => i.files && i.files.length > 0);
                }'''))
            except Exception:
                return False

        def _save_logo_modal_if_open() -> None:
            """若出现“编辑logo”弹窗，点击弹窗内“保存”"""
            try:
                has_modal = bool(self.page.locator('text=编辑logo').count())
                if not has_modal:
                    return

                save_in_modal = self.page.locator(
                    '.ww_dialog button:has-text("保存"), .ww_dialog a:has-text("保存"), '
                    '.ui_dialog button:has-text("保存"), .ui_dialog a:has-text("保存")'
                )
                if save_in_modal.count() > 0:
                    save_in_modal.first.click(timeout=2000, force=True)
                    time.sleep(1.5)
                    logger.info("Logo 弹窗已点击保存")
            except Exception as e:
                logger.warning(f"Logo 弹窗保存步骤异常: {e}")

        # 方案1：先点小相机/Logo 入口，触发“编辑logo”弹窗
        camera_selectors = [
            'text=应用logo',
            '[class*="logo"]',
            '[class*="camera"]',
            '.js_upload',
        ]
        for selector in camera_selectors:
            try:
                loc = self.page.locator(selector)
                if loc.count() > 0:
                    loc.first.click(timeout=1200, force=True)
                    time.sleep(0.4)
                    if self.page.locator('text=编辑logo').count() > 0:
                        break
            except Exception:
                continue

        # 方案2：在弹窗/页面内触发文件选择并注入图片
        trigger_selectors = [
            '.ww_dialog text=选择图片',
            '.ui_dialog text=选择图片',
            'text=选择图片',
            'text=上传',
            'text=更换',
            'input[type="file"][accept*="image"]',
            'input[type="file"]',
        ]

        # 2.1 优先直接给 file input 注入文件
        for selector in ['input[type="file"][accept*="image"]', 'input[type="file"]']:
            try:
                loc = self.page.locator(selector)
                count = min(loc.count(), 5)
                for i in range(count):
                    try:
                        loc.nth(i).set_input_files(OPENCLAW_LOGO_PATH)
                        time.sleep(0.8)
                        if _has_bound_file_input():
                            _save_logo_modal_if_open()
                            logger.info("Logo 已通过 file input 直接上传")
                            return OPENCLAW_LOGO_PATH
                    except Exception:
                        continue
            except Exception:
                continue

        # 2.2 再尝试 filechooser 路径
        for selector in trigger_selectors:
            try:
                loc = self.page.locator(selector)
                count = min(loc.count(), 5)
            except Exception:
                continue

            for i in range(count):
                try:
                    with self.page.expect_file_chooser(timeout=1500) as fc_info:
                        loc.nth(i).click(timeout=1200, force=True)
                    fc_info.value.set_files(OPENCLAW_LOGO_PATH)
                    time.sleep(0.8)
                    _save_logo_modal_if_open()
                    if _has_bound_file_input() or self.page.locator('text=重新上传').count() > 0:
                        logger.info(f"Logo 已通过触发器上传: {selector}[{i}]")
                        return OPENCLAW_LOGO_PATH
                except Exception:
                    continue

        logger.warning("上传 Logo 失败：未触发可用 filechooser，或弹窗未完成保存")
        return None
    
    def create_app_for_member(
        self,
        member_name: str,
        member_user_id: str,
        callback_url: str = "",
        headless: bool = True,
        keep_page_open: bool = True
    ) -> Dict:
        """
        为新成员创建专属企业应用
        
        Args:
            member_name: 成员名称
            member_user_id: 成员 UserID
            headless: 是否无头模式
            keep_page_open: 是否保持页面打开（用于后续更新 URL）
        
        Returns:
            {
                "success": bool,
                "corp_id": str,
                "agent_id": str,
                "secret": str,
                "token": str,
                "aes_key": str,
                "app_name": str,
                "member_name": str,
                "member_user_id": str,
                "error": str
            }
        """
        app_name = f"{member_name}的openclaw"
        app_desc = f"{member_name}的openclaw"
        callback_url = callback_url or ""
        
        result = {
            "success": False,
            "corp_id": self.corp_id,
            "agent_id": "",
            "secret": "",
            "token": "",
            "aes_key": "",
            "app_name": app_name,
            "member_name": member_name,
            "member_user_id": member_user_id,
            "error": ""
        }
        
        try:
            logger.info(f"开始为成员 {member_name} 创建专属应用...")
            
            self._init_browser(headless=headless)
            
            # 验证登录
            self.page.goto(f"{self.base_url}/wework_admin/frame")
            time.sleep(3)
            if not self._check_login():
                result["error"] = "登录失效，请重新登录"
                return result
            
            logger.info("✓ 登录验证成功")
            
            # ============================================================
            # Step 1: 创建自建应用
            # ============================================================
            logger.info("[Step 1] 创建自建应用...")
            
            self.page.goto(f"{self.base_url}/wework_admin/frame#apps/createSelfApp")
            time.sleep(3)

            # 兼容新版入口：先从“应用分类页”点击“创建应用”进入表单
            try:
                entered_form = self.page.evaluate('''() => {
                    const nodes = Array.from(document.querySelectorAll('.js_add_app, a, button'));
                    const cands = nodes.filter(n => {
                        const t = (n.innerText || n.textContent || '').trim();
                        const visible = !!(n.offsetParent || n.getClientRects().length);
                        return visible && t.includes('创建应用');
                    });
                    if (!cands.length) return false;
                    cands[cands.length - 1].click();
                    return true;
                }''')
                if entered_form:
                    logger.info("  ✓ 已从应用分类页进入创建表单")
                    time.sleep(2)
            except Exception as e:
                logger.warning(f"  进入创建表单失败，继续按旧流程尝试: {e}")
            
            # 上传 Logo（OpenClaw 小龙虾图片）
            logo_uploaded = self._upload_logo()
            if logo_uploaded:
                logger.info("  ✓ Logo 已上传")
            else:
                # 兜底重试不同 file input 选择器
                try:
                    fallback_input = self.page.query_selector('input[type="file"][accept*="image"], input[type="file"]')
                    if fallback_input and os.path.exists(OPENCLAW_LOGO_PATH):
                        fallback_input.set_input_files(OPENCLAW_LOGO_PATH)
                        time.sleep(2)
                        logger.info("  ✓ Logo 已通过兜底选择器上传")
                except Exception as e:
                    logger.warning(f"  Logo 兜底上传失败: {e}")
            
            # 填写应用名称（使用更稳定选择器）
            name_input = self.page.query_selector('input[name="name"], input[placeholder*="应用名称"], input[placeholder*="应用名"], .app_name_input input, input.ww_input')
            if name_input:
                name_input.fill(app_name)
                logger.info(f"  ✓ 应用名称: {app_name}")
            else:
                self.page.evaluate(f'''() => {{
                    const inputs = document.querySelectorAll('input');
                    for (const input of inputs) {{
                        const p = input.placeholder || '';
                        const n = input.name || '';
                        if (n === 'name' || p.includes('应用名称') || p.includes('应用名')) {{
                            input.value = '{app_name}';
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            return true;
                        }}
                    }}
                    return false;
                }}''')

            # 填写应用简介
            desc_textarea = self.page.query_selector('textarea[name="description"], textarea[placeholder*="描述"], textarea')
            if desc_textarea:
                desc_textarea.fill(app_desc)
                logger.info(f"  ✓ 应用简介: {app_desc}")

            # ============================================================
            # Step 2: 设置可见范围
            # ============================================================
            logger.info("[Step 2] 设置可见范围...")
            member_selected = False
            try:
                # 打开可见范围选择
                self.page.click('text=选择成员', timeout=3000)
                time.sleep(1)

                # 优先选择目标成员
                if member_name:
                    search_input = self.page.query_selector('.ww_dialog input[type="text"], .search_input, input[placeholder*="搜索"]')
                    if search_input:
                        search_input.fill(member_name)
                        time.sleep(1)

                    member_selected = self.page.evaluate(f'''() => {{
                        const anchors = document.querySelectorAll('.jstree-anchor');
                        for (const anchor of anchors) {{
                            const text = anchor.innerText || anchor.textContent;
                            if (text && text.includes('{member_name}')) {{
                                anchor.click();
                                return true;
                            }}
                        }}
                        return false;
                    }}''')

                # 回退：选择全部成员，确保可创建
                if not member_selected:
                    self.page.evaluate('''() => {
                        const anchors = Array.from(document.querySelectorAll('.jstree-anchor'));
                        const allNode = anchors.find(a => {
                            const t = (a.innerText || a.textContent || '').trim();
                            return t.includes('全部成员') || t.includes('全体成员');
                        });
                        if (allNode) allNode.click();
                    }''')
                    logger.warning(f"  未锁定成员 {member_name}，已回退选择全部成员")
                else:
                    logger.info(f"  ✓ 已选择成员: {member_name}")

                # 确认选择
                confirm_btn = self.page.query_selector('.js_submit, button:has-text("确定")')
                if confirm_btn:
                    confirm_btn.click()
                    time.sleep(1)
            except Exception as e:
                logger.warning(f"  设置可见范围异常，继续尝试创建: {e}")
            
            # ============================================================
            # Step 3: 提交创建应用
            # ============================================================
            logger.info("[Step 3] 提交创建应用...")
            
            create_btn = self.page.query_selector('button.js_create, a.js_create, button:has-text("创建应用"), a:has-text("创建应用")')
            if create_btn:
                create_btn.click()
            else:
                # 兜底：点击可见的“创建应用”按钮
                self.page.evaluate('''() => {
                    const nodes = Array.from(document.querySelectorAll('button, a, div'));
                    const candidates = nodes.filter(n => {
                        const txt = (n.innerText || n.textContent || '').trim();
                        const visible = !!(n.offsetParent || n.getClientRects().length);
                        return visible && txt === '创建应用';
                    });
                    if (candidates.length) candidates[candidates.length - 1].click();
                }''')

            try:
                self.page.wait_for_url("**modApiApp/**", timeout=12000)
            except Exception:
                time.sleep(4)

            # 若仍停留在创建页，尝试处理阻塞后重试提交
            if 'createApiApp' in self.page.url:
                page_text = ''
                try:
                    page_text = self.page.inner_text('body')
                except Exception:
                    pass

                # 阻塞1：Logo 未上传，先补传再重试
                if '请上传应用logo' in page_text or '请上传应用Logo' in page_text:
                    logger.warning("  检测到 Logo 校验失败，尝试重新上传")
                    if self._upload_logo():
                        retry_btn = self.page.query_selector('button.js_create, a.js_create, button:has-text("创建应用"), a:has-text("创建应用")')
                        if retry_btn:
                            retry_btn.click()
                            try:
                                self.page.wait_for_url("**modApiApp/**", timeout=12000)
                            except Exception:
                                time.sleep(3)

                # 阻塞2：应用名重复，改名后重试
                if '应用名称已存在' in page_text or '名称已存在' in page_text:
                    retry_name = f"{app_name}_{int(time.time()) % 10000}"
                    name_input_retry = self.page.query_selector('input[name="name"], input[placeholder*="应用名称"], input[placeholder*="应用名"], .app_name_input input, input.ww_input')
                    if name_input_retry:
                        name_input_retry.fill(retry_name)
                        app_name = retry_name
                        result['app_name'] = retry_name
                        logger.info(f"  检测到应用名重复，重试名称: {retry_name}")
                    retry_btn = self.page.query_selector('button.js_create, a.js_create, button:has-text("创建应用"), a:has-text("创建应用")')
                    if retry_btn:
                        retry_btn.click()
                        try:
                            self.page.wait_for_url("**modApiApp/**", timeout=12000)
                        except Exception:
                            time.sleep(3)

            # 从 URL 获取 AgentId（兼容多种路由格式）
            current_url = self.page.url
            logger.info(f"  当前URL: {current_url}")
            if 'createApiApp' in current_url:
                try:
                    ts = int(time.time())
                    form_html = f"/tmp/wecom_create_form_{ts}.html"
                    form_png = f"/tmp/wecom_create_form_{ts}.png"
                    with open(form_html, 'w', encoding='utf-8') as f:
                        f.write(self.page.content())
                    self.page.screenshot(path=form_png, full_page=True)
                    logger.info(f"  创建页快照: {form_html}, {form_png}")
                except Exception as e:
                    logger.warning(f"  保存创建页快照失败: {e}")
            patterns = [
                r'modApiApp/(\d+)',
                r'[?&]agentid=(\d+)',
                r'[?&]agent_id=(\d+)'
            ]
            for ptn in patterns:
                m = re.search(ptn, current_url)
                if m:
                    result["agent_id"] = m.group(1)
                    break

            if not result["agent_id"]:
                # 尝试从页面元素提取
                agent_el = self.page.query_selector('[data-agent-id], .agent_id, [name="agentid"]')
                if agent_el:
                    result["agent_id"] = (
                        agent_el.get_attribute('data-agent-id')
                        or agent_el.get_attribute('value')
                        or (agent_el.text_content() or '').strip()
                    )

            if not result["agent_id"]:
                # 最后兜底：从页面HTML里正则抓取
                html = self.page.content()
                m = re.search(r'modApiApp/(\d+)', html)
                if m:
                    result["agent_id"] = m.group(1)

            if not result["agent_id"]:
                # 回退到应用列表页，根据应用名定位并提取 AgentId
                logger.info("  直接提取 AgentId 失败，尝试从应用列表回查...")
                self.page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp")
                time.sleep(3)
                current_url = self.page.url

                # 优先：直接点击刚创建的应用名，观察是否跳到详情页
                try:
                    app_locator = self.page.locator(f"text={app_name}").first
                    if app_locator.count() > 0:
                        app_locator.click()
                        time.sleep(2)
                        jump_url = self.page.url
                        m_jump = re.search(r'modApiApp/(\d+)', jump_url)
                        if m_jump:
                            result["agent_id"] = m_jump.group(1)
                            logger.info(f"  应用列表点击后URL: {jump_url}")
                except Exception as e:
                    logger.warning(f"  应用列表点击回查失败: {e}")

                html = self.page.content()

                # 次优：从页面HTML里找带应用名附近的 modApiApp 链接
                if not result["agent_id"]:
                    escaped_name = re.escape(app_name)
                    around_pattern = rf'(modApiApp/(\d+)[\s\S]{{0,1200}}{escaped_name}|{escaped_name}[\s\S]{{0,1200}}modApiApp/(\d+))'
                    m = re.search(around_pattern, html)
                    if m:
                        result["agent_id"] = m.group(2) or m.group(3) or ""

                # 兜底：全页面首个 modApiApp id
                if not result["agent_id"]:
                    m2 = re.search(r'modApiApp/(\d+)', html)
                    if m2:
                        result["agent_id"] = m2.group(1)

                logger.info(f"  应用列表回查URL: {current_url}")

            if result["agent_id"]:
                logger.info(f"  ✓ AgentId: {result['agent_id']}")
            else:
                page_hint = ""
                debug_html_path = ""
                debug_png_path = ""
                try:
                    page_hint = self.page.evaluate('''() => {
                        const sels = ['.ww_inputWithTips_tips', '.ui_tips, .ww_tips', '.error, .err'];
                        for (const s of sels) {
                            const n = document.querySelector(s);
                            if (n && (n.innerText || n.textContent)) return (n.innerText || n.textContent).trim();
                        }
                        return '';
                    }''') or ""
                except Exception:
                    pass
                try:
                    ts = int(time.time())
                    debug_html_path = f"/tmp/wecom_create_debug_{ts}.html"
                    debug_png_path = f"/tmp/wecom_create_debug_{ts}.png"
                    with open(debug_html_path, 'w', encoding='utf-8') as f:
                        f.write(self.page.content())
                    self.page.screenshot(path=debug_png_path, full_page=True)
                    logger.info(f"  已保存调试快照: {debug_html_path}, {debug_png_path}")
                except Exception as e:
                    logger.warning(f"  保存调试快照失败: {e}")
                extra = []
                if page_hint:
                    extra.append(f"页面提示: {page_hint}")
                if debug_html_path:
                    extra.append(f"html: {debug_html_path}")
                if debug_png_path:
                    extra.append(f"png: {debug_png_path}")
                detail = ('；' + '；'.join(extra)) if extra else ''
                result["error"] = f"未能获取 AgentId（创建后页面未跳转到详情）{detail}"
                return result
            
            # ============================================================
            # Step 4: 获取 Secret
            # ============================================================
            logger.info("[Step 4] 获取 Secret...")
            
            # 确保在应用详情页
            self.page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp/{result['agent_id']}")
            time.sleep(3)
            
            # 点击查看 Secret
            view_btn = self.page.query_selector('a:has-text("查看"), .js_show_secret')
            if view_btn:
                view_btn.click()
                time.sleep(1)
            
            # 获取 Secret 值
            secret_el = self.page.query_selector('.secret_value, .js_secret, input[readonly]')
            if secret_el:
                result["secret"] = secret_el.get_attribute('value') or secret_el.text_content().strip()
                if result["secret"]:
                    logger.info(f"  ✓ Secret: {result['secret'][:10]}...")
            
            # 如果没有直接显示，可能需要发送到邮箱或企业微信
            if not result["secret"]:
                logger.warning("  未能直接获取 Secret，可能需要通过其他方式获取")
            
            # ============================================================
            # Step 5: 配置接收消息 API
            # ============================================================
            logger.info("[Step 5] 配置接收消息 API...")
            
            # 先生成兜底 Token 和 EncodingAESKey（若页面可随机生成则优先页面生成值）
            result["token"], result["aes_key"] = self._generate_token_and_aes_key()
            logger.info(f"  预置 Token: {result['token']}")
            logger.info(f"  预置 EncodingAESKey: {result['aes_key']}")
            
            # 进入接收消息设置页面
            # 点击"接收消息"或"API接收消息"
            api_link = self.page.query_selector('a:has-text("接收消息"), a:has-text("API接收")')
            if api_link:
                api_link.click()
                time.sleep(2)
            else:
                # 直接通过 URL 跳转
                self.page.goto(f"{self.base_url}/wework_admin/frame#apps/modApiApp/{result['agent_id']}/apiReceive")
                time.sleep(2)
            
            # 点击设置按钮
            setup_btn = self.page.query_selector('a:has-text("设置API接收"), a:has-text("设置"), .js_setup_api')
            if setup_btn:
                setup_btn.click()
                time.sleep(2)
            
            # 填写配置
            # URL 暂时为空
            url_input = self.page.query_selector('input[name="url"], input[placeholder*="URL"]')
            if url_input:
                url_input.fill("")  # URL 暂时为空
                logger.info("  ✓ URL: (暂时为空，等待后续更新)")
            
            # Token / EncodingAESKey 输入框
            token_input = self.page.query_selector('input[name="token"], input[placeholder*="Token"]')
            aes_input = self.page.query_selector('input[name="encodingAESKey"], input[placeholder*="EncodingAESKey"]')

            # 点击“随机生成/随机获取”按钮（优先使用后台页面随机值）
            random_btn = self.page.query_selector(
                'a:has-text("随机获取"), button:has-text("随机获取"), a:has-text("随机生成"), button:has-text("随机生成")'
            )
            if random_btn:
                random_btn.click()
                time.sleep(1)
                logger.info("  ✓ 已点击随机生成 Token/EncodingAESKey")

            # 读取页面生成值；若读取失败则回退使用本地生成值
            if token_input:
                page_token = (token_input.get_attribute('value') or "").strip()
                if page_token:
                    result["token"] = page_token
                else:
                    token_input.fill(result["token"])

            if aes_input:
                page_aes = (aes_input.get_attribute('value') or "").strip()
                if page_aes:
                    result["aes_key"] = page_aes
                else:
                    aes_input.fill(result["aes_key"])

            logger.info(f"  ✓ Token: {result['token']}")
            logger.info(f"  ✓ EncodingAESKey: {result['aes_key']}")
            
            logger.info("  ✓ 接收消息配置已填写（等待后续流程触发保存）")
            
            # ============================================================
            # 保存配置（不关闭页面）
            # ============================================================
            result["success"] = True
            self.created_config = result.copy()
            
            logger.info("\n" + "=" * 60)
            logger.info("  新成员应用创建完成")
            logger.info("=" * 60)
            logger.info(f"  成员名称: {member_name}")
            logger.info(f"  应用名称: {app_name}")
            logger.info(f"  Corp ID: {self.corp_id}")
            logger.info(f"  Agent ID: {result['agent_id']}")
            logger.info(f"  Secret: {result['secret'][:10] if result['secret'] else '(待获取)'}...")
            logger.info(f"  Token: {result['token']}")
            logger.info(f"  EncodingAESKey: {result['aes_key']}")
            logger.info("=" * 60)
            logger.info("  页面保持打开，等待功能4执行完成后点击保存")
            logger.info("=" * 60)
            
            if not keep_page_open:
                self._close_browser()
            
            return result
            
        except Exception as e:
            result["error"] = str(e)
            logger.exception(f"创建应用失败: {e}")
            return result
    
    def update_callback_url(self, url: str) -> bool:
        """
        更新接收消息的回调 URL
        需要在 create_app_for_member 后调用，且页面仍然打开
        """
        if not self.page:
            logger.error("页面未打开，请先调用 create_app_for_member")
            return False
        
        try:
            logger.info(f"更新回调 URL: {url}")
            
            # 找到 URL 输入框并填写
            url_input = self.page.query_selector('input[name="url"], input[placeholder*="URL"]')
            if url_input:
                url_input.fill(url)
            else:
                # 使用 JS 设置
                self.page.evaluate(f'''() => {{
                    const inputs = document.querySelectorAll('input');
                    for (const input of inputs) {{
                        if (input.name === 'url' || (input.placeholder && input.placeholder.includes('URL'))) {{
                            input.value = '{url}';
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            return true;
                        }}
                    }}
                    return false;
                }}''')
            
            # 点击保存按钮
            save_btn = self.page.query_selector('a:has-text("保存"), button:has-text("保存"), .js_save')
            if save_btn:
                save_btn.click()
                time.sleep(2)
            
            logger.info("✓ 回调 URL 已更新并保存")
            return True
            
        except Exception as e:
            logger.error(f"更新 URL 失败: {e}")
            return False
    
    def save_api_config(self) -> bool:
        """点击 API 接收页面的保存按钮（功能4执行完成后调用）"""
        if not self.page:
            logger.error("页面未打开，无法保存")
            return False

        try:
            save_btn = self.page.query_selector(
                'a:has-text("保存"), button:has-text("保存"), .js_save, .js_submit'
            )
            if not save_btn:
                logger.error("未找到保存按钮")
                return False

            save_btn.click()
            time.sleep(2)
            logger.info("✓ 已点击保存按钮")
            return True
        except Exception as e:
            logger.error(f"保存 API 配置失败: {e}")
            return False

    def get_created_config(self) -> Optional[Dict]:
        """获取已创建的应用配置"""
        return self.created_config
    
    def close(self):
        """关闭浏览器"""
        self._close_browser()


class ContactChangeCallbackHandler:
    """
    通讯录变更事件回调处理器
    
    用于接收和处理企微后台推送的通讯录变更事件
    """
    
    def __init__(self, corp_id: str, token: str, encoding_aes_key: str):
        self.corp_id = corp_id
        self.crypt = WXBizMsgCrypt(token, encoding_aes_key, corp_id)
        # 保存待处理的新成员队列
        self.pending_members: List[Dict] = []
        # 保存已创建的应用配置
        self.created_apps: Dict[str, Dict] = {}  # user_id -> app_config
    
    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> Optional[str]:
        """
        验证 URL 有效性（首次配置回调 URL 时企微会发送验证请求）
        
        Returns:
            解密后的 echostr，用于回复给企微
        """
        if not self.crypt.verify_signature(msg_signature, timestamp, nonce, echostr):
            logger.error("签名验证失败")
            return None
        
        # 解密 echostr
        decrypted = self.crypt.decrypt_msg(echostr)
        return decrypted
    
    def handle_callback(
        self, 
        msg_signature: str, 
        timestamp: str, 
        nonce: str, 
        request_body: str
    ) -> Optional[Dict]:
        """
        处理回调请求
        
        Returns:
            {
                "event_type": "create_user" | "update_user" | ...,
                "member_info": {...}
            }
        """
        # 解析 XML
        try:
            root = ET.fromstring(request_body)
            encrypted_msg = root.findtext("Encrypt", "")
        except ET.ParseError:
            logger.error("XML 解析失败")
            return None
        
        # 验证签名
        if not self.crypt.verify_signature(msg_signature, timestamp, nonce, encrypted_msg):
            logger.error("签名验证失败")
            return None
        
        # 解密消息
        decrypted = self.crypt.decrypt_msg(encrypted_msg)
        if not decrypted:
            return None
        
        # 解析事件
        event = parse_contact_change_event(decrypted)
        if not event:
            return None
        
        logger.info(f"收到通讯录变更事件: {event.get('change_type')}")
        
        # 处理新成员加入事件
        if event.get("change_type") == "create_user":
            member_info = {
                "user_id": event.get("user_id"),
                "name": event.get("name"),
                "department": event.get("department"),
                "mobile": event.get("mobile"),
                "position": event.get("position"),
                "email": event.get("email"),
            }
            self.pending_members.append(member_info)
            logger.info(f"新成员加入: {member_info['name']} ({member_info['user_id']})")
            
            return {
                "event_type": "create_user",
                "member_info": member_info
            }
        
        return {
            "event_type": event.get("change_type"),
            "raw_event": event
        }
    
    def process_pending_member(self, headless: bool = True) -> Optional[Dict]:
        """
        处理队列中的第一个待处理成员
        
        Returns:
            创建的应用配置
        """
        if not self.pending_members:
            return None
        
        member = self.pending_members.pop(0)
        
        creator = NewMemberAppCreator(self.corp_id)
        try:
            config = creator.create_app_for_member(
                member_name=member["name"],
                member_user_id=member["user_id"],
                headless=headless,
                keep_page_open=True
            )
            
            if config["success"]:
                self.created_apps[member["user_id"]] = config
            
            return config
        finally:
            # 不关闭浏览器，因为需要等待 URL 更新
            pass
    
    def get_pending_count(self) -> int:
        """获取待处理成员数量"""
        return len(self.pending_members)


# 命令行测试入口
if __name__ == "__main__":
    import sys
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )
    
    if len(sys.argv) < 2:
        print("用法:")
        print("  python wecom_member_callback.py create <corp_id> <member_name> [--visible]")
        print("    为指定成员创建专属应用")
        print("")
        print("  python wecom_member_callback.py test-parse <xml_file>")
        print("    测试解析通讯录变更事件 XML")
        print("")
        print("示例:")
        print("  python wecom_member_callback.py create ww95aca10dfcf3d6e2 张三")
        print("  python wecom_member_callback.py create ww95aca10dfcf3d6e2 张三 --visible")
        sys.exit(1)
    
    action = sys.argv[1]
    
    if action == "create":
        corp_id = sys.argv[2] if len(sys.argv) > 2 else "ww95aca10dfcf3d6e2"
        member_name = sys.argv[3] if len(sys.argv) > 3 else "测试成员"
        headless = "--visible" not in sys.argv
        
        creator = NewMemberAppCreator(corp_id)
        result = creator.create_app_for_member(
            member_name=member_name,
            member_user_id=f"test_{member_name}",
            headless=headless,
            keep_page_open=True
        )
        
        print("\n创建结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        
        if result["success"]:
            print("\n页面保持打开，按回车键后关闭...")
            input()
            creator.close()
    
    elif action == "test-parse":
        xml_file = sys.argv[2] if len(sys.argv) > 2 else None
        if xml_file and os.path.exists(xml_file):
            with open(xml_file, 'r') as f:
                xml_content = f.read()
        else:
            # 测试 XML
            xml_content = """
            <xml>
                <ToUserName><![CDATA[toUser]]></ToUserName>
                <FromUserName><![CDATA[sys]]></FromUserName>
                <CreateTime>1403610513</CreateTime>
                <MsgType><![CDATA[event]]></MsgType>
                <Event><![CDATA[change_contact]]></Event>
                <ChangeType>create_user</ChangeType>
                <UserID><![CDATA[zhangsan]]></UserID>
                <Name><![CDATA[张三]]></Name>
                <Department><![CDATA[1,2,3]]></Department>
                <Mobile><![CDATA[13800000000]]></Mobile>
                <Position><![CDATA[工程师]]></Position>
                <Email><![CDATA[zhangsan@example.com]]></Email>
            </xml>
            """
        
        event = parse_contact_change_event(xml_content)
        print("解析结果:")
        print(json.dumps(event, indent=2, ensure_ascii=False))
    
    else:
        print(f"未知操作: {action}")
        sys.exit(1)
