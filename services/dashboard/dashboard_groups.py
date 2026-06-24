from flask import Flask, render_template, request, jsonify
import sqlite3
import time
import subprocess
import sys
from datetime import datetime

# ⭐ NEW: Import translator
from bot.translation.translator_openai import OpenAITranslator
import os

app = Flask(__name__)

from construction_dashboard import construction_bp
app.register_blueprint(construction_bp)
# ⭐ Initialize translator
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
translator = OpenAITranslator(OPENAI_API_KEY) if OPENAI_API_KEY else None

# ===== DATABASE FUNCTIONS =====
def get_db_connection_read():
    """Read-only connection"""
    conn = sqlite3.connect('bot_data.db', timeout=20, check_same_thread=False)
    conn.execute('PRAGMA query_only = ON')
    return conn

def get_db_connection_write():
    """Write connection with retries"""
    max_retries = 10
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect('bot_data.db', timeout=20, isolation_level='DEFERRED')
            conn.execute("PRAGMA journal_mode=WAL")
            return conn
        except sqlite3.OperationalError as e:
            if attempt < max_retries - 1:
                time.sleep(0.2 * (attempt + 1))
            else:
                raise e

def get_pending_approval(token):
    """Get approval by token"""
    try:
        conn = get_db_connection_read()
        c = conn.cursor()
        c.execute('''SELECT user_id, sender_name, incoming_msg, ai_suggestion, language, timestamp, 
                            is_group, chat_id, chat_title, topic_id, topic_name,
                            source_language, translated_message, original_message
                     FROM pending_approvals WHERE token = ?''', (token,))
        result = c.fetchone()
        conn.close()
        
        if result:
            return {
                'user_id': result[0],
                'sender_name': result[1],
                'incoming_msg': result[2],
                'ai_suggestion': result[3],
                'language': result[4],
                'timestamp': result[5],
                'is_group': bool(result[6]),
                'chat_id': result[7],
                'chat_title': result[8],
                'topic_id': result[9],
                'topic_name': result[10],
                'source_language': result[11] or 'en',
                'translated_message': result[12],
                'original_message': result[13]
            }
        return None
    except Exception as e:
        print(f"❌ Database read error: {e}")
        return None

def get_all_pending_approvals():
    """Get all pending approvals"""
    try:
        conn = get_db_connection_read()
        c = conn.cursor()
        c.execute('''SELECT token, user_id, sender_name, incoming_msg, ai_suggestion, language, timestamp,
                            is_group, chat_title, source_language, original_message, topic_name
                     FROM pending_approvals ORDER BY timestamp DESC''')
        results = c.fetchall()
        conn.close()
        
        approvals = {}
        for row in results:
            approvals[row[0]] = {
                'user_id': row[1],
                'sender_name': row[2],
                'incoming_msg': row[3],
                'ai_suggestion': row[4],
                'language': row[5],
                'timestamp': row[6],
                'is_group': bool(row[7]),
                'chat_title': row[8] or '',
                'source_language': row[9] or 'en',
                'original_message': row[10],
                'topic_name': row[11] or ''
            }
        return approvals
    except Exception as e:
        print(f"❌ Database read error: {e}")
        return {}

def delete_pending_approval(token):
    """Delete approval from database"""
    max_retries = 15
    for attempt in range(max_retries):
        try:
            conn = get_db_connection_write()
            c = conn.cursor()
            c.execute('DELETE FROM pending_approvals WHERE token = ?', (token,))
            conn.commit()
            conn.close()
            print(f"✅ Deleted approval: {token}")
            return True
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                print(f"⏳ Database locked, retry {attempt + 1}/{max_retries}...")
                time.sleep(0.3 * (attempt + 1))
            else:
                print(f"❌ Database error: {e}")
                return False
    print(f"❌ Failed to delete after {max_retries} retries")
    return False
    print(f"❌ Database error: {e}")
    return False
    return False

