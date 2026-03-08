"""
配置文件

架构说明：
- 本服务(app.py)部署在 Ubuntu 服务器
- OpenClaw 服务部署在另一台机器
- 两者通过飞书消息通道通信，不共享文件系统
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
    PORT = int(os.environ.get('PORT', 5001))
    
    # 飞书状态文件
    FEISHU_STATE_FILE = '/tmp/feishu-bot-creator-state.json'
    
    # OpenClaw 服务配置（一次性配置）
    # 新成员应用的回调 URL 格式: http://{OPENCLAW_HOST}:3000/wecom
    OPENCLAW_HOST = os.environ.get('OPENCLAW_HOST', '')
    OPENCLAW_CALLBACK_PORT = int(os.environ.get('OPENCLAW_CALLBACK_PORT', 3000))
