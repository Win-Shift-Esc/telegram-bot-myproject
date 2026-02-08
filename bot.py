import os
import logging
import sqlite3
from datetime import datetime
from typing import Dict, Any

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, filters, ContextTypes
)

from dotenv import load_dotenv

# Загружаем настройки из .env
load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(id_.strip()) for id_ in os.getenv('ADMIN_IDS', '').split(',') if id_.strip()]

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Создаем папки если их нет
os.makedirs('data', exist_ok=True)
os.makedirs('temp', exist_ok=True)
os.makedirs('logs', exist_ok=True)

# ==================== СОСТОЯНИЯ ДЛЯ ДИАЛОГОВ ====================
# Получение материалов
SELECT_CLASS, SELECT_SUBJECT, SELECT_CATEGORY, SELECT_TOPIC = range(4)

# Запрос материалов
REQUEST_CLASS, REQUEST_SUBJECT, REQUEST_CATEGORY, REQUEST_TOPIC, REQUEST_DESC = range(4, 9)

# Добавление материалов (админ)
ADMIN_ADD_CLASS, ADMIN_ADD_SUBJECT, ADMIN_ADD_CATEGORY, ADMIN_ADD_TOPIC, ADMIN_ADD_FILE = range(9, 14)

# Удаление материалов (админ)
ADMIN_DELETE_SELECT_CLASS, ADMIN_DELETE_SELECT_SUBJECT, ADMIN_DELETE_SELECT_CATEGORY, ADMIN_DELETE_SELECT_TOPIC, ADMIN_DELETE_CONFIRM = range(14, 19)

