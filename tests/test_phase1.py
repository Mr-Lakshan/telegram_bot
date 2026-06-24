#!/usr/bin/env python3
"""
PHASE 1 TEST SCRIPT — Run on VPS without starting the bot
============================================================
Tests:
  1. Pre-filter (local, no API needed)
  2. Classifier (needs OpenAI API key)
  3. Full pipeline simulation

Usage:
  cd /path/to/telegram
  python3 test_phase1.py              # Pre-filter only (no API key needed)
  python3 test_phase1.py --full       # Full test including classifier (needs API key)
"""

import sys
import os
import time

# ── Load .env manually (no dotenv dependency needed) ──
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, val = line.split('=', 1)
                os.environ.setdefault(key.strip(), val.strip())

from bot.ai.message_prefilter import MessagePreFilter
from bot.core.model_config import MODEL_CONFIG, PROMPTS


def test_prefilter():
    """Test 1: Pre-filter (zero cost, no API)"""
    print("\n" + "="*60)
    print("TEST 1: MESSAGE PRE-FILTER (zero cost)")
    print("="*60)

    pf = MessagePreFilter()

    tests = [
        # (text, expected, description)
        # ── Should SKIP (False) ──
        ("", False, "Empty message"),
        ("ok", False, "Smalltalk: ok"),
        ("Danke", False, "Smalltalk: Danke"),
        ("👍", False, "Emoji only"),
        ("🔨🏗️", False, "Emoji only (construction)"),
        ("ja", False, "Smalltalk: ja"),
        ("Guten Morgen", False, "Greeting: Guten Morgen"),
        ("Hallo", False, "Greeting: Hallo"),
        ("Tschüss", False, "Greeting: Tschüss"),
        ("hi", False, "Too short"),
        ("ok", False, "Smalltalk"),
        ("/start", False, "Bot command"),
        ("/help", False, "Bot command"),
        ("Dobranoc", False, "Polish greeting"),
        ("theek hai", False, "Hindi smalltalk"),
        ("super", False, "Smalltalk: super"),
        ("perfekt", False, "Smalltalk: perfekt"),
        ("alles klar", False, "Smalltalk: alles klar"),
        ("got it", False, "Smalltalk: got it"),
        ("dzięki", False, "Polish thanks"),
        ("Bis morgen", False, "Greeting: Bis morgen"),
        ("Gute Nacht", False, "Greeting: Gute Nacht"),

        # ── Should PASS (True) ──
        ("Wo wohnt der Kunde?", True, "Question: address"),
        ("Adresse?", True, "Question: short address"),
        ("Welche Fliesen nehmen wir für Nassbereich?", True, "Question: material"),
        ("Wie ist die Telefonnummer?", True, "Question: phone"),
        ("bin fertig mit Bad", True, "Status update (passes to classifier)"),
        ("Material ist angekommen", True, "Info (passes to classifier)"),
        ("Wann geht es bei Müller los?", True, "Question: schedule"),
        ("Gibt es Fotos im Drive?", True, "Question: drive files"),
        ("kya address hai customer ka?", True, "Hindi question"),
        ("Guten Morgen zusammen, wie ist die Adresse?", True, "Greeting + question (long)"),
        ("Können wir morgen früher anfangen?", True, "Question: schedule"),
        ("DIN 18534 welche Abdichtung?", True, "Technical question"),
        ("Brauchen wir noch Silikon?", True, "Material question"),
    ]

    passed = 0
    failed = 0

    for text, expected, desc in tests:
        result = pf.should_process(text, sender_id=123, chat_id=456)
        status = "✅" if result == expected else "❌"
        if result != expected:
            failed += 1
            print(f"  {status} FAIL: \"{text}\" → got {result}, expected {expected} ({desc})")
        else:
            passed += 1
            print(f"  {status} \"{text}\" → {'PROCESS' if result else 'SKIP'} ({desc})")

    stats = pf.get_stats()
    print(f"\n  Results: {passed}/{passed+failed} passed")
    print(f"  Filter rate: {stats['filter_rate']}%")
    print(f"  Breakdown: {stats}")

    if failed > 0:
        print(f"\n  ⚠️  {failed} tests FAILED — check logic")
    else:
        print(f"\n  ✅ All tests passed!")

    return failed == 0


