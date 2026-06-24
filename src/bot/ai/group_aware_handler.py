"""
GROUP-AWARE INTEGRATED HANDLER - v3
✅ Har message (Group + DM) analyze + save hota hai
✅ AI se topic detect hota hai (content se, Telegram thread name se nahi)
✅ Reply sirf tab generate hoti hai jab confidence >= REPLY_CONFIDENCE_THRESHOLD
✅ Sab kuch DB mein save hota hai - groups + DMs dono ke liye learning
"""

import json
import sqlite3
from datetime import datetime
from typing import Dict, List, Optional
from bot.ai.message_classifier import MessageClassifier
from bot.ai.group_message_classifier import GroupMessageClassifier
from bot.ai.smart_reply_generator import SmartReplyGenerator


# ── Confidence threshold: isse kam ho to sirf save karo, reply mat karo ──
REPLY_CONFIDENCE_THRESHOLD = 75


class GroupAwareMessageHandler:
    """
    Unified handler for Groups and DMs.

    Flow (har message ke liye):
      1. Message save karo DB mein (group_messages / dm_messages)
      2. AI se analyze karo — type, urgency, AI-detected topic
      3. Analysis save karo (message_analysis table)
      4. Confidence >= threshold? → reply generate karo + queue
         Confidence <  threshold? → sirf log karo, koi reply nahi
    """

    def __init__(
        self,
        openai_api_key: str,
        business_info: Dict,
        db_path: str = "bot_data.db",
        enable_auto_reply: bool = False,
        bot_username: str = "rohit",
    ):
        self.dm_classifier    = MessageClassifier(openai_api_key)
        self.group_classifier = GroupMessageClassifier(openai_api_key)
        self.reply_generator  = SmartReplyGenerator(openai_api_key, business_info)
        self.db_path          = db_path
        self.bot_username     = bot_username.lower()

        self.ENABLE_AUTO_REPLY         = enable_auto_reply
        self.AUTO_SEND_THRESHOLD       = 85
        self.QUEUE_APPROVAL_THRESHOLD  = 60
        self.REPLY_CONFIDENCE_THRESHOLD = REPLY_CONFIDENCE_THRESHOLD

        # Types that always need human review before sending
        self.ALWAYS_QUEUE = ["decision_required", "customer_complaint"]

        # Types where auto-send is safe (only if auto-reply is on)
        self.SAFE_AUTO_SEND = ["acknowledgment", "status_update", "factual_question"]

        mode = "⚡ AUTO-REPLY ENABLED" if enable_auto_reply else "🛡️  SAFE MODE (approval required)"
        print(f"{mode} | Reply threshold: {REPLY_CONFIDENCE_THRESHOLD}%")

    # ══════════════════════════════════════════════════════════════════════
    #  PUBLIC: process_message
    # ══════════════════════════════════════════════════════════════════════

    def process_message(
        self,
        message: str,
        sender_name: str,
        sender_id: int = 0,
        is_group: bool = False,
        chat_id: int = None,
        chat_title: str = "",
        topic_name: str = "",          # Telegram thread name (optional, for context only)
        recent_messages: Optional[List[Dict]] = None,
        mentioned_users: Optional[List[str]] = None,
        sender_language: str = "German",
    ) -> Dict:
        """
        Har message yahan aata hai — group ya DM.

        Returns dict with keys:
          should_queue  : bool  — kya approval queue mein daalna chahiye?
          should_respond: bool  — kya reply generate hui?
          analysis_id   : int   — message_analysis table ka row id
          classification: dict
          reply         : dict | None
          final_decision: dict
        """

        print(f"\n{'='*70}")
        src = f"👥 GROUP [{chat_title}]" if is_group else "💬 DM"
        print(f"📨 {src} | From: {sender_name} | Msg: {message[:80]}...")
        print(f"{'='*70}")

        # ── STEP 1: Raw message DB mein save karo ──────────────────────────
        raw_id = self._save_raw_message(
            message=message,
            sender_id=sender_id,
            sender_name=sender_name,
            is_group=is_group,
            chat_id=chat_id,
            chat_title=chat_title,
            topic_name=topic_name,
        )
        print(f"   💾 Raw message saved (id={raw_id})")

        # ── STEP 2: AI Classification ───────────────────────────────────────
        print("\n📊 STEP 2: AI Classification...")
        if is_group:
            classification = self.group_classifier.classify_group_message(
                message=message,
                sender_name=sender_name,
                chat_title=chat_title,
                topic_name=topic_name,
                recent_messages=recent_messages or [],
                mentioned_users=mentioned_users or [],
            )
        else:
            classification = self.dm_classifier.classify(
                message=message,
                sender_name=sender_name,
                context_messages=recent_messages or [],
            )

        # AI se jo topic detect hua (content se) — yahi use karenge
        ai_topic = classification.get("topic") or classification.get("intent", "unknown")
        print(f"   ✅ Type      : {classification['message_type']}")
        print(f"   ✅ Urgency   : {classification['urgency']}")
        print(f"   ✅ Confidence: {classification['confidence']}%")
        print(f"   ✅ AI Topic  : {ai_topic}")

        # ── STEP 3: Analysis save karo ─────────────────────────────────────
        analysis_id = self._save_analysis(
            raw_message_id=raw_id,
            is_group=is_group,
            classification=classification,
            ai_topic=ai_topic,
            sender_name=sender_name,
            chat_id=chat_id,
        )
        print(f"   💾 Analysis saved (id={analysis_id})")

        # ── STEP 4: Confidence check → reply ya sirf log? ──────────────────
        confidence = classification.get("confidence", 0)

        if confidence < self.REPLY_CONFIDENCE_THRESHOLD:
            print(f"\n⏭️  Confidence {confidence}% < {self.REPLY_CONFIDENCE_THRESHOLD}% — saving only, no reply")
            return {
                "should_queue"  : False,
                "should_respond": False,
                "analysis_id"   : analysis_id,
                "raw_id"        : raw_id,
                "classification": classification,
                "reply"         : None,
                "final_decision": {
                    "action": "log_only",
                    "reason": f"Confidence too low ({confidence}%)",
                },
                "input": {
                    "message"        : message,
                    "sender_name"    : sender_name,
                    "sender_id"      : sender_id,
                    "chat_title"     : chat_title,
                    "topic_name"     : topic_name,
                    "language"       : sender_language,
                    "is_group"       : is_group,
                    "chat_id"        : chat_id,
                },
            }

        # ── STEP 5: DB context ─────────────────────────────────────────────
        print("\n🗄️  STEP 5: Querying DB context...")
        database_context = self._get_database_context(classification)
        print(f"   ✅ Context keys: {list(database_context.keys())}")

        # ── STEP 6: Past corrections (learning) ────────────────────────────
        print("\n📚 STEP 6: Loading learning examples...")
        past_corrections = self._get_past_corrections(
            msg_type=classification["message_type"],
            language=sender_language,
            is_group=is_group,
        )
        print(f"   ✅ Found {len(past_corrections)} examples")

        # ── STEP 7: Reply generate karo ────────────────────────────────────
        print("\n💬 STEP 7: Generating reply...")
        reply_data = self.reply_generator.generate_reply(
            classification=classification,
            database_context=database_context,
            past_corrections=past_corrections,
            sender_language=sender_language,
        )
        print(f"   ✅ Reply confidence: {reply_data['confidence']}%")

        # ── STEP 8: Action decide karo ─────────────────────────────────────
        print("\n🎯 STEP 8: Deciding action...")
        final_action = (
            self._decide_group_action(classification, reply_data)
            if is_group
            else self._decide_dm_action(classification, reply_data)
        )
        print(f"   ✅ Action: {final_action['action'].upper()}")

        result = {
            "should_queue"    : final_action["action"] in ("queue_approval", "auto_send"),
            "should_respond"  : True,
            "analysis_id"     : analysis_id,
            "raw_id"          : raw_id,
            "classification"  : classification,
            "database_context": database_context,
            "reply"           : reply_data,
            "final_decision"  : final_action,
            "input": {
                "message"     : message,
                "sender_name" : sender_name,
                "sender_id"   : sender_id,
                "chat_title"  : chat_title,
                "topic_name"  : topic_name,
                "language"    : sender_language,
                "is_group"    : is_group,
                "chat_id"     : chat_id,
            },
        }

        print(f"\n{'='*70}")
        print(f"✅ PROCESSING COMPLETE | should_queue={result['should_queue']}")
        print(f"{'='*70}\n")
        return result

    # ══════════════════════════════════════════════════════════════════════
    #  DB: Raw message save
    # ══════════════════════════════════════════════════════════════════════

    def _save_raw_message(
        self,
        message: str,
        sender_id: int,
        sender_name: str,
        is_group: bool,
        chat_id: int,
        chat_title: str,
        topic_name: str,
    ) -> int:
        """
        Group → group_messages table
        DM    → dm_messages table
        Returns the inserted row id.
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()

            if is_group:
                c.execute(
                    """
                    INSERT INTO group_messages
                        (chat_id, topic_id, sender_id, sender_name, message_text,
                         chat_title, topic_name, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (chat_id, 0, sender_id, sender_name, message,
                     chat_title, topic_name, datetime.now()),
                )
            else:
                c.execute(
                    """
                    INSERT INTO dm_messages
                        (sender_id, sender_name, message_text, timestamp)
                    VALUES (?, ?, ?, ?)
                    """,
                    (sender_id, sender_name, message, datetime.now()),
                )

            row_id = c.lastrowid
            conn.commit()
            conn.close()
            return row_id

        except Exception as e:
            print(f"   ⚠️  _save_raw_message error: {e}")
            return -1

    # ══════════════════════════════════════════════════════════════════════
    #  DB: Analysis save
    # ══════════════════════════════════════════════════════════════════════

    def _save_analysis(
        self,
        raw_message_id: int,
        is_group: bool,
        classification: Dict,
        ai_topic: str,
        sender_name: str,
        chat_id: int,
    ) -> int:
        """
        message_analysis table mein AI classification save karo.
        Returns the inserted row id.
        """
        try:
            entities = classification.get("entities", {})
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO message_analysis
                    (raw_message_id, source_type, sender_name, chat_id,
                     message_type, urgency, confidence, ai_topic,
                     intent, suggested_action, needs_db_lookup,
                     entities_json, should_respond, response_reason,
                     timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_message_id,
                    "group" if is_group else "dm",
                    sender_name,
                    chat_id,
                    classification.get("message_type", "unknown"),
                    classification.get("urgency", "medium"),
                    classification.get("confidence", 0),
                    ai_topic,
                    classification.get("intent", ""),
                    classification.get("suggested_action", ""),
                    int(classification.get("needs_database_lookup", False)),
                    json.dumps(entities, ensure_ascii=False),
                    int(classification.get("should_respond", True)),
                    classification.get("response_reason", ""),
                    datetime.now(),
                ),
            )
            row_id = c.lastrowid
            conn.commit()
            conn.close()
            return row_id

        except Exception as e:
            print(f"   ⚠️  _save_analysis error: {e}")
            return -1

    # ══════════════════════════════════════════════════════════════════════
    #  DB: Learning — save interaction after reply is approved/edited
    # ══════════════════════════════════════════════════════════════════════

    def save_message_for_learning(
        self,
        sender_name: str,
        incoming_msg: str,
        ai_suggestion: str,
        final_reply: str,
        language: str,
        was_edited: bool,
        is_group: bool = False,
        chat_title: str = "",
    ):
        """
        Dashboard se approve/edit hone ke baad call karo.
        message_corrections table mein save karta hai.
        """
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                INSERT INTO message_corrections
                    (user_name, incoming_message, ai_suggestion, your_edit,
                     language, timestamp, is_group, chat_title)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sender_name, incoming_msg, ai_suggestion, final_reply,
                    language, datetime.now(), int(is_group), chat_title,
                ),
            )
            conn.commit()
            conn.close()
            action = "edited" if was_edited else "approved as-is"
            print(f"   ✅ Learning saved ({action}) for '{sender_name}'")
            return True
        except Exception as e:
            print(f"   ⚠️  save_message_for_learning error: {e}")
            return False

    # ══════════════════════════════════════════════════════════════════════
    #  DB: Context for reply generation
    # ══════════════════════════════════════════════════════════════════════

    def _get_database_context(self, classification: Dict) -> Dict:
        """Real DB se relevant history fetch karo."""
        context = {}
        entities = classification.get("entities", {})

        try:
            conn = self._get_conn()
            c = conn.cursor()

            # Customer/project ke related past group messages
            customer = entities.get("customer_name") or entities.get("project_name")
            if customer:
                c.execute(
                    """
                    SELECT sender_name, message_text, timestamp
                    FROM group_messages
                    WHERE LOWER(message_text) LIKE ?
                    ORDER BY timestamp DESC LIMIT 5
                    """,
                    (f"%{customer.lower()}%",),
                )
                rows = c.fetchall()
                if rows:
                    context["related_group_messages"] = [
                        {"sender": r[0], "text": r[1], "time": r[2]} for r in rows
                    ]

            # Material history
            material = entities.get("material")
            if material:
                c.execute(
                    """
                    SELECT sender_name, message_text, timestamp
                    FROM group_messages
                    WHERE LOWER(message_text) LIKE ?
                    ORDER BY timestamp DESC LIMIT 3
                    """,
                    (f"%{material.lower()}%",),
                )
                rows = c.fetchall()
                if rows:
                    context["material_history"] = [
                        {"sender": r[0], "text": r[1], "time": r[2]} for r in rows
                    ]

            # Same topic ke past analyzed messages
            ai_topic = classification.get("topic", "")
            if ai_topic and ai_topic != "unknown":
                c.execute(
                    """
                    SELECT sender_name, ai_topic, intent, timestamp
                    FROM message_analysis
                    WHERE LOWER(ai_topic) LIKE ?
                    ORDER BY timestamp DESC LIMIT 5
                    """,
                    (f"%{ai_topic.lower()[:30]}%",),
                )
                rows = c.fetchall()
                if rows:
                    context["same_topic_history"] = [
                        {"sender": r[0], "topic": r[1], "intent": r[2], "time": r[3]}
                        for r in rows
                    ]

            # Similar past solutions from corrections
            problem = entities.get("problem_type")
            if problem:
                c.execute(
                    """
                    SELECT incoming_message, your_edit
                    FROM message_corrections
                    WHERE LOWER(incoming_message) LIKE ?
                    ORDER BY timestamp DESC LIMIT 3
                    """,
                    (f"%{problem.lower()}%",),
                )
                rows = c.fetchall()
                if rows:
                    context["similar_past_solutions"] = [
                        {"problem": r[0], "solution": r[1]} for r in rows
                    ]

            conn.close()

        except Exception as e:
            print(f"   ⚠️  _get_database_context error: {e}")

        if not context:
            context["note"] = "No matching history in DB"

        return context

    def _get_past_corrections(
        self,
        msg_type: str,
        language: str,
        is_group: bool = False,
        limit: int = 5,
    ) -> List[Dict]:
        """Learning examples from message_corrections."""
        try:
            conn = self._get_conn()
            c = conn.cursor()
            c.execute(
                """
                SELECT incoming_message, ai_suggestion, your_edit, language
                FROM message_corrections
                WHERE language = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (language, limit),
            )
            rows = c.fetchall()
            conn.close()
            return [
                {
                    "incoming_msg" : r[0],
                    "ai_suggestion": r[1],
                    "your_edit"    : r[2],
                    "language"     : r[3],
                }
                for r in rows
            ]
        except Exception as e:
            print(f"   ⚠️  _get_past_corrections error: {e}")
            return []

    # ══════════════════════════════════════════════════════════════════════
    #  Action decision
    # ══════════════════════════════════════════════════════════════════════

    def _decide_group_action(self, classification: Dict, reply_data: Dict) -> Dict:
        msg_type   = classification["message_type"]
        urgency    = classification["urgency"]
        confidence = reply_data["confidence"]
        bot_mentioned = classification.get("bot_mentioned", False)

        if not self.ENABLE_AUTO_REPLY:
            return {
                "action": "queue_approval",
                "reason": "Auto-reply disabled",
                "confidence": confidence,
            }
        if msg_type in self.ALWAYS_QUEUE:
            return {
                "action": "queue_approval",
                "reason": f"{msg_type} always needs review",
                "confidence": confidence,
            }
        if bot_mentioned and confidence >= 90:
            return {
                "action": "auto_send",
                "reason": f"Bot mentioned + high confidence ({confidence}%)",
                "confidence": confidence,
            }
        if msg_type == "factual_question" and confidence >= 90:
            return {
                "action": "auto_send",
                "reason": f"Factual question + high confidence ({confidence}%)",
                "confidence": confidence,
            }
        if msg_type == "technical_problem" and urgency == "critical" and confidence >= 85:
            return {
                "action": "auto_send",
                "reason": "Critical problem + good confidence",
                "confidence": confidence,
            }
        return {
            "action": "queue_approval",
            "reason": f"Group message — review recommended ({confidence}%)",
            "confidence": confidence,
        }

    def _decide_dm_action(self, classification: Dict, reply_data: Dict) -> Dict:
        msg_type   = classification["message_type"]
        confidence = reply_data["confidence"]

        if not self.ENABLE_AUTO_REPLY:
            return {
                "action": "queue_approval",
                "reason": "Auto-reply disabled",
                "confidence": confidence,
            }
        if msg_type in self.ALWAYS_QUEUE:
            return {
                "action": "queue_approval",
                "reason": f"{msg_type} requires review",
                "confidence": confidence,
            }
        if confidence >= self.AUTO_SEND_THRESHOLD and msg_type in self.SAFE_AUTO_SEND:
            return {
                "action": "auto_send",
                "reason": f"High confidence ({confidence}%) + safe type",
                "confidence": confidence,
            }
        if confidence >= 90:
            return {
                "action": "auto_send",
                "reason": f"Very high confidence ({confidence}%)",
                "confidence": confidence,
            }
        return {
            "action": "queue_approval",
            "reason": f"Moderate confidence ({confidence}%)",
            "confidence": confidence,
        }

    # ══════════════════════════════════════════════════════════════════════
    #  Helpers for telegram_bot_groups.py
    # ══════════════════════════════════════════════════════════════════════

    def generate_approval_data(self, result: Dict) -> Dict:
        classification = result["classification"]
        reply          = result.get("reply") or {}
        decision       = result["final_decision"]

        return {
            "sender_name"  : result["input"]["sender_name"],
            "incoming_msg" : result["input"]["message"],
            "ai_suggestion": reply.get("reply", "No response generated"),
            "language"     : result["input"]["language"],
            "confidence"   : reply.get("confidence", 0),
            "action"       : decision["action"],
            "urgency"      : classification.get("urgency", "medium"),
            "message_type" : classification.get("message_type", "unknown"),
            "is_group"     : result["input"].get("is_group", False),
            "chat_title"   : result["input"].get("chat_title", ""),
            "topic_name"   : result["input"].get("topic_name", ""),
            "should_respond": result.get("should_respond", False),
            "reasoning"    : reply.get("reasoning", ""),
            "context"      : result.get("database_context", {}),
            "ai_topic"     : classification.get("topic", ""),
            "analysis_id"  : result.get("analysis_id"),
        }

    def format_notification(self, result: Dict) -> str:
        classification = result["classification"]
        reply          = result.get("reply") or {}
        decision       = result["final_decision"]
        is_group       = result["input"].get("is_group", False)

        if is_group:
            notif = (
                f"\n🔔 NEW GROUP MESSAGE\n{'='*40}\n\n"
                f"👥 Group: {result['input'].get('chat_title','Unknown')}\n"
            )
            if result["input"].get("topic_name"):
                notif += f"📌 Thread: {result['input']['topic_name']}\n"
            notif += f"🧠 AI Topic: {classification.get('topic','?')}\n"
        else:
            notif = f"\n🔔 NEW DIRECT MESSAGE\n{'='*40}\n\n"

        notif += f"📨 From: {result['input']['sender_name']}\n"
        notif += f"💬 Message: {result['input']['message']}\n\n"
        notif += f"📊 ANALYSIS:\n"
        notif += f"• Type     : {classification.get('message_type','unknown')}\n"
        notif += f"• Urgency  : {classification.get('urgency','medium')}\n"
        notif += f"• Confidence: {classification.get('confidence',0)}%\n"

        if result.get("should_respond"):
            notif += (
                f"\n🤖 AI SUGGESTION:\n{reply.get('reply','—')}\n\n"
                f"🎯 ACTION: {decision['action'].upper()}\n"
            )
        else:
            notif += f"\n⏭️  No reply generated — {decision.get('reason','low confidence')}\n"

        return notif

    # ══════════════════════════════════════════════════════════════════════
    #  Internal helper
    # ══════════════════════════════════════════════════════════════════════

    def _get_conn(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn