#!/usr/bin/env python3
"""
TRANSLATION SYSTEM TEST
Test all translation features before integration
"""

import os
import sys
from bot.translation.translator_openai import OpenAITranslator
from bot.config import OPENAI_API_KEY
def test_translation_system():
    """Run comprehensive translation tests"""
    
    print("\n" + "="*70)
    print("🧪 TRANSLATION SYSTEM - COMPREHENSIVE TEST")
    print("="*70 + "\n")
    
    # Check API key
    API_KEY = OPENAI_API_KEY
    if not API_KEY:
        print("❌ ERROR: OPENAI_API_KEY not set!")
        print("\n💡 Set it like this:")
        print("   export OPENAI_API_KEY='sk-your-key-here'")
        print("\n   Or add to your .bashrc/.zshrc:")
        print("   echo 'export OPENAI_API_KEY=\"sk-your-key-here\"' >> ~/.bashrc")
        return False
    
    print(f"✅ API Key found: {API_KEY[:20]}...")
    
    # Initialize translator
    print("\n📚 Initializing OpenAI Translator...")
    translator = OpenAITranslator(API_KEY)
    print("✅ Translator initialized\n")
    
    # ===== TEST 1: Language Detection =====
    print("="*70)
    print("TEST 1: LANGUAGE DETECTION")
    print("="*70 + "\n")
    
    test_phrases = [
        ("Hello, how are you?", "English"),
        ("Привет, как дела?", "Russian"),
        ("Gdzie jest materiał?", "Polish"),
        ("नमस्ते, कैसे हैं आप?", "Hindi"),
        ("Wo ist das Glas?", "German"),
        ("¿Dónde está el vidrio?", "Spanish")
    ]
    
    detection_pass = 0
    for phrase, expected in test_phrases:
        result = translator.detect_language(phrase)
        status = "✅" if expected.lower() in result['name'].lower() else "❌"
        print(f"{status} '{phrase}'")
        print(f"   → Detected: {result['name']} ({result['code']}) - Confidence: {result['confidence']}%")
        print(f"   → Expected: {expected}\n")
        if status == "✅":
            detection_pass += 1
    
    print(f"📊 Detection Score: {detection_pass}/{len(test_phrases)}\n")
    
    # ===== TEST 2: Basic Translation =====
    print("="*70)
    print("TEST 2: BASIC TRANSLATION")
    print("="*70 + "\n")
    
    translations = [
        ("Where is the glass?", "en", "hi"),
        ("The installation is complete", "en", "de"),
        ("Kiedy przyjdzie materiał?", "pl", "en"),
        ("Душ работает хорошо", "ru", "en")
    ]
    
    translation_pass = 0
    for text, source, target in translations:
        print(f"📝 Translating: '{text}'")
        print(f"   {source} → {target}")
        
        result = translator.translate(text, target, source)
        
        if 'error' not in result:
            print(f"   ✅ Result: {result['translated_text']}\n")
            translation_pass += 1
        else:
            print(f"   ❌ Error: {result.get('error', 'Unknown error')}\n")
    
    print(f"📊 Translation Score: {translation_pass}/{len(translations)}\n")
    
    # ===== TEST 3: Context-Aware Translation =====
    print("="*70)
    print("TEST 3: CONTEXT-AWARE TRANSLATION")
    print("="*70 + "\n")
    
    context_tests = [
        {
            'text': "The shower is leaking",
            'target': 'de',
            'context': "Construction project - bathroom renovation",
            'description': "Technical construction term"
        },
        {
            'text': "Customer is not happy with the finish",
            'target': 'pl',
            'context': "Customer complaint about wall panels",
            'description': "Customer service context"
        }
    ]
    
    context_pass = 0
    for test in context_tests:
        print(f"📝 {test['description']}")
        print(f"   Text: '{test['text']}'")
        print(f"   Context: {test['context']}")
        
        result = translator.translate(
            test['text'],
            test['target'],
            context=test['context']
        )
        
        if 'error' not in result:
            print(f"   ✅ Translation: {result['translated_text']}\n")
            context_pass += 1
        else:
            print(f"   ❌ Error: {result.get('error')}\n")
    
    print(f"📊 Context Translation Score: {context_pass}/{len(context_tests)}\n")
    
    # ===== TEST 4: User Language Preferences =====
    print("="*70)
    print("TEST 4: USER LANGUAGE PREFERENCES")
    print("="*70 + "\n")
    
    test_users = [
        (111111111, 'hi', 'Hindi'),
        (222222222, 'de', 'German'),
        (333333333, 'pl', 'Polish'),
        (444444444, 'ru', 'Russian'),
        (555555555, 'en', 'English')
    ]
    
    preference_pass = 0
    print("📝 Setting language preferences...")
    for user_id, lang_code, lang_name in test_users:
        success = translator.set_user_language(user_id, lang_code, lang_name)
        if success:
            saved_lang = translator.get_user_language(user_id)
            if saved_lang == lang_code:
                print(f"   ✅ User {user_id}: {lang_name} ({lang_code})")
                preference_pass += 1
            else:
                print(f"   ❌ User {user_id}: Save/retrieve mismatch")
        else:
            print(f"   ❌ User {user_id}: Failed to set language")
    
    print(f"\n📊 Preference Score: {preference_pass}/{len(test_users)}\n")
    
    # ===== TEST 5: Translation Cache =====
    print("="*70)
    print("TEST 5: TRANSLATION CACHE")
    print("="*70 + "\n")
    
    print("📝 First translation (will be cached)...")
    result1 = translator.translate("Hello world", "hi")
    from_cache_1 = result1.get('from_cache', False)
    print(f"   Result: {result1['translated_text']}")
    print(f"   From cache: {from_cache_1}")
    
    print("\n📝 Second translation (should be from cache)...")
    result2 = translator.translate("Hello world", "hi")
    from_cache_2 = result2.get('from_cache', False)
    print(f"   Result: {result2['translated_text']}")
    print(f"   From cache: {from_cache_2}")
    
    cache_pass = 1 if from_cache_2 else 0
    print(f"\n📊 Cache Score: {cache_pass}/1 {'✅' if cache_pass else '❌'}\n")
    
    # ===== TEST 6: Group Translation =====
    print("="*70)
    print("TEST 6: GROUP TRANSLATION")
    print("="*70 + "\n")
    
    # Set up group members with different languages
    group_members = [111111111, 222222222, 333333333]  # Hindi, German, Polish
    sender_id = 111111111
    message = "The glass will arrive tomorrow at 10 AM"
    
    print(f"📝 Translating group message:")
    print(f"   Sender: {sender_id} (Hindi)")
    print(f"   Message: '{message}'")
    print(f"   Recipients: {len(group_members)} members\n")
    
    translations = translator.translate_group_message(
        text=message,
        sender_id=sender_id,
        group_members=group_members
    )
    
    group_pass = 0
    for user_id, translated in translations.items():
        user_lang = translator.get_user_language(user_id)
        lang_name = translator.LANGUAGES.get(user_lang, user_lang)
        print(f"   👤 User {user_id} ({lang_name}):")
        print(f"      → {translated}\n")
        if translated:
            group_pass += 1
    
    print(f"📊 Group Translation Score: {group_pass}/{len(group_members)}\n")
    
    # ===== FINAL SCORE =====
    print("="*70)
    print("📊 FINAL TEST RESULTS")
    print("="*70 + "\n")
    
    total_tests = 6
    total_pass = sum([
        1 if detection_pass >= len(test_phrases) * 0.8 else 0,
        1 if translation_pass >= len(translations) * 0.8 else 0,
        1 if context_pass >= len(context_tests) * 0.8 else 0,
        1 if preference_pass == len(test_users) else 0,
        cache_pass,
        1 if group_pass == len(group_members) else 0
    ])
    
    print(f"✅ Detection Test:       {detection_pass}/{len(test_phrases)}")
    print(f"✅ Translation Test:     {translation_pass}/{len(translations)}")
    print(f"✅ Context Test:         {context_pass}/{len(context_tests)}")
    print(f"✅ Preferences Test:     {preference_pass}/{len(test_users)}")
    print(f"✅ Cache Test:           {cache_pass}/1")
    print(f"✅ Group Translation:    {group_pass}/{len(group_members)}")
    
    print(f"\n🎯 OVERALL SCORE: {total_pass}/{total_tests} tests passed")
    
    if total_pass == total_tests:
        print("\n🎉 ALL TESTS PASSED! System ready for integration! ✅")
        return True
    elif total_pass >= total_tests * 0.7:
        print("\n⚠️  Most tests passed. Minor issues to fix.")
        return True
    else:
        print("\n❌ Multiple tests failed. Please debug before integration.")
        return False

if __name__ == "__main__":
    print("\n🚀 Starting Translation System Tests...\n")
    
    try:
        success = test_translation_system()
        
        if success:
            print("\n" + "="*70)
            print("✅ TESTING COMPLETE - SYSTEM READY!")
            print("="*70)
            print("\n📋 Next Steps:")
            print("   1. ✅ Translation module tested")
            print("   2. 📝 Update telegram_bot_groups.py")
            print("   3. 🌐 Update dashboard_groups.py")
            print("   4. 🚀 Start your bot and test!\n")
            sys.exit(0)
        else:
            print("\n⚠️  Please fix the issues and re-run tests\n")
            sys.exit(1)
            
    except KeyboardInterrupt:
        print("\n\n⚠️  Tests interrupted by user\n")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)