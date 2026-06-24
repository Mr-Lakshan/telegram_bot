"""
QUESTION CLASSIFIER — Smart Message Router
=============================================
Uses cheapest possible AI model to classify messages:
  - Is it a question?
  - Is it company-related?
  - Dynamic (customer-specific) or Static (general knowledge)?
  - What's the specific intent?

This classifier sits BETWEEN the pre-filter and the actual handlers.
Pre-filter removes obvious junk → Classifier routes the rest.

Cost: ~0.01 cent per classification (gpt-4.1-nano)
"""

import json
import os
from typing import Dict, Optional, List
from openai import OpenAI
from bot.core.model_config import MODEL_CONFIG, PROMPTS


class QuestionClassifier:
    """
    Classifies messages using the cheapest available model.
    Returns routing decision for the message pipeline.
    """

    def __init__(self, openai_api_key: str = ""):
        self.api_key = openai_api_key or os.getenv("OPENAI_API_KEY", "")
        self.client = OpenAI(api_key=self.api_key) if self.api_key else None

        self.config = MODEL_CONFIG["classifier"]
        self.prompt_style = MODEL_CONFIG.get("prompt_style", "caveman")

        self._stats = {
            'total': 0, 'dynamic': 0, 'static': 0,
            'not_question': 0, 'offtopic': 0, 'errors': 0,
        }

        model = self.config['model']
        style = self.prompt_style
        print(f"✅ QuestionClassifier initialized (model={model}, style={style})")

    # ══════════════════════════════════════════════════════════════════════
    #  MAIN: Classify a message
    # ══════════════════════════════════════════════════════════════════════

    def classify(
        self,
        message: str,
        sender_name: str = "",
        chat_title: str = "",
        recent_messages: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Classify a message that passed the pre-filter.

        Returns:
            {
                'is_question': bool,
                'is_company_topic': bool,
                'question_type': 'dynamic' | 'static' | 'not_question',
                'intent': str,
                'confidence': int (0-100),
                'route_to': 'dynamic_handler' | 'static_handler' | 'skip',
            }
        """
        self._stats['total'] += 1

        # Build context
        context = f"Group: {chat_title}\nSender: {sender_name}\nMessage: {message}"
        if recent_messages:
            recent_str = "\n".join(
                f"  {m.get('sender', '?')}: {m.get('text', '')[:80]}"
                for m in recent_messages[-3:]
            )
            context += f"\n\nRecent chat:\n{recent_str}"

        # Get system prompt (caveman or normal)
        system_prompt = PROMPTS["classifier"].get(
            self.prompt_style,
            PROMPTS["classifier"]["caveman"]
        )

        # Call cheap model
        result = self._call_model(system_prompt, context)

        if not result:
            self._stats['errors'] += 1
            return self._default_skip("classifier_error")

        # Add routing decision
        result['route_to'] = self._decide_route(result)

        # Update stats
        route = result['route_to']
        if route == 'dynamic_handler':
            self._stats['dynamic'] += 1
        elif route == 'static_handler':
            self._stats['static'] += 1
        elif result.get('is_company_topic') is False:
            self._stats['offtopic'] += 1
        else:
            self._stats['not_question'] += 1

        return result

    # ══════════════════════════════════════════════════════════════════════
    #  ROUTING DECISION
    # ══════════════════════════════════════════════════════════════════════

    def _decide_route(self, classification: Dict) -> str:
        """Decide where to route based on classification."""

        if not classification.get('is_question', False):
            return 'skip'

        if not classification.get('is_company_topic', True):
            return 'skip'

        confidence = classification.get('confidence', 0)
        if confidence < 40:
            return 'skip'

        q_type = classification.get('question_type', 'not_question')
        intent = classification.get('intent', 'none')

        if q_type == 'dynamic':
            return 'dynamic_handler'
        elif q_type == 'static':
            # Material/technique questions in a GROUP context → route to dynamic
            # because the answer is in the customer's Drive documents
            if intent in ('material', 'technique', 'schedule', 'process', 'other'):
                return 'dynamic_handler'  # Check customer docs first
            return 'static_handler'
        else:
            return 'skip'

    # ══════════════════════════════════════════════════════════════════════
    #  MODEL CALL
    # ══════════════════════════════════════════════════════════════════════

    def _call_model(self, system_prompt: str, user_message: str) -> Optional[Dict]:
        """Call the classifier model. Try primary, fallback if needed."""
        if not self.client:
            print("   ⚠️ QuestionClassifier: No API key configured")
            return None

        model = self.config['model']
        fallback = self.config.get('fallback_model', 'gpt-4o-mini')

        for attempt_model in [model, fallback]:
            try:
                resp = self.client.chat.completions.create(
                    model=attempt_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    max_tokens=self.config.get('max_tokens', 150),
                    temperature=self.config.get('temperature', 0),
                )
                text = resp.choices[0].message.content.strip()

                # Parse JSON from response
                text = self._clean_json(text)
                result = json.loads(text)

                # Validate required fields
                if 'is_question' not in result:
                    result['is_question'] = False
                if 'question_type' not in result:
                    result['question_type'] = 'not_question'
                if 'intent' not in result:
                    result['intent'] = 'none'
                if 'confidence' not in result:
                    result['confidence'] = 50
                if 'is_company_topic' not in result:
                    result['is_company_topic'] = True

                result['model_used'] = attempt_model
                return result

            except json.JSONDecodeError as e:
                print(f"   ⚠️ Classifier JSON parse error ({attempt_model}): {e}")
                if attempt_model == fallback:
                    return None
                continue

            except Exception as e:
                error_msg = str(e)
                if 'model_not_found' in error_msg or '404' in error_msg:
                    print(f"   ⚠️ Model {attempt_model} not available, trying fallback...")
                    continue
                print(f"   ⚠️ Classifier error ({attempt_model}): {e}")
                if attempt_model == fallback:
                    return None
                continue

        return None

    def _clean_json(self, text: str) -> str:
        """Extract JSON from model response."""
        text = text.strip()
        if text.startswith('```'):
            lines = text.split('\n')
            json_lines = []
            in_block = False
            for line in lines:
                if line.startswith('```') and not in_block:
                    in_block = True
                    continue
                elif line.startswith('```') and in_block:
                    break
                elif in_block:
                    json_lines.append(line)
            text = '\n'.join(json_lines)
        return text.strip()

    def _default_skip(self, reason: str = "") -> Dict:
        """Return a safe default that skips processing."""
        return {
            'is_question': False,
            'is_company_topic': False,
            'question_type': 'not_question',
            'intent': 'none',
            'confidence': 0,
            'route_to': 'skip',
            'error': reason,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  STATS
    # ══════════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return classification statistics for daily report."""
        return self._stats.copy()

    def reset_stats(self):
        """Reset stats (call after daily report)."""
        for key in self._stats:
            self._stats[key] = 0