def store_interaction(user_id, user_name, incoming_msg, ai_response, final_reply, was_edited, language,
                      is_group=False, chat_title=''):
    """
    Learning ke liye interaction save karo.
    Seedha approve ho ya edit ho - dono cases mein save karo.
    """
    try:
        conn = get_db_connection_write()
        c = conn.cursor()
        c.execute('''INSERT INTO message_corrections
                     (user_name, incoming_message, ai_suggestion, your_edit, language, timestamp, is_group, chat_title)
                     VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
                  (user_name, incoming_msg, ai_response, final_reply,
                   language, datetime.now(), int(is_group), chat_title))
        conn.commit()
        conn.close()
        action = "edited" if was_edited else "approved as-is"
        print(f"✅ Learning saved ({action}) for '{user_name}'")
        return True
    except Exception as e:
        print(f"❌ Store interaction error: {e}")
        return False

# ✅ UPDATED: Queue message with correct sender_type
def queue_telegram_message(user_id, message, is_group=False, chat_id=None, topic_id=None, target_language=None):
    """
    Queue message with translation support
    All dashboard messages go via personal account (sender_type='user')
    """
    try:
        conn = get_db_connection_write()
        c = conn.cursor()
        
        # Check if columns exist
        c.execute("PRAGMA table_info(outgoing_messages)")
        columns = [column[1] for column in c.fetchall()]
        
        if 'sender_type' in columns and 'message_category' in columns:
            # ✅ Dashboard messages always via 'user' account (not bot)
            c.execute("""
                INSERT INTO outgoing_messages 
                (user_id, message, created_at, is_group, chat_id, topic_id, target_language, sender_type, message_category)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, message, datetime.now(), int(is_group), chat_id, topic_id, target_language, 'user', 'response'))
        elif 'sender_type' in columns:
            c.execute("""
                INSERT INTO outgoing_messages 
                (user_id, message, created_at, is_group, chat_id, topic_id, target_language, sender_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, message, datetime.now(), int(is_group), chat_id, topic_id, target_language, 'user'))
        else:
            # Fallback for old schema
            c.execute("""
                INSERT INTO outgoing_messages (user_id, message, created_at, is_group, chat_id, topic_id, target_language)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (user_id, message, datetime.now(), int(is_group), chat_id, topic_id, target_language))
        
        conn.commit()
        conn.close()
        print(f"✅ Message queued via personal account (lang: {target_language})")
        return True
    except Exception as e:
        print(f"❌ Queue error: {e}")
        import traceback
        traceback.print_exc()
        return False

# ===== ROUTES =====
@app.route('/')
def home():
    approvals = get_all_pending_approvals()
    
    # Add language names for display
    if translator:
        for token, data in approvals.items():
            lang_code = data.get('source_language', 'en')
            data['language_name'] = translator.LANGUAGES.get(lang_code, lang_code)
    
    return render_template('home.html', approvals=approvals)

@app.route('/approve/<token>')
def approve_page(token):
    data = get_pending_approval(token)
    if not data:
        return render_template('error.html', message="Invalid or expired approval link")
    
    # Add language name
    if translator:
        lang_code = data.get('source_language', 'en')
        data['language_name'] = translator.LANGUAGES.get(lang_code, lang_code)
    
    return render_template('approve.html', token=token, data=data)

@app.route('/api/send/<token>', methods=['POST'])
def send_message(token):
    print(f"\n{'='*60}")
    print(f"📤 SEND REQUEST for token: {token}")
    
    data = get_pending_approval(token)
    if not data:
        print(f"❌ Invalid token")
        return jsonify({'success': False, 'error': 'Invalid token'})
    
    try:
        print(f"📨 Queuing message to {data['sender_name']}...")
        
        # ⭐ TRANSLATION: Queue with target language
        target_lang = data.get('source_language', 'en')
        
        if data['is_group']:
            # Group message
            success = queue_telegram_message(
                user_id=data['user_id'],
                message=data['ai_suggestion'],
                is_group=True,
                chat_id=data['chat_id'],
                topic_id=data['topic_id'],
                target_language=target_lang
            )
        else:
            # DM
            success = queue_telegram_message(
                user_id=data['user_id'],
                message=data['ai_suggestion'],
                target_language=target_lang
            )
        
        if success:
            # Learning: seedha approve kiya - AI suggestion hi final reply hai
            store_interaction(
                user_id=data['user_id'],
                user_name=data['sender_name'],
                incoming_msg=data['incoming_msg'],
                ai_response=data['ai_suggestion'],
                final_reply=data['ai_suggestion'],  # Edit nahi hua
                was_edited=False,
                language=data['language'],
                is_group=data.get('is_group', False),
                chat_title=data.get('chat_title', '')
            )
            
            print(f"✅ Message queued successfully!")
            delete_pending_approval(token)
            
            # Show translation info
            if translator and target_lang != 'en':
                lang_name = translator.LANGUAGES.get(target_lang, target_lang)
                message = f"Message queued for {data['sender_name']}! Will be translated to {lang_name}."
            else:
                message = f"Message queued for {data['sender_name']}!"
            
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            print(f"❌ Failed to queue message")
            return jsonify({'success': False, 'error': 'Failed to queue message'})
            
    except Exception as e:
        print(f"❌ Error in send_message: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        print(f"{'='*60}\n")

@app.route('/api/edit/<token>', methods=['POST'])
def edit_message(token):
    print(f"\n{'='*60}")
    print(f"✏️ EDIT REQUEST for token: {token}")
    
    data = get_pending_approval(token)
    if not data:
        print(f"❌ Invalid token")
        return jsonify({'success': False, 'error': 'Invalid token'})
    
    edited_message = request.json.get('message', '').strip()
    if not edited_message:
        return jsonify({'success': False, 'error': 'Message cannot be empty'})
    
    try:
        print(f"📨 Queuing edited message to {data['sender_name']}...")
        
        # ⭐ TRANSLATION: Queue edited message with target language
        target_lang = data.get('source_language', 'en')
        
        if data['is_group']:
            send_success = queue_telegram_message(
                user_id=data['user_id'],
                message=edited_message,
                is_group=True,
                chat_id=data['chat_id'],
                topic_id=data['topic_id'],
                target_language=target_lang
            )
        else:
            send_success = queue_telegram_message(
                user_id=data['user_id'],
                message=edited_message,
                target_language=target_lang
            )
        
        if send_success:
            print(f"✅ Edited message queued!")
            print(f"📚 Storing learning...")

            # Learning: edited reply save karo
            store_interaction(
                user_id=data['user_id'],
                user_name=data['sender_name'],
                incoming_msg=data['incoming_msg'],
                ai_response=data['ai_suggestion'],
                final_reply=edited_message,   # Tumne jo likha
                was_edited=True,
                language=data['language'],
                is_group=data.get('is_group', False),
                chat_title=data.get('chat_title', '')
            )
            
            delete_pending_approval(token)
            
            # Show translation info
            if translator and target_lang != 'en':
                lang_name = translator.LANGUAGES.get(target_lang, target_lang)
                message = f"Edited message queued for {data['sender_name']}! Will be translated to {lang_name}."
            else:
                message = f"Edited message queued for {data['sender_name']}!"
            
            return jsonify({
                'success': True,
                'message': message
            })
        else:
            print(f"❌ Failed to queue edited message")
            return jsonify({'success': False, 'error': 'Failed to queue message'})
            
    except Exception as e:
        print(f"❌ Error in edit_message: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})
    finally:
        print(f"{'='*60}\n")

@app.route('/api/skip/<token>', methods=['POST'])
def skip_message(token):
    print(f"\n{'='*60}")
    print(f"⏭️ SKIP REQUEST for token: {token}")
    
    data = get_pending_approval(token)
    if not data:
        print(f"❌ Invalid token")
        return jsonify({'success': False, 'error': 'Invalid token'})
    
    print(f"🗑️ Deleting from database...")
    delete_success = delete_pending_approval(token)
    
    if delete_success:
        print(f"✅ Skipped successfully")
        return jsonify({
            'success': True,
            'message': f"Message from {data['sender_name']} skipped"
        })
    else:
        print(f"❌ Failed to skip")
        return jsonify({
            'success': False,
            'error': 'Failed to skip message'
        })

@app.route('/api/translate/<token>', methods=['POST'])
def translate_suggestion(token):
    """⭐ NEW: Translate AI suggestion to different language"""
    print(f"\n{'='*60}")
    print(f"🌍 TRANSLATE REQUEST for token: {token}")
    
    data = get_pending_approval(token)
    if not data:
        return jsonify({'success': False, 'error': 'Invalid token'})
    
    target_lang = request.json.get('language', 'en')
    
    if not translator:
        return jsonify({'success': False, 'error': 'Translator not initialized'})
    
    try:
        result = translator.translate(
            text=data['ai_suggestion'],
            target_lang=target_lang,
            source_lang='en'  # Assuming AI suggestions are in English
        )
        
        return jsonify({
            'success': True,
            'translated_text': result['translated_text'],
            'language': translator.LANGUAGES.get(target_lang, target_lang)
        })
        
    except Exception as e:
        print(f"❌ Translation error: {e}")
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    print("\n" + "="*70)
    print("🌐 WEB DASHBOARD - TRANSLATION ENABLED")
    print("="*70)
    print("💾 Database: SQLite (bot_data.db)")
    print("🔒 Locking: WAL mode + 15 retries")
    print("🚀 Telegram: Database queue")
    print("🌍 Translation: OpenAI GPT-4")
    if not translator:
        print("⚠️  WARNING: OPENAI_API_KEY not set - translation disabled!")
    print("⚠️  Make sure telegram_bot_groups_translated.py is running!")
    print("="*70 + "\n")
    
    app.run(host="0.0.0.0", debug=True, port=5000, use_reloader=False, threaded=True)