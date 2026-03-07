"""
配置文件
飞书扫码后自动获取 OpenClaw 配置，无需手工填写
"""
import os

class Config:
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'wecom-bridge-secret-key-change-in-production')
    
    # 企业微信配置（从Cookie文件名推断，或手动指定）
    WECOM_CORP_ID = os.environ.get('WECOM_CORP_ID', '')
    
    # Cookie存储路径
    COOKIE_DIR = os.path.join(os.path.dirname(__file__), 'cookies')
    
    # 企微后台URL
    WECOM_ADMIN_URL = "https://work.weixin.qq.com"
    
    # Web服务配置
    HOST = os.environ.get('HOST', '0.0.0.0')
    PORT = int(os.environ.get('PORT', 5000))
    
    # OpenClaw 配置文件路径（飞书扫码后自动写入）
    OPENCLAW_CONFIG_PATH = os.environ.get('OPENCLAW_CONFIG_PATH', '/root/.openclaw/openclaw.json')
    OPENCLAW_ALLOW_FROM_PATH = os.environ.get('OPENCLAW_ALLOW_FROM_PATH', '/root/.openclaw/credentials/feishu-default-allowFrom.json')
    
    # 飞书状态文件
    FEISHU_STATE_FILE = '/tmp/feishu-bot-creator-state.json'
