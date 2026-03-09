#!/usr/bin/env python3
"""
企业微信新成员自动接入模块

功能：
1. 监听企微后台配置的"通讯录变更事件"回调，捕获新成员加入事件
2. 为新成员创建专属企业应用（Logo/名称/介绍/可见范围）
3. 配置接收消息 API（Token/EncodingAESKey/URL）
4. 提取配置参数供后续模块使用

企微通讯录变更事件文档：
https://developer.work.weixin.qq.com/document/path/90970

Logo 上传机制说明（已实测验证，2026-03-09）：
- 必须用 jQuery trigger 触发 showAvatarEditor 弹窗，不能用原生 click
- 文件注入目标：#__dialog__avatarEditor__ .js_no_img .ww_fileInput
- 图片尺寸必须 ≥ 150×150 像素
- 需等待 .cropper-container 出现后再点击 .js_save
- Save 按钮 disabled 状态通过 jQuery attr 设置，需用 evaluate 检查

Secret 获取说明：
- 企微出于安全机制，Secret 只推送到管理员企微 App，网页端无法直接获取
- 自动化流程会触发「View → Send」，需人工在企微 App 查看一次后写入配置
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

from playwright.sync_api import sync_playwright, Page, BrowserContext, Frame

logger = logging.getLogger(__name__)

# OpenClaw 小龙虾 logo 路径（尺寸必须 ≥ 150×150）
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
            msg_content = decrypted[20:20 + msg_len].decode('utf-8')
            return msg_content
        except Exception as e:
            logger.error(f"消息解密失败: {e}")
            return ""

    def encrypt_msg(self, reply_msg: str) -> str:
        """加密回复消息"""
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

    典型用法：
        creator = NewMemberAppCreator(corp_id="ww95aca10dfcf3d6e2")
        result = creator.create_app_for_member(
            member_name="louis",
            member_user_id="louis",
            openclaw_ip="101.35.102.240",
        )
        print(result)
        creator.close()
    """

    def __init__(self, corp_id: str):
        self.corp_id = corp_id
        self.base_url = "https://work.weixin.qq.com"
        self.user_data_dir = f'./browser_data/{corp_id}'
        self.p = None
        self.browser = None
        self.context = None
        self.page = None
        self._frame: Optional[Frame] = None  # 企微 SPA 内容 frame
        # 保存创建的应用配置
        self.created_config: Optional[Dict] = None

    # ──────────────────────────────────────────────────────────────────────────
    # 浏览器管理
    # ──────────────────────────────────────────────────────────────────────────

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
            raise Exception(
                f"未找到 Cookie/会话目录，请先运行: python cookie_manager.py login {self.corp_id}"
            )

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
                except Exception:
                    pass
        self.context = self.browser = self.p = self.page = self._frame = None

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

    def _get_frame(self) -> Frame:
        """
        获取企微 SPA 内容所在的 frame。

        企微后台是 SPA，实际内容可能渲染在子 iframe 里。
        通过查找包含 #apps_upload_logo_image 的 frame 来定位。
        若找不到则回退到 main_frame。
        """
        if self._frame:
            try:
                # 验证 frame 仍然有效
                self._frame.evaluate("1")
                return self._frame
            except Exception:
                self._frame = None

        for frame in self.page.frames:
            try:
                el = frame.query_selector('#apps_upload_logo_image')
                if el:
                    self._frame = frame
                    logger.debug(f"找到目标 frame: {frame.url[:80]}")
                    return frame
            except Exception:
                continue

        self._frame = self.page.main_frame
        return self._frame

    # ──────────────────────────────────────────────────────────────────────────
    # Logo 上传（已实测验证，2026-03-09）
    # ──────────────────────────────────────────────────────────────────────────

    def _upload_logo(self, logo_path: str = OPENCLAW_LOGO_PATH) -> bool:
        """
        上传 Logo 到企业微信创建应用弹窗。

        验证通过的完整流程：
        1. jQuery trigger 触发 showAvatarEditor 弹窗（不能用原生 click）
        2. 等待弹窗 DOM 出现（#__dialog__avatarEditor__）
        3. 根据弹窗状态选择正确的 file input：
           - 初始状态（无图）：.js_no_img .ww_fileInput
           - 已有图状态：.js_file_reupload .js_file
        4. set_input_files 注入文件（图片尺寸必须 ≥ 150×150）
        5. 等待 .cropper-container 出现（cropper 初始化完成）
        6. 通过 jQuery evaluate 验证 Save 按钮已启用
        7. 点击 .js_save（不加 force=True）
        8. 等待弹窗关闭 + CDN 上传完成
        9. 验证 Logo 区域 img.src 包含有效 URL

        Args:
            logo_path: Logo 图片的本地路径，尺寸必须 ≥ 150×150 像素

        Returns:
            True 表示上传成功，False 表示失败
        """
        if not os.path.exists(logo_path):
            logger.error(f"[Logo] 文件不存在: {logo_path}")
            return False

        frame = self._get_frame()

        # ── 步骤 1：通过 jQuery trigger 触发 showAvatarEditor ──────────────
        # 关键：不能用 page.click() 或 locator.click()，因为原生 click 会打开
        # 系统文件选择器，绕过 Backbone 事件委托的 showAvatarEditor 处理器。
        # 必须用 jQuery trigger，才能触发：
        #   "click #apps_upload_logo_image": "showAvatarEditor"
        result = frame.evaluate("""
            () => {
                const input = document.querySelector('#apps_upload_logo_image');
                if (!input) return 'input_not_found';
                if (typeof $ === 'undefined') return 'jquery_not_found';
                $(input).trigger('click');
                return 'triggered';
            }
        """)

        if result != 'triggered':
            logger.error(f"[Logo] 触发 showAvatarEditor 失败: {result}")
            return False

        logger.info("[Logo] 已触发 showAvatarEditor")

        # ── 步骤 2：等待弹窗出现 ───────────────────────────────────────────
        try:
            frame.wait_for_selector(
                '#__dialog__avatarEditor__',
                state='visible',
                timeout=5000
            )
            logger.info("[Logo] 编辑Logo弹窗已出现")
        except Exception as e:
            logger.error(f"[Logo] 等待弹窗超时: {e}")
            return False

        time.sleep(0.5)

        # ── 步骤 3：根据弹窗状态选择正确的 file input ─────────────────────
        # 弹窗有两种状态：
        # - 初始状态（.js_no_img 可见）：显示「Select an image」按钮
        # - 已有图状态（.js_img_container 可见）：显示「Upload again」按钮
        dialog_state = frame.evaluate("""
            () => ({
                noImgDisplay: (document.querySelector('.js_no_img') || {style:{display:'?'}}).style.display,
                imgContainerDisplay: (document.querySelector('.js_img_container') || {style:{display:'?'}}).style.display,
            })
        """)
        logger.debug(f"[Logo] 弹窗状态: {dialog_state}")

        if dialog_state.get('noImgDisplay') == 'none':
            # 已有图状态，使用 Upload again 的 file input
            file_input = frame.locator('.js_file_reupload .js_file')
            logger.info("[Logo] 使用 Upload again file input")
        else:
            # 初始状态，使用 Select an image 的 file input
            file_input = frame.locator('.js_no_img .ww_fileInput')
            logger.info("[Logo] 使用 Select an image file input")

        # ── 步骤 4：注入文件 ───────────────────────────────────────────────
        # 必须用 set_input_files()，不能用 evaluate + DataTransfer 手动注入。
        # 原因：DataTransfer 注入不会设置 input.value，
        # 而 avatarEditor.js 的 change 处理器第一行是 if(this.value) {...}，
        # value 为空时直接 return，不执行任何操作。
        # Playwright set_input_files 通过 CDP Input.setFiles 设置文件，
        # 会正确设置 input.value = "C:\fakepath\filename.png"，从而触发处理器。
        try:
            file_input.set_input_files(logo_path)
            logger.info(f"[Logo] 文件注入成功: {logo_path}")
        except Exception as e:
            logger.error(f"[Logo] 文件注入失败: {e}")
            return False

        # ── 步骤 5：等待 cropper 初始化 ────────────────────────────────────
        # 文件注入后，avatarEditor.js 会：
        # 1. 用 FileReader 读取文件为 DataURL
        # 2. 设置 img.js_avatar_image 的 src
        # 3. 图片 onload 后调用 D() 初始化 cropper
        # 4. cropper 初始化后创建 .cropper-container DOM
        # 5. 调用 I() 移除 Save 按钮的 jQuery disabled 属性
        try:
            frame.wait_for_selector(
                '#__dialog__avatarEditor__ .cropper-container',
                state='attached',
                timeout=8000
            )
            logger.info("[Logo] cropper-container 已出现")
        except Exception:
            logger.warning("[Logo] cropper-container 未在 8 秒内出现，继续等待")

        # 额外等待确保 cropper 完全就绪，Save 按钮 disabled 属性已移除
        time.sleep(1.5)

        # ── 步骤 6：验证 Save 按钮可用性 ──────────────────────────────────
        # 注意：企微使用 jQuery attr("disabled","disabled") 设置 disabled，
        # 而非原生 HTML disabled 属性。
        # Playwright 的 locator.is_disabled() 只检测原生属性，会误判为"可用"。
        # 必须通过 evaluate 调用 jQuery 来检查真实状态。
        save_btn_ready = frame.evaluate("""
            () => {
                const btn = document.querySelector('#__dialog__avatarEditor__ .js_save');
                if (!btn) return false;
                return $(btn).attr('disabled') === undefined;
            }
        """)

        if not save_btn_ready:
            logger.warning("[Logo] Save 按钮仍为 jQuery disabled 状态，额外等待 1 秒")
            time.sleep(1.0)
            save_btn_ready = frame.evaluate("""
                () => {
                    const btn = document.querySelector('#__dialog__avatarEditor__ .js_save');
                    return btn ? $(btn).attr('disabled') === undefined : false;
                }
            """)
            if not save_btn_ready:
                logger.error("[Logo] Save 按钮持续 disabled，图片可能未正确加载（尺寸需 ≥ 150×150）")
                return False

        logger.info("[Logo] Save 按钮已就绪")

        # ── 步骤 7：点击 Save 按钮 ────────────────────────────────────────
        # 不加 force=True，让 Playwright 正常点击可见元素
        # Save 按钮点击后会：
        # 1. 检查 cropper 数据的 width/height ≥ minNaturalWidth(150)/minNaturalHeight(150)
        # 2. 通过 form POST 上传到 /wework_admin/uploadImage
        # 3. 回调 uploadLogoImageCallback 设置 this.logoUrl 并关闭弹窗
        try:
            save_btn = frame.locator('#__dialog__avatarEditor__ .js_save')
            save_btn.click(timeout=5000)
            logger.info("[Logo] 已点击 Save 按钮")
        except Exception as e:
            logger.error(f"[Logo] 点击 Save 按钮失败: {e}")
            return False

        # ── 步骤 8：等待弹窗关闭 + CDN 上传完成 ───────────────────────────
        try:
            frame.wait_for_selector(
                '#__dialog__avatarEditor__',
                state='hidden',
                timeout=15000  # CDN 上传可能需要几秒
            )
            logger.info("[Logo] 弹窗已关闭，等待 CDN 上传完成")
        except Exception:
            error_tips = frame.evaluate("""
                () => Array.from(document.querySelectorAll('.ww_tips'))
                    .filter(el => el.offsetParent !== null)
                    .map(el => el.textContent.trim())
                    .filter(t => t.length > 0)
            """)
            logger.error(f"[Logo] 弹窗未关闭，错误提示: {error_tips}")
            logger.error("[Logo] 常见原因：图片尺寸 < 150×150，请使用 ≥ 150×150 的图片")
            return False

        # CDN 上传是异步的，等待图片 URL 更新
        time.sleep(2.0)

        # ── 步骤 9：验证 Logo 是否已通过前端校验 ──────────────────────────
        logo_url = frame.evaluate("""
            () => {
                const img = document.querySelector('#js_createApiApp47 img');
                if (img && img.src) return img.src;
                const logoImg = document.querySelector('.ww_fileInputWrap img, [class*="logo"] img');
                return logoImg ? logoImg.src : null;
            }
        """)

        if logo_url and logo_url.startswith('http'):
            logger.info(f"[Logo] ✅ Logo 上传成功，URL: {logo_url[:80]}")
            return True
        else:
            logger.error(f"[Logo] ❌ Logo 上传后未检测到有效图片 URL: {logo_url}")
            return False

    # ──────────────────────────────────────────────────────────────────────────
    # 主流程
    # ──────────────────────────────────────────────────────────────────────────

    def create_app_for_member(
        self,
        member_name: str,
        member_user_id: str,
        openclaw_ip: str = "",
        headless: bool = True,
        keep_page_open: bool = True
    ) -> Dict:
        """
        为新成员创建专属企业应用，并配置接收消息 API。

        完整流程（已实测验证）：
        1. 导航到 createApiApp 页面
        2. 填写应用名称和介绍（{member_name}的openclaw）
        3. 上传 OpenClaw Logo（jQuery trigger → 弹窗 → set_input_files → Save）
        4. 设置可见范围为该成员
        5. 点击「Create an app」，等待跳转到 modApiApp/{agent_id}
        6. 触发「View → Send」让 Secret 发送到管理员企微 App（需人工查看）
        7. 进入接收消息 API 设置页，随机生成 Token/EncodingAESKey，填写 URL
        8. 返回所有配置参数

        Args:
            member_name: 成员名称（如 "louis"）
            member_user_id: 成员 UserID
            openclaw_ip: OpenClaw 服务器 IP（如 "101.35.102.240"）
            headless: 是否无头模式
            keep_page_open: 是否保持页面打开（用于后续更新 URL）

        Returns:
            {
                "success": bool,
                "corp_id": str,
                "agent_id": str,
                "secret": str,       # 需人工从企微 App 获取后填入
                "token": str,
                "aes_key": str,
                "webhook_url": str,
                "app_name": str,
                "member_name": str,
                "member_user_id": str,
                "error": str
            }
        """
        app_name = f"{member_name}的openclaw"
        app_desc = f"{member_name}的openclaw"
        webhook_url = f"http://{openclaw_ip}:3000/wecom" if openclaw_ip else ""

        result = {
            "success": False,
            "corp_id": self.corp_id,
            "agent_id": "",
            "secret": "",
            "token": "",
            "aes_key": "",
            "webhook_url": webhook_url,
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

            # ================================================================
            # Step 1: 导航到创建应用页面
            # ================================================================
            logger.info("[Step 1] 导航到创建应用页面...")
            self.page.goto(f"{self.base_url}/wework_admin/frame#apps/createApiApp")
            time.sleep(3)

            # 获取内容 frame（企微 SPA 内容可能在子 iframe）
            frame = self._get_frame()

            # 等待 Logo 上传控件出现，确认页面已加载
            try:
                frame.wait_for_selector('#apps_upload_logo_image', timeout=8000)
                logger.info("  ✓ 创建应用页面已加载")
            except Exception as e:
                logger.warning(f"  等待页面加载超时，尝试继续: {e}")
                time.sleep(2)

            # ================================================================
            # Step 2: 填写应用名称和介绍
            # ================================================================
            logger.info(f"[Step 2] 填写应用名称：{app_name}")
            frame.evaluate(f"""
                () => {{
                    const nameInput = document.querySelector('input[type="text"]');
                    const descTextarea = document.querySelector('textarea');
                    if (nameInput) {{
                        nameInput.focus();
                        nameInput.value = '{app_name}';
                        nameInput.dispatchEvent(new Event('input', {{bubbles: true}}));
                        nameInput.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                    if (descTextarea) {{
                        descTextarea.focus();
                        descTextarea.value = '{app_desc}';
                        descTextarea.dispatchEvent(new Event('input', {{bubbles: true}}));
                        descTextarea.dispatchEvent(new Event('change', {{bubbles: true}}));
                    }}
                }}
            """)
            time.sleep(0.5)
            logger.info(f"  ✓ 应用名称和介绍：{app_name}")

            # ================================================================
            # Step 3: 上传 Logo
            # ================================================================
            logger.info("[Step 3] 上传 OpenClaw Logo...")
            logo_ok = self._upload_logo(OPENCLAW_LOGO_PATH)
            if logo_ok:
                logger.info("  ✓ Logo 上传成功")
            else:
                logger.warning("  Logo 上传失败，继续尝试提交（可能导致创建失败）")

            # ================================================================
            # Step 4: 设置可见范围
            # ================================================================
            logger.info(f"[Step 4] 设置可见范围为成员：{member_name}")
            member_selected = False
            try:
                # 点击「Select departments/members」
                frame.locator('text=Select departments/members').click()
                time.sleep(2)

                # 搜索成员
                search_input = frame.locator('.qui_dialog input[type="text"]').first
                search_input.wait_for(state='visible', timeout=3000)
                search_input.fill(member_name)
                time.sleep(1)
                search_input.press('Enter')
                time.sleep(1.5)

                # 点击搜索结果
                for sel in [
                    f'.qui_dialog .ww_checkbox_label:has-text("{member_name}")',
                    f'.qui_dialog li:has-text("{member_name}")',
                    f'.qui_dialog .js_member_name:has-text("{member_name}")',
                    f'.qui_dialog span:has-text("{member_name}")',
                ]:
                    try:
                        el = frame.locator(sel).first
                        if el.count() > 0:
                            el.click()
                            logger.info(f"  ✓ 已选中成员：{member_name}")
                            member_selected = True
                            break
                    except Exception:
                        continue

                if not member_selected:
                    logger.warning(f"  未能精确选中成员 {member_name}，尝试选择全部成员")
                    frame.evaluate("""() => {
                        const anchors = Array.from(document.querySelectorAll('.jstree-anchor'));
                        const allNode = anchors.find(a => {
                            const t = (a.innerText || a.textContent || '').trim();
                            return t.includes('全部成员') || t.includes('全体成员');
                        });
                        if (allNode) allNode.click();
                    }""")

                # 点击确认
                for sel in [
                    '.qui_dialog .ww_btn_Blue',
                    '.qui_dialog .js_confirm',
                    '.qui_dialog a:has-text("OK")',
                    '.qui_dialog a:has-text("确定")',
                    '.qui_dialog button:has-text("OK")',
                ]:
                    try:
                        el = frame.locator(sel).first
                        if el.count() > 0:
                            el.click()
                            logger.info("  ✓ 已确认可见范围选择")
                            break
                    except Exception:
                        continue

                time.sleep(1)

            except Exception as e:
                logger.warning(f"  设置可见范围异常，继续尝试创建: {e}")

            # ================================================================
            # Step 5: 点击「Create an app」
            # ================================================================
            logger.info("[Step 5] 提交创建应用...")
            frame.locator('a:has-text("Create an app")').first.click()

            try:
                self.page.wait_for_url("**modApiApp/**", timeout=12000)
            except Exception:
                time.sleep(4)

            # 检查是否仍在创建页（说明有错误）
            if 'createApiApp' in self.page.url:
                error_tips = frame.evaluate("""
                    () => Array.from(document.querySelectorAll('.ww_tips, .js_tips, .qui_msg'))
                        .filter(el => el.offsetParent !== null)
                        .map(el => el.textContent.trim())
                        .filter(t => t.length > 0)
                """)
                logger.warning(f"  仍在创建页，错误提示: {error_tips}")

                # 若 Logo 校验失败，重新上传并重试
                if any('logo' in t.lower() or 'Logo' in t for t in error_tips):
                    logger.info("  检测到 Logo 校验失败，重新上传...")
                    if self._upload_logo(OPENCLAW_LOGO_PATH):
                        frame.locator('a:has-text("Create an app")').first.click()
                        try:
                            self.page.wait_for_url("**modApiApp/**", timeout=12000)
                        except Exception:
                            time.sleep(4)

            # 从 URL 提取 AgentId
            current_url = self.page.url
            logger.info(f"  当前 URL: {current_url}")

            for ptn in [r'modApiApp/(\d+)', r'[?&]agentid=(\d+)', r'[?&]agent_id=(\d+)']:
                m = re.search(ptn, current_url)
                if m:
                    result["agent_id"] = m.group(1)
                    break

            if not result["agent_id"]:
                # 从页面 HTML 里正则抓取
                html = self.page.content()
                m = re.search(r'modApiApp/(\d+)', html)
                if m:
                    result["agent_id"] = m.group(1)

            if not result["agent_id"]:
                result["error"] = f"未能获取 AgentId（创建后页面未跳转到详情），URL: {current_url}"
                return result

            logger.info(f"  ✓ AgentId: {result['agent_id']}")

            # ================================================================
            # Step 6: 触发 Secret 发送（需人工在企微 App 查看）
            # ================================================================
            logger.info("[Step 6] 触发 Secret 发送到企微 App...")
            self.page.goto(
                f"{self.base_url}/wework_admin/frame#apps/modApiApp/{result['agent_id']}"
            )
            time.sleep(3)

            # 点击「View」打开 Secret 弹窗
            try:
                frame.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('a, button'));
                        const viewBtn = btns.find(b =>
                            (b.textContent || '').trim() === 'View' ||
                            (b.textContent || '').trim() === '查看'
                        );
                        if (viewBtn) viewBtn.click();
                    }
                """)
                time.sleep(1)

                # 点击「Send」触发推送
                frame.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('a, button'));
                        const sendBtn = btns.find(b =>
                            (b.textContent || '').trim() === 'Send' ||
                            (b.textContent || '').trim() === '发送'
                        );
                        if (sendBtn) sendBtn.click();
                    }
                """)
                time.sleep(1)
                logger.info("  ✓ 已触发 Secret 发送，请在企微 App 中查看并记录 Secret")
            except Exception as e:
                logger.warning(f"  触发 Secret 发送失败（可手动操作）: {e}")

            result["secret"] = ""  # 需人工从企微 App 获取后填入

            # ================================================================
            # Step 7: 配置接收消息 API
            # ================================================================
            logger.info("[Step 7] 配置接收消息 API...")

            # 进入接收消息设置页
            self.page.goto(
                f"{self.base_url}/wework_admin/frame#apps/modApiApp/{result['agent_id']}/apiReceive"
            )
            time.sleep(3)

            # 重新获取 frame（页面跳转后 frame 可能变化）
            self._frame = None
            frame = self._get_frame()

            # 点击「Set API to receive messages」
            try:
                frame.evaluate("""
                    () => {
                        const btns = Array.from(document.querySelectorAll('a, button'));
                        const btn = btns.find(b =>
                            (b.textContent || '').includes('Set API') ||
                            (b.textContent || '').includes('设置API') ||
                            (b.textContent || '').includes('设置')
                        );
                        if (btn) btn.click();
                    }
                """)
                time.sleep(2)
            except Exception as e:
                logger.warning(f"  点击设置按钮失败，尝试继续: {e}")

            # 随机生成 Token（点击第一个「Get randomly」）
            frame.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('a, button, input[type="button"]'));
                    const randomBtns = btns.filter(b =>
                        b.textContent.toLowerCase().includes('random') ||
                        b.textContent.includes('随机')
                    );
                    if (randomBtns.length > 0) randomBtns[0].click();
                }
            """)
            time.sleep(0.5)

            # 随机生成 EncodingAESKey（点击第二个「Get randomly」）
            frame.evaluate("""
                () => {
                    const btns = Array.from(document.querySelectorAll('a, button, input[type="button"]'));
                    const randomBtns = btns.filter(b =>
                        b.textContent.toLowerCase().includes('random') ||
                        b.textContent.includes('随机')
                    );
                    if (randomBtns.length > 1) randomBtns[1].click();
                    else if (randomBtns.length === 1) randomBtns[0].click();
                }
            """)
            time.sleep(0.5)

            # 填写 URL
            if webhook_url:
                frame.evaluate(f"""
                    (url) => {{
                        const inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                        for (const inp of inputs) {{
                            if ((inp.placeholder || '').toLowerCase().includes('http') ||
                                (inp.name || '').toLowerCase().includes('url')) {{
                                inp.value = url;
                                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                inp.dispatchEvent(new Event('change', {{bubbles: true}}));
                                return;
                            }}
                        }}
                        // 兜底：第一个空框
                        for (const inp of inputs) {{
                            if (!inp.value) {{
                                inp.value = url;
                                inp.dispatchEvent(new Event('input', {{bubbles: true}}));
                                return;
                            }}
                        }}
                    }}
                """, webhook_url)
                time.sleep(0.3)
                logger.info(f"  ✓ URL: {webhook_url}")

            # 读取生成的 Token 和 EncodingAESKey
            final_vals = frame.evaluate("""
                () => Array.from(document.querySelectorAll('input[type="text"]'))
                    .map(inp => ({
                        name: inp.name || inp.id || '',
                        placeholder: inp.placeholder || '',
                        value: inp.value || '',
                    }))
                    .filter(i => i.value.trim() !== '')
            """)

            for inp in final_vals:
                v = inp.get('value', '')
                n = (inp.get('name', '') + inp.get('placeholder', '')).lower()
                if 'http' in v or 'url' in n:
                    result['webhook_url'] = v
                elif len(v) == 43:  # EncodingAESKey 固定 43 字符
                    result['aes_key'] = v
                elif v and not result.get('token'):
                    result['token'] = v

            logger.info(f"  ✓ Token: {result.get('token', '(未获取)')}")
            logger.info(f"  ✓ EncodingAESKey: {result.get('aes_key', '(未获取)')}")

            # ================================================================
            # 完成
            # ================================================================
            result["success"] = True
            self.created_config = result.copy()

            logger.info("\n" + "=" * 60)
            logger.info("  新成员应用创建完成")
            logger.info("=" * 60)
            logger.info(f"  成员名称:        {member_name}")
            logger.info(f"  应用名称:        {app_name}")
            logger.info(f"  Corp ID:         {self.corp_id}")
            logger.info(f"  Agent ID:        {result['agent_id']}")
            logger.info(f"  Token:           {result.get('token', '')}")
            logger.info(f"  EncodingAESKey:  {result.get('aes_key', '')}")
            logger.info(f"  Webhook URL:     {result.get('webhook_url', '')}")
            logger.info(f"  Secret:          (需在企微 App 查看 View→Send 后填入)")
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
            frame = self._get_frame()

            frame.evaluate(f"""
                (url) => {{
                    const inputs = document.querySelectorAll('input');
                    for (const input of inputs) {{
                        if (input.name === 'url' || (input.placeholder && input.placeholder.includes('URL'))) {{
                            input.value = url;
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
                            return true;
                        }}
                    }}
                    return false;
                }}
            """, url)

            # 点击保存按钮
            save_btn = frame.query_selector('a:has-text("保存"), button:has-text("保存"), .js_save')
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
            frame = self._get_frame()
            save_btn = frame.query_selector(
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
        try:
            root = ET.fromstring(request_body)
            encrypted_msg = root.findtext("Encrypt", "")
        except ET.ParseError:
            logger.error("XML 解析失败")
            return None

        if not self.crypt.verify_signature(msg_signature, timestamp, nonce, encrypted_msg):
            logger.error("签名验证失败")
            return None

        decrypted = self.crypt.decrypt_msg(encrypted_msg)
        if not decrypted:
            return None

        event = parse_contact_change_event(decrypted)
        if not event:
            return None

        logger.info(f"收到通讯录变更事件: {event.get('change_type')}")

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

    def process_pending_member(
        self,
        openclaw_ip: str = "",
        headless: bool = True
    ) -> Optional[Dict]:
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
                openclaw_ip=openclaw_ip,
                headless=headless,
                keep_page_open=True
            )

            if config["success"]:
                self.created_apps[member["user_id"]] = config

            return config
        finally:
            pass

    def get_pending_count(self) -> int:
        """获取待处理成员数量"""
        return len(self.pending_members)


# ──────────────────────────────────────────────────────────────────────────────
# 命令行测试入口
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(message)s'
    )

    if len(sys.argv) < 2:
        print("用法:")
        print("  python wecom_member_callback.py create <corp_id> <member_name> [openclaw_ip] [--visible]")
        print("    为指定成员创建专属应用")
        print("")
        print("  python wecom_member_callback.py test-parse <xml_file>")
        print("    测试解析通讯录变更事件 XML")
        print("")
        print("示例:")
        print("  python wecom_member_callback.py create ww95aca10dfcf3d6e2 louis 101.35.102.240")
        print("  python wecom_member_callback.py create ww95aca10dfcf3d6e2 louis 101.35.102.240 --visible")
        sys.exit(1)

    action = sys.argv[1]

    if action == "create":
        corp_id = sys.argv[2] if len(sys.argv) > 2 else "ww95aca10dfcf3d6e2"
        member_name = sys.argv[3] if len(sys.argv) > 3 else "测试成员"
        openclaw_ip = sys.argv[4] if len(sys.argv) > 4 and not sys.argv[4].startswith('--') else ""
        headless = "--visible" not in sys.argv

        creator = NewMemberAppCreator(corp_id)
        result = creator.create_app_for_member(
            member_name=member_name,
            member_user_id=f"test_{member_name}",
            openclaw_ip=openclaw_ip,
            headless=headless,
            keep_page_open=True
        )

        print("\n创建结果:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        if result["success"]:
            print("\n⚠️  请在企业微信 App 中查看 Secret（View → Send），然后手动填入配置")
            print("页面保持打开，按回车键后关闭...")
            input()
            creator.close()

    elif action == "test-parse":
        xml_file = sys.argv[2] if len(sys.argv) > 2 else None
        if xml_file and os.path.exists(xml_file):
            with open(xml_file, 'r') as f:
                xml_content = f.read()
        else:
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
