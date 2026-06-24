"""
GROUP MESSAGE CLASSIFIER - UPGRADED VERSION
Analyzes group messages and topics with context awareness
"""

import json
from openai import OpenAI
from typing import Dict, List, Optional
from datetime import datetime

class GroupMessageClassifier:
    """
    Enhanced classifier for group messages with topic detection
    Identifies:
    - Message relevance (should bot respond or not)
    - Topic/thread context
    - Multiple participants
    - Decision chains
    """
    
    def __init__(self, openai_api_key: str):
        self.client = OpenAI(api_key=openai_api_key)
        
        # Message types (same as before + new group-specific)
        self.MESSAGE_TYPES = {
            'factual_question': 'Asking about specs, materials, status, or project details',
            'scheduling': 'About dates, appointments, availability, timeline',
            'status_update': 'Informing about completion, changes, or current state',
            'technical_problem': 'Reporting an issue that needs a solution',
            'customer_complaint': 'Customer dissatisfaction or quality concerns',
            'decision_required': 'Needs management approval or authorization',
            'task_assignment': 'Delegating or requesting work',
            'acknowledgment': 'Simple confirmation or "okay" type messages',
            'general_chat': 'Small talk or non-work related',
            'question_to_specific_person': 'Directed question to someone specific',
            'group_discussion': 'Multi-person discussion or debate',
            'follow_up': 'Following up on previous message/topic'
        }
        
        # Urgency levels
        self.URGENCY_LEVELS = {
            'low': 'Can wait days, no immediate action needed',
            'medium': 'Needs attention within 24-48 hours',
            'high': 'Urgent, needs immediate response or same-day action',
            'critical': 'Emergency, blocking work or customer escalation'
        }
    
    def classify_group_message(
        self,
        message: str,
        sender_name: str,
        chat_title: str = "",
        topic_name: str = "",
        recent_messages: Optional[List[Dict]] = None,
        mentioned_users: Optional[List[str]] = None
    ) -> Dict:
        """
        Classify a group message with full context
        
        Args:
            message: The message text
            sender_name: Who sent it
            chat_title: Group chat name
            topic_name: Topic/thread name (if applicable)
            recent_messages: Last 5-10 messages in the group
            mentioned_users: Any @mentioned users
            
        Returns:
            Enhanced classification with group context
        """
        
        # Build conversation context
        context_str = ""
        if recent_messages:
            context_str = "\n\nRECENT GROUP CONVERSATION:\n"
            for msg in recent_messages[-10:]:  # Last 10 messages
                sender = msg.get('sender', 'Unknown')
                text = msg.get('text', '')
                context_str += f"- {sender}: {text}\n"
        
        # Build mentions context
        mentions_str = ""
        if mentioned_users:
            mentions_str = f"\nMENTIONED USERS: {', '.join(mentioned_users)}\n"
        
        # Determine if bot is mentioned or if message needs response
        bot_mentioned = any(name.lower() in message.lower() for name in ['rohit', 'bot', 'ai'])
        
        # Build the classification prompt
        prompt = f"""You are analyzing a message from a GROUP CHAT.

GROUP INFO:
- Chat Name: {chat_title}
- Topic: {topic_name or "Main chat"}

MESSAGE TYPES:
{json.dumps(self.MESSAGE_TYPES, indent=2)}

URGENCY LEVELS:
{json.dumps(self.URGENCY_LEVELS, indent=2)}

SENDER INFO:
- Name: {sender_name}

{context_str}

CURRENT MESSAGE:
"{message}"

{mentions_str}

CRITICAL ANALYSIS NEEDED:
1. Should the bot respond to this message? (Consider: Is it directed at the bot? Is it a general question? Is it a private conversation between others?)
2. What is the topic/thread being discussed?
3. Is this part of an ongoing discussion?
4. Who is the intended audience? (everyone, specific person, bot)
5. Does this need a response or is it just informational?

Return ONLY valid JSON in this exact format:
{{
  "should_respond": true/false,
  "response_reason": "why bot should or shouldn't respond",
  "message_type": "one of the types above",
  "urgency": "low/medium/high/critical",
  "confidence": 85,
  "topic": "what topic is being discussed",
  "intended_audience": "everyone/specific_person/bot/group_discussion",
  "entities": {{
    "customer_name": "extracted name or null",
    "project_name": "project identifier or null",
    "date": "any mentioned date or null",
    "cost": "any mentioned cost/price or null",
    "material": "materials mentioned or null",
    "problem_type": "type of issue or null",
    "location": "location/room mentioned or null",
    "measurement": "any dimensions or null",
    "mentioned_person": "who is being asked or mentioned or null"
  }},
  "intent": "brief description of what sender wants",
  "suggested_action": "what should be done next",
  "needs_database_lookup": true/false,
  "context_from_previous": "summary of how this relates to previous messages",
  "reasoning": "brief explanation of classification and response decision"
}}"""

        try:
            # Call OpenAI API
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at analyzing group chat communications and determining when a bot should respond. You understand context, topics, and conversation flow. Return only valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.3,
                response_format={"type": "json_object"}
            )
            
            # Parse the response
            result = json.loads(response.choices[0].message.content)
            
            # Add metadata
            result['original_message'] = message
            result['sender_name'] = sender_name
            result['chat_title'] = chat_title
            result['topic_name'] = topic_name
            result['bot_mentioned'] = bot_mentioned
            result['timestamp'] = datetime.now().isoformat()
            result['model_used'] = "gpt-4o"
            result['is_group_message'] = True
            
            return result
            
        except Exception as e:
            # Fallback classification
            return {
                'error': str(e),
                'should_respond': False,
                'response_reason': 'Classification failed - skipping response',
                'message_type': 'unknown',
                'urgency': 'medium',
                'confidence': 0,
                'topic': 'unknown',
                'intended_audience': 'unknown',
                'entities': {},
                'intent': 'Classification failed',
                'suggested_action': 'Manual review required',
                'original_message': message,
                'sender_name': sender_name,
                'chat_title': chat_title,
                'topic_name': topic_name,
                'timestamp': datetime.now().isoformat(),
                'is_group_message': True
            }
    
    def summarize_topic_thread(
        self,
        messages: List[Dict],
        topic_name: str = ""
    ) -> Dict:
        """
        Summarize an entire topic/thread
        Useful for understanding what's been discussed
        
        Args:
            messages: All messages in the topic
            topic_name: Name of the topic
            
        Returns:
            Summary with key points, decisions, and action items
        """
        
        if not messages:
            return {
                'summary': 'No messages in topic',
                'key_points': [],
                'decisions_made': [],
                'pending_questions': [],
                'action_items': []
            }
        
        # Build message thread
        thread_text = "\n".join([
            f"{msg.get('sender', 'Unknown')}: {msg.get('text', '')}"
            for msg in messages
        ])
        
        prompt = f"""Analyze this group chat topic/thread and provide a summary.

TOPIC: {topic_name}

CONVERSATION:
{thread_text}

Provide:
1. Brief summary (2-3 sentences)
2. Key points discussed
3. Decisions made (if any)
4. Pending questions (unanswered)
5. Action items (who needs to do what)

Return ONLY valid JSON:
{{
  "summary": "brief overview",
  "key_points": ["point1", "point2"],
  "decisions_made": ["decision1", "decision2"],
  "pending_questions": ["question1", "question2"],
  "action_items": [
    {{"person": "name", "action": "what to do", "deadline": "when or null"}}
  ],
  "main_topic": "what is this really about",
  "participants": ["person1", "person2"]
}}"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {
                        "role": "system",
                        "content": "You are an expert at summarizing group discussions and identifying key information. Return only valid JSON."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.4,
                response_format={"type": "json_object"}
            )
            
            result = json.loads(response.choices[0].message.content)
            result['topic_name'] = topic_name
            result['message_count'] = len(messages)
            result['timestamp'] = datetime.now().isoformat()
            
            return result
            
        except Exception as e:
            return {
                'error': str(e),
                'summary': 'Failed to generate summary',
                'key_points': [],
                'decisions_made': [],
                'pending_questions': [],
                'action_items': []
            }
    
    def generate_summary(self, classification: Dict) -> str:
        """
        Generate human-readable summary of group message classification
        """
        
        summary = f"""