def test_duplicate_filter():
    """Test 1b: Duplicate detection"""
    print("\n" + "="*60)
    print("TEST 1b: DUPLICATE DETECTION")
    print("="*60)

    pf = MessagePreFilter(duplicate_cooldown=5)  # 5 second cooldown for test

    # First message should pass
    r1 = pf.should_process("Wann starten wir?", sender_id=100, chat_id=200)
    print(f"  {'✅' if r1 else '❌'} First message: {'PROCESS' if r1 else 'SKIP'} (expected: PROCESS)")

    # Same message within cooldown should be filtered
    r2 = pf.should_process("Wann starten wir?", sender_id=100, chat_id=200)
    print(f"  {'✅' if not r2 else '❌'} Duplicate (within 5s): {'PROCESS' if r2 else 'SKIP'} (expected: SKIP)")

    # Same message from DIFFERENT sender should pass
    r3 = pf.should_process("Wann starten wir?", sender_id=101, chat_id=200)
    print(f"  {'✅' if r3 else '❌'} Same text, different sender: {'PROCESS' if r3 else 'SKIP'} (expected: PROCESS)")

    all_ok = r1 and not r2 and r3
    print(f"\n  {'✅ All duplicate tests passed!' if all_ok else '❌ Some tests failed'}")
    return all_ok


def test_classifier():
    """Test 2: Question Classifier (needs OpenAI API key)"""
    print("\n" + "="*60)
    print("TEST 2: QUESTION CLASSIFIER (OpenAI API call)")
    print("="*60)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  ⚠️  OPENAI_API_KEY not set — skipping classifier test")
        print("  Set it in .env or: export OPENAI_API_KEY=sk-...")
        return True  # Don't count as failure

    from bot.ai.question_classifier import QuestionClassifier
    qc = QuestionClassifier(openai_api_key=api_key)

    tests = [
        # (message, sender, group, expected_route, description)
        ("Wo wohnt der Kunde?", "Worker1", "Baustart Müller", "dynamic_handler", "Address question → dynamic"),
        ("Wie ist die Telefonnummer?", "Worker1", "Baustart Müller", "dynamic_handler", "Phone question → dynamic"),
        ("Welche Fliesen für Nassbereich?", "Worker1", "Baustart Müller", "static_handler", "Material question → static"),
        ("bin fertig mit Bad", "Worker1", "Baustart Müller", "skip", "Status update → skip"),
        ("Wie bereitet man Chicken Curry?", "Worker1", "Baustart Müller", "skip", "Off-topic → skip"),
        ("Gibt es Fotos im Drive?", "Worker1", "Baustart Müller", "dynamic_handler", "Drive files → dynamic"),
        ("Wann ist Baustart bei Müller?", "Worker1", "Baustart Müller", "dynamic_handler", "Date question → dynamic"),
    ]

    passed = 0
    failed = 0

    for msg, sender, group, expected_route, desc in tests:
        print(f"\n  Testing: \"{msg}\"")
        result = qc.classify(
            message=msg,
            sender_name=sender,
            chat_title=group,
        )

        route = result.get('route_to', 'skip')
        intent = result.get('intent', 'none')
        conf = result.get('confidence', 0)
        model = result.get('model_used', '?')

        is_ok = route == expected_route
        status = "✅" if is_ok else "❌"

        if is_ok:
            passed += 1
        else:
            failed += 1

        print(f"  {status} route={route} (expected={expected_route}) | intent={intent} | conf={conf}% | model={model}")
        if not is_ok:
            print(f"       Full result: {result}")

        time.sleep(0.5)  # Rate limit friendly

    print(f"\n  Results: {passed}/{passed+failed} passed")
    stats = qc.get_stats()
    print(f"  Stats: {stats}")

    if failed > 0:
        print(f"\n  ⚠️  {failed} tests didn't match expected route")
        print(f"  (This is OK if the classification is reasonable — AI may interpret differently)")
    else:
        print(f"\n  ✅ All classifier tests passed!")

    return True  # Don't hard-fail on classifier since AI is probabilistic


