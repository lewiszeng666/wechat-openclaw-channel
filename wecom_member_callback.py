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
        self.p = None
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
        """初始化浏览器"""
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
        """上传 OpenClaw logo 图片，返回图片 URL"""
        if not os.path.exists(OPENCLAW_LOGO_PATH):
            logger.warning(f"Logo 文件不存在: {OPENCLAW_LOGO_PATH}")
            return None
        
        try:
            # 进入创建应用页面会有上传按钮
            # 使用 JavaScript 触发文件上传
            file_input = self.page.query_selector('input[type="file"]')
            if file_input:
                file_input.set_input_files(OPENCLAW_LOGO_PATH)
                time.sleep(2)
                logger.info("Logo 已上传")
                return OPENCLAW_LOGO_PATH
        except Exception as e:
            logger.warning(f"上传 Logo 失败: {e}")
        
        return None
    
    def create_app_for_member(
        self,
        member_name: str,
        member_user_id: str,
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
            
            # 上传 Logo（OpenClaw 小龙虾图片）
            logo_input = self.page.query_selector('input[type="file"][accept*="image"]')
            if logo_input and os.path.exists(OPENCLAW_LOGO_PATH):
                logo_input.set_input_files(OPENCLAW_LOGO_PATH)
                time.sleep(2)
                logger.info("  ✓ Logo 已上传")
            
            # 填写应用名称
            name_input = self.page.query_selector('input[placeholder*="应用名"], input.ww_input')
            if name_input:
                name_input.fill(app_name)
                logger.info(f"  ✓ 应用名称: {app_name}")
            else:
                # 尝试其他选择器
                self.page.evaluate(f'''() => {{
                    const inputs = document.querySelectorAll('input');
                    for (const input of inputs) {{
                        if (input.placeholder && input.placeholder.includes('应用')) {{
                            input.value = '{app_name}';
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            return true;
                        }}
                    }}
                    return false;
                }}''')
            
            # 填写应用简介
            desc_textarea = self.page.query_selector('textarea')
            if desc_textarea:
                desc_textarea.fill(app_desc)
                logger.info(f"  ✓ 应用简介: {app_desc}")
            
            # ============================================================
            # Step 2: 设置可见范围（仅限新成员本人）
            # ============================================================
            logger.info("[Step 2] 设置可见范围...")
            
            # 点击可见范围选择按钮
            visible_btn = self.page.query_selector('.js_show_visible_mod, .ww_groupSelBtn, a:has-text("选择")')
            if visible_btn:
                visible_btn.click()
                time.sleep(2)
                
                # 在成员选择对话框中搜索并选择成员
                search_input = self.page.query_selector('.ww_dialog input[type="text"], .search_input')
                if search_input:
                    search_input.fill(member_name)
                    time.sleep(1)
                
                # 选择成员（在 jstree 中点击）
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
                
                if member_selected:
                    logger.info(f"  ✓ 已选择成员: {member_name}")
                else:
                    # 尝试通过 UserID 选择
                    logger.warning(f"  未找到成员 {member_name}，尝试使用 UserID")
                
                # 点击确认按钮
                confirm_btn = self.page.query_selector('.js_submit, button:has-text("确定")')
                if confirm_btn:
                    confirm_btn.click()
                    time.sleep(1)
            
            # ============================================================
            # Step 3: 提交创建应用
            # ============================================================
            logger.info("[Step 3] 提交创建应用...")
            
            create_btn = self.page.query_selector('a:has-text("创建应用"), button:has-text("创建应用"), .js_create')
            if create_btn:
                create_btn.click()
                time.sleep(3)
            
            # 从 URL 获取 AgentId
            current_url = self.page.url
            match = re.search(r'modApiApp/(\d+)', current_url)
            if match:
                result["agent_id"] = match.group(1)
                logger.info(f"  ✓ AgentId: {result['agent_id']}")
            else:
                # 尝试从页面提取
                agent_el = self.page.query_selector('[data-agent-id], .agent_id')
                if agent_el:
                    result["agent_id"] = agent_el.get_attribute('data-agent-id') or agent_el.text_content().strip()
            
            if not result["agent_id"]:
                result["error"] = "未能获取 AgentId"
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
            
            # 生成 Token 和 EncodingAESKey
            result["token"], result["aes_key"] = self._generate_token_and_aes_key()
            logger.info(f"  ✓ Token: {result['token']}")
            logger.info(f"  ✓ EncodingAESKey: {result['aes_key']}")
            
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
            
            # Token
            token_input = self.page.query_selector('input[name="token"], input[placeholder*="Token"]')
            if token_input:
                token_input.fill(result["token"])
            
            # EncodingAESKey
            aes_input = self.page.query_selector('input[name="encodingAESKey"], input[placeholder*="EncodingAESKey"]')
            if aes_input:
                aes_input.fill(result["aes_key"])
            
            # 随机生成按钮（如果有的话）
            random_btn = self.page.query_selector('a:has-text("随机获取")')
            if random_btn and not result["token"]:
                random_btn.click()
                time.sleep(1)
                # 重新获取生成的值
                if token_input:
                    result["token"] = token_input.get_attribute('value') or ""
                if aes_input:
                    result["aes_key"] = aes_input.get_attribute('value') or ""
            
            logger.info("  ✓ 接收消息配置已填写（URL 待更新）")
            
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
            logger.info("  页面保持打开，等待 URL 更新")
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
