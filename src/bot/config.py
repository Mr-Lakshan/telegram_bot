"""
CONFIGURATION FILE
Bot settings and credentials - loaded from .env file
"""

import os
from dotenv import load_dotenv

# .env file load karo
load_dotenv()

# ===== OPENAI CREDENTIALS =====
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# ===== TELEGRAM CREDENTIALS =====
YOUR_API_ID = int(os.getenv("YOUR_API_ID", "0"))
YOUR_API_HASH = os.getenv("YOUR_API_HASH", "")
YOUR_PHONE = os.getenv("YOUR_PHONE", "")
YOUR_LANGUAGE = os.getenv("YOUR_LANGUAGE", "en")
SESSION_STRING = os.getenv("SESSION_STRING","")

# ===== BOT CREDENTIALS =====
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")

# ===== BUSINESS INFORMATION =====
BUSINESS_INFO = {
    'company_name': os.getenv("COMPANY_NAME", "Lothar Construction GmbH"),
    'business_type': os.getenv("BUSINESS_TYPE", "Bathroom and Kitchen Renovation"),
    'location': os.getenv("LOCATION", "Berlin, Germany"),
    'specialization': os.getenv("SPECIALIZATION", "Bathroom renovation, DIN 18534, Abdichtung, Fliesen, SPC panels"),
}

# ===== BOT BEHAVIOR =====
AI_SETTINGS = {
    'enable_auto_reply': os.getenv("ENABLE_AUTO_REPLY", "False") == "True",
    'confidence_threshold': int(os.getenv("CONFIDENCE_THRESHOLD", "85")),
}

# ===== TRANSLATION SETTINGS =====
TRANSLATION_SETTINGS = {
    'enabled': os.getenv("TRANSLATION_ENABLED", "True") == "True",
    'group_languages': os.getenv("GROUP_LANGUAGES", "pl,de").split(","),
    'use_bot_for_translations': os.getenv("USE_BOT_FOR_TRANSLATIONS", "True") == "True",
}

# ===== DASHBOARD =====
DASHBOARD_URL = os.getenv("DASHBOARD_URL", "http://localhost:5000")

# ===== CONSTRUCTION DOCUMENTATION GROUP =====
BAUDOKU_GROUP_ID = int(os.getenv("BAUDOKU_GROUP_ID", "0"))

# ===== AUTO-REPLY CONTROL =====
ENABLE_AUTO_REPLY = AI_SETTINGS['enable_auto_reply']