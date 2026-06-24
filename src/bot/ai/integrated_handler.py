"""
INTEGRATED MESSAGE HANDLER - PHASE 1
Combines classification, database context, and reply generation
This is what will integrate with your telegram_bot.py
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional
from bot.ai.message_classifier import MessageClassifier
from bot.core.database_simulator import MockDatabase
from bot.ai.smart_reply_generator import SmartReplyGenerator


class IntegratedMessageHandler:
    """
    Main handler that orchestrates the entire process:
    1. Message classification
    2. Database context gathering
    3. Reply generation
    4. Decision on action (auto-send vs queue vs escalate)
    """
    
    def __init__(
        self, 
        openai_api_key: str,
        business_info: Dict,
        db_path: str = "bot_data.db",
        enable_auto_reply: bool = False  # 🔴 AUTO-REPLY DISABLED BY DEFAULT
    ):
        """
        Initialize the integrated handler
        
        Args:
            openai_api_key: Your OpenAI API key
            business_info: Company information dict
            db_path: Path to your existing SQLite database
            enable_auto_reply: Enable automatic replies (default: False)
        """
        self.classifier = MessageClassifier(openai_api_key)
        self.db_simulator = MockDatabase()  # Will be replaced with real DB
        self.reply_generator = SmartReplyGenerator(openai_api_key, business_info)
        self.db_path = db_path
        
        # 🔴 AUTO-REPLY TOGGLE - Change this to True when ready
        self.ENABLE_AUTO_REPLY = enable_auto_reply
        
        # Action thresholds (only used if auto-reply enabled)
        self.AUTO_SEND_THRESHOLD = 85
        self.QUEUE_APPROVAL_THRESHOLD = 60
        
        # Message types that should always be queued
        self.ALWAYS_QUEUE = [
            'decision_required',
            'customer_complaint'
        ]
        
        # Message types safe for auto-send
        self.SAFE_AUTO_SEND = [
            'acknowledgment',
            'status_update'
        ]
        
        # Log mode
        if self.ENABLE_AUTO_REPLY:
            print("⚡ AUTO-REPLY MODE: ENABLED")
        else:
            print("🛡️  SAFE MODE: All messages require approval (auto-reply disabled)")
    
    def process_message(
        self,
        message: str,
        sender_name: str,
        sender_role: str = "worker",
        context_messages: Optional[List[Dict]] = None,
        sender_language: str = "German"
    ) -> Dict:
        """
        Process a complete message through the entire pipeline
        
        Args:
            message: The incoming message text
            sender_name: Who sent the message
            sender_role: Their role (worker, coordinator, manager, boss)
            context_messages: Recent conversation context
            sender_language: Language to reply in
            
        Returns:
            Complete analysis with suggested action
        """
        
        print(f"\n{'='*70}")
        print(f"🔄 PROCESSING MESSAGE")
        print(f"{'='*70}")
        print(f"📨 From: {sender_name} ({sender_role})")
        print(f"💬 Message: {message[:100]}...")
        
        # STEP 1: Classify the message
        print("\n📊 STEP 1: Classifying message...")
        classification = self.classifier.classify(
            message=message,
            sender_name=sender_name,
            sender_role=sender_role,
            context_messages=context_messages
        )
        
        print(f"   ✅ Type: {classification['message_type']}")
        print(f"   ⚡ Urgency: {classification['urgency']}")
        print(f"   📈 Confidence: {classification['confidence']}%")
        print(f"   🎯 Intent: {classification['intent']}")
        
        # STEP 2: Gather database context
        print("\n🗄️  STEP 2: Querying database context...")
        database_context = self._get_database_context(classification)
        
        if database_context:
            print(f"   ✅ Found: {', '.join(database_context.keys())}")
        else:
            print(f"   ⚠️  No specific data found")
        
        # STEP 3: Get past corrections for learning
        print("\n📚 STEP 3: Retrieving learning examples...")
        past_corrections = self._get_past_corrections(
            msg_type=classification['message_type'],
            language=sender_language
        )
        
        if past_corrections:
            print(f"   ✅ Found {len(past_corrections)} similar corrections")
        else:
            print(f"   ℹ️  No past corrections available yet")
        
        # STEP 4: Generate reply
        print("\n💬 STEP 4: Generating smart reply...")
        reply_data = self.reply_generator.generate_reply(
            classification=classification,
            database_context=database_context,
            past_corrections=past_corrections,
            sender_language=sender_language
        )
        
        print(f"   ✅ Reply generated")
        print(f"   📊 Confidence: {reply_data['confidence']}%")
        print(f"   🎬 Suggested: {reply_data['action']}")
        
        # STEP 5: Make final decision
        print("\n🎯 STEP 5: Making action decision...")
        final_action = self._decide_action(
            classification=classification,
            reply_data=reply_data
        )
        
        print(f"   ✅ Final decision: {final_action['action'].upper()}")
        if final_action.get('escalate_to'):
            print(f"   👤 Escalate to: {final_action['escalate_to']}")
        
        # Compile complete result
        result = {
            'timestamp': datetime.now().isoformat(),
            'input': {
                'message': message,
                'sender_name': sender_name,
                'sender_role': sender_role,
                'language': sender_language
            },
            'classification': classification,
            'database_context': database_context,
            'reply': reply_data,
            'final_decision': final_action,
            'processing_steps': {
                'step1_classify': 'complete',
                'step2_database': 'complete',
                'step3_learning': 'complete',
                'step4_reply': 'complete',
                'step5_decision': 'complete'
            }
        }
        
        print(f"\n{'='*70}")
        print(f"✅ PROCESSING COMPLETE")
        print(f"{'='*70}\n")
        
        return result
    
    def _get_database_context(self, classification: Dict) -> Dict:
        """
        Get relevant context from database based on classification
        In Phase 2, this will query your real database
        """
        entities = classification.get('entities', {})
        
        # Use mock database for now
        context = self.db_simulator.query_context(entities)
        
        # TODO Phase 2: Replace with real database queries
        # context = self._query_real_database(entities)
        
        return context
    
    def _get_past_corrections(
        self,
        msg_type: str,
        language: str,
        limit: int = 5
    ) -> List[Dict]:
        """
        Get past corrections for learning
        Queries your existing message_corrections table
        """
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            c = conn.cursor()
            
            # Query similar past corrections
            c.execute("""
                SELECT incoming_message, ai_suggestion, your_edit, language
                FROM message_corrections
                WHERE language = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (language, limit))
            
            rows = c.fetchall()
            conn.close()
            
            corrections = []
            for row in rows:
                corrections.append({
                    'incoming_msg': row[0],
                    'ai_suggestion': row[1],
                    'your_edit': row[2],
                    'language': row[3]
                })
            
            return corrections
            
        except Exception as e:
            print(f"   ⚠️  Could not load corrections: {e}")
            return []
    
    def _decide_action(
        self,
        classification: Dict,
        reply_data: Dict
    ) -> Dict:
        """
        Make final decision on what action to take
        Based on confidence, message type, and urgency
        
        🔴 SAFE MODE: If auto-reply disabled, ALL messages queue for approval
        """
        
        msg_type = classification['message_type']
        urgency = classification['urgency']
        confidence = reply_data['confidence']
        suggested_action = reply_data.get('action', 'queue_approval')
        escalate_to = reply_data.get('escalate_to')
        
        # 🔴 SAFE MODE CHECK - If auto-reply disabled, queue everything
        if not self.ENABLE_AUTO_REPLY:
            return {
                'action': 'queue_approval',
                'reason': 'Auto-reply is disabled - all messages require approval',
                'escalate_to': escalate_to,
                'confidence': confidence,
                'note': 'Enable auto-reply in config to allow automatic sending'
            }
        
        # ════════════════════════════════════════════════════════════
        # BELOW CODE ONLY RUNS WHEN AUTO-REPLY IS ENABLED
        # ════════════════════════════════════════════════════════════
        
        # Always queue these types
        if msg_type in self.ALWAYS_QUEUE:
            return {
                'action': 'queue_approval',
                'reason': f'{msg_type} always requires human review',
                'escalate_to': escalate_to,
                'confidence': confidence
            }
        
        # Escalation requested by AI
        if suggested_action == 'escalate' or escalate_to:
            return {
                'action': 'escalate',
                'reason': 'AI determined escalation needed',
                'escalate_to': escalate_to or 'manager',
                'confidence': confidence
            }
        
        # High confidence + safe type = auto-send
        if confidence >= self.AUTO_SEND_THRESHOLD and msg_type in self.SAFE_AUTO_SEND:
            return {
                'action': 'auto_send',
                'reason': f'High confidence ({confidence}%) + safe message type',
                'escalate_to': None,
                'confidence': confidence
            }
        
        # Medium-high confidence for other types = auto-send
        if confidence >= 90:
            return {
                'action': 'auto_send',
                'reason': f'Very high confidence ({confidence}%)',
                'escalate_to': None,
                'confidence': confidence
            }
        
        # Decent confidence = queue for approval
        if confidence >= self.QUEUE_APPROVAL_THRESHOLD:
            return {
                'action': 'queue_approval',
                'reason': f'Moderate confidence ({confidence}%), needs review',
                'escalate_to': None,
                'confidence': confidence
            }
        
        # Low confidence = queue with warning
        return {
            'action': 'queue_approval',
            'reason': f'Low confidence ({confidence}%), needs careful review',
            'escalate_to': None,
            'confidence': confidence,
            'warning': 'AI is uncertain about this response'
        }
    
    def generate_approval_data(self, result: Dict) -> Dict:
        """
        Format result for your existing approval system
        Compatible with your current dashboard_fixed.py
        """
        
        classification = result['classification']
        reply = result['reply']
        decision = result['final_decision']
        
        return {
            'sender_name': result['input']['sender_name'],
            'incoming_msg': result['input']['message'],
            'ai_suggestion': reply['reply'],
            'language': result['input']['language'],
            'confidence': reply['confidence'],
            'action': decision['action'],
            'urgency': classification['urgency'],
            'message_type': classification['message_type'],
            'escalate_to': decision.get('escalate_to'),
            'reasoning': reply.get('reasoning', ''),
            'context': result['database_context']
        }
    
    def format_notification(self, result: Dict) -> str:
        """
        Format a nice notification message for Telegram
        To send to yourself for approval
        """
        
        classification = result['classification']
        reply = result['reply']
        decision = result['final_decision']
        
        # Build notification
        notification = f"""
🔔 NEW MESSAGE ANALYSIS
{'='*40}

📨 From: {result['input']['sender_name']}
💬 Message: {result['input']['message']}

📊 ANALYSIS:
• Type: {classification['message_type']}
• Urgency: {classification['urgency']}
• Confidence: {reply['confidence']}%

🤖 AI SUGGESTION:
{reply['reply']}

🎯 RECOMMENDED ACTION: {decision['action'].upper()}
"""
        
        if decision.get('escalate_to'):
            notification += f"\n👤 Should escalate to: {decision['escalate_to']}"
        
        if decision.get('warning'):
            notification += f"\n⚠️  {decision['warning']}"
        
        if reply.get('reasoning'):
            notification += f"\n\n💭 Reasoning: {reply['reasoning']}"
        
        return notification