# ==================== БАЗА ДАННЫХ ====================
def init_db():
    """Инициализация базы данных SQLite"""
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    # Таблица пользователей
    c.execute('''CREATE TABLE IF NOT EXISTS users
                 (telegram_id INTEGER PRIMARY KEY, 
                  username TEXT, 
                  first_name TEXT, 
                  role TEXT DEFAULT 'student',
                  join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    
    # Таблица материалов с категорией
    c.execute('''CREATE TABLE IF NOT EXISTS materials
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  class TEXT NOT NULL,
                  subject TEXT NOT NULL,
                  category TEXT NOT NULL,
                  topic TEXT NOT NULL,
                  file_path TEXT NOT NULL,
                  file_name TEXT NOT NULL,
                  file_size INTEGER,
                  upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  downloads INTEGER DEFAULT 0,
                  uploaded_by INTEGER,
                  FOREIGN KEY (uploaded_by) REFERENCES users(telegram_id))''')
    
    # Таблица запросов
    c.execute('''CREATE TABLE IF NOT EXISTS requests
                 (id INTEGER PRIMARY KEY AUTOINCREMENT,
                  user_id INTEGER,
                  class TEXT,
                  subject TEXT,
                  category TEXT,
                  topic TEXT,
                  description TEXT,
                  status TEXT DEFAULT 'pending',
                  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  completed_at TIMESTAMP,
                  FOREIGN KEY (user_id) REFERENCES users(telegram_id))''')
    
    # Создаем индексы для быстрого поиска
    c.execute('''CREATE INDEX IF NOT EXISTS idx_materials_class ON materials(class)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_materials_subject ON materials(subject)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_materials_category ON materials(category)''')
    c.execute('''CREATE INDEX IF NOT EXISTS idx_materials_topic ON materials(topic)''')
    
    conn.commit()
    conn.close()
    logger.info("База данных инициализирована")

def get_user(user_id: int):
    """Получение или создание пользователя"""
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    c.execute("SELECT role FROM users WHERE telegram_id = ?", (user_id,))
    user = c.fetchone()
    
    if not user:
        c.execute("INSERT INTO users (telegram_id, role) VALUES (?, 'student')", (user_id,))
        conn.commit()
        user = ('student',)
    
    conn.close()
    return {'role': user[0]}

def is_admin(user_id: int):
    """Проверка является ли пользователь администратором"""
    user = get_user(user_id)
    return user['role'] == 'admin' or user_id in ADMIN_IDS

# ==================== КЛАВИАТУРЫ ====================
def main_menu(is_admin=False):
    """Главное меню"""
    keyboard = [
        [KeyboardButton("Получить материалы")],
        [KeyboardButton("Запросить материал")]
    ]
    if is_admin:
        keyboard.append([KeyboardButton("Админ-панель")])
    
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def admin_panel_keyboard():
    """Клавиатура админ-панели"""
    keyboard = [
        [KeyboardButton("Добавить материал"), KeyboardButton("Просмотреть запросы")],
        [KeyboardButton("Удалить материал"), KeyboardButton("Статистика")],
        [KeyboardButton("В главное меню")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def class_selection_keyboard(back_button=True):
    """Клавиатура выбора класса (5-11)"""
    buttons = [[KeyboardButton(f"{i} класс")] for i in range(5, 12)]
    if back_button:
        buttons.append([KeyboardButton("Назад")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def get_subjects_for_class(class_num: str):
    """Получение списка предметов для указанного класса"""
    class_num_int = int(class_num)
    
    # Базовые предметы для всех классов (5-11)
    base_subjects = [
        "Русский", "Литература", "История", 
        "Греческий", "Латынь", "Биология", 
        "Английский", "Немецкий"
    ]
    
    # Предметы по классам
    subjects = []
    
    # Математика для 5-6, Алгебра и Геометрия для 7-11
    if class_num_int in [5, 6]:
        subjects.append("Математика")
    else:
        subjects.extend(["Алгебра", "Геометрия"])
    
    # Добавляем базовые предметы
    subjects.extend(base_subjects)
    
    # География с 5 по 11 класс
    subjects.append("География")
    
    # Физика с 6 по 11 класс
    if class_num_int >= 6:
        subjects.append("Физика")
    
    # Химия с 7 по 11 класс
    if class_num_int >= 7:
        subjects.append("Химия")
    
    return subjects

def subject_selection_keyboard(class_num: str, back_button=True):
    """Клавиатура выбора предмета с учетом класса"""
    subjects = get_subjects_for_class(class_num)
    
    # Создаем строки по 2 кнопки
    rows = []
    for i in range(0, len(subjects), 2):
        row = subjects[i:i+2]
        rows.append([KeyboardButton(subj) for subj in row])
    
    if back_button:
        rows.append([KeyboardButton("Назад")])
    
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def category_selection_keyboard(back_button=True):
    """Клавиатура выбора категории материала"""
    categories = [
        "Конспекты",
        "Билеты к зачету",
        "Шпаргалки",
        "Учебники"
    ]
    
    buttons = [[KeyboardButton(cat)] for cat in categories]
    if back_button:
        buttons.append([KeyboardButton("Назад")])
    
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)

def yes_no_keyboard():
    """Клавиатура Да/Нет"""
    keyboard = [
        [KeyboardButton("Да, удалить"), KeyboardButton("Нет, отменить")],
        [KeyboardButton("Назад")]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== ОСНОВНЫЕ КОМАНДЫ ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    
    # Регистрируем пользователя
    user_data = get_user(user.id)
    
    # Если пользователь в списке админов, делаем его админом
    if user.id in ADMIN_IDS and user_data['role'] != 'admin':
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        c.execute("UPDATE users SET role = 'admin' WHERE telegram_id = ?", (user.id,))
        conn.commit()
        conn.close()
        user_data['role'] = 'admin'
    
    welcome_text = (
        f"Привет уставший гимназист, {user.first_name}!\n\n"
        "Я — бот-склад шпаргалок и конспектов, которые оставили те, кто выжил после сессии.\n"
        "Они были добры и великодушны.\n\n"
        "Выбери действие:"
    )
    
    await update.message.reply_text(
        welcome_text,
        reply_markup=main_menu(user_data['role'] == 'admin')
    )

# ==================== ПОЛУЧЕНИЕ МАТЕРИАЛОВ ====================
async def get_materials_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало получения материалов"""
    await update.message.reply_text(
        "Выберите ваш класс:",
        reply_markup=class_selection_keyboard()
    )
    return SELECT_CLASS

async def select_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора класса"""
    text = update.message.text
    
    if text == "Назад":
        user_data = get_user(update.effective_user.id)
        await update.message.reply_text(
            "Возвращаемся в главное меню...",
            reply_markup=main_menu(user_data['role'] == 'admin')
        )
        return ConversationHandler.END
    
    # Извлекаем номер класса
    try:
        class_num = text.split()[0]  # "5 класс" -> "5"
        if not class_num.isdigit() or int(class_num) < 5 or int(class_num) > 11:
            raise ValueError
    except:
        await update.message.reply_text(
            "Пожалуйста, выберите класс из списка:",
            reply_markup=class_selection_keyboard()
        )
        return SELECT_CLASS
    
    # Сохраняем выбор в контексте
    context.user_data['class'] = class_num
    context.user_data['class_text'] = text
    
    await update.message.reply_text(
        f"Класс: {text}\n\n"
        "Теперь выберите предмет:",
        reply_markup=subject_selection_keyboard(class_num)
    )
    
    return SELECT_SUBJECT

async def select_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора предмета"""
    text = update.message.text
    
    if text == "Назад":
        await update.message.reply_text(
            "Выберите класс:",
            reply_markup=class_selection_keyboard()
        )
        return SELECT_CLASS
    
    # Проверяем что выбран класс
    if 'class' not in context.user_data:
        await update.message.reply_text(
            "Ошибка сессии. Начните заново.",
            reply_markup=main_menu()
        )
        return ConversationHandler.END
    
    # Проверяем допустимость предмета для данного класса
    class_num = context.user_data['class']
    valid_subjects = get_subjects_for_class(class_num)
    
    if text not in valid_subjects:
        await update.message.reply_text(
            "Пожалуйста, выберите предмет из списка:",
            reply_markup=subject_selection_keyboard(class_num)
        )
        return SELECT_SUBJECT
    
    # Сохраняем предмет
    context.user_data['subject'] = text
    
    await update.message.reply_text(
        f"Предмет: {text}\n\n"
        "Выберите категорию материалов:",
        reply_markup=category_selection_keyboard()
    )
    
    return SELECT_CATEGORY

async def select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора категории"""
    text = update.message.text
    
    if text == "Назад":
        class_num = context.user_data.get('class')
        if class_num:
            await update.message.reply_text(
                "Выберите предмет:",
                reply_markup=subject_selection_keyboard(class_num)
            )
            return SELECT_SUBJECT
        else:
            await update.message.reply_text(
                "Ошибка сессии. Начните заново.",
                reply_markup=main_menu()
            )
            return ConversationHandler.END
    
    # Проверяем что выбран класс и предмет
    if 'class' not in context.user_data or 'subject' not in context.user_data:
        await update.message.reply_text(
            "Ошибка сессии. Начните заново.",
            reply_markup=main_menu()
        )
        return ConversationHandler.END
    
    # Сохраняем категорию
    context.user_data['category'] = text
    
    # Ищем материалы в базе данных
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    c.execute("""
        SELECT topic, downloads, file_name 
        FROM materials 
        WHERE class = ? AND subject = ? AND category = ?
        ORDER BY topic
    """, (context.user_data['class'], context.user_data['subject'], text))
    
    materials = c.fetchall()
    conn.close()
    
    if not materials:
        await update.message.reply_text(
            f"В категории '{text}' для {context.user_data['subject']} пока нет материалов.\n\n"
            "Вы можете:\n"
            "• Запросить этот материал через меню\n"
            "• Выбрать другую категорию\n"
            "• Вернуться к выбору предмета",
            reply_markup=category_selection_keyboard()
        )
        return SELECT_CATEGORY
    
    # Создаем клавиатуру с темами
    topics = [material[0] for material in materials]
    context.user_data['topics'] = topics
    context.user_data['materials_info'] = {m[0]: (m[1], m[2]) for m in materials}
    
    # Разбиваем темы на группы по 3
    topic_buttons = []
    for i in range(0, len(topics), 3):
        row = topics[i:i+3]
        topic_buttons.append([KeyboardButton(topic) for topic in row])
    
    topic_buttons.append([KeyboardButton("Назад к категориям")])
    
    await update.message.reply_text(
        f"Категория: {text}\n\n"
        f"Доступные материалы ({len(topics)}):\n"
        "Выберите тему:",
        reply_markup=ReplyKeyboardMarkup(topic_buttons, resize_keyboard=True)
    )
    
    return SELECT_TOPIC

async def select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора темы и отправка файла"""
    text = update.message.text
    
    if text == "Назад к категориям":
        await update.message.reply_text(
            "Выберите категорию:",
            reply_markup=category_selection_keyboard()
        )
        return SELECT_CATEGORY
    
    # Проверяем наличие всех данных
    required = ['class', 'subject', 'category', 'topics', 'materials_info']
    if not all(key in context.user_data for key in required):
        await update.message.reply_text(
            "Ошибка сессии. Начните заново.",
            reply_markup=main_menu()
        )
        return ConversationHandler.END
    
    # Проверяем что тема есть в списке
    if text not in context.user_data['topics']:
        await update.message.reply_text(
            "Пожалуйста, выберите тему из списка:",
            reply_markup=ReplyKeyboardMarkup(
                [[KeyboardButton(t)] for t in context.user_data['topics'][:10]] + 
                [[KeyboardButton("Назад к категориям")]],
                resize_keyboard=True
            )
        )
        return SELECT_TOPIC
    
    # Ищем файл в базе данных
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    c.execute("""
        SELECT file_path, file_name, downloads 
        FROM materials 
        WHERE class = ? AND subject = ? AND category = ? AND topic = ?
    """, (context.user_data['class'], context.user_data['subject'], 
          context.user_data['category'], text))
    
    material = c.fetchone()
    
    if not material:
        await update.message.reply_text(
            "Файл не найден в базе данных.",
            reply_markup=main_menu()
        )
        conn.close()
        return ConversationHandler.END
    
    file_path, file_name, downloads = material
    
    # Обновляем счетчик скачиваний
    c.execute("""
        UPDATE materials 
        SET downloads = downloads + 1 
        WHERE class = ? AND subject = ? AND category = ? AND topic = ?
    """, (context.user_data['class'], context.user_data['subject'], 
          context.user_data['category'], text))
    
    conn.commit()
    conn.close()
    
    # Отправляем файл пользователю
    try:
        with open(file_path, 'rb') as file:
            caption = (
                f"{text}\n\n"
                f"Класс: {context.user_data['class_text']}\n"
                f"Предмет: {context.user_data['subject']}\n"
                f"Категория: {context.user_data['category']}\n"
                f"Файл: {file_name}\n"
                f"Скачиваний: {downloads + 1}\n\n"
                f"Успешной подготовки!"
            )
            
            await update.message.reply_document(
                document=file,
                filename=file_name,
                caption=caption
            )
        
        # Сохраняем информацию о скачивании в лог
        log_message = (
            f"Скачивание: {update.effective_user.id} | "
            f"Класс: {context.user_data['class']} | "
            f"Предмет: {context.user_data['subject']} | "
            f"Категория: {context.user_data['category']} | "
            f"Тема: {text}"
        )
        logger.info(log_message)
        
    except FileNotFoundError:
        await update.message.reply_text(
            "Файл не найден на сервере. Сообщите администратору.",
            reply_markup=main_menu()
        )
    except Exception as e:
        logger.error(f"Ошибка отправки файла: {e}")
        await update.message.reply_text(
            "Произошла ошибка при отправке файла.",
            reply_markup=main_menu()
        )
    
    # Предлагаем дальнейшие действия
    user_data = get_user(update.effective_user.id)
    await update.message.reply_text(
        "Файл успешно отправлен!\n\n"
        "Что хотите сделать дальше?",
        reply_markup=main_menu(user_data['role'] == 'admin')
    )
    
    # Очищаем временные данные
    for key in ['class', 'subject', 'category', 'topics', 'materials_info']:
        if key in context.user_data:
            del context.user_data[key]
    
    return ConversationHandler.END

# ==================== ЗАПРОС МАТЕРИАЛОВ ====================
async def request_material_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало запроса материала"""
    await update.message.reply_text(
        "Запрос нового материала\n\n"
        "Для какого класса нужен материал?",
        reply_markup=class_selection_keyboard()
    )
    return REQUEST_CLASS

async def request_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор класса для запроса"""
    text = update.message.text
    
    if text == "Назад":
        user_data = get_user(update.effective_user.id)
        await update.message.reply_text(
            "Возвращаемся в главное меню...",
            reply_markup=main_menu(user_data['role'] == 'admin')
        )
        return ConversationHandler.END
    
    # Сохраняем класс
    context.user_data['req_class'] = text
    context.user_data['req_class_num'] = text.split()[0]
    
    await update.message.reply_text(
        f"Класс: {text}\n\n"
        "По какому предмету нужен материал?",
        reply_markup=subject_selection_keyboard(context.user_data['req_class_num'])
    )
    return REQUEST_SUBJECT

async def request_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор предмета для запроса"""
    text = update.message.text
    
    if text == "Назад":
        await update.message.reply_text(
            "Для какого класса?",
            reply_markup=class_selection_keyboard()
        )
        return REQUEST_CLASS
    
    # Проверяем допустимость предмета для выбранного класса
    class_num = context.user_data.get('req_class_num')
    valid_subjects = get_subjects_for_class(class_num) if class_num else []
    
    if text not in valid_subjects:
        await update.message.reply_text(
            "Пожалуйста, выберите предмет из списка:",
            reply_markup=subject_selection_keyboard(class_num)
        )
        return REQUEST_SUBJECT
    
    context.user_data['req_subject'] = text
    
    await update.message.reply_text(
        f"Предмет: {text}\n\n"
        "Какая категория материала нужна?",
        reply_markup=category_selection_keyboard()
    )
    return REQUEST_CATEGORY

async def request_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории для запроса"""
    text = update.message.text
    
    if text == "Назад":
        class_num = context.user_data.get('req_class_num')
        if class_num:
            await update.message.reply_text(
                "По какому предмету?",
                reply_markup=subject_selection_keyboard(class_num)
            )
            return REQUEST_SUBJECT
        else:
            await update.message.reply_text(
                "Ошибка сессии. Начните заново.",
                reply_markup=main_menu()
            )
            return ConversationHandler.END
    
    context.user_data['req_category'] = text
    
    await update.message.reply_text(
        f"Категория: {text}\n\n"
        "Напишите название темы (например, 'Квадратные уравнения', 'Первая мировая война'):",
        reply_markup=ReplyKeyboardRemove()
    )
    return REQUEST_TOPIC

async def request_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод темы для запроса"""
    text = update.message.text
    
    if len(text) < 3:
        await update.message.reply_text(
            "Слишком короткое название. Введите развернутое название темы:"
        )
        return REQUEST_TOPIC
    
    context.user_data['req_topic'] = text
    
    # Создаем клавиатуру с вариантами
    keyboard = [
        [KeyboardButton("Пропустить описание")],
        [KeyboardButton("Назад к категории")]
    ]
    
    await update.message.reply_text(
        f"Тема: {text}\n\n"
        "Добавьте описание или требования к материалу "
        "(например: 'Нужны задачи с решениями', 'Конспект по всей теме', "
        "'Билеты с ответами'):\n\n"
        "Или нажмите 'Пропустить описание'",
        reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    )
    return REQUEST_DESC

async def request_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка описания и сохранение запроса"""
    text = update.message.text
    
    if text == "Назад к категории":
        await update.message.reply_text(
            "Выберите категорию:",
            reply_markup=category_selection_keyboard()
        )
        return REQUEST_CATEGORY
    
    # Определяем описание
    description = None if text == "Пропустить описание" else text
    
    # Сохраняем запрос в базу данных
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    c.execute("""
        INSERT INTO requests (user_id, class, subject, category, topic, description)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (update.effective_user.id,
          context.user_data['req_class_num'],
          context.user_data['req_subject'],
          context.user_data['req_category'],
          context.user_data['req_topic'],
          description))
    
    request_id = c.lastrowid
    conn.commit()
    conn.close()
    
    # Формируем сообщение для пользователя
    user_message = (
        f"Запрос успешно отправлен!\n\n"
        f"Класс: {context.user_data['req_class']}\n"
        f"Предмет: {context.user_data['req_subject']}\n"
        f"Категория: {context.user_data['req_category']}\n"
        f"Тема: {context.user_data['req_topic']}\n"
    )
    
    if description:
        user_message += f"Описание: {description}\n\n"
    
    user_message += f"ID вашего запроса: #{request_id}\n\n"
    user_message += "Администратор рассмотрит ваш запрос в ближайшее время."
    
    # ОТПРАВЛЯЕМ ПОДТВЕРЖДЕНИЕ ПОЛЬЗОВАТЕЛЮ
    await update.message.reply_text(
        user_message,
        reply_markup=main_menu(is_admin(update.effective_user.id))
    )
    
    # Отправляем уведомление администраторам
    admin_message = (
        f"Новый запрос материала!\n\n"
        f"ID: #{request_id}\n"
        f"Пользователь: @{update.effective_user.username if update.effective_user.username else update.effective_user.first_name}\n"
        f"ID пользователя: {update.effective_user.id}\n"
        f"Класс: {context.user_data['req_class']}\n"
        f"Предмет: {context.user_data['req_subject']}\n"
        f"Категория: {context.user_data['req_category']}\n"
        f"Тема: {context.user_data['req_topic']}\n"
    )
    
    if description:
        admin_message += f"Описание: {description}\n"
    
    admin_message += f"\nВремя: {datetime.now().strftime('%d.%m.%Y %H:%M')}"
    
    # Отправляем всем администраторам
    notification_sent = False
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(
                admin_id,
                admin_message
            )
            print(f"✅ Уведомление отправлено админу {admin_id}")
            notification_sent = True
        except Exception as e:
            print(f"❌ Ошибка отправки админу {admin_id}: {e}")
    
    # Очищаем временные данные
    for key in list(context.user_data.keys()):
        if key.startswith('req_'):
            del context.user_data[key]
    
    return ConversationHandler.END

# ==================== АДМИН-ПАНЕЛЬ ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отображение админ-панели"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав доступа.")
        return
    
    # Получаем статистику для админа
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    c.execute("SELECT COUNT(*) FROM requests WHERE status = 'pending'")
    pending_requests = c.fetchone()[0]
    
    c.execute("SELECT COUNT(*) FROM materials")
    total_materials = c.fetchone()[0]
    
    conn.close()
    
    stats_text = (
        f"Панель администратора\n\n"
        f"Статистика:\n"
        f"• Ожидающих запросов: {pending_requests}\n"
        f"• Всего материалов: {total_materials}\n\n"
        f"Выберите действие:"
    )
    
    await update.message.reply_text(
        stats_text,
        reply_markup=admin_panel_keyboard()
    )

async def admin_add_material_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало добавления материала"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав доступа.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "Добавление нового материала\n\n"
        "Для какого класса материал?",
        reply_markup=class_selection_keyboard()
    )
    return ADMIN_ADD_CLASS

async def admin_add_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор класса для добавления"""
    text = update.message.text
    
    if text == "Назад":
        await admin_panel(update, context)
        return ConversationHandler.END
    
    context.user_data['add_class'] = text
    context.user_data['add_class_num'] = text.split()[0]
    
    await update.message.reply_text(
        f"Класс: {text}\n\n"
        "По какому предмету?",
        reply_markup=subject_selection_keyboard(context.user_data['add_class_num'])
    )
    return ADMIN_ADD_SUBJECT

async def admin_add_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор предмета для добавления"""
    text = update.message.text
    
    if text == "Назад":
        await update.message.reply_text(
            "Для какого класса?",
            reply_markup=class_selection_keyboard()
        )
        return ADMIN_ADD_CLASS
    
    # Проверяем допустимость предмета для выбранного класса
    class_num = context.user_data.get('add_class_num')
    valid_subjects = get_subjects_for_class(class_num) if class_num else []
    
    if text not in valid_subjects:
        await update.message.reply_text(
            "Пожалуйста, выберите предмет из списка:",
            reply_markup=subject_selection_keyboard(class_num)
        )
        return ADMIN_ADD_SUBJECT
    
    context.user_data['add_subject'] = text
    
    await update.message.reply_text(
        f"Предмет: {text}\n\n"
        "Выберите категорию материала:",
        reply_markup=category_selection_keyboard()
    )
    return ADMIN_ADD_CATEGORY

async def admin_add_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории для добавления"""
    text = update.message.text
    
    if text == "Назад":
        await update.message.reply_text(
            "По какому предмету?",
            reply_markup=subject_selection_keyboard(context.user_data['add_class_num'])
        )
        return ADMIN_ADD_SUBJECT
    
    context.user_data['add_category'] = text
    
    await update.message.reply_text(
        f"Категория: {text}\n\n"
        "Введите название темы материала:",
        reply_markup=ReplyKeyboardRemove()
    )
    return ADMIN_ADD_TOPIC

async def admin_add_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ввод темы для добавления"""
    text = update.message.text
    
    if len(text) < 2:
        await update.message.reply_text(
            "Слишком короткое название. Введите полное название темы:"
        )
        return ADMIN_ADD_TOPIC
    
    context.user_data['add_topic'] = text
    
    await update.message.reply_text(
        f"Тема: {text}\n\n"
        "Теперь отправьте файл с материалом.\n"
        "Поддерживаемые форматы:\n"
        "• PDF, DOC, DOCX, TXT (документы)\n"
        "• PPT, PPTX (презентации)\n"
        "• JPG, PNG (изображения)\n\n"
        "Максимальный размер: 20 MB"
    )
    return ADMIN_ADD_FILE

async def admin_add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка файла и сохранение материала"""
    if not (update.message.document or update.message.photo):
        await update.message.reply_text(
            "Пожалуйста, отправьте файл (документ или изображение)."
        )
        return ADMIN_ADD_FILE
    
    # Проверяем наличие всех необходимых данных
    required = ['add_class_num', 'add_subject', 'add_category', 'add_topic']
    if not all(key in context.user_data for key in required):
        await update.message.reply_text(
            "Ошибка сессии. Начните заново.",
            reply_markup=admin_panel_keyboard()
        )
        return ConversationHandler.END
    
    # Скачиваем файл
    try:
        if update.message.document:
            file = await update.message.document.get_file()
            file_name = update.message.document.file_name
        else:
            # Для фото берем самое качественное
            file = await update.message.photo[-1].get_file()
            file_name = f"photo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        
        # Создаем временный путь
        temp_path = os.path.join('temp', file_name)
        await file.download_to_drive(temp_path)
        
        # Получаем размер файла
        file_size = os.path.getsize(temp_path)
        
        # Создаем структуру папок для сохранения
        class_folder = f"{context.user_data['add_class_num']}_class"
        subject_folder = context.user_data['add_subject']
        category_folder = context.user_data['add_category']
        
        save_dir = os.path.join('data', class_folder, subject_folder, category_folder)
        os.makedirs(save_dir, exist_ok=True)
        
        # Генерируем уникальное имя файла если нужно
        base_name, ext = os.path.splitext(file_name)
        counter = 1
        final_name = file_name
        final_path = os.path.join(save_dir, final_name)
        
        while os.path.exists(final_path):
            final_name = f"{base_name}_{counter}{ext}"
            final_path = os.path.join(save_dir, final_name)
            counter += 1
        
        # Перемещаем файл в постоянное место
        os.rename(temp_path, final_path)
        
        # Сохраняем информацию в базу данных
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        
        c.execute("""
            INSERT INTO materials 
            (class, subject, category, topic, file_path, file_name, file_size, uploaded_by)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (context.user_data['add_class_num'],
              context.user_data['add_subject'],
              context.user_data['add_category'],
              context.user_data['add_topic'],
              final_path,
              final_name,
              file_size,
              update.effective_user.id))
        
        material_id = c.lastrowid
        conn.commit()
        conn.close()
        
        # Формируем сообщение об успехе
        success_message = (
            f"Материал успешно добавлен!\n\n"
            f"ID: #{material_id}\n"
            f"Класс: {context.user_data['add_class_num']}\n"
            f"Предмет: {context.user_data['add_subject']}\n"
            f"Категория: {context.user_data['add_category']}\n"
            f"Тема: {context.user_data['add_topic']}\n"
            f"Файл: {final_name}\n"
            f"Размер: {file_size // 1024} KB\n\n"
            f"Материал доступен для скачивания."
        )
        
        await update.message.reply_text(
            success_message,
            reply_markup=admin_panel_keyboard()
        )
        
        # Проверяем, есть ли запросы на этот материал и отмечаем их выполненными
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        
        c.execute("""
            SELECT id, user_id FROM requests 
            WHERE class = ? AND subject = ? AND category = ? AND topic = ?
            AND status = 'pending'
        """, (context.user_data['add_class_num'],
              context.user_data['add_subject'],
              context.user_data['add_category'],
              context.user_data['add_topic']))
        
        completed_requests = c.fetchall()
        
        for req_id, user_id in completed_requests:
            c.execute("""
                UPDATE requests 
                SET status = 'completed', completed_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            """, (req_id,))
            
            # Уведомляем пользователя
            try:
                await context.bot.send_message(
                    user_id,
                    f"Ваш запрос #{req_id} выполнен!\n\n"
                    f"Материал '{context.user_data['add_topic']}' теперь доступен в боте.\n"
                    f"Найти его можно в разделе:\n"
                    f"{context.user_data['add_class_num']} класс → "
                    f"{context.user_data['add_subject']} → "
                    f"{context.user_data['add_category']}"
                )
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
        
        conn.commit()
        conn.close()
        
        if completed_requests:
            await update.message.reply_text(
                f"Автоматически выполнено {len(completed_requests)} запросов на этот материал.",
                reply_markup=admin_panel_keyboard()
            )
        
        # Очищаем временные данные
        for key in list(context.user_data.keys()):
            if key.startswith('add_'):
                del context.user_data[key]
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении материала: {e}")
        await update.message.reply_text(
            f"Произошла ошибка: {str(e)[:100]}...",
            reply_markup=admin_panel_keyboard()
        )
    
    return ConversationHandler.END

async def admin_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать статистику"""
    print(f"DEBUG: Вызвана admin_statistics для пользователя {update.effective_user.id}")
    
    if not is_admin(update.effective_user.id):
        print(f"DEBUG: Пользователь {update.effective_user.id} не админ")
        await update.message.reply_text("У вас нет прав доступа.")
        return
    
    print(f"DEBUG: Пользователь {update.effective_user.id} является админом")
    
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    try:
        c.execute("SELECT COUNT(*) FROM materials")
        total_materials = c.fetchone()[0]
        print(f"DEBUG: total_materials = {total_materials}")
        
        c.execute("SELECT SUM(downloads) FROM materials")
        result = c.fetchone()[0]
        total_downloads = result if result else 0
        print(f"DEBUG: total_downloads = {total_downloads}")
        
        c.execute("SELECT COUNT(*) FROM users")
        total_users = c.fetchone()[0]
        print(f"DEBUG: total_users = {total_users}")
        
        c.execute("SELECT COUNT(*) FROM requests WHERE status = 'pending'")
        pending_requests = c.fetchone()[0]
        print(f"DEBUG: pending_requests = {pending_requests}")
        
        c.execute("SELECT COUNT(*) FROM requests WHERE status = 'completed'")
        completed_requests = c.fetchone()[0]
        print(f"DEBUG: completed_requests = {completed_requests}")
        
        stats_text = (
            f"📊 Статистика бота\n\n"
            f"👥 Пользователей: {total_users}\n"
            f"📁 Материалов: {total_materials}\n"
            f"📥 Скачиваний: {total_downloads}\n"
            f"📝 Запросов: {pending_requests + completed_requests}\n"
            f"  • Ожидают: {pending_requests}\n"
            f"  • Выполнены: {completed_requests}"
        )
        
        print(f"DEBUG: Отправляю статистику: {stats_text[:50]}...")
        await update.message.reply_text(
            stats_text,
            reply_markup=admin_panel_keyboard()
        )
        
    except Exception as e:
        print(f"DEBUG: Ошибка в admin_statistics: {e}")
        await update.message.reply_text(
            f"Ошибка получения статистики: {str(e)[:100]}",
            reply_markup=admin_panel_keyboard()
        )
    finally:
        conn.close()

async def admin_view_requests(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр запросов материалов"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав доступа.")
        return
    
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    # Получаем все запросы
    c.execute("""
        SELECT r.id, r.user_id, u.username, u.first_name, 
               r.class, r.subject, r.category, r.topic, 
               r.description, r.status, r.created_at
        FROM requests r
        LEFT JOIN users u ON r.user_id = u.telegram_id
        ORDER BY r.status, r.created_at DESC
        LIMIT 20
    """)
    
    requests = c.fetchall()
    conn.close()
    
    if not requests:
        await update.message.reply_text(
            "📭 Нет запросов материалов.",
            reply_markup=admin_panel_keyboard()
        )
        return
    
    # Создаем клавиатуру с запросами
    keyboard = []
    for req in requests:
        req_id, user_id, username, first_name, class_num, subject, category, topic, description, status, created_at = req
        
        # Форматируем дату
        try:
            created_date = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y')
        except:
            created_date = created_at
        
        # Создаем текст для кнопки
        button_text = f"#{req_id} {class_num}кл {subject[:10]}..."
        
        # Добавляем кнопку
        keyboard.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"view_request_{req_id}"
        )])
    
    # Добавляем кнопку обновления
    keyboard.append([InlineKeyboardButton("🔄 Обновить список", callback_data="refresh_requests")])
    
    # Статистика
    pending_count = sum(1 for r in requests if r[9] == 'pending')
    completed_count = sum(1 for r in requests if r[9] == 'completed')
    
    message_text = (
        f"📋 Запросы материалов\n\n"
        f"📊 Статистика:\n"
        f"• Всего: {len(requests)}\n"
        f"• Ожидают: {pending_count}\n"
        f"• Выполнены: {completed_count}\n\n"
        f"Выберите запрос для просмотра деталей:"
    )
    
    await update.message.reply_text(
        message_text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def handle_request_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка нажатий на запросы"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data.startswith("view_request_"):
        request_id = data.replace("view_request_", "")
        
        # Получаем информацию о запросе
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        
        c.execute("""
            SELECT r.id, r.user_id, u.username, u.first_name, 
                   r.class, r.subject, r.category, r.topic, 
                   r.description, r.status, r.created_at,
                   u.telegram_id
            FROM requests r
            LEFT JOIN users u ON r.user_id = u.telegram_id
            WHERE r.id = ?
        """, (request_id,))
        
        req = c.fetchone()
        conn.close()
        
        if not req:
            await query.edit_message_text("Запрос не найден.")
            return
        
        (req_id, user_id, username, first_name, class_num, subject, category, 
         topic, description, status, created_at, telegram_id) = req
        
        # Форматируем дату
        try:
            created_date = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
        except:
            created_date = created_at
        
        # Статус
        status_emoji = "🟡" if status == 'pending' else "✅"
        status_text = "Ожидает" if status == 'pending' else "Выполнен"
        
        # Формируем сообщение
        message_text = (
            f"📄 Запрос #{req_id}\n\n"
            f"👤 Пользователь: {first_name} (@{username if username else 'нет'})\n"
            f"🆔 ID: {user_id}\n"
            f"📅 Дата: {created_date}\n"
            f"📊 Статус: {status_emoji} {status_text}\n\n"
            f"📚 Детали запроса:\n"
            f"• Класс: {class_num}\n"
            f"• Предмет: {subject}\n"
            f"• Категория: {category}\n"
            f"• Тема: {topic}\n"
        )
        
        if description:
            message_text += f"• Описание: {description}\n"
        
        # Создаем клавиатуру действий
        keyboard = []
        
        if status == 'pending':
            keyboard.append([
                InlineKeyboardButton("✅ Выполнить", callback_data=f"complete_request_{req_id}"),
                InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_request_{req_id}")
            ])
        
        keyboard.append([
            InlineKeyboardButton("📨 Уведомить", callback_data=f"notify_request_{req_id}"),
            InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_requests")
        ])
        
        await query.edit_message_text(
            text=message_text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("complete_request_"):
        request_id = data.replace("complete_request_", "")
        
        # Помечаем запрос как выполненный
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        
        c.execute("""
            UPDATE requests 
            SET status = 'completed', completed_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (request_id,))
        
        # Получаем данные для уведомления
        c.execute("""
            SELECT user_id, topic FROM requests WHERE id = ?
        """, (request_id,))
        
        result = c.fetchone()
        if result:
            user_id, topic = result
        else:
            user_id, topic = None, None
            
        conn.commit()
        conn.close()
        
        # Отправляем уведомление пользователю
        if user_id and topic:
            try:
                await context.bot.send_message(
                    user_id,
                    f"✅ Ваш запрос выполнен!\n\n"
                    f"Материал '{topic}' теперь доступен в боте.\n"
                    f"Найти его можно в соответствующем разделе."
                )
                await query.answer("Запрос помечен как выполненный. Пользователь уведомлен.")
            except Exception as e:
                logger.error(f"Не удалось уведомить пользователя {user_id}: {e}")
                await query.answer("Запрос помечен как выполненный, но не удалось уведомить пользователя.")
        else:
            await query.answer("Запрос помечен как выполненный.")
        
        # Обновляем сообщение
        await query.edit_message_text(
            text=f"✅ Запрос #{request_id} выполнен.\nПользователь уведомлен.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_requests")]
            ])
        )
    
    elif data.startswith("delete_request_"):
        request_id = data.replace("delete_request_", "")
        
        # Удаляем запрос
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        
        c.execute("DELETE FROM requests WHERE id = ?", (request_id,))
        conn.commit()
        conn.close()
        
        await query.answer("Запрос удален.")
        
        # Обновляем сообщение
        await query.edit_message_text(
            text=f"🗑️ Запрос #{request_id} удален.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад к списку", callback_data="back_to_requests")]
            ])
        )
    
    elif data.startswith("notify_request_"):
        request_id = data.replace("notify_request_", "")
        
        # Получаем данные пользователя
        conn = sqlite3.connect('school_bot.db')
        c = conn.cursor()
        
        c.execute("SELECT user_id, topic FROM requests WHERE id = ?", (request_id,))
        result = c.fetchone()
        conn.close()
        
        if not result:
            await query.answer("Запрос не найден.")
            return
            
        user_id, topic = result
        
        # Сохраняем данные для ввода сообщения
        context.user_data['notify_user_id'] = user_id
        context.user_data['notify_request_id'] = request_id
        
        await query.edit_message_text(
            text=f"📨 Отправка уведомления пользователю {user_id}\n"
                 f"по запросу: {topic}\n\n"
                 f"Введите текст уведомления:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data=f"view_request_{request_id}")]
            ])
        )
        
        # Устанавливаем состояние для получения текста уведомления
        context.user_data['awaiting_notification'] = True
    
    elif data == "back_to_requests":
        # Возвращаемся к списку запросов
        await admin_view_requests(query, context)
        return
    
    elif data == "refresh_requests":
        # Обновляем список запросов
        await admin_view_requests(query, context)
        return

async def handle_notification_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текста уведомления"""
    # Проверяем, ожидается ли уведомление
    if context.user_data.get('awaiting_notification'):
        user_id = context.user_data.get('notify_user_id')
        request_id = context.user_data.get('notify_request_id')
        notification_text = update.message.text
        
        if not user_id or not request_id:
            context.user_data['awaiting_notification'] = False
            return
        
        # Отправляем уведомление пользователю
        try:
            await context.bot.send_message(
                user_id,
                f"📨 Уведомление от администратора по вашему запросу #{request_id}:\n\n"
                f"{notification_text}"
            )
            
            await update.message.reply_text(
                f"✅ Уведомление отправлено пользователю {user_id}.",
                reply_markup=admin_panel_keyboard()
            )
        except Exception as e:
            logger.error(f"Ошибка отправки уведомления: {e}")
            await update.message.reply_text(
                f"❌ Не удалось отправить уведомление: {str(e)[:100]}",
                reply_markup=admin_panel_keyboard()
            )
        
        # Очищаем состояние
        context.user_data['awaiting_notification'] = False
        context.user_data['notify_user_id'] = None
        context.user_data['notify_request_id'] = None
        return
    
    # Если уведомление не ожидается, проверяем не нажата ли кнопка главного меню
    text = update.message.text
    if text in ["/start", "/menu", "В главное меню"]:
        await start(update, context)
    elif text == "Админ-панель" and is_admin(update.effective_user.id):
        await admin_panel(update, context)

async def admin_delete_material_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Начало удаления материала"""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("У вас нет прав доступа.")
        return ConversationHandler.END
    
    await update.message.reply_text(
        "🗑️ Удаление материала\n\n"
        "Для какого класса удалить материал?",
        reply_markup=class_selection_keyboard()
    )
    return ADMIN_DELETE_SELECT_CLASS

async def admin_delete_select_class(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор класса для удаления"""
    text = update.message.text
    
    if text == "Назад":
        await admin_panel(update, context)
        return ConversationHandler.END
    
    context.user_data['delete_class'] = text
    context.user_data['delete_class_num'] = text.split()[0]
    
    await update.message.reply_text(
        f"Класс: {text}\n\n"
        "По какому предмету удалить материал?",
        reply_markup=subject_selection_keyboard(context.user_data['delete_class_num'])
    )
    return ADMIN_DELETE_SELECT_SUBJECT

async def admin_delete_select_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор предмета для удаления"""
    text = update.message.text
    
    if text == "Назад":
        await update.message.reply_text(
            "Для какого класса?",
            reply_markup=class_selection_keyboard()
        )
        return ADMIN_DELETE_SELECT_CLASS
    
    # Проверяем допустимость предмета для выбранного класса
    class_num = context.user_data.get('delete_class_num')
    valid_subjects = get_subjects_for_class(class_num) if class_num else []
    
    if text not in valid_subjects:
        await update.message.reply_text(
            "Пожалуйста, выберите предмет из списка:",
            reply_markup=subject_selection_keyboard(class_num)
        )
        return ADMIN_DELETE_SELECT_SUBJECT
    
    context.user_data['delete_subject'] = text
    
    await update.message.reply_text(
        f"Предмет: {text}\n\n"
        "Выберите категорию материала для удаления:",
        reply_markup=category_selection_keyboard()
    )
    return ADMIN_DELETE_SELECT_CATEGORY

async def admin_delete_select_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор категории для удаления"""
    text = update.message.text
    
    if text == "Назад":
        await update.message.reply_text(
            "По какому предмету?",
            reply_markup=subject_selection_keyboard(context.user_data['delete_class_num'])
        )
        return ADMIN_DELETE_SELECT_SUBJECT
    
    context.user_data['delete_category'] = text
    
    # Ищем материалы в базе данных
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    
    c.execute("""
        SELECT id, topic, downloads, file_name, upload_date
        FROM materials 
        WHERE class = ? AND subject = ? AND category = ?
        ORDER BY topic
    """, (context.user_data['delete_class_num'], 
          context.user_data['delete_subject'], 
          text))
    
    materials = c.fetchall()
    conn.close()
    
    if not materials:
        await update.message.reply_text(
            f"В категории '{text}' для {context.user_data['delete_subject']} нет материалов для удаления.",
            reply_markup=admin_panel_keyboard()
        )
        return ConversationHandler.END
    
    # Создаем клавиатуру с темами
    topics = [material[1] for material in materials]
    context.user_data['delete_topics'] = topics
    context.user_data['delete_materials_info'] = {m[1]: (m[0], m[2], m[3], m[4]) for m in materials}
    
    # Разбиваем темы на группы по 3
    topic_buttons = []
    for i in range(0, len(topics), 3):
        row = topics[i:i+3]
        topic_buttons.append([KeyboardButton(topic) for topic in row])
    
    topic_buttons.append([KeyboardButton("Назад к категориям")])
    
    await update.message.reply_text(
        f"Категория: {text}\n\n"
        f"Доступные материалы для удаления ({len(topics)}):\n"
        "Выберите тему:",
        reply_markup=ReplyKeyboardMarkup(topic_buttons, resize_keyboard=True)
    )
    
    return ADMIN_DELETE_SELECT_TOPIC

async def admin_delete_select_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выбор темы для удаления и показ информации"""
    text = update.message.text
    
    if text == "Назад к категориям":
        await update.message.reply_text(
            "Выберите категорию:",
            reply_markup=category_selection_keyboard()
        )
        return ADMIN_DELETE_SELECT_CATEGORY
    
    # Сохраняем выбранную тему
    context.user_data['delete_topic'] = text
    
    # Получаем информацию о материале
    material_id, downloads, file_name, upload_date = context.user_data['delete_materials_info'][text]
    context.user_data['delete_material_id'] = material_id
    context.user_data['delete_file_name'] = file_name
    
    # Получаем полный путь к файлу
    conn = sqlite3.connect('school_bot.db')
    c = conn.cursor()
    c.execute("SELECT file_path FROM materials WHERE id = ?", (material_id,))
    result = c.fetchone()
    conn.close()
    
    if not result:
        await update.message.reply_text(
            "Файл не найден в базе данных.",
            reply_markup=admin_panel_keyboard()
        )
        return ConversationHandler.END
    
    file_path = result[0]
    context.user_data['delete_file_path'] = file_path
    
    # Форматируем дату
    try:
        if isinstance(upload_date, str):
            upload_date_formatted = datetime.strptime(upload_date, '%Y-%m-%d %H:%M:%S').strftime('%d.%m.%Y %H:%M')
        else:
            upload_date_formatted = upload_date
    except:
        upload_date_formatted = upload_date
    
    # Показываем информацию о материале и запрашиваем подтверждение
    confirm_text = (
        f"⚠️ ВНИМАНИЕ: Вы собираетесь удалить материал\n\n"
        f"📋 Информация о материале:\n"
        f"• ID: #{material_id}\n"
        f"• Класс: {context.user_data['delete_class_num']}\n"
        f"• Предмет: {context.user_data['delete_subject']}\n"
        f"• Категория: {context.user_data['delete_category']}\n"
        f"• Тема: {text}\n"
        f"• Файл: {file_name}\n"
        f"• Загружен: {upload_date_formatted}\n"
        f"• Скачиваний: {downloads}\n\n"
        f"❓ Вы уверены, что хотите удалить этот материал?"
    )
    
    await update.message.reply_text(
        confirm_text,
        reply_markup=yes_no_keyboard()
    )
    
    return ADMIN_DELETE_CONFIRM

async def admin_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления материала"""
    text = update.message.text
    
    if text == "Назад":
        # Возвращаемся к выбору темы
        topics = context.user_data.get('delete_topics', [])
        topic_buttons = []
        for i in range(0, len(topics), 3):
            row = topics[i:i+3]
            topic_buttons.append([KeyboardButton(topic) for topic in row])
        
        topic_buttons.append([KeyboardButton("Назад к категориям")])
        
        await update.message.reply_text(
            "Выберите тему для удаления:",
            reply_markup=ReplyKeyboardMarkup(topic_buttons, resize_keyboard=True)
        )
        return ADMIN_DELETE_SELECT_TOPIC
    
    if text == "Нет, отменить":
        await update.message.reply_text(
            "Удаление отменено.",
            reply_markup=admin_panel_keyboard()
        )
        return ConversationHandler.END
    
    if text == "Да, удалить":
        try:
            # Получаем данные о материале
            material_id = context.user_data.get('delete_material_id')
            file_path = context.user_data.get('delete_file_path')
            
            # Удаляем файл с диска
            file_deleted = False
            if os.path.exists(file_path):
                os.remove(file_path)
                file_deleted = True
            
            # Удаляем запись из базы данных
            conn = sqlite3.connect('school_bot.db')
            c = conn.cursor()
            c.execute("DELETE FROM materials WHERE id = ?", (material_id,))
            conn.commit()
            conn.close()
            
            # Получаем информацию об удаленном материале для сообщения
            class_num = context.user_data.get('delete_class_num')
            subject = context.user_data.get('delete_subject')
            category = context.user_data.get('delete_category')
            topic = context.user_data.get('delete_topic')
            
            success_text = (
                f"✅ Материал успешно удален!\n\n"
                f"Удаленная информация:\n"
                f"• ID: #{material_id}\n"
                f"• Класс: {class_num}\n"
                f"• Предмет: {subject}\n"
                f"• Категория: {category}\n"
                f"• Тема: {topic}\n"
                f"• Файл удален: {'Да' if file_deleted else 'Нет'}"
            )
            
            await update.message.reply_text(
                success_text,
                reply_markup=admin_panel_keyboard()
            )
            
            # Логируем удаление
            logger.info(f"Материал #{material_id} удален администратором {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"Ошибка при удалении материала: {e}")
            await update.message.reply_text(
                f"❌ Ошибка при удалении материала: {str(e)[:200]}",
                reply_markup=admin_panel_keyboard()
            )
        
        # Очищаем временные данные
        for key in list(context.user_data.keys()):
            if key.startswith('delete_'):
                del context.user_data[key]
        
        return ConversationHandler.END
    
    # Если текст не распознан
    await update.message.reply_text(
        "Пожалуйста, выберите вариант из клавиатуры:",
        reply_markup=yes_no_keyboard()
    )
    return ADMIN_DELETE_CONFIRM

# ==================== ОБРАБОТЧИКИ КНОПОК ====================
async def handle_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка поиска материалов"""
    await update.message.reply_text(
        "Функция поиска находится в разработке.\n"
        "Скоро здесь можно будет искать материалы по ключевым словам.",
        reply_markup=main_menu(is_admin(update.effective_user.id))
    )

# ==================== ГЛАВНАЯ ФУНКЦИЯ ====================
def main():
    """Запуск бота"""
    # Инициализируем базу данных
    init_db()
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # ============ ДОБАВЛЯЕМ ОБРАБОТЧИК ОШИБОК ============
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
        """Логируем ошибки"""
        logger.error(f"Ошибка в боте: {context.error}")
        print(f"⚠️ ОШИБКА: {context.error}")
        
        # Отправляем сообщение об ошибке администратору
        if ADMIN_IDS:
            error_msg = f"Ошибка в боте: {context.error}"
            for admin_id in ADMIN_IDS:
                try:
                    await context.bot.send_message(admin_id, error_msg[:4000])
                except:
                    pass
    
    app.add_error_handler(error_handler)
    
    # ==================== HANDLERS ====================
    
    # Получение материалов
    conv_get = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Получить материалы$"), get_materials_start)],
        states={
            SELECT_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_class)],
            SELECT_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_subject)],
            SELECT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_category)],
            SELECT_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_topic)],
        },
        fallbacks=[
            CommandHandler("cancel", start),
            MessageHandler(filters.Regex("^(/start|/menu)$"), start)
        ]
    )
    
    # Запрос материалов
    conv_request = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Запросить материал$"), request_material_start)],
        states={
            REQUEST_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_class)],
            REQUEST_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_subject)],
            REQUEST_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_category)],
            REQUEST_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_topic)],
            REQUEST_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, request_description)],
        },
        fallbacks=[
            CommandHandler("cancel", start),
            MessageHandler(filters.Regex("^(/start|/menu)$"), start)
        ]
    )
    
    # Добавление материалов (админ)
    conv_admin_add = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Добавить материал$"), admin_add_material_start)],
        states={
            ADMIN_ADD_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_class)],
            ADMIN_ADD_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_subject)],
            ADMIN_ADD_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_category)],
            ADMIN_ADD_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_topic)],
            ADMIN_ADD_FILE: [MessageHandler(filters.Document.ALL | filters.PHOTO, admin_add_file)],
        },
        fallbacks=[
            CommandHandler("cancel", admin_panel),
            MessageHandler(filters.Regex("^Назад"), admin_panel)
        ]
    )

    # Удаление материалов (админ)
    conv_admin_delete = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^Удалить материал$"), admin_delete_material_start)],
        states={
            ADMIN_DELETE_SELECT_CLASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_select_class)],
            ADMIN_DELETE_SELECT_SUBJECT: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_select_subject)],
            ADMIN_DELETE_SELECT_CATEGORY: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_select_category)],
            ADMIN_DELETE_SELECT_TOPIC: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_select_topic)],
            ADMIN_DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_delete_confirm)],
        },
        fallbacks=[
            CommandHandler("cancel", admin_panel),
            MessageHandler(filters.Regex("^Назад"), admin_panel)
        ]
    )
    
    # Регистрируем все обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_get)
    app.add_handler(conv_request)
    app.add_handler(conv_admin_add)
    app.add_handler(conv_admin_delete)
    
    # Обработчики инлайн-кнопок для запросов
    app.add_handler(CallbackQueryHandler(handle_request_callback))
    
    # Кнопки админ-панели
    app.add_handler(MessageHandler(filters.Regex("^Админ-панель$"), admin_panel))
    app.add_handler(MessageHandler(filters.Regex("^Просмотреть запросы$"), admin_view_requests))
    app.add_handler(MessageHandler(filters.Regex("^Статистика$"), admin_statistics))
    app.add_handler(MessageHandler(filters.Regex("^В главное меню$"), start))
    
     # 5. Обработчик кнопок главного меню
    app.add_handler(MessageHandler(filters.Regex("^Получить материалы$"), get_materials_start))
    app.add_handler(MessageHandler(filters.Regex("^Запросить материал$"), request_material_start))
    
    # 6. Обработчик уведомлений (В САМОМ КОНЦЕ!)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_notification_text))
    
    # Запускаем бота
    print("=" * 50)
    print("БОТ ЗАПУЩЕН!")
    print(f"Классы: 5-11")
    print("=" * 50)
    print("Напишите /start в Telegram для начала работы")
    
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == '__main__':
    main()