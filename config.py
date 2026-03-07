"""
配置文件
"""
import os

class Config:
    # Flask配置
    SECRET_KEY = os.environ.get('SECRET_KEY', 'wecom-bridge-secret-key-change-in-production')
    
    # OpenClaw SSH配置
    OPENCLAW_HOST = os.environ.get('OPENCLAW_HOST', '')
    OPENCLAW_SSH_USER = os.environ.get('OPENCLAW_SSH_USER', 'root')
    OPENCLAW_SSH_KEY = os.environ.get('OPENCLAW_SSH_KEY', '~/.ssh/id_rsa')
    OPENCLAW_SSH_PORT = int(os.environ.get('OPENCLAW_SSH_PORT', 22))
    
    # 飞书配置（用于触发安装）
    FEISHU_APP_ID = os.environ.get('FEISHU_APP_ID', '')
    FEISHU_APP_SECRET = os.environ.get('FEISHU_APP_SECRET', '')
    
    # 企业微信配置
    WECOM_CORP_ID = os.environ.get('WECOM_CORP_ID', '')
    
    # Cookie存储路径
    COOKIE_DIR = os.path.join(os.path.dirname(__file__), 'cookies')
    
    # 企微后台URL
    WECOM_ADMIN_URL = "https://work.weixin.qq.com"
    
    # Web服务配置
    HOST = os.environ.get('HOST', '0.0.0.0')
    PORT = int(os.environ.get('PORT', 5000))