# Test the complete system
if __name__ == "__main__":
    import os
    
    API_KEY = os.getenv('OPENAI_API_KEY', 'your-api-key-here')
    
    business_info = {
        'company_name': 'Lothar Construction GmbH',
        'business_type': 'Construction & Renovation',
        'location': 'Cologne, Germany',
        'specialization': 'Bathroom and kitchen renovations'
    }
    
    # Initialize handler
    handler = IntegratedMessageHandler(API_KEY, business_info)
    
    print("\n" + "="*70)
    print("🚀 TESTING INTEGRATED MESSAGE HANDLER")
    print("🛡️  Mode: SAFE MODE (auto-reply disabled)")
    print("="*70)
    
    # Test messages from the Seidel conversation
    test_cases = [
        {
            'message': 'Are the pipes inside the insulation made of copper?',
            'sender_name': 'Weronika',
            'sender_role': 'coordinator',
            'language': 'English'
        },
        {
            'message': 'If the 120 cm glass wall is installed, the washing machine will no longer fit. I will be at the customer early tomorrow and need this information immediately.',
            'sender_name': 'Piotr',
            'sender_role': 'worker',
            'language': 'English'
        },
        {
            'message': 'The customer wants new panels and refuses a cover strip. New panels would cost approximately €1,500–2,000.',
            'sender_name': 'Lukasz',
            'sender_role': 'worker',
            'language': 'English'
        }
    ]
    
    results = []
    
    for i, test in enumerate(test_cases, 1):
        print(f"\n\n{'#'*70}")
        print(f"TEST CASE {i}/{len(test_cases)}")
        print(f"{'#'*70}")
        
        result = handler.process_message(**test)
        results.append(result)
        
        # Show formatted notification
        print("\n📱 TELEGRAM NOTIFICATION:")
        print(handler.format_notification(result))
        
        # Show approval data
        print("\n📋 APPROVAL SYSTEM DATA:")
        approval_data = handler.generate_approval_data(result)
        print(json.dumps(approval_data, indent=2))
        
        # Save full result
        filename = f'/home/claude/test_result_case_{i}.json'
        with open(filename, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n💾 Full result saved: {filename}")
    
    # Summary
    print(f"\n\n{'='*70}")
    print(f"✅ ALL TESTS COMPLETE")
    print(f"{'='*70}")
    print(f"\nProcessed {len(results)} messages:")
    
    for i, result in enumerate(results, 1):
        decision = result['final_decision']
        reply = result['reply']
        print(f"\n{i}. {result['classification']['message_type']}")
        print(f"   Action: {decision['action']} (confidence: {reply['confidence']}%)")
    
    print("\n✅ System integration test successful!")
    print("\nNext step: Integrate this with your telegram_bot.py")