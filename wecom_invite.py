#!/usr/bin/env python3
"""
企微邀请二维码获取 - 使用持久化浏览器会话

现在使用持久化浏览器目录，不再单独保存/加载Cookie文件。
只需要每24小时刷新一次登录即可。
"""

import json
import time
import base64
import os
from playwright.sync_api import sync_playwright

# 导入浏览器会话管理
from cookie_manager import get_browser_data_dir, get_session_status, create_persistent_context


def get_invite_qrcode(corp_id: str) -> dict:
    """
    获取企微邀请同事加入的二维码
    
    Args:
        corp_id: 企业ID
    
    Returns:
        {
            "success": bool,
            "qrcode_base64": str,  # 二维码图片base64
            "qrcode_url": str,     # 二维码图片URL（如果有）
            "error": str,          # 错误信息
            "need_relogin": bool   # 是否需要重新登录
        }
    """
    # 检查会话状态
    status = get_session_status(corp_id)
    if not status["valid"]:
        return {
            "success": False,
            "error": f"会话无效: {status['message']}。请运行: python3 cookie_manager.py login {corp_id}",
            "need_relogin": True
        }
    
    print(f"会话有效，剩余 {status['remaining_hours']:.1f} 小时")
    
    result = {"success": False}
    
    # 使用持久化浏览器Context
    p, context = create_persistent_context(corp_id, headless=True)
    
    try:
        page = context.pages[0] if context.pages else context.new_page()
        
        # 1. 打开企微后台首页
        print("打开企微后台首页...")
        page.goto("https://work.weixin.qq.com/wework_admin/frame#/index")
        time.sleep(3)
        
        # 检查是否登录成功
        if "login" in page.url.lower():
            result["error"] = "会话已过期，请重新登录"
            result["need_relogin"] = True
            return result
        
        # 2. 查找"邀请同事加入"或"发起邀请"按钮
        print("查找邀请按钮...")
        
        # 尝试多种选择器
        invite_selectors = [
            'text=发起邀请',
            'text=邀请同事',
            'text=微信扫码快速邀请',
            '.ww_indexGuide_step2 a',  # 首页引导步骤2
            'a:has-text("邀请")',
            '[class*="invite"]',
        ]
        
        invite_btn = None
        for selector in invite_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=1000):
                    invite_btn = btn
                    print(f"找到邀请按钮: {selector}")
                    break
            except:
                continue
        
        if not invite_btn:
            # 截图调试
            page.screenshot(path="/tmp/wecom_invite_debug.png")
            result["error"] = "未找到邀请按钮，请查看 /tmp/wecom_invite_debug.png"
            return result
        
        # 3. 点击邀请按钮
        print("点击邀请按钮...")
        invite_btn.click()
        time.sleep(2)
        
        # 4. 等待弹窗出现并查找二维码
        print("查找二维码...")
        
        # 尝试多种二维码选择器
        qrcode_selectors = [
            '.ww_dialog img[src*="qrcode"]',
            '.ww_dialog img[src*="invite"]',
            '.js_qrcode_img',
            'img.qrcode',
            '.ww_inviteDialog img',
            '[class*="qrcode"] img',
            '.ww_dialog canvas',  # 可能是canvas绘制的二维码
        ]
        
        qrcode_element = None
        for selector in qrcode_selectors:
            try:
                elem = page.locator(selector).first
                if elem.is_visible(timeout=2000):
                    qrcode_element = elem
                    print(f"找到二维码元素: {selector}")
                    break
            except:
                continue
        
        # 截图当前状态
        page.screenshot(path="/tmp/wecom_invite_dialog.png")
        
        if qrcode_element:
            # 获取二维码图片
            tag_name = qrcode_element.evaluate("el => el.tagName.toLowerCase()")
            
            if tag_name == "img":
                # 是img标签，获取src
                src = qrcode_element.get_attribute("src")
                if src:
                    if src.startswith("data:"):
                        # 已经是base64
                        result["qrcode_base64"] = src
                    else:
                        # 是URL，下载图片
                        result["qrcode_url"] = src
                        # 截图二维码区域
                        qrcode_element.screenshot(path="/tmp/wecom_invite_qrcode.png")
                        with open("/tmp/wecom_invite_qrcode.png", "rb") as f:
                            result["qrcode_base64"] = "data:image/png;base64," + base64.b64encode(f.read()).decode()
                    result["success"] = True
                    
            elif tag_name == "canvas":
                # 是canvas，转换为图片
                data_url = qrcode_element.evaluate("el => el.toDataURL('image/png')")
                result["qrcode_base64"] = data_url
                result["success"] = True
        
        if not result.get("success"):
            # 尝试从整个弹窗截图获取二维码
            dialog = page.locator('.ww_dialog, [class*="dialog"], [class*="modal"]').first
            if dialog.is_visible():
                dialog.screenshot(path="/tmp/wecom_invite_qrcode.png")
                with open("/tmp/wecom_invite_qrcode.png", "rb") as f:
                    result["qrcode_base64"] = "data:image/png;base64," + base64.b64encode(f.read()).decode()
                result["success"] = True
                result["note"] = "从弹窗截图获取"
            else:
                result["error"] = "未找到二维码，请查看 /tmp/wecom_invite_dialog.png"
        
    except Exception as e:
        result["error"] = str(e)
        try:
            page.screenshot(path="/tmp/wecom_invite_error.png")
        except:
            pass
    
    finally:
        context.close()
        p.stop()
    
    return result


def test(corp_id: str = "ww95aca10dfcf3d6e2"):
    """测试获取邀请二维码"""
    print("="*50)
    print("测试获取企微邀请二维码")
    print("="*50)
    
    result = get_invite_qrcode(corp_id)
    
    if result["success"]:
        print("\n✓ 获取成功!")
        if result.get("qrcode_base64"):
            print(f"  二维码Base64长度: {len(result['qrcode_base64'])}")
            # 保存为文件
            if result["qrcode_base64"].startswith("data:"):
                b64_data = result["qrcode_base64"].split(",", 1)[1]
            else:
                b64_data = result["qrcode_base64"]
            with open("/tmp/wecom_invite_result.png", "wb") as f:
                f.write(base64.b64decode(b64_data))
            print("  已保存到: /tmp/wecom_invite_result.png")
    else:
        print(f"\n✗ 获取失败: {result.get('error')}")
        if result.get("need_relogin"):
            print(f"\n请先登录: python3 cookie_manager.py login {corp_id}")
    
    return result


if __name__ == "__main__":
    import sys
    corp_id = sys.argv[1] if len(sys.argv) > 1 else "ww95aca10dfcf3d6e2"
    test(corp_id)
