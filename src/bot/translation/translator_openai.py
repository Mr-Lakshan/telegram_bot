#!/usr/bin/env python3
"""
OPENAI TRANSLATOR MODULE
Uses GPT-4 for high-quality translations with context awareness
"""

import sqlite3
import json
from openai import OpenAI
from datetime import datetime
from typing import Dict, Optional, List
from bot.config import OPENAI_API_KEY

class OpenAITranslator:
    """
    OpenAI-powered translator with:
    - Smart language detection
    - Context-aware translation
    - Caching for efficiency
    - Technical term preservation
    """
    
    # Supported languages
    LANGUAGES = {
        'hi': 'Hindi',
        'en': 'English',
        'de': 'German',
        'pl': 'Polish',
        'uk': 'Ukrainian',
        'ru': 'Russian',
        'es': 'Spanish',
        'fr': 'French',
        'it': 'Italian',
        'pt': 'Portuguese',
        'nl': 'Dutch',
        'auto': 'Auto-detect'
    }
    
    def __init__(self, openai_api_key: str, db_path: str = 'bot_data.db'):
        """
        Initialize translator
        
        Args:
            openai_api_key: Your OpenAI API key
            db_path: Path to SQLite database
        """
        self.client = OpenAI(api_key=openai_api_key)
        self.db_path = db_path
        
    def get_db_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn
    
    def detect_language(self, text: str) -> Dict[str, str]:
        """
        Detect the language of text using GPT
        
        Returns:
            {'code': 'hi', 'name': 'Hindi', 'confidence': 95}
        """
        
        if not text or len(text.strip()) < 3:
            return {'code': 'en', 'name': 'English', 'confidence': 0}
        
        # Only the first part matters for detection, and a shorter prompt is both
        # cheaper and less likely to be derailed by quoted foreign phrases later
        # in a long message.
        sample = text.strip()[:1200]

        prompt = f"""Detect the MAIN language of this text and return ONLY a JSON object.

The text may mix languages. It often contains quoted phrases, names, button
labels or a sign-off in another language. Judge by the language most of the
SENTENCES are written in, not by individual quoted words.

If the text is genuinely mixed with no clear majority, lower the confidence.

Return format:
{{
  "language_code": "two-letter code (hi/en/de/pl/ru/etc)",
  "language_name": "language name",
  "confidence": 95
}}

Common codes: hi=Hindi, en=English, de=German, pl=Polish, ru=Russian

TEXT:
<<<
{sample}
>>>"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",  # Cheaper for detection
                messages=[
                    {
                        "role": "system",
                        "content": "You are a language detection expert. Return only valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            return {
                'code': result.get('language_code', 'en'),
                'name': result.get('language_name', 'English'),
                'confidence': result.get('confidence', 80)
            }
            
        except Exception as e:
            print(f"❌ Language detection error: {e}")
            return {'code': 'en', 'name': 'English', 'confidence': 0}
    
    def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str = 'auto',
        context: Optional[str] = None,
        preserve_terms: Optional[List[str]] = None
    ) -> Dict[str, str]:
        """
        Translate text using OpenAI GPT
        
        Args:
            text: Text to translate
            target_lang: Target language code (hi/en/de/pl/ru)
            source_lang: Source language (auto for auto-detect)
            context: Additional context for better translation
            preserve_terms: Technical terms to NOT translate
            
        Returns:
            {
                'translated_text': 'translated text',
                'source_lang': 'detected language',
                'target_lang': 'target language',
                'original_text': 'original text'
            }
        """
        
        if not text or not text.strip():
            return {
                'translated_text': text,
                'source_lang': source_lang,
                'target_lang': target_lang,
                'original_text': text
            }
        
        # Check cache first
        cached = self._get_from_cache(text, target_lang)
        if cached:
            print(f"✅ Translation from cache")
            return cached
        
        # Detect source language if needed
        if source_lang == 'auto':
            detected = self.detect_language(text)
            source_lang = detected['code']
            print(f"🔍 Detected language: {detected['name']}")
        
        # If source and target are same, no translation needed
        if source_lang == target_lang:
            return {
                'translated_text': text,
                'source_lang': source_lang,
                'target_lang': target_lang,
                'original_text': text
            }
        
        # Build translation prompt
        target_lang_name = self.LANGUAGES.get(target_lang, target_lang)
        
        prompt = f"""Translate this text to {target_lang_name}.