📊 GROUP MESSAGE CLASSIFICATION
{'='*70}

💬 Message: {classification['original_message'][:100]}...
👤 Sender: {classification['sender_name']}
👥 Group: {classification.get('chat_title', 'Unknown')}
📌 Topic: {classification.get('topic_name', 'Main chat')}

🤖 SHOULD BOT RESPOND? {('✅ YES' if classification['should_respond'] else '❌ NO')}
💭 Reason: {classification['response_reason']}

🏷️  Type: {classification['message_type'].upper()}
⚡ Urgency: {classification['urgency'].upper()}
📈 Confidence: {classification['confidence']}%
🎯 Topic: {classification['topic']}
👥 Audience: {classification['intended_audience']}

🎯 Intent: {classification['intent']}
💡 Suggested Action: {classification['suggested_action']}
"""
        
        if classification.get('context_from_previous'):
            summary += f"\n🔗 Context: {classification['context_from_previous']}\n"
        
        entities = classification.get('entities', {})
        if any(entities.values()):
            summary += "\n📦 Extracted Entities:\n"
            for key, value in entities.items():
                if value:
                    summary += f"   • {key}: {value}\n"
        
        if classification.get('needs_database_lookup'):
            summary += "\n🗄️ Database lookup recommended\n"
        
        summary += f"\n💭 Reasoning: {classification.get('reasoning', 'N/A')}\n"
        summary += "="*70
        
        return summary


# Example usage
if __name__ == "__main__":
    import os
    
    API_KEY = os.getenv('OPENAI_API_KEY', 'your-api-key-here')
    
    classifier = GroupMessageClassifier(API_KEY)
    
    print("\n🚀 TESTING GROUP MESSAGE CLASSIFIER\n")
    print("="*70)
    
    # Simulate group conversation about a construction project
    recent_messages = [
        {'sender': 'Piotr', 'text': 'I\'m at the Seidel bathroom now'},
        {'sender': 'Weronika', 'text': 'How does it look?'},
        {'sender': 'Piotr', 'text': 'The shower drain is installed but glass wall pending'},
        {'sender': 'Weronika', 'text': 'When will glass be ready?'}
    ]
    
    # New message arrives
    test_message = "What are the exact dimensions of the glass wall again? I need to confirm with supplier."
    
    print("📨 Testing message classification in group context:")
    print(f"Message: {test_message}")
    print(f"Recent context: {len(recent_messages)} previous messages\n")
    
    result = classifier.classify_group_message(
        message=test_message,
        sender_name='Lukasz',
        chat_title='Seidel Bathroom Project',
        topic_name='Glass Wall Installation',
        recent_messages=recent_messages
    )
    
    print(classifier.generate_summary(result))
    
    # Save result
    with open('/home/claude/group_test_result.json', 'w') as f:
        json.dump(result, f, indent=2)
    
    print("\n💾 Full result saved to: group_test_result.json")
    
    # Test topic summarization
    print("\n\n" + "="*70)
    print("📝 TESTING TOPIC SUMMARIZATION")
    print("="*70 + "\n")
    
    all_messages = recent_messages + [
        {'sender': 'Lukasz', 'text': test_message},
        {'sender': 'Weronika', 'text': '120cm wide x 200cm high'},
        {'sender': 'Lukasz', 'text': 'Ok, I\'ll order it today'},
        {'sender': 'Piotr', 'text': 'Installation planned for Friday'}
    ]
    
    summary = classifier.summarize_topic_thread(
        messages=all_messages,
        topic_name='Glass Wall Installation'
    )
    
    print(json.dumps(summary, indent=2))
    
    print("\n✅ Group message classifier test complete!")