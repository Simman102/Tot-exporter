#   Code imports
from fastapi import FastAPI, Response
from datetime import datetime, timezone

#   Run imports
from uvicorn import run
import multiprocessing

app = FastAPI()

@app.get('/message')
def save_log(sender: str, message: str):
    now = datetime.now(timezone.utc)
    with open(f'{now.date().isoformat()}.txt', 'a', encoding='utf-8') as file:
        file.write(f'[{now.strftime("%H:%M:%S")}] {sender}: {message}\n')
    return Response(status_code=200)

if __name__ == '__main__':
    multiprocessing.freeze_support()
    run(app, host="0.0.0.0", port=2137, reload=False, workers=1)