IMPORTANT RULES:
1. Keep the meaning and tone exactly the same
2. Use natural, conversational language
3. For construction/technical terms, use appropriate technical vocabulary
4. Preserve numbers, dates, and measurements exactly
5. Keep formatting (newlines, spacing)
"""
        
        if preserve_terms:
            prompt += f"\n6. DO NOT translate these terms: {', '.join(preserve_terms)}\n"
        
        if context:
            prompt += f"\nCONTEXT: {context}\n"
        
        prompt += f'\nTEXT TO TRANSLATE:\n"{text}"\n\nReturn ONLY the translated text, nothing else.'
        
        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",  # Better quality for translation
                messages=[
                    {
                        "role": "system",
                        "content": f"You are an expert translator. Translate accurately to {target_lang_name}. Return ONLY the translated text."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                # A translation is roughly the same length as its source. This
                # ceiling is generous enough never to clip a chat message while
                # still bounding a runaway response.
                max_tokens=4000
            )
            
            translated_text = response.choices[0].message.content.strip()
            if not translated_text:
                raise RuntimeError("model returned an empty translation")
            
            # Remove quotes if GPT added them
            if translated_text.startswith('"') and translated_text.endswith('"'):
                translated_text = translated_text[1:-1]
            
            result = {
                'translated_text': translated_text,
                'source_lang': source_lang,
                'target_lang': target_lang,
                'original_text': text
            }
            
            # Save to cache
            self._save_to_cache(text, source_lang, target_lang, translated_text)
            
            print(f"✅ Translated: {source_lang} → {target_lang}")
            
            return result
            
        except Exception as e:
            # The original text is still returned so the caller can fall back to
            # it, but 'failed' makes the failure visible. Previously a failed
            # call looked identical to a successful one, so a message that never
            # got translated was posted as if it had been — with nothing to see
            # except a line on stdout.
            print(f"❌ Translation error ({source_lang}→{target_lang}, "
                  f"{len(text)} chars): {e}")
            return {
                'translated_text': text,
                'source_lang': source_lang,
                'target_lang': target_lang,
                'original_text': text,
                'failed': True,
                'error': str(e)
            }
    
    def translate_for_user(
        self,
        text: str,
        user_id: int,
        source_lang: str = 'auto',
        context: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Translate text to user's preferred language
        
        Args:
            text: Text to translate
            user_id: Telegram user ID
            source_lang: Source language (auto for auto-detect)
            context: Additional context
            
        Returns:
            Translation result
        """
        
        # Get user's preferred language
        user_lang = self.get_user_language(user_id)
        
        return self.translate(
            text=text,
            target_lang=user_lang,
            source_lang=source_lang,
            context=context
        )
    
    def get_user_language(self, user_id: int) -> str:
        """Get user's preferred language from database"""
        try:
            conn = self.get_db_connection()
            c = conn.cursor()
            c.execute("SELECT language FROM user_languages WHERE user_id = ?", (user_id,))
            result = c.fetchone()
            conn.close()
            
            return result[0] if result else 'en'
            
        except Exception as e:
            print(f"❌ Error getting user language: {e}")
            return 'en'
    
    def set_user_language(self, user_id: int, language: str, language_name: str = None):
        """Set user's preferred language"""
        try:
            if language_name is None:
                language_name = self.LANGUAGES.get(language, language)
            
            conn = self.get_db_connection()
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO user_languages 
                (user_id, language, language_name, updated_at)
                VALUES (?, ?, ?, ?)
            ''', (user_id, language, language_name, datetime.now()))
            conn.commit()
            conn.close()
            
            print(f"✅ Language set for user {user_id}: {language_name}")
            return True
            
        except Exception as e:
            print(f"❌ Error setting user language: {e}")
            return False
    
    def _get_from_cache(self, text: str, target_lang: str) -> Optional[Dict]:
        """Get translation from cache if exists"""
        try:
            conn = self.get_db_connection()
            c = conn.cursor()
            c.execute('''
                SELECT translated_text, source_lang
                FROM translation_cache
                WHERE original_text = ? AND target_lang = ?
                ORDER BY created_at DESC
                LIMIT 1
            ''', (text, target_lang))
            result = c.fetchone()
            conn.close()
            
            if result:
                return {
                    'translated_text': result[0],
                    'source_lang': result[1],
                    'target_lang': target_lang,
                    'original_text': text,
                    'from_cache': True
                }
            return None
            
        except Exception as e:
            print(f"❌ Cache read error: {e}")
            return None
    
    def _save_to_cache(self, original: str, source_lang: str, target_lang: str, translated: str):
        """Save translation to cache"""
        try:
            conn = self.get_db_connection()
            c = conn.cursor()
            c.execute('''
                INSERT INTO translation_cache 
                (original_text, source_lang, target_lang, translated_text, created_at)
                VALUES (?, ?, ?, ?, ?)
            ''', (original, source_lang, target_lang, translated, datetime.now()))
            conn.commit()
            conn.close()
            
        except Exception as e:
            print(f"❌ Cache write error: {e}")
    
    def translate_group_message(
        self,
        text: str,
        sender_id: int,
        group_members: List[int],
        context: Optional[str] = None
    ) -> Dict[int, str]:
        """
        Translate one message for all group members
        
        Args:
            text: Message text
            sender_id: Who sent it
            group_members: List of user IDs in group
            context: Optional context
            
        Returns:
            {user_id: translated_text, ...}
        """
        
        # Detect source language
        detected = self.detect_language(text)
        source_lang = detected['code']
        
        translations = {}
        
        for user_id in group_members:
            # Skip sender (they see original)
            if user_id == sender_id:
                translations[user_id] = text
                continue
            
            # Get user's language
            user_lang = self.get_user_language(user_id)
            
            # Translate
            result = self.translate(
                text=text,
                target_lang=user_lang,
                source_lang=source_lang,
                context=context
            )
            
            translations[user_id] = result['translated_text']
        
        return translations


# Example usage and testing
if __name__ == "__main__":
    import os
    
    # Get API key from environment
    API_KEY = OPENAI_API_KEY
    
    if not API_KEY:
        print("❌ Please set OPENAI_API_KEY environment variable")
        print("   export OPENAI_API_KEY='your-key-here'")
        exit(1)
    
    print("\n" + "="*70)
    print("🌍 OPENAI TRANSLATOR TEST")
    print("="*70 + "\n")
    
    translator = OpenAITranslator(API_KEY)
    
    # Test 1: Language detection
    print("📝 Test 1: Language Detection")
    test_texts = [
        "Hello, how are you?",
        "Привет, как дела?",
        "Gdzie jest szkło?",
        "नमस्ते, कैसे हो?",
        "Wo ist das Glas?"
    ]
    
    for text in test_texts:
        detected = translator.detect_language(text)
        print(f"   '{text}' → {detected['name']} ({detected['code']}) - {detected['confidence']}%")
    
    # Test 2: Simple translation
    print("\n📝 Test 2: Translation")
    result = translator.translate(
        text="Where is the glass wall?",
        target_lang="hi",
        source_lang="en"
    )
    print(f"   Original: {result['original_text']}")
    print(f"   Translated: {result['translated_text']}")
    print(f"   {result['source_lang']} → {result['target_lang']}")
    
    # Test 3: Context-aware translation
    print("\n📝 Test 3: Context-Aware Translation")
    result = translator.translate(
        text="The shower installation is delayed",
        target_lang="de",
        context="Construction project - bathroom renovation"
    )
    print(f"   Original: {result['original_text']}")
    print(f"   Translated: {result['translated_text']}")
    
    # Test 4: Set user language
    print("\n📝 Test 4: User Language Preference")
    translator.set_user_language(123456789, 'hi', 'Hindi')
    translator.set_user_language(987654321, 'de', 'German')
    
    lang1 = translator.get_user_language(123456789)
    lang2 = translator.get_user_language(987654321)
    
    print(f"   User 123456789 → {lang1}")
    print(f"   User 987654321 → {lang2}")
    
    print("\n" + "="*70)
    print("✅ TRANSLATOR TESTS COMPLETE!")
    print("="*70 + "\n")