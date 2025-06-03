import os
import uuid
import asyncio
import logging
from datetime import datetime
from aiohttp import web
import socketio
from colorama import init, Fore, Back, Style

init(autoreset=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CON_MESSENGER")
logger.propagate = False
handler = logging.StreamHandler()
formatter = logging.Formatter(f"{Fore.CYAN}%(asctime)s {Style.BRIGHT}{Fore.MAGENTA}%(levelname)s{Style.RESET_ALL} {Fore.GREEN}%(message)s{Style.RESET_ALL}")
handler.setFormatter(formatter)
logger.addHandler(handler)

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
message_ids = set()

def generate_message_id():
    return str(uuid.uuid4())

def save_message(sender, recipient, text):
    key = tuple(sorted([sender, recipient]))
    if key not in message_history:
        message_history[key] = []
    
    message_id = generate_message_id()
    while message_id in message_ids:
        message_id = generate_message_id()
    message_ids.add(message_id)
    
    message = {
        'id': message_id,
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
    sio.emit('user_status', {'username': username, 'online': status})

@sio.event
async def connect(sid, environ):
    logger.info(f"{Fore.YELLOW}➤ Соединение: {Style.BRIGHT}{Fore.CYAN}{sid}")

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
                logger.info(f"{Fore.RED}✗ Отключен: {Style.BRIGHT}{Fore.YELLOW}{username}")

@sio.event
async def login(sid, data):
    username = data.get('username', '').strip().lower()
    password = data.get('password', '')
    
    if not username or not password:
        return await sio.emit('auth_error', "Заполните все поля", room=sid)
    
    if username not in users_db or users_db[username] != password:
        return await sio.emit('auth_error', "Неверный логин/пароль", room=sid)
    
    if username not in user_sessions:
        user_sessions[username] = []
    
    online_users[sid] = username
    user_sessions[username].append(sid)
    
    await sio.save_session(sid, {'username': username})
    await sio.emit('auth_success', {'username': username}, room=sid)
    update_user_status(username, True)
    
    logger.info(f"{Fore.GREEN}✓ Вход: {Style.BRIGHT}{Fore.CYAN}{username}")
    
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

@sio.event
async def register(sid, data):
    username = data.get('username', '').strip().lower()
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
    logger.info(f"{Fore.BLUE}★ Новый: {Style.BRIGHT}{Fore.CYAN}{username}")
    await sio.emit('reg_success', "Аккаунт создан!", room=sid)

@sio.event
async def get_online_users(sid):
    session = await sio.get_session(sid)
    current_user = session.get('username')
    users = [u for u in user_sessions.keys() if u != current_user]
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
    temp_id = data.get('temp_id')
    
    if not recipient or not text:
        return
        
    session = await sio.get_session(sid)
    sender = session.get('username')
    
    if not sender:
        return
    
    message = save_message(sender, recipient, text)
    logger.info(f"{Fore.MAGENTA}✉ {sender} → {recipient}: {text[:20]}{'...' if len(text) > 20 else ''}")
    
    if recipient in user_sessions:
        for recipient_sid in user_sessions[recipient]:
            await sio.emit('new_message', message, room=recipient_sid)
            message['status'] = 'delivered'
    
    await sio.emit('message_sent', {
        'temp_id': temp_id,
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
    logger.info(f"{Fore.CYAN}📖 История: {username} ↔ {contact} ({len(history)} сообщ.)")
    
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

async def health_check(request):
    return web.Response(text=">_ Messenger")

app.router.add_get('/health', health_check)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"{Fore.GREEN}🚀 Сервер запущен: {Style.BRIGHT}{Fore.CYAN}{port}")
    web.run_app(app, host='0.0.0.0', port=port)