def test_full_pipeline():
    """Test 3: Full pipeline simulation"""
    print("\n" + "="*60)
    print("TEST 3: FULL PIPELINE SIMULATION")
    print("="*60)

    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        print("  ⚠️  OPENAI_API_KEY not set — skipping pipeline test")
        return True

    from bot.ai.question_classifier import QuestionClassifier

    pf = MessagePreFilter()
    qc = QuestionClassifier(openai_api_key=api_key)

    messages = [
        # Simulated message stream in a construction group
        {"text": "Guten Morgen", "sender": "Worker1", "sender_id": 101},
        {"text": "Morgen", "sender": "Worker2", "sender_id": 102},
        {"text": "👍", "sender": "Worker1", "sender_id": 101},
        {"text": "Bin auf dem Weg zur Baustelle", "sender": "Worker1", "sender_id": 101},
        {"text": "ok", "sender": "Boss", "sender_id": 103},
        {"text": "Wo wohnt der Kunde nochmal?", "sender": "Worker2", "sender_id": 102},
        {"text": "Welche Fliesen nehmen wir?", "sender": "Worker1", "sender_id": 101},
        {"text": "Danke für die Info", "sender": "Worker2", "sender_id": 102},
        {"text": "Wie macht man Pizza?", "sender": "Worker1", "sender_id": 101},
        {"text": "Gibt es Fotos vom Bad im Drive?", "sender": "Worker2", "sender_id": 102},
    ]

    chat_id = 12345
    chat_title = "Baustart Müller 15.5.2026"

    print(f"\n  Simulating {len(messages)} messages in [{chat_title}]...\n")

    ai_calls = 0
    skipped_prefilter = 0
    skipped_classifier = 0
    dynamic = 0
    static = 0

    for msg in messages:
        text = msg["text"]
        sender = msg["sender"]
        sender_id = msg["sender_id"]

        print(f"  💬 {sender}: \"{text}\"")

        # Step 1: Pre-filter
        if not pf.should_process(text, sender_id, chat_id, is_group=True):
            print(f"     → ⏭️ Pre-filter: SKIP (no AI call)")
            skipped_prefilter += 1
            continue

        # Step 2: Classifier
        result = qc.classify(
            message=text,
            sender_name=sender,
            chat_title=chat_title,
        )
        ai_calls += 1
        route = result.get('route_to', 'skip')
        intent = result.get('intent', 'none')
        conf = result.get('confidence', 0)

        if route == 'skip':
            print(f"     → 🧠 Classifier: SKIP ({intent}, conf={conf}%)")
            skipped_classifier += 1
        elif route == 'dynamic_handler':
            print(f"     → 📋 DYNAMIC: {intent} (conf={conf}%) → would fetch from CRM/Drive")
            dynamic += 1
        elif route == 'static_handler':
            print(f"     → 📚 STATIC: {intent} (conf={conf}%) → would need AI answer + approval")
            static += 1

        time.sleep(0.5)

    total = len(messages)
    print(f"\n  {'='*50}")
    print(f"  PIPELINE RESULTS:")
    print(f"  {'='*50}")
    print(f"  Total messages:        {total}")
    print(f"  Pre-filter skipped:    {skipped_prefilter} ({round(skipped_prefilter/total*100)}%)")
    print(f"  Classifier calls:      {ai_calls} (cheap model)")
    print(f"  Classifier skipped:    {skipped_classifier}")
    print(f"  Dynamic (CRM/Drive):   {dynamic}")
    print(f"  Static (needs AI):     {static}")
    print(f"  Token saving estimate: ~{round((skipped_prefilter + skipped_classifier) / total * 100)}% messages need NO expensive AI")
    print(f"\n  Pre-filter stats: {pf.get_stats()}")
    print(f"  Classifier stats: {qc.get_stats()}")


def test_model_config():
    """Test config file is valid"""
    print("\n" + "="*60)
    print("TEST: MODEL CONFIG VALIDATION")
    print("="*60)

    required_roles = ['classifier', 'simple', 'complex', 'drive_analysis']
    all_ok = True

    for role in required_roles:
        if role not in MODEL_CONFIG:
            print(f"  ❌ Missing role: {role}")
            all_ok = False
        else:
            cfg = MODEL_CONFIG[role]
            model = cfg.get('model', '?')
            provider = cfg.get('provider', '?')
            fallback = cfg.get('fallback_model', 'none')
            print(f"  ✅ {role}: {provider}/{model} (fallback: {fallback})")

    style = MODEL_CONFIG.get('prompt_style', 'unknown')
    print(f"  ✅ Prompt style: {style}")

    if 'classifier' in PROMPTS:
        caveman_len = len(PROMPTS['classifier'].get('caveman', '').split())
        normal_len = len(PROMPTS['classifier'].get('normal', '').split())
        saving = round((1 - caveman_len/max(normal_len, 1)) * 100)
        print(f"  ✅ Classifier prompts: caveman={caveman_len} words, normal={normal_len} words ({saving}% saving)")

    if all_ok:
        print(f"\n  ✅ Config valid!")
    return all_ok


# ══════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    full_test = "--full" in sys.argv

    print("╔══════════════════════════════════════════════════╗")
    print("║       PHASE 1 TEST — Smart Filter System        ║")
    print("╚══════════════════════════════════════════════════╝")

    if not full_test:
        print("\nRunning LOCAL tests only (no API key needed)")
        print("For full test with classifier: python3 test_phase1.py --full\n")

    # Always run these (no API needed)
    test_model_config()
    ok1 = test_prefilter()
    ok2 = test_duplicate_filter()

    if full_test:
        # These need OpenAI API key
        test_classifier()
        test_full_pipeline()

    print("\n" + "="*60)
    if ok1 and ok2:
        print("✅ Phase 1 local tests PASSED")
    else:
        print("❌ Some tests FAILED — check above")

    if not full_test:
        print("\n💡 Run with --full for classifier + pipeline tests:")
        print("   python3 test_phase1.py --full")
    print("="*60)
