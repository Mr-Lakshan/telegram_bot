"""
DAILY REPORT — AI-Powered Evening Summary at 19:00
=====================================================
Every day at 19:00, generates a comprehensive report:
  - How many questions were asked today
  - Which were answered by AI vs employees
  - Which topics came up most often
  - Which questions are still unanswered
  - AI improvement suggestions (powered by Claude Opus 4.7)

Uses Claude Opus 4.7 for maximum quality analysis — runs only once daily.

Usage:
    report = DailyReport(db_path, bot_client, approval_chat_id, anthropic_api_key)
    await report.generate_and_send()  # Call at 19:00 via scheduler
    report.start_scheduler()          # Auto-run at 19:00 daily
"""

import os
import json
import sqlite3
import asyncio
import requests
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

from bot.knowledge.conversation_learner import ConversationLearner


# ── Config ──
OPUS_MODEL = "claude-opus-4-7"
# 23.10.2026: time can be set in .env (DAILY_REPORT_HOUR / DAILY_REPORT_MINUTE) — default unchanged 19:00
REPORT_HOUR = int(os.getenv("DAILY_REPORT_HOUR", "19"))  # German time
REPORT_MINUTE = int(os.getenv("DAILY_REPORT_MINUTE", "0"))
# 23.10.2026 — short call report (calls per employee) right after the daily report. Off: CALL_REPORT_ENABLED=False
CALL_REPORT_ENABLED = os.getenv("CALL_REPORT_ENABLED", "True") == "True"
# Standardmäßig KURZER Bericht (nur Zahlen, keine KI-Story). Wieder ausführlich via .env DAILY_REPORT_FULL=True
DAILY_REPORT_FULL = os.getenv("DAILY_REPORT_FULL", "False") == "True"

# Germany timezone (CET = UTC+1, CEST = UTC+2)
# Using fixed offset — for proper DST handling, install pytz
try:
    from zoneinfo import ZoneInfo
    GERMANY_TZ = ZoneInfo("Europe/Berlin")
except ImportError:
    # Fallback: CEST (summer time) = UTC+2
    GERMANY_TZ = timezone(timedelta(hours=2))


def _now_germany():
    """Get current time in Germany timezone."""
    return datetime.now(GERMANY_TZ)


