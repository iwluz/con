import os
import json
import uuid
import asyncio
from datetime import datetime
from aiohttp import web
import socketio

# Инициализация Socket.IO
sio = socketio.AsyncServer(
    async_mode='aiohttp',
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25,
    engineio_logger=False
)
app = web.Application()
sio.attach(app)

# База данных в памяти
users_db = {
    "alice": "pass123",
    "bob": "qwerty"
}
online_users = {}
message_history = {}

# Генерация ID сообщения
def generate_message_id():
    return str(uuid.uuid4())[:8]

# Сохранение сообщения
def save_message(sender, recipient, text):
    key = f"{min(sender, recipient)}:{max(sender, recipient)}"
    
    if key not in message_history:
        message_history[key] = []
    
    message = {
        'id': generate_message_id(),
        'sender': sender,
        'recipient': recipient,
        'text': text,
        'timestamp': datetime.now().strftime("%H:%M:%S"),
        'status': 'delivered'
    }
    
    message_history[key].append(message)
    return message

# Получение истории
def get_message_history(user1, user2):
    key = f"{min(user1, user2)}:{max(user1, user2)}"
    return message_history.get(key, [])

# Socket.IO события
@sio.event
async def connect(sid, environ):
    print(f"✅ Подключение: {sid}")

@sio.event
async def disconnect(sid):
    if sid in online_users:
        username = online_users[sid]
        del online_users[sid]
        await sio.emit('user_offline', username)
        print(f"⛔ Отключение: {username}")

@sio.event
async def login(sid, data):
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return await sio.emit('auth_error', "Заполните все поля", room=sid)
    
    if username not in users_db or users_db[username] != password:
        return await sio.emit('auth_error', "Неверный логин/пароль", room=sid)
    
    if username in online_users.values():
        return await sio.emit('auth_error', "Уже в системе", room=sid)
    
    online_users[sid] = username
    await sio.save_session(sid, {'username': username})
    await sio.emit('auth_success', {'username': username}, room=sid)
    await sio.emit('user_online', username)
    print(f"🔑 Вход: {username}")

@sio.event
async def register(sid, data):
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    if not username or not password:
        return await sio.emit('reg_error', "Заполните все поля", room=sid)
    
    if len(username) < 3:
        return await sio.emit('reg_error', "Имя > 3 символов", room=sid)
    
    if len(password) < 4:
        return await sio.emit('reg_error', "Пароль > 4 символов", room=sid)
    
    if username in users_db:
        return await sio.emit('reg_error', "Имя занято", room=sid)
    
    users_db[username] = password
    await sio.emit('reg_success', "Аккаунт создан!", room=sid)
    print(f"🆕 Регистрация: {username}")

@sio.event
async def get_online_users(sid):
    users = list(online_users.values())
    current_user = (await sio.get_session(sid)).get('username')
    
    if current_user in users:
        users.remove(current_user)
    
    await sio.emit('online_users', users, room=sid)

@sio.event
async def start_typing(sid, data):
    recipient = data.get('recipient')
    sender = (await sio.get_session(sid)).get('username')
    
    if recipient in online_users.values():
        await sio.emit('typing_start', {'sender': sender}, room=sid)

@sio.event
async def stop_typing(sid, data):
    recipient = data.get('recipient')
    sender = (await sio.get_session(sid)).get('username')
    
    if recipient in online_users.values():
        await sio.emit('typing_stop', {'sender': sender}, room=sid)

@sio.event
async def send_message(sid, data):
    recipient = data.get('recipient')
    text = data.get('message', '').strip()
    
    if not recipient or not text:
        return
        
    session = await sio.get_session(sid)
    sender = session.get('username')
    
    if not sender:
        return
    
    # Сохраняем сообщение
    message = save_message(sender, recipient, text)
    
    # Отправка получателю
    if recipient in online_users.values():
        await sio.emit('new_message', message, room=sid)
    
    # Подтверждение отправителю
    await sio.emit('message_sent', {
        'temp_id': data.get('temp_id'),
        'message_id': message['id'],
        'status': 'delivered' if recipient in online_users.values() else 'sent'
    }, room=sid)

@sio.event
async def get_message_history(sid, data):
    contact = data.get('contact')
    session = await sio.get_session(sid)
    username = session.get('username')
    
    if not username or not contact:
        return
        
    history = get_message_history(username, contact)
    await sio.emit('message_history', {
        'contact': contact,
        'messages': history
    }, room=sid)

# Статика
async def index(request):
    return web.FileResponse('./static/index.html')

app.router.add_get('/', index)
app.router.add_static('/static', path='./static')

# Проверка работоспособности
async def health_check(request):
    return web.Response(text="CON Messenger работает!")

app.router.add_get('/health', health_check)

# Точка входа
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    web.run_app(app, host='0.0.0.0', port=port, access_log=None)