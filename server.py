import os
import uuid
import asyncio
import logging
from datetime import datetime
from aiohttp import web
import socketio

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CON_MESSENGER")

sio = socketio.AsyncServer(
    async_mode='aiohttp',
    cors_allowed_origins="*",
    logger=False,
    engineio_logger=False
)
app = web.Application()
sio.attach(app)


users_db = {}
online_users = {}
user_sessions = {}
message_history = {}

def generate_message_id():
    return str(uuid.uuid4())

def save_message(sender, recipient, text):
    key = tuple(sorted([sender, recipient]))
    
    if key not in message_history:
        message_history[key] = []
    
    message = {
        'id': generate_message_id(),
        'sender': sender,
        'recipient': recipient,
        'text': text,
        'timestamp': datetime.now().strftime("%H:%M"),
        'status': 'sent'
    }
    
    message_history[key].append(message)
    return message

def get_message_history(user1, user2):
    key = tuple(sorted([user1, user2]))
    return message_history.get(key, [])

def update_user_status(username, status):
    for sid in user_sessions.get(username, []):
        sio.emit('user_status', {'username': username, 'online': status}, room=sid)

@sio.event
async def connect(sid, environ):
    logger.info(f"подключение: {sid}")

@sio.event
async def disconnect(sid):
    if sid in online_users:
        username = online_users[sid]
        del online_users[sid]
        
        if username in user_sessions:
            user_sessions[username].remove(sid)
            if not user_sessions[username]:
                del user_sessions[username]
                update_user_status(username, False)
                logger.info(f"пользователь отключен: {username}")
        
        logger.info(f"сессия завершена: {username}/{sid}")

@sio.event
async def login(sid, data):
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    
    if not username or not password:
        return await sio.emit('auth_error', "заполните все поля", room=sid)
    
    if username not in users_db or users_db[username] != password:
        return await sio.emit('auth_error', "неверный логин/пароль", room=sid)
        
    if username not in user_sessions:
        user_sessions[username] = []
    
    online_users[sid] = username
    user_sessions[username].append(sid)
    
    await sio.save_session(sid, {'username': username})
    await sio.emit('auth_success', {'username': username}, room=sid)
    update_user_status(username, True)
    
    for key in list(message_history.keys()):
        if username in key:
            other_user = key[0] if key[1] == username else key[1]
            messages = [msg for msg in message_history[key] 
                       if msg['recipient'] == username and msg.get('status') != 'delivered']
            
            if messages:
                await sio.emit('unread_messages', {
                    'sender': other_user,
                    'messages': messages
                }, room=sid)
                
                for msg in messages:
                    msg['status'] = 'delivered'

    logger.info(f"вход: {username}")

@sio.event
async def register(sid, data):
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    
    if not username or not password:
        return await sio.emit('reg_error', "заполните все поля", room=sid)
    
    if len(username) < 3:
        return await sio.emit('reg_error', "имя > 3 символов", room=sid)
    
    if len(password) < 4:
        return await sio.emit('reg_error', "пароль > 4 символов", room=sid)
    
    if username in users_db:
        return await sio.emit('reg_error', "имя занято", room=sid)
    
    users_db[username] = password
    await sio.emit('reg_success', "аккаунт создан!", room=sid)
    logger.info(f"регистрация: {username}")

@sio.event
async def get_online_users(sid):
    users = list(user_sessions.keys())
    session = await sio.get_session(sid)
    current_user = session.get('username')
    
    if current_user in users:
        users.remove(current_user)
    
    await sio.emit('online_users', users, room=sid)

@sio.event
async def start_typing(sid, data):
    recipient = data.get('recipient')
    session = await sio.get_session(sid)
    sender = session.get('username')
    
    if recipient in user_sessions:
        for recipient_sid in user_sessions[recipient]:
            await sio.emit('typing_start', {'sender': sender}, room=recipient_sid)

@sio.event
async def stop_typing(sid, data):
    recipient = data.get('recipient')
    session = await sio.get_session(sid)
    sender = session.get('username')
    
    if recipient in user_sessions:
        for recipient_sid in user_sessions[recipient]:
            await sio.emit('typing_stop', {'sender': sender}, room=recipient_sid)

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

    message = save_message(sender, recipient, text)
    
    if recipient in user_sessions:
        for recipient_sid in user_sessions[recipient]:
            await sio.emit('new_message', message, room=recipient_sid)
            message['status'] = 'delivered'
    
    await sio.emit('message_sent', {
        'temp_id': data.get('temp_id'),
        'message_id': message['id'],
        'status': 'delivered' if recipient in user_sessions else 'sent'
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
    
    key = tuple(sorted([username, contact]))
    if key in message_history:
        for msg in message_history[key]:
            if msg['recipient'] == username:
                msg['status'] = 'read'

async def index(request):
    return web.FileResponse('./static/index.html')

app.router.add_get('/', index)
app.router.add_static('/static', path='static')
app.router.add_static('/assets', path='assets')

async def health_check(request):
    return web.Response(text=">_ Messenger работает!")

app.router.add_get('/health', health_check)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"Starting server on port {port}")
    web.run_app(app, host='0.0.0.0', port=port)
