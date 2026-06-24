"""
SMART REPLY GENERATOR - PHASE 1
Generates contextual replies based on classification and database context
"""

import json
from openai import OpenAI
from typing import Dict, Optional
from datetime import datetime

class SmartReplyGenerator:
    """
    Generates intelligent replies using:
    1. Message classification
    2. Database context
    3. Past learning examples
    """
    
    def __init__(self, openai_api_key: str, business_info: Dict):
        self.client = OpenAI(api_key=openai_api_key)
        self.business_info = business_info
    
    def generate_reply(
        self,
        classification: Dict,
        database_context: Dict,
        past_corrections: Optional[list] = None,
        sender_language: str = "German"
    ) -> Dict:
        """
        Generate a smart reply based on all available information
        
        Args:
            classification: Result from MessageClassifier
            database_context: Result from database query
            past_corrections: Learning examples from previous edits
            sender_language: Language to reply in
            
        Returns:
            Dict with reply, confidence, action recommendation
        """
        
        message = classification['original_message']
        msg_type = classification['message_type']
        urgency = classification['urgency']
        entities = classification.get('entities', {})
        
        # Build learning context
        learning_context = ""
        if past_corrections:
            learning_context = "\n\nLEARNING FROM PAST CORRECTIONS:\n"
            for correction in past_corrections[-5:]:  # Last 5 corrections
                learning_context += f"""
- Original message: {correction.get('incoming_msg', '')}
- AI suggested: {correction.get('ai_suggestion', '')}
- You edited to: {correction.get('your_edit', '')}
"""
        
        # Build database context string
        db_context_str = "\n\nAVAILABLE DATA:\n"
        if database_context:
            db_context_str += json.dumps(database_context, indent=2)
        else:
            db_context_str += "No specific project data available."
        
        # Build system prompt based on message type
        system_prompt = self._build_system_prompt(
            msg_type, urgency, sender_language
        )
        
        # Build user prompt
        user_prompt = f"""
ORIGINAL MESSAGE:
"{message}"

SENDER: {classification['sender_name']}

MESSAGE ANALYSIS:
- Type: {msg_type}
- Urgency: {urgency}
- Intent: {classification['intent']}
- Suggested Action: {classification['suggested_action']}

EXTRACTED ENTITIES:
{json.dumps(entities, indent=2)}

{db_context_str}

{learning_context}

TASK:
Generate an appropriate reply that:
1. Addresses the sender's intent
2. Uses facts from available data when possible
3. Is concise (1-3 sentences typically)
4. Matches the {sender_language} language
5. Has appropriate tone for the message type and urgency
6. Indicates if you need to escalate or if information is missing

SPECIAL INSTRUCTIONS BY MESSAGE TYPE:

- factual_question: Answer directly using database facts. If data not available, say so.
- scheduling: Provide specific dates/times from schedule. If conflict, suggest alternatives.
- status_update: Acknowledge and confirm what was said. Log the update.
- technical_problem: Suggest solution based on specs or similar past issues.
- customer_complaint: Acknowledge concern, suggest solution or escalate if cost > €1000.
- decision_required: Clearly state who needs to decide (Lothar for €1000+).
- task_assignment: Confirm who will do it and when.

Return ONLY valid JSON:
{{
  "reply": "your generated response in {sender_language}",
  "confidence": 0-100,
  "action": "auto_send" or "queue_approval" or "escalate",
  "escalate_to": "person name if escalation needed, else null",
  "reasoning": "why this reply and confidence level",
  "missing_info": "what information is needed but not available",
  "suggested_followup": "what might be asked next or what to prepare"
}}
"""
        
        try:
            # Generate reply
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.5,  # Balance between consistency and creativity
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            
            # Add metadata
            result['generated_at'] = datetime.now().isoformat()
            result['model_used'] = 'gpt-4o'
            result['message_type'] = msg_type
            result['urgency'] = urgency
            
            # Validate and adjust confidence based on available data
            result['confidence'] = self._adjust_confidence(
                result['confidence'],
                database_context,
                msg_type
            )
            
            return result
            
        except Exception as e:
            return {
                'error': str(e),
                'reply': f"I need to check on this. Let me get back to you.",
                'confidence': 0,
                'action': 'queue_approval',
                'escalate_to': None,
                'reasoning': 'Error in reply generation',
                'missing_info': 'System error occurred'
            }
    
    def _build_system_prompt(
        self, 
        msg_type: str, 
        urgency: str,
        language: str
    ) -> str:
        """Build appropriate system prompt based on message type"""
        
        base_prompt = f"""You are an AI assistant for {self.business_info['company_name']}, 
a {self.business_info['business_type']} company.

Your role is to help coordinate projects, answer questions, and assist team communication.

COMPANY INFO:
- Name: {self.business_info['company_name']}
- Location: {self.business_info['location']}
- Specialization: {self.business_info['specialization']}

DECISION AUTHORITY LEVELS:
- Workers (Piotr, Lukasz): Field decisions, material usage
- Coordinator (Weronika): Scheduling, basic customer communication
- Manager (Yevhenniatop): Project decisions, customer issues
- Boss (Lothar): Final decisions, financial approval (€1000+)

RESPONSE STYLE:
- Professional but friendly
- Direct and concise
- Use data/facts when available
- Admit when you don't know
- Escalate appropriately
"""
        
        # Add type-specific guidance
        if msg_type == 'customer_complaint':
            base_prompt += """

FOR CUSTOMER COMPLAINTS:
- Acknowledge the concern first
- Offer immediate solution if simple
- Escalate to manager if quality issue
- Escalate to Lothar if cost > €1000
- Always maintain professional, calm tone
"""
        
        elif msg_type == 'decision_required':
            base_prompt += """

FOR DECISIONS:
- Clearly state what decision is needed
- Present options with cost/impact
- Tag appropriate decision-maker
- Don't make decisions above your authority
"""
        
        elif msg_type == 'technical_problem':
            base_prompt += """

FOR TECHNICAL PROBLEMS:
- Check if similar issue solved before
- Suggest solution from past successes
- If urgent, prioritize quick workaround
- If complex, suggest on-site assessment
"""
        
        if urgency in ['high', 'critical']:
            base_prompt += """

⚡ URGENT MESSAGE - Respond quickly and clearly, prioritize immediate actionable information.
"""
        
        return base_prompt
    
    def _adjust_confidence(
        self,
        base_confidence: int,
        database_context: Dict,
        msg_type: str
    ) -> int:
        """
        Adjust confidence based on available data
        More data = higher confidence
        """
        
        confidence = base_confidence
        
        # Reduce confidence if no database context
        if not database_context or not database_context.get('project'):
            confidence = min(confidence, 70)
        
        # Factual questions need data
        if msg_type == 'factual_question':
            if not database_context:
                confidence = min(confidence, 50)
        
        # Scheduling needs schedule data
        if msg_type == 'scheduling':
            if not database_context.get('schedule'):
                confidence = min(confidence, 60)
        
        # Decision/complaint should always be reviewed
        if msg_type in ['decision_required', 'customer_complaint']:
            confidence = min(confidence, 75)
        
        return max(0, min(100, confidence))
    
    def generate_summary(self, reply_data: Dict) -> str:
        """Generate human-readable summary"""
        
        summary = f"""
💬 SMART REPLY GENERATED
{'='*60}

📝 Reply: {reply_data['reply']}

📊 Confidence: {reply_data['confidence']}%
🎬 Recommended Action: {reply_data['action'].upper()}
"""
        
        if reply_data.get('escalate_to'):
            summary += f"👤 Escalate to: {reply_data['escalate_to']}\n"
        
        summary += f"""
💭 Reasoning: {reply_data.get('reasoning', 'N/A')}
"""
        
        if reply_data.get('missing_info'):
            summary += f"⚠️  Missing Info: {reply_data['missing_info']}\n"
        
        if reply_data.get('suggested_followup'):
            summary += f"💡 Possible Follow-up: {reply_data['suggested_followup']}\n"
        
        summary += "="*60
        
        return summary


