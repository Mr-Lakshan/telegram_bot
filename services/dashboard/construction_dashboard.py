#!/usr/bin/env python3
"""
CONSTRUCTION DASHBOARD ROUTES
==============================
Add these routes to dashboard_groups.py or import as a Blueprint.

Usage Option 1 — Blueprint (recommended):
    from construction_dashboard import construction_bp
    app.register_blueprint(construction_bp)

Usage Option 2 — Copy routes into dashboard_groups.py
"""

from flask import Blueprint, render_template, request, jsonify, render_template_string
from datetime import datetime
from bot.handlers.construction_video_handler import (
    get_construction_description,
    get_pending_descriptions,
    get_all_descriptions,
    approve_construction_description,
    discard_construction_description,
    update_customer_match,
    get_todays_appointments_from_cache,
    crm_update_description,
)

construction_bp = Blueprint('construction', __name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES
# ═══════════════════════════════════════════════════════════════════════════════

@construction_bp.route('/construction')
def construction_home():
    """List all pending construction descriptions"""
    pending = get_pending_descriptions()
    all_descs = get_all_descriptions(limit=20)
    return render_template_string(
        CONSTRUCTION_HOME_TEMPLATE,
        pending=pending,
        all_descriptions=all_descs,
    )


@construction_bp.route('/construction/<token>')
def construction_detail(token):
    """View/edit a single construction description"""
    data = get_construction_description(token)
    if not data:
        return render_template_string(ERROR_TEMPLATE, message="Ungültiger oder abgelaufener Link"), 404

    appointments = get_todays_appointments_from_cache()
    return render_template_string(
        CONSTRUCTION_DETAIL_TEMPLATE,
        token=token,
        data=data,
        appointments=appointments,
    )


@construction_bp.route('/api/construction/approve/<token>', methods=['POST'])
def api_approve(token):
    """Approve a construction description"""
    data = get_construction_description(token)
    if not data:
        return jsonify({'success': False, 'error': 'Ungültiger Token'})

    body = request.json or {}
    final_description = body.get('description', data['generated_description'])
    crm_record_id = body.get('crm_record_id', data.get('crm_record_id', ''))
    customer_name = body.get('customer_name', data.get('customer_name', ''))

    success = approve_construction_description(
        token=token,
        final_description=final_description,
        crm_record_id=crm_record_id,
        customer_name=customer_name,
    )

    if success:
        # Push to CRM if record_id is available
        crm_result = {'success': False, 'error': 'No CRM record ID'}
        if crm_record_id:
            try:
                crm_result = crm_update_description(
                    record_id=int(crm_record_id),
                    description=final_description,
                )
            except Exception as e:
                print(f"❌ CRM push error: {e}")
                crm_result = {'success': False, 'error': str(e)}

        crm_saved = crm_result.get('success', False)
        msg = f'Baubeschreibung für {customer_name} gespeichert!'
        if crm_saved:
            msg += ' ✅ CRM aktualisiert.'
        elif crm_record_id:
            msg += f' ⚠️ CRM Update fehlgeschlagen: {crm_result.get("error", "unbekannt")}'

        return jsonify({'success': True, 'message': msg, 'crm_saved': crm_saved})
    return jsonify({'success': False, 'error': 'Speichern fehlgeschlagen'})


@construction_bp.route('/api/construction/discard/<token>', methods=['POST'])
def api_discard(token):
    """Discard a construction description"""
    success = discard_construction_description(token)
    if success:
        return jsonify({'success': True, 'message': 'Baubeschreibung verworfen'})
    return jsonify({'success': False, 'error': 'Verwerfen fehlgeschlagen'})


@construction_bp.route('/api/construction/update-customer/<token>', methods=['POST'])
def api_update_customer(token):
    """Update customer assignment"""
    body = request.json or {}
    success = update_customer_match(
        token=token,
        crm_record_id=body.get('crm_record_id', ''),
        customer_name=body.get('customer_name', ''),
        customer_address=body.get('customer_address', ''),
        calendar_event_id=body.get('calendar_event_id', ''),
    )
    if success:
        return jsonify({'success': True, 'message': 'Kundenzuordnung aktualisiert'})
    return jsonify({'success': False, 'error': 'Update fehlgeschlagen'})


# ═══════════════════════════════════════════════════════════════════════════════
# HTML TEMPLATES (inline — move to templates/ folder for production)
# ═══════════════════════════════════════════════════════════════════════════════

ERROR_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Fehler</title>
    <style>
        body { font-family: -apple-system, system-ui, sans-serif; padding: 40px; text-align: center; background: #1a1a2e; color: #eee; }
        .error { background: #16213e; border: 1px solid #e94560; border-radius: 12px; padding: 40px; max-width: 400px; margin: 0 auto; }
        h2 { color: #e94560; }
    </style>
</head>
<body>
    <div class="error">
        <h2>❌ Fehler</h2>
        <p>{{ message }}</p>
        <a href="/construction" style="color: #0f3460;">← Zurück</a>
    </div>
</body>
</html>
"""

CONSTRUCTION_HOME_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Baudokumentation — Dashboard</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: #0f0f1a;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 24px 20px;
            border-bottom: 1px solid #2a2a4a;
        }
        .header h1 { font-size: 20px; color: #fff; }
        .header p { color: #888; font-size: 13px; margin-top: 4px; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }

        .section-title {
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #e94560;
            margin: 24px 0 12px;
            padding-bottom: 8px;
            border-bottom: 1px solid #2a2a4a;
        }

        .card {
            background: #16213e;
            border: 1px solid #2a2a4a;
            border-radius: 10px;
            padding: 16px;
            margin-bottom: 12px;
            transition: border-color 0.2s;
        }
        .card:hover { border-color: #e94560; }
        .card a { text-decoration: none; color: inherit; display: block; }
        .card-header { display: flex; justify-content: space-between; align-items: center; }
        .card-title { font-size: 16px; font-weight: 600; color: #fff; }
        .card-meta { font-size: 12px; color: #888; margin-top: 6px; }
        .card-preview { font-size: 13px; color: #aaa; margin-top: 8px; line-height: 1.5; }

        .badge {
            font-size: 11px;
            padding: 3px 10px;
            border-radius: 20px;
            font-weight: 600;
        }
        .badge-pending { background: #e9456020; color: #e94560; border: 1px solid #e94560; }
        .badge-approved { background: #00b87c20; color: #00b87c; border: 1px solid #00b87c; }
        .badge-discarded { background: #66666620; color: #888; border: 1px solid #666; }
        .badge-failed { background: #ff444420; color: #ff4444; border: 1px solid #ff4444; }

        .empty { text-align: center; padding: 40px; color: #666; }
        .nav-link { display: inline-block; margin-top: 16px; color: #0f3460; font-size: 13px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🏗️ Baudokumentation</h1>
        <p>Video → Transkription → Baubeschreibung</p>
    </div>
    <div class="container">
        <div class="section-title">⏳ Ausstehend ({{ pending|length }})</div>
        {% if pending %}
            {% for desc in pending %}
            <div class="card">
                <a href="/construction/{{ desc.token }}">
                    <div class="card-header">
                        <span class="card-title">
                            📍 {{ desc.customer_name or 'Nicht zugeordnet' }}
                        </span>
                        <span class="badge badge-pending">Ausstehend</span>
                    </div>
                    <div class="card-meta">
                        {{ desc.media_type }} · {{ desc.appointment_date or '' }} · {{ desc.created_at[:16] if desc.created_at else '' }}
                    </div>
                    <div class="card-preview">
                        {{ desc.generated_description[:200] if desc.generated_description else 'Wird verarbeitet...' }}...
                    </div>
                </a>
            </div>
            {% endfor %}
        {% else %}
            <div class="empty">Keine ausstehenden Beschreibungen</div>
        {% endif %}

        <div class="section-title">📋 Verlauf</div>
        {% for desc in all_descriptions %}
        <div class="card">
            <a href="/construction/{{ desc.token }}">
                <div class="card-header">
                    <span class="card-title">{{ desc.customer_name or 'Unbekannt' }}</span>
                    {% if desc.status == 'approved' %}
                        <span class="badge badge-approved">✅ Bestätigt</span>
                    {% elif desc.status == 'discarded' %}
                        <span class="badge badge-discarded">Verworfen</span>
                    {% elif desc.status == 'failed' %}
                        <span class="badge badge-failed">Fehler</span>
                    {% else %}
                        <span class="badge badge-pending">{{ desc.status }}</span>
                    {% endif %}
                </div>
                <div class="card-meta">{{ desc.created_at[:16] if desc.created_at else '' }}</div>
            </a>
        </div>
        {% endfor %}

        <a href="/" class="nav-link">← Zurück zum Haupt-Dashboard</a>
    </div>
</body>
</html>
"""

CONSTRUCTION_DETAIL_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Baubeschreibung — {{ data.customer_name or 'Neu' }}</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
            background: #0f0f1a;
            color: #e0e0e0;
            min-height: 100vh;
        }
        .header {
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            padding: 20px;
            border-bottom: 1px solid #2a2a4a;
        }
        .header h1 { font-size: 18px; color: #fff; }
        .header .meta { color: #888; font-size: 12px; margin-top: 4px; }
        .container { max-width: 800px; margin: 0 auto; padding: 20px; }

        .section {
            background: #16213e;
            border: 1px solid #2a2a4a;
            border-radius: 10px;
            padding: 16px;
            margin-bottom: 16px;
        }
        .section-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 1px;
            color: #e94560;
            margin-bottom: 8px;
        }

        .transcript {
            font-size: 13px;
            line-height: 1.6;
            color: #aaa;
            max-height: 200px;
            overflow-y: auto;
            white-space: pre-wrap;
            background: #0f0f1a;
            padding: 12px;
            border-radius: 8px;
        }

        .description-editor {
            width: 100%;
            min-height: 300px;
            background: #0f0f1a;
            border: 1px solid #2a2a4a;
            border-radius: 8px;
            color: #e0e0e0;
            font-size: 14px;
            line-height: 1.6;
            padding: 12px;
            font-family: inherit;
            resize: vertical;
        }
        .description-editor:focus { outline: none; border-color: #e94560; }

        .customer-section { display: flex; gap: 12px; flex-wrap: wrap; }
        .customer-section input, .customer-section select {
            flex: 1;
            min-width: 200px;
            padding: 10px 12px;
            background: #0f0f1a;
            border: 1px solid #2a2a4a;
            border-radius: 8px;
            color: #e0e0e0;
            font-size: 14px;
        }
        .customer-section input:focus, .customer-section select:focus {
            outline: none;
            border-color: #e94560;
        }

        .actions {
            display: flex;
            gap: 12px;
            margin-top: 20px;
            flex-wrap: wrap;
        }
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            flex: 1;
            min-width: 140px;
            text-align: center;
        }
        .btn:hover { transform: translateY(-1px); }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }
        .btn-approve { background: #00b87c; color: #fff; }
        .btn-approve:hover { background: #00a06c; }
        .btn-discard { background: #333; color: #aaa; border: 1px solid #555; }
        .btn-discard:hover { background: #444; color: #fff; }

        .status-bar {
            padding: 12px 16px;
            border-radius: 8px;
            margin-bottom: 16px;
            font-size: 14px;
            display: none;
        }
        .status-success { background: #00b87c20; color: #00b87c; border: 1px solid #00b87c; display: block; }
        .status-error { background: #e9456020; color: #e94560; border: 1px solid #e94560; display: block; }

        .back-link { color: #666; text-decoration: none; font-size: 13px; }
        .back-link:hover { color: #e94560; }

        .badge-inline {
            display: inline-block;
            font-size: 11px;
            padding: 2px 8px;
            border-radius: 10px;
            margin-left: 8px;
        }
        .readonly-notice { background: #33333350; padding: 16px; border-radius: 8px; text-align: center; color: #888; }
    </style>
</head>
<body>
    <div class="header">
        <a href="/construction" class="back-link">← Alle Baubeschreibungen</a>
        <h1 style="margin-top: 8px;">
            📋 {{ data.customer_name or 'Baubeschreibung' }}
            {% if data.status == 'approved' %}
                <span class="badge-inline" style="background:#00b87c30;color:#00b87c;">✅ Bestätigt</span>
            {% elif data.status == 'discarded' %}
                <span class="badge-inline" style="background:#66666630;color:#888;">Verworfen</span>
            {% endif %}
        </h1>
        <div class="meta">
            {{ data.media_type }} · {{ data.appointment_date or '' }} · Erstellt: {{ data.created_at[:16] if data.created_at else '' }}
            {% if data.matched_via %} · Zuordnung: {{ data.matched_via }}{% endif %}
        </div>
    </div>

    <div class="container">
        <div id="statusBar" class="status-bar"></div>

        <!-- Transcript -->
        <div class="section">
            <div class="section-label">🎤 Transkription ({{ data.transcript_language or '?' }})</div>
            <div class="transcript">{{ data.transcript or 'Keine Transkription verfügbar' }}</div>
        </div>

        <!-- Customer Assignment -->
        <div class="section">
            <div class="section-label">📍 Kundenzuordnung</div>
            <div class="customer-section">
                <input type="text" id="customerName" value="{{ data.customer_name or '' }}"
                       placeholder="Kundenname"
                       {% if data.status != 'pending_approval' %}disabled{% endif %}>
                <input type="text" id="crmRecordId" value="{{ data.crm_record_id or '' }}"
                       placeholder="CRM Record ID"
                       {% if data.status != 'pending_approval' %}disabled{% endif %}>
            </div>
            {% if appointments and data.status == 'pending_approval' %}
            <div style="margin-top: 12px;">
                <div class="section-label">📅 Heutige Termine</div>
                <select id="appointmentSelect" onchange="fillFromAppointment()" style="width:100%;padding:10px;background:#0f0f1a;border:1px solid #2a2a4a;border-radius:8px;color:#e0e0e0;">
                    <option value="">— Termin auswählen —</option>
                    {% for apt in appointments %}
                    <option value="{{ apt.event_id }}"
                            data-name="{{ apt.customer_name or apt.summary }}"
                            data-crm="{{ apt.crm_record_id or '' }}"
                            data-address="{{ apt.customer_address or apt.location or '' }}"
                            {% if apt.event_id == data.calendar_event_id %}selected{% endif %}>
                        {{ apt.start_time[11:16] if apt.start_time|length > 11 else '' }} — {{ apt.customer_name or apt.summary }}
                    </option>
                    {% endfor %}
                </select>
            </div>
            {% endif %}
        </div>

        <!-- Generated Description -->
        <div class="section">
            <div class="section-label">📋 Baubeschreibung</div>
            {% if data.status == 'pending_approval' %}
                <textarea class="description-editor" id="descriptionEditor">{{ data.final_description or data.generated_description or '' }}</textarea>
            {% else %}
                <div class="transcript" style="max-height:none;">{{ data.final_description or data.generated_description or '' }}</div>
            {% endif %}
        </div>

        <!-- Actions -->
        {% if data.status == 'pending_approval' %}
        <div class="actions">
            <button class="btn btn-approve" id="btnApprove" onclick="approveDescription()">
                ✅ Speichern & an CRM senden
            </button>
            <button class="btn btn-discard" id="btnDiscard" onclick="discardDescription()">
                🗑️ Verwerfen
            </button>
        </div>
        {% elif data.status == 'approved' %}
        <div class="readonly-notice">
            ✅ Diese Beschreibung wurde am {{ data.approved_at[:16] if data.approved_at else '?' }} bestätigt.
        </div>
        {% endif %}
    </div>

    <script>
        function fillFromAppointment() {
            const sel = document.getElementById('appointmentSelect');
            const opt = sel.options[sel.selectedIndex];
            if (opt.value) {
                document.getElementById('customerName').value = opt.dataset.name || '';
                document.getElementById('crmRecordId').value = opt.dataset.crm || '';
            }
        }

        function showStatus(msg, isError) {
            const bar = document.getElementById('statusBar');
            bar.textContent = msg;
            bar.className = 'status-bar ' + (isError ? 'status-error' : 'status-success');
        }

        async function approveDescription() {
            const btn = document.getElementById('btnApprove');
            btn.disabled = true;
            btn.textContent = '⏳ Wird gespeichert...';

            try {
                const resp = await fetch('/api/construction/approve/{{ token }}', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        description: document.getElementById('descriptionEditor').value,
                        customer_name: document.getElementById('customerName').value,
                        crm_record_id: document.getElementById('crmRecordId').value,
                    })
                });
                const data = await resp.json();
                if (data.success) {
                    showStatus('✅ ' + data.message, false);
                    setTimeout(() => window.location.href = '/construction', 1500);
                } else {
                    showStatus('❌ ' + data.error, true);
                    btn.disabled = false;
                    btn.textContent = '✅ Speichern & an CRM senden';
                }
            } catch (e) {
                showStatus('❌ Netzwerkfehler: ' + e.message, true);
                btn.disabled = false;
                btn.textContent = '✅ Speichern & an CRM senden';
            }
        }

        async function discardDescription() {
            if (!confirm('Baubeschreibung wirklich verwerfen?')) return;

            try {
                const resp = await fetch('/api/construction/discard/{{ token }}', {
                    method: 'POST'
                });
                const data = await resp.json();
                if (data.success) {
                    showStatus('🗑️ Verworfen', false);
                    setTimeout(() => window.location.href = '/construction', 1000);
                } else {
                    showStatus('❌ ' + data.error, true);
                }
            } catch (e) {
                showStatus('❌ ' + e.message, true);
            }
        }
    </script>
</body>
</html>
"""