class DailyReport:
    """
    Generates and sends daily AI analysis report.
    """

    def __init__(
        self,
        db_path: str = "bot_data.db",
        bot_client=None,
        approval_chat_id: int = 0,
        anthropic_api_key: str = "",
        prefilter=None,
        classifier=None,
        kb=None,
        suggestion_manager=None,
    ):
        self.db_path = db_path
        self.bot = bot_client
        self.chat_id = approval_chat_id
        self.api_key = anthropic_api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.prefilter = prefilter
        self.classifier = classifier
        self.kb = kb
        self.suggestion_manager = suggestion_manager
        self.crm_url = os.getenv("CRM_API_URL", "")
        self.crm_key = os.getenv("CRM_BOT_API_KEY", "")

        self._scheduler_task = None
        self.learner = ConversationLearner(
            db_path=db_path,
            anthropic_api_key=self.api_key,
            kb=kb,
            suggestion_manager=suggestion_manager,
        )
        print(f"✅ DailyReport initialized (model: {OPUS_MODEL}, time: {REPORT_HOUR}:{REPORT_MINUTE:02d})")

    def _get_db(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ══════════════════════════════════════════════════════════════════════
    #  SCHEDULER — auto-run at 19:00 daily
    # ══════════════════════════════════════════════════════════════════════

    def start_scheduler(self):
        """Start background task that runs report at REPORT_HOUR:REPORT_MINUTE daily."""
        self._scheduler_task = asyncio.ensure_future(self._scheduler_loop())
        print(f"🕐 Daily report scheduler started (every day at {REPORT_HOUR}:{REPORT_MINUTE:02d})")

    async def _scheduler_loop(self):
        """Background loop — waits until 19:00 German time, generates report, repeats."""
        while True:
            try:
                now = _now_germany()
                target = now.replace(hour=REPORT_HOUR, minute=REPORT_MINUTE, second=0, microsecond=0)

                # If already past 19:00 today in Germany, schedule for tomorrow
                if now >= target:
                    target += timedelta(days=1)

                wait_seconds = (target - now).total_seconds()
                hours_left = wait_seconds / 3600
                print(f"🕐 Next daily report in {hours_left:.1f} hours ({target.strftime('%d.%m.%Y %H:%M')} German time)")

                await asyncio.sleep(wait_seconds)

                # Generate and send report
                print(f"\n{'='*60}")
                print(f"📊 DAILY REPORT — {_now_germany().strftime('%d.%m.%Y %H:%M')} (German time)")
                print(f"{'='*60}")
                await self.generate_and_send()
                # 23.10.2026: calls per employee — also when the chat report was skipped
                if CALL_REPORT_ENABLED:
                    await self.send_call_report()

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"⚠️ Daily report scheduler error: {e}")
                await asyncio.sleep(3600)  # Wait 1 hour on error

    # ══════════════════════════════════════════════════════════════════════
    #  GENERATE: Collect data + AI analysis
    # ══════════════════════════════════════════════════════════════════════

    async def generate_and_send(self):
        """Generate the daily report and send to approval chat."""
        try:
            # Collect today's data
            data = self._collect_data()

            if data['total_messages'] == 0:
                print("   ℹ️ No messages today — skipping report")
                return

            # KI-Analyse (Story) nur im ausführlichen Modus — sonst nur Zahlen
            ai_analysis = self._generate_ai_analysis(data) if DAILY_REPORT_FULL else ""

            # Format report
            report_text = self._format_report(data, ai_analysis)

            # Send to approval chat
            if self.bot and self.chat_id:
                await self.bot.send_message(
                    self.chat_id,
                    report_text,
                    parse_mode='md',
                )
                print(f"   ✅ Daily report sent to approval chat!")
            else:
                print(f"   ⚠️ Cannot send — bot or chat_id not configured")
                print(report_text)

            # ── Conversation Learning: SUGGEST knowledge from today's chats ──
            try:
                if self.suggestion_manager:
                    # Phase B: suggest with buttons (Lothar confirms)
                    result = await self.learner.suggest_daily_learnings()
                    suggested = result.get('suggested', 0)
                    if suggested == 0 and self.bot and self.chat_id:
                        await self.bot.send_message(
                            self.chat_id,
                            "📚 Heute keine neuen Wissensdatenbank-Vorschläge.",
                        )
                else:
                    # Fallback: auto-save
                    learning_result = self.learner.extract_daily_learnings()
                    saved = learning_result.get('learnings_saved', 0)
                    if saved > 0 and self.bot and self.chat_id:
                        await self.bot.send_message(
                            self.chat_id,
                            f"📚 **Wissensdatenbank Update:** {saved} neue Erkenntnisse aus den heutigen Gesprächen gespeichert.",
                        )
            except Exception as e:
                print(f"   ⚠️ Conversation learning error: {e}")

            # Reset stats
            if self.prefilter:
                self.prefilter.reset_stats()
            if self.classifier:
                self.classifier.reset_stats()
            if self.kb:
                self.kb.reset_stats()

        except Exception as e:
            print(f"   ❌ Daily report error: {e}")
            import traceback; traceback.print_exc()

    # ══════════════════════════════════════════════════════════════════════
    #  COLLECT: Gather today's data from DB
    # ══════════════════════════════════════════════════════════════════════

    def _collect_data(self) -> Dict:
        """Collect all relevant data from today."""
        conn = self._get_db()
        c = conn.cursor()

        today_start = _now_germany().replace(hour=0, minute=0, second=0).strftime('%Y-%m-%d %H:%M:%S')

        # Total messages stored
        c.execute("SELECT COUNT(*) FROM group_messages WHERE timestamp >= ?", (today_start,))
        total_messages = c.fetchone()[0]

        # AI approvals today
        c.execute("""
            SELECT status, COUNT(*) as cnt
            FROM ai_approvals
            WHERE created_at >= ?
            GROUP BY status
        """, (today_start,))
        approval_stats = {row['status']: row['cnt'] for row in c.fetchall()}

        # Questions with details
        c.execute("""
            SELECT question_text, ai_suggestion, sender_name, chat_title,
                   ai_topic, ai_confidence, status, employee_name, employee_answer,
                   classification_type
            FROM ai_approvals
            WHERE created_at >= ?
            ORDER BY created_at
        """, (today_start,))
        questions = [dict(row) for row in c.fetchall()]

        # Group activity
        c.execute("""
            SELECT chat_title, COUNT(*) as msg_count
            FROM group_messages
            WHERE timestamp >= ? AND chat_title != ''
            GROUP BY chat_title
            ORDER BY msg_count DESC
            LIMIT 10
        """, (today_start,))
        group_activity = [dict(row) for row in c.fetchall()]

        # KB stats
        kb_stats = self.kb.get_stats() if self.kb else {}

        # Pre-filter stats
        prefilter_stats = self.prefilter.get_stats() if self.prefilter else {}

        # Classifier stats
        classifier_stats = self.classifier.get_stats() if self.classifier else {}

        conn.close()

        # Fetch CRM lead stats
        lead_stats = self._fetch_crm_lead_stats()

        return {
            'date': _now_germany().strftime('%d.%m.%Y'),
            'total_messages': total_messages,
            'approval_stats': approval_stats,
            'questions': questions,
            'group_activity': group_activity,
            'kb_stats': kb_stats,
            'prefilter_stats': prefilter_stats,
            'classifier_stats': classifier_stats,
            'total_questions': len(questions),
            'approved': approval_stats.get('approved', 0),
            'edited': approval_stats.get('edited', 0),
            'rejected': approval_stats.get('rejected', 0),
            'employee_answered': approval_stats.get('employee_answered', 0),
            'pending': approval_stats.get('pending', 0),
            'lead_stats': lead_stats,
        }


    # ══════════════════════════════════════════════════════════════════════
    #  CRM: Fetch lead statistics
    # ══════════════════════════════════════════════════════════════════════

    def _fetch_crm_lead_stats(self) -> Dict:
        """Fetch today's lead stats from CRM API."""
        if not self.crm_url or not self.crm_key:
            return {}
        try:
            today = _now_germany().strftime('%Y-%m-%d')
            resp = requests.get(
                self.crm_url,
                params={'action': 'get_daily_lead_stats', 'date': today},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=15,
            )
            data = resp.json()
            if data.get('success'):
                return data
            return {}
        except Exception as e:
            print(f"   Warning: CRM lead stats error: {e}")
            return {}

    # ══════════════════════════════════════════════════════════════════════
    #  AI ANALYSIS: Claude Opus for business insights
    # ══════════════════════════════════════════════════════════════════════

    def _generate_ai_analysis(self, data: Dict) -> str:
        """Use Claude Opus 4.7 — short, business-focused analysis."""

        if not self.api_key:
            return "Keine KI-Analyse verfuegbar (API-Key fehlt)"

        questions_summary = []
        for q in data['questions'][:20]:
            status_icon = {'approved': 'OK', 'edited': 'EDIT', 'rejected': 'NEIN',
                'employee_answered': 'MITARBEITER', 'pending': 'OFFEN'}.get(q.get('status', ''), '?')
            q_text = (q.get('question_text') or 'keine Frage')[:100]
            q_title = q.get('chat_title') or '?'
            q_sender = q.get('sender_name') or '?'
            questions_summary.append(f"[{q_title}] {q_sender}: {q_text} -> {status_icon}")

        questions_text = "\n".join(questions_summary) if questions_summary else "Keine Fragen heute"

        lead_stats = data.get('lead_stats', {})
        lead_text = "Keine CRM-Daten verfuegbar"
        if lead_stats:
            total = lead_stats.get('total_leads_today', 0)
            by_product = lead_stats.get('by_product', {})
            by_source = lead_stats.get('by_source', {})
            if isinstance(by_product, list): by_product = {}
            if isinstance(by_source, list): by_source = {}
            p_lines = ", ".join([f"{k}: {v}" for k, v in by_product.items()]) or "keine"
            s_lines = ", ".join([f"{k}: {v}" for k, v in by_source.items()]) or "keine"
            lead_text = f"Neue Leads heute: {total} | Produkte: {p_lines} | Quellen: {s_lines}"

        system_prompt = """Du bist ein KI-Berater fuer den Geschaeftsfuehrer eines Badsanierungs-Unternehmens (Premiobad/Seniorex).

Erstelle eine KURZE, PRAEGNANTE Tagesanalyse (max 10 Saetze). Der Chef will KEINE langweiligen Statistiken, sondern:
1. Was war heute geschaeftlich wichtig? (Leads, neue Kunden, Baustellen)
2. Gab es Probleme oder Auffaelligkeiten?
3. 1-2 konkrete Verbesserungsvorschlaege

Schreibe wie ein persoenlicher Berater, nicht wie ein Report-Generator. Auf Deutsch."""

        user_prompt = f"TAGESUEBERSICHT {data['date']}\n\nLEADS: {lead_text}\n\nFRAGEN ({data['total_questions']} erkannt):\n{questions_text}\n\nWissensdatenbank: {data.get('kb_stats', {}).get('total_entries', 0)} Eintraege\n\nKurze Analyse bitte."

        try:
            headers = {
                "x-api-key": self.api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            }
            payload = {
                "model": OPUS_MODEL,
                "max_tokens": 600,
                "system": system_prompt,
                "messages": [{"role": "user", "content": user_prompt}],
            }

            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=headers, json=payload, timeout=60,
            )

            if resp.status_code != 200:
                error_body = resp.text[:200]
                print(f"   Warning: Opus API error {resp.status_code}: {error_body}")
                return f"KI-Analyse fehlgeschlagen (HTTP {resp.status_code})"

            result = resp.json()

            text = ""
            for block in result.get("content", []):
                if block.get("type") == "text":
                    text += block["text"]

            return text.strip() if text else "Analyse konnte nicht erstellt werden."

        except Exception as e:
            print(f"   Warning: Opus analysis error: {e}")
            return f"KI-Analyse fehlgeschlagen: {str(e)[:100]}"
    # ══════════════════════════════════════════════════════════════════════
    #  FORMAT: Short, business-focused report
    # ══════════════════════════════════════════════════════════════════════

    def _format_report(self, data: Dict, ai_analysis: str) -> str:
        """Format the daily report — short, business-focused, no boring stats."""

        lines = [
            f"📊 **Tagesbericht — {data['date']}**",
            "",
        ]

        # Lead stats (most important for Lothar)
        lead_stats = data.get('lead_stats', {})
        if lead_stats and lead_stats.get('total_leads_today', 0) > 0:
            total = lead_stats['total_leads_today']
            lines.append(f"📥 **Neue Leads heute: {total}**")
            by_prod = lead_stats.get('by_product', {})
            if isinstance(by_prod, list): by_prod = {}
            for product, count in by_prod.items():
                lines.append(f"  • {product}: {count}")
            # Quelle/Herkunft bewusst weggelassen (Lothar braucht das nicht)
            lines.append("")

        # Questions summary (brief)
        if data['total_questions'] > 0:
            answered = data['approved'] + data['edited'] + data['employee_answered']
            lines.append(f"❓ **{data['total_questions']} Fragen** | {answered} beantwortet | {data['pending']} offen")
            lines.append("")

        # KB brief
        kb = data.get('kb_stats', {})
        if kb.get('total_entries', 0) > 0:
            lines.append(f"📚 Wissensdatenbank: {kb['total_entries']} Einträge")
            lines.append("")

        # KI-Analyse (Story) nur im ausführlichen Modus
        if DAILY_REPORT_FULL and ai_analysis:
            lines.extend(["🧠 **KI-Analyse:**", "", ai_analysis])

        return "\n".join(lines)

    #  MANUAL TRIGGER
    # ══════════════════════════════════════════════════════════════════════

    async def manual_trigger(self):
        """Manually trigger the daily report (for testing)."""
        print("📊 Manual daily report trigger...")
        await self.generate_and_send()
        if CALL_REPORT_ENABLED:
            await self.send_call_report()

    # ══════════════════════════════════════════════════════════════════════
    #  CALL REPORT — 23.10.2026
    # ══════════════════════════════════════════════════════════════════════
    # Lothar (21.10.2026): "Ich möchte jeden Abend auf einen Blick sehen, wer wie viel
    # telefoniert hat … Zusätzlich die Erfolgsquote: wie viele Anrufversuche waren
    # notwendig, um einen Termin oder einen ausgefüllten Antrag zu erzielen?"
    # Short on purpose — per employee one line. The full list of calls is on the CRM page
    # Anrufanalyse (link at the end). Numbers come from the CRM (api/bot_construction.php
    # ?action=get_daily_call_stats) = the same calculation as that page.

    def _fetch_crm_call_stats(self, date: str) -> Optional[Dict]:
        """Calls per employee for one day from the CRM. None if the CRM cannot be reached."""
        if not self.crm_url or not self.crm_key:
            print("   ⚠️ Call report: CRM_API_URL / CRM_BOT_API_KEY missing in .env")
            return None
        try:
            resp = requests.get(
                self.crm_url,
                params={'action': 'get_daily_call_stats', 'date': date},
                headers={'X-Bot-Api-Key': self.crm_key},
                timeout=30,
            )
            data = resp.json()
            return data if data.get('success') else None
        except Exception as e:
            print(f"   ⚠️ Call report: CRM error: {e}")
            return None

    @staticmethod
    def _call_num(v, decimals: int = 0) -> str:
        """German number format: 8.1 -> '8,1'."""
        if v is None:
            return '—'
        txt = f"{float(v):.{decimals}f}"
        if '.' in txt:
            txt = txt.rstrip('0').rstrip('.')
        return txt.replace('.', ',')

    @staticmethod
    def _call_count(n, one: str, many: str) -> str:
        """1 Termin / 2 Termine."""
        n = int(n or 0)
        return f"{n} {one if n == 1 else many}"

    @staticmethod
    def _call_day_title(date: str) -> str:
        """'heute' for today, otherwise the weekday + date."""
        d = datetime.strptime(date, '%Y-%m-%d')
        tag = ['Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So'][d.weekday()]
        if date == _now_germany().strftime('%Y-%m-%d'):
            return f"heute — {tag} {d.strftime('%d.%m.%Y')}"
        return f"{tag} {d.strftime('%d.%m.%Y')}"

    @staticmethod
    def _call_duration(seconds) -> str:
        s = int(seconds or 0)
        if s < 60:
            return f"{s} Sek."
        if s < 3600:
            return f"{round(s / 60)} Min."
        return f"{s // 3600}:{(s % 3600) // 60:02d} Std."

    @staticmethod
    def _call_name(name: str) -> str:
        """Names go between ** … ** — remove characters Telegram markdown would misread."""
        return ''.join(ch for ch in str(name or '?') if ch not in '*_`[]').strip() or '?'

    def _call_link(self, date: str) -> str:
        """Link to the CRM page Anrufanalyse for that day."""
        if '/api/' not in self.crm_url:
            return ''
        base = self.crm_url.split('/api/')[0]
        return f"{base}/call-analysis.php?von={date}&bis={date}"

    def _format_call_report(self, stats: Dict, date: str) -> str:
        """Short: one line per employee + total + success rate + link."""
        lines = [f"📞 **Anrufe {self._call_day_title(date)}**", ""]
        employees = stats.get('employees') or []
        for e in employees:
            if not e.get('attempts') and not e.get('appointments') and not e.get('applications'):
                continue
            line = (f"**{self._call_name(e.get('name'))}**: {self._call_count(e.get('attempts'), 'Anruf', 'Anrufe')} · "
                    f"{e.get('customers_reached', 0)} erreicht · {self._call_count(e.get('appointments'), 'Termin', 'Termine')} · "
                    f"{self._call_count(e.get('applications'), 'Antrag', 'Anträge')}")
            extra = []
            if e.get('talk_seconds'):
                extra.append(f"⏱ {self._call_duration(e['talk_seconds'])}")
            if e.get('attempts_per_appointment') is not None:
                extra.append(f"{self._call_num(e['attempts_per_appointment'], 1)} Anrufe je Termin")
            lines.append(line)
            if extra:
                lines.append("   " + " · ".join(extra))
        t = stats.get('total') or {}
        if len(employees) > 1:
            lines.append("")
            lines.append(f"**Gesamt: {self._call_count(t.get('attempts'), 'Anruf', 'Anrufe')} · {t.get('customers_reached', 0)} erreicht · "
                         f"{self._call_count(t.get('appointments'), 'Termin', 'Termine')} · "
                         f"{self._call_count(t.get('applications'), 'Antrag', 'Anträge')}**")
        rate = []
        if t.get('talk_seconds'):
            rate.append(f"⏱ Gesprächszeit {self._call_duration(t['talk_seconds'])}")
        if t.get('attempts_per_appointment') is not None:
            rate.append(f"🎯 {self._call_num(t['attempts_per_appointment'], 1)} Anrufe je Termin")
        if t.get('attempts_per_application') is not None:
            rate.append(f"{self._call_num(t['attempts_per_application'], 1)} je Antrag")
        if rate:
            lines.append(" · ".join(rate))
        link = self._call_link(date)
        if link:
            lines.extend(["", f"Alle Anrufe: {link}"])
        return "\n".join(lines)

    async def send_call_report(self, date: Optional[str] = None) -> bool:
        """Send the short call report to KI Freigaben. Never raises."""
        try:
            date = date or _now_germany().strftime('%Y-%m-%d')
            stats = self._fetch_crm_call_stats(date)
            if stats is None:
                text = "📞 Anrufbericht: CRM nicht erreichbar — bitte in der Anrufanalyse im CRM nachsehen."
            elif not (stats.get('total') or {}).get('attempts') and not (stats.get('total') or {}).get('appointments'):
                # nothing called: on weekends say nothing, on workdays one short line
                if datetime.strptime(date, '%Y-%m-%d').weekday() >= 5:
                    print("   ℹ️ Call report: no calls (weekend) — skipped")
                    return False
                text = f"📞 Anrufe {self._call_day_title(date)}: keine."
            else:
                text = self._format_call_report(stats, date)
            if self.bot and self.chat_id:
                await self.bot.send_message(self.chat_id, text, parse_mode='md', link_preview=False)
                print("   ✅ Call report sent to approval chat")
                return True
            print(text)
            return False
        except Exception as e:
            print(f"   ⚠️ Call report error: {e}")
            return False