# Example usage
if __name__ == "__main__":
    import os
    from bot.ai.message_classifier import MessageClassifier
    from bot.core.database_simulator import MockDatabase
    
    API_KEY = os.getenv('OPENAI_API_KEY', 'your-api-key-here')
    
    # Business info
    business_info = {
        'company_name': 'Lothar Construction GmbH',
        'business_type': 'Construction & Renovation',
        'location': 'Cologne, Germany',
        'specialization': 'Bathroom and kitchen renovations'
    }
    
    # Initialize components
    classifier = MessageClassifier(API_KEY)
    db = MockDatabase()
    reply_gen = SmartReplyGenerator(API_KEY, business_info)
    
    print("\n🤖 TESTING SMART REPLY GENERATOR\n")
    print("="*70)
    
    # Test message
    test_message = "Are the pipes inside the insulation made of copper?"
    sender = "Weronika"
    
    print(f"\n📨 Testing message: \"{test_message}\"")
    print(f"👤 From: {sender}\n")
    
    print("Step 1: Classifying message...")
    classification = classifier.classify(test_message, sender)
    print(f"✅ Type: {classification['message_type']}, Urgency: {classification['urgency']}")
    
    # Step 2: Get database context
    print("\nStep 2: Querying database...")
    context = db.query_context(classification['entities'])
    print(f"✅ Found: {list(context.keys())}")
    
    # Step 3: Generate reply
    print("\nStep 3: Generating smart reply...")
    reply = reply_gen.generate_reply(
        classification=classification,
        database_context=context,
        sender_language="German"
    )
    
    print(reply_gen.generate_summary(reply))
    
    # Save full result
    full_result = {
        'classification': classification,
        'database_context': context,
        'reply': reply
    }
    
    with open('/home/claude/test_smart_reply.json', 'w') as f:
        json.dump(full_result, f, indent=2)
    
    print("\n💾 Full result saved to: test_smart_reply.json")
    print("\n✅ Smart reply generation test complete!")