from flask import Flask, request
from datetime import datetime, timezone

app = Flask(__name__)

@app.get('/message')
def save_log():
    now = datetime.now(timezone.utc)
    with open(f'{now.date().isoformat()}.txt', 'a', encoding='utf-8') as file:
        file.write(f'[{now.strftime("%H:%M:%S")}] {request.args.get("sender")}: {request.args.get("message")}\n')
    return '', 200