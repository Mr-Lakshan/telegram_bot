"""
MODEL CONFIG — Provider-Agnostic Model Configuration
======================================================
All model selections in one place. Change provider/model
without touching any other code.

Caveman mode: compressed prompts = 70% less tokens per call.
"""

# ── Model Configuration ─────────────────────────────────────────────────
# Change these to switch providers/models instantly.
# Supported providers: "openai", "anthropic"

MODEL_CONFIG = {
    # Ultra-cheap classifier — decides if message needs AI + what type
    "classifier": {
        "provider": "openai",
        "model": "gpt-4.1-nano",
        "max_tokens": 150,
        "temperature": 0,
        "fallback_model": "gpt-4o-mini",
    },

    # Simple answers — KB retrieval, template-based
    "simple": {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "max_tokens": 400,
        "temperature": 0.3,
        "fallback_model": "gpt-4o-mini",
    },

    # Complex answers — needs reasoning, context understanding
    "complex": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "max_tokens": 600,
        "temperature": 0.4,
        "fallback_provider": "openai",
        "fallback_model": "gpt-4o",
    },

    # Drive/document analysis — Claude for intelligent file analysis
    "drive_analysis": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "max_tokens": 500,
        "temperature": 0.2,
        "fallback_provider": "openai",
        "fallback_model": "gpt-4o",
    },

    # Image analysis — Claude vision for construction site photos
    "image_analysis": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-6",
        "max_tokens": 400,
        "temperature": 0.3,
        "fallback_provider": "openai",
        "fallback_model": "gpt-4o",
    },

    # Global settings
    "prompt_style": "caveman",
}


# ── Prompt Templates ─────────────────────────────────────────────────────
# Caveman style = ~70% fewer tokens, same accuracy for structured tasks

PROMPTS = {
    "classifier": {
        "caveman": """Construction company Telegram group message classifier.

YOUR JOB: Decide what to do with this message. Be VERY precise.

MESSAGE TYPES:
1. QUESTION asking for customer-specific data (address, phone, name, dates, files)
   → question_type: "dynamic"
2. QUESTION asking for general knowledge (materials, techniques, DIN norms, processes)  
   → question_type: "static"
3. EVERYTHING ELSE (statements, updates, confirmations, greetings, small talk, off-topic)
   → question_type: "not_question"

EXAMPLES:
"Wo wohnt der Kunde?" → dynamic, customer_address, 90
"Wie ist die Telefonnummer?" → dynamic, customer_phone, 90
"Gibt es Dokumente im Drive?" → dynamic, drive_files, 85
"Wann ist Baustart?" → dynamic, construction_date, 85
"Welche Fliesen für Nassbereich?" → static, material, 85
"Was ist DIN 18534?" → static, technique, 80
"Paneele werden Donnerstag geliefert" → not_question, none, 90
"Ich suche einen Ausführenden" → not_question, none, 85
"Material ist angekommen" → not_question, none, 90
"Bin fertig mit dem Bad" → not_question, none, 90
"Ok danke" → not_question, none, 95
"Wie macht man Pizza?" → not_question, none, 95 (offtopic)
"Wie ist das Wetter?" → not_question, none, 95 (offtopic)
"Was gibt es zum Essen?" → not_question, none, 95 (offtopic)
"Wann spielt Bayern?" → not_question, none, 95 (offtopic)
"Hast du Feuer?" → not_question, none, 95 (offtopic)

RULES:
- Statements/updates/confirmations = ALWAYS not_question, even if about construction
- Only EXPLICIT questions requesting info = dynamic or static
- If unsure → not_question with low confidence
- confidence 80+ only for clear cases

Return JSON only:
{"is_question":bool,"is_company_topic":bool,"question_type":"dynamic|static|not_question","intent":"customer_address|customer_phone|customer_name|customer_email|construction_date|construction_status|drive_files|material|technique|schedule|process|other|none","confidence":0-100}""",

        "normal": """You are an AI assistant for a German construction/renovation company (bathroom & kitchen renovation).
Your job is to classify messages from Telegram group chats.

Determine:
1. Is this message a question that needs answering?
2. Is it related to company/construction topics?
3. Is the answer customer-specific (dynamic) or general knowledge (static)?
4. What is the specific intent?

Dynamic intents (answer changes per customer/group):
- customer_address: asking for customer's address
- customer_phone: asking for phone number
- customer_name: asking for customer name
- customer_email: asking for email
- construction_date: asking about construction start date, schedule
- construction_status: asking about project status
- drive_files: asking about documents, photos, images in Drive

Static intents (same answer regardless of customer):
- material: questions about building materials, products
- technique: questions about construction techniques, methods
- schedule: general scheduling questions
- process: company process questions
- other: other company-related questions

Respond ONLY with JSON:
{"is_question": bool, "is_company_topic": bool, "question_type": "dynamic|static|not_question", "intent": "...", "confidence": 0-100}"""
    },
}