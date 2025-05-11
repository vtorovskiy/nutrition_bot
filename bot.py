import telebot
from telebot import types
import os
import sys
import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from telebot.handler_backends import State, StatesGroup
from telebot.storage import StateMemoryStorage
from telebot import custom_filters
from food_recognition.aitunnel_adapter import AITunnelNutritionAdapter
from database.db_manager import DatabaseManager, Session
from database.models import User, FoodAnalysis
from datetime import datetime, timedelta, date
from utils.helpers import get_nutrition_indicators
from database.models import User, FoodAnalysis, UserSubscription
from food_recognition.barcode_scanner import BarcodeScanner
from monitoring.metrics import metrics_collector
from monitoring.decorators import track_command, track_api_call, track_user_action
import time
from config import PAYMENT_PROVIDER_TOKEN, SUBSCRIPTION_COST
import traceback
import json
import re


# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Импорт модулей проекта
from config import TELEGRAM_BOT_TOKEN, SUBSCRIPTION_COST, FREE_REQUESTS_LIMIT
from database.db_manager import DatabaseManager
from food_recognition.vision_api import FoodRecognition
from food_recognition.nutrition_calc import NutritionCalculator
from payments.yukassa import YuKassaPayment
from utils.helpers import (
    download_photo, format_nutrition_result, get_subscription_info,
    format_datetime, get_remaining_subscription_days
)

# Московское время: UTC+3
TIMEZONE_OFFSET = 3  # Часы

# ID администраторов, которые могут просматривать метрики
ADMIN_IDS = [931190875]

# Инициализация хранилища состояний
state_storage = StateMemoryStorage()

# Инициализация бота с поддержкой состояний
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN, state_storage=state_storage)

# Создаем класс состояний
class BotStates(StatesGroup):
    waiting_for_food_name = State()  # Ожидание ввода названия блюда
    waiting_for_portion_size = State()  # Ожидание ввода размера порции
    waiting_for_gender = State()
    waiting_for_age = State()
    waiting_for_weight = State()
    waiting_for_height = State()
    waiting_for_activity = State()
    waiting_for_goal = State()
    waiting_for_product_name = State()  # Ожидание ввода названия продукта по штрихкоду
    waiting_for_product_calories = State()  # Ожидание ввода калорий
    waiting_for_product_pfc = State()  # Ожидание ввода БЖУ

# Временное хранилище данных пользователей
user_data = {}
user_stats_dates = {}

# Инициализация компонентов
food_recognition = FoodRecognition()
aitunnel_adapter = AITunnelNutritionAdapter()

# Обработчик команды /start
@bot.message_handler(commands=['start'])
@track_command('start')
def start(message):
    """Обработчик команды /start"""
    user_id = message.from_user.id
    username = message.from_user.username
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name
    
    # Регистрация пользователя
    DatabaseManager.get_or_create_user(user_id, username, first_name, last_name)
    
    # Приветственное сообщение
    welcome_text = (
        f"👋 Привет, {first_name or username or 'дорогой пользователь'}!\n\n"
        f"Я SnapEat — твой помощник для анализа КБЖУ блюд.\n"
        f"Отправь мне фото еды или штрихкод продукта.\n\n"
    )
    
    # Добавляем информацию о подписке
    is_subscribed = DatabaseManager.check_subscription_status(user_id)
    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
    
    if not is_subscribed:
        welcome_text += f"🔸 Доступно {remaining_requests} бесплатных анализов\n"
    else:
        welcome_text += "✅ У вас активная подписка\n"
    
    # Кнопки (изменяем порядок)
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("Настроить профиль", callback_data="setup_profile"))
    
    if not is_subscribed:
        markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
    
    # Путь к приветственной фотографии
    welcome_image_path = os.path.join(os.path.dirname(__file__), 'static', 'start_photo.jpg')
    
    try:
        # Отправляем фото с текстом
        with open(welcome_image_path, 'rb') as photo:
            bot.send_photo(
                message.chat.id, 
                photo, 
                caption=welcome_text, 
                parse_mode="Markdown", 
                reply_markup=markup
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке приветственного изображения: {str(e)}")
        # В случае ошибки отправляем только текст
        bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['metrics'])
@track_command('metrics')
def metrics_command(message):
    """Обработчик команды /metrics для просмотра текущих метрик"""
    user_id = message.from_user.id
    
    # Проверка прав доступа (замените на ваш ID)
    if user_id not in [931190875]:
        bot.reply_to(message, "У вас нет доступа к этой команде.")
        return
    
    try:
        # Получение сводки по метрикам
        metrics_summary = metrics_collector.get_metrics_summary()
        
        # Основная информация
        main_metrics = (
            "📊 Сводка по метрикам\n\n"
            f"⏱ Время работы: {metrics_summary.get('uptime', 'N/A')}\n"
            f"🔄 Количество перезапусков: {metrics_summary.get('restart_count', 0)}\n\n"
            f"👤 Уникальных пользователей: {metrics_summary.get('unique_users_count', 0)}\n"
            f"📸 Анализов фотографий: {metrics_summary.get('photo_analyses', 0)}\n"
            f"🔍 Сканирований штрихкодов: {metrics_summary.get('barcode_scans', 0)}\n"
            f"💳 Покупок подписок: {metrics_summary.get('subscription_purchases', 0)}\n\n"
            f"📡 API вызовов всего: {metrics_summary.get('total_api_calls', 0)}\n"
            f"⚠️ Ошибок API: {metrics_summary.get('total_api_errors', 0)} "
            f"({metrics_summary.get('error_rate', '0%')})"
        )
        
        # Отправляем основную информацию
        bot.reply_to(message, main_metrics)
        
        # Популярные команды - без Markdown
        if metrics_summary.get('popular_commands'):
            commands_text = "Популярные команды:\n"
            for cmd, count in metrics_summary.get('popular_commands', {}).items():
                commands_text += f"• {cmd}: {count}\n"
            
            bot.send_message(message.chat.id, commands_text)
        
        # Время ответа API - без Markdown
        if metrics_summary.get('avg_response_times'):
            api_text = "Среднее время ответа API (сек):\n"
            for api, avg_time in metrics_summary.get('avg_response_times', {}).items():
                api_text += f"• {api}: {avg_time:.3f}\n"
            
            bot.send_message(message.chat.id, api_text)
        
        # Ошибки - без Markdown
        if metrics_summary.get('top_errors'):
            errors_text = "Частые ошибки:\n"
            for error, count in metrics_summary.get('top_errors', {}).items():
                errors_text += f"• {error}: {count}\n"
            
            bot.send_message(message.chat.id, errors_text)
            
    except Exception as e:
        logger.error(f"Ошибка при формировании метрик: {str(e)}")
        bot.reply_to(message, f"Произошла ошибка при формировании метрик: {str(e)}")

@bot.message_handler(commands=['setup'])
@track_command('setup')
def setup_command(message):
    """Обработчик команды /setup для настройки профиля пользователя"""
    user_id = message.from_user.id
    
    # Получаем текущий профиль пользователя
    user_profile = DatabaseManager.get_user_profile(user_id)

    # Путь к изображению для команды setup
    setup_image_path = os.path.join(os.path.dirname(__file__), 'static', 'setup.jpg')
    
    if user_profile and (user_profile.get('gender') or user_profile.get('daily_calories')):
        # Если профиль уже настроен, показываем текущие данные
        profile_text = "⚙️ *Ваш профиль*\n\n"
        
        if user_profile.get('gender'):
            profile_text += f"• Пол: {'Мужской' if user_profile['gender'] == 'male' else 'Женский'}\n"
        if user_profile.get('age'):
            profile_text += f"• Возраст: {user_profile['age']} лет\n"
        if user_profile.get('weight'):
            profile_text += f"• Вес: {user_profile['weight']} кг\n"
        if user_profile.get('height'):
            profile_text += f"• Рост: {user_profile['height']} см\n"
        if user_profile.get('activity_level'):
            profile_text += f"• Уровень активности: {user_profile['activity_level']}\n"
        
        profile_text += "\n*Ваши дневные нормы КБЖУ:*\n"
        
        if user_profile.get('daily_calories'):
            profile_text += f"• Калории: {user_profile['daily_calories']} ккал\n"
        if user_profile.get('daily_proteins'):
            profile_text += f"• Белки: {user_profile['daily_proteins']} г\n"
        if user_profile.get('daily_fats'):
            profile_text += f"• Жиры: {user_profile['daily_fats']} г\n"
        if user_profile.get('daily_carbs'):
            profile_text += f"• Углеводы: {user_profile['daily_carbs']} г\n"
        
        # Кнопки для обновления профиля
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(
            InlineKeyboardButton("Обновить данные", callback_data="setup_profile"),
            InlineKeyboardButton("Задать нормы вручную", callback_data="setup_manual_norms")
        )

        try:
            # Отправляем фото с текстом
            with open(setup_image_path, 'rb') as photo:
                bot.send_photo(
                    message.chat.id, 
                    photo, 
                    caption=profile_text, 
                    parse_mode="Markdown", 
                    reply_markup=markup
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке изображения для команды setup: {str(e)}")
            # В случае ошибки отправляем только текст
            bot.send_message(message.chat.id, profile_text, parse_mode="Markdown", reply_markup=markup)
        
    else:
        # Если профиль не настроен, предлагаем настроить
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Настроить профиль", callback_data="setup_profile"),
            InlineKeyboardButton("Задать нормы вручную", callback_data="setup_manual_norms")
        )
        
        setup_text = (
            "⚙️ *Настройка персонального профиля*\n\n"
            "Для точного расчета ваших дневных норм КБЖУ я могу использовать ваши физические параметры.\n\n"
            "Выберите способ настройки:\n"
            "1. *Настроить профиль* - я помогу вам ввести пол, возраст, вес, рост и уровень активности, "
            "а затем рассчитаю рекомендуемые нормы КБЖУ.\n"
            "2. *Задать нормы вручную* - вы сможете сами указать желаемые дневные нормы калорий, белков, жиров и углеводов.\n\n"
            "_Все данные хранятся только в нашей базе и используются исключительно для расчета норм._"
        )
        
        try:
            # Отправляем фото с текстом
            with open(setup_image_path, 'rb') as photo:
                bot.send_photo(
                    message.chat.id, 
                    photo, 
                    caption=setup_text, 
                    parse_mode="Markdown", 
                    reply_markup=markup
                )
        except Exception as e:
            logger.error(f"Ошибка при отправке изображения для команды setup: {str(e)}")
            # В случае ошибки отправляем только текст
            bot.send_message(message.chat.id, setup_text, parse_mode="Markdown", reply_markup=markup)

# Обработчик для кнопок настройки профиля
@bot.callback_query_handler(func=lambda call: call.data.startswith("setup_"))
def setup_callback(call):
    """Обработчик кнопок настройки профиля"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    if call.data == "setup_profile":
        # Начинаем процесс настройки профиля
        bot.delete_message(chat_id, call.message.message_id)
        
        # Запрашиваем пол пользователя
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(
            InlineKeyboardButton("Мужской", callback_data="gender_male"),
            InlineKeyboardButton("Женский", callback_data="gender_female")
        )
        
        bot.send_message(
            chat_id,
            "Выберите ваш пол:",
            reply_markup=markup
        )
    
    elif call.data == "setup_manual_norms":
        # Переходим к ручному вводу норм
        bot.delete_message(chat_id, call.message.message_id)
        
        manual_norms_text = (
            "*Ввод дневных норм КБЖУ вручную*\n\n"
            "Пожалуйста, введите ваши дневные нормы в следующем формате:\n"
            "`калории белки жиры углеводы`\n\n"
            "Например: `2000 150 70 200`\n\n"
            "Это означает:\n"
            "- 2000 ккал\n"
            "- 150 г белка\n"
            "- 70 г жиров\n"
            "- 200 г углеводов"
        )
        
        sent_message = bot.send_message(
            chat_id,
            manual_norms_text,
            parse_mode="Markdown"
        )
        
        # Устанавливаем состояние ожидания ввода норм
        bot.register_next_step_handler(sent_message, process_manual_norms)

@bot.callback_query_handler(func=lambda call: call.data.startswith("gender_"))
def gender_callback(call):
    """Обработчик выбора пола"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    gender = call.data.split("_")[1]  # 'male' или 'female'
    
    # Сохраняем пол пользователя
    user_data[user_id] = user_data.get(user_id, {})
    user_data[user_id]['gender'] = gender
    
    # Обновляем сообщение и запрашиваем возраст
    bot.edit_message_text(
        f"*Настройка профиля*\n\n"
        f"Пол: {'Мужской' if gender == 'male' else 'Женский'}\n\n"
        f"Введите ваш возраст (полных лет):",
        chat_id,
        call.message.message_id,
        parse_mode="Markdown"
    )
    
    # Устанавливаем состояние ожидания ввода возраста
    bot.set_state(user_id, BotStates.waiting_for_age, chat_id)

@bot.message_handler(state=BotStates.waiting_for_age)
def process_age(message):
    """Обработчик ввода возраста"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    age_text = message.text.strip()
    
    # Проверяем корректность ввода
    try:
        age = int(age_text)
        if age < 12 or age > 100:
            raise ValueError("Возраст должен быть от 12 до 100 лет")
    except ValueError as e:
        bot.send_message(chat_id, f"⚠️ {str(e)}. Пожалуйста, введите корректный возраст (число от 12 до 100):")
        return
    
    # Сохраняем возраст пользователя
    user_data[user_id]['age'] = age
    
    # Удаляем предыдущее сообщение (вопрос о возрасте)
    try:
        bot.delete_message(chat_id, message.message_id-1)
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {str(e)}")
    
    # Создаем новое сообщение с обновленной информацией
    sent_message = bot.send_message(
        chat_id,
        f"*Настройка профиля*\n\n"
        f"Пол: {'Мужской' if user_data[user_id]['gender'] == 'male' else 'Женский'}\n"
        f"Возраст: {age} лет\n\n"
        f"Введите ваш вес в килограммах:",
        parse_mode="Markdown"
    )
    
    # Устанавливаем состояние ожидания ввода веса
    bot.set_state(user_id, BotStates.waiting_for_weight, chat_id)

@bot.message_handler(state=BotStates.waiting_for_weight)
def process_weight(message):
    """Обработчик ввода веса"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    weight_text = message.text.strip()
    
    # Проверяем корректность ввода
    try:
        weight = float(weight_text.replace(',', '.'))
        if weight < 30 or weight > 300:
            raise ValueError("Вес должен быть от 30 до 300 кг")
    except ValueError as e:
        bot.send_message(chat_id, f"⚠️ {str(e)}. Пожалуйста, введите корректный вес (число от 30 до 300):")
        return
    
    # Сохраняем вес пользователя
    user_data[user_id]['weight'] = weight
    
    # Удаляем предыдущее сообщение (вопрос о весе)
    try:
        bot.delete_message(chat_id, message.message_id-1)
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {str(e)}")
    
    # Создаем новое сообщение с обновленной информацией
    sent_message = bot.send_message(
        chat_id,
        f"*Настройка профиля*\n\n"
        f"Пол: {'Мужской' if user_data[user_id]['gender'] == 'male' else 'Женский'}\n"
        f"Возраст: {user_data[user_id]['age']} лет\n"
        f"Вес: {weight} кг\n\n"
        f"Введите ваш рост в сантиметрах:",
        parse_mode="Markdown"
    )
    
    # Устанавливаем состояние ожидания ввода роста
    bot.set_state(user_id, BotStates.waiting_for_height, chat_id)

@bot.message_handler(state=BotStates.waiting_for_height)
def process_height(message):
    """Обработчик ввода роста"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    height_text = message.text.strip()
    
    # Проверяем корректность ввода
    try:
        height = float(height_text.replace(',', '.'))
        if height < 100 or height > 250:
            raise ValueError("Рост должен быть от 100 до 250 см")
    except ValueError as e:
        bot.send_message(chat_id, f"⚠️ {str(e)}. Пожалуйста, введите корректный рост (число от 100 до 250):")
        return
    
    # Сохраняем рост пользователя
    user_data[user_id]['height'] = height
    
    # Сбрасываем состояние после успешного ввода роста
    bot.delete_state(user_id, chat_id)
    
    # Запрашиваем уровень активности
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Сидячий образ жизни (1.2)", callback_data="activity_1.2"),
        InlineKeyboardButton("Легкая активность (1.375)", callback_data="activity_1.375"),
        InlineKeyboardButton("Умеренная активность (1.55)", callback_data="activity_1.55"),
        InlineKeyboardButton("Высокая активность (1.725)", callback_data="activity_1.725"),
        InlineKeyboardButton("Очень высокая активность (1.9)", callback_data="activity_1.9")
    )
    
    activity_text = (
        f"Рост: {height} см\n\n"
        "Выберите ваш уровень физической активности:\n\n"
        "• *Сидячий образ жизни* - минимальная или отсутствие физической нагрузки\n"
        "• *Легкая активность* - легкие тренировки 1-3 раза в неделю\n"
        "• *Умеренная активность* - тренировки 3-5 раз в неделю\n"
        "• *Высокая активность* - интенсивные тренировки 6-7 раз в неделю\n"
        "• *Очень высокая активность* - тяжелая физическая работа, 2 тренировки в день"
    )
    
    bot.send_message(chat_id, activity_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("activity_"))
def activity_callback(call):
    """Обработчик выбора уровня активности"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    activity_level = float(call.data.split("_")[1])
    
    # Сохраняем уровень активности пользователя
    user_data[user_id]['activity_level'] = activity_level
    
    # Запрашиваем цель
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Похудение", callback_data="goal_weight_loss"),
        InlineKeyboardButton("Поддержание веса", callback_data="goal_maintenance"),
        InlineKeyboardButton("Набор массы", callback_data="goal_weight_gain")
    )
    
    goal_text = (
        f"Уровень активности: {activity_level}\n\n"
        "Выберите вашу цель:\n\n"
        "• *Похудение* - снижение веса, дефицит калорий\n"
        "• *Поддержание веса* - сохранение текущего веса\n"
        "• *Набор массы* - увеличение веса и мышечной массы"
    )
    
    bot.edit_message_text(
        goal_text,
        chat_id,
        call.message.message_id,
        parse_mode="Markdown",
        reply_markup=markup
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("goal_"))
def goal_callback(call):
    """Обработчик выбора цели"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    goal = call.data.split("_", 1)[1]  # 'weight_loss', 'maintenance' или 'weight_gain'
    
    # Сохраняем цель пользователя
    user_data[user_id]['goal'] = goal
    
    # Получаем все данные пользователя
    user_profile = user_data[user_id]
    
    # Обновляем профиль пользователя и рассчитываем дневные нормы с учетом цели
    norms = DatabaseManager.update_user_profile(
        user_id,
        gender=user_profile['gender'],
        age=user_profile['age'],
        weight=user_profile['weight'],
        height=user_profile['height'],
        activity_level=user_profile['activity_level'],
        goal=user_profile['goal']
    )
    
    if norms:
        # Отображаем результаты
        result_text = (
            "✅ *Ваш профиль успешно настроен!*\n\n"
            f"• Пол: {'Мужской' if user_profile['gender'] == 'male' else 'Женский'}\n"
            f"• Возраст: {user_profile['age']} лет\n"
            f"• Вес: {user_profile['weight']} кг\n"
            f"• Рост: {user_profile['height']} см\n"
            f"• Уровень активности: {user_profile['activity_level']}\n"
            f"• Цель: {'Похудение' if user_profile['goal'] == 'weight_loss' else 'Поддержание веса' if user_profile['goal'] == 'maintenance' else 'Набор массы'}\n\n"
            "*Рекомендуемые дневные нормы КБЖУ:*\n"
            f"• Калории: {norms['daily_calories']} ккал\n"
            f"• Белки: {norms['daily_proteins']} г\n"
            f"• Жиры: {norms['daily_fats']} г\n"
            f"• Углеводы: {norms['daily_carbs']} г\n\n"
            "Теперь ваша статистика будет отображаться с указанием прогресса относительно этих норм."
        )
        
        bot.edit_message_text(
            result_text,
            chat_id,
            call.message.message_id,
            parse_mode="Markdown"
        )
        
        # Очищаем данные пользователя после успешного обновления профиля
        if user_id in user_data:
            del user_data[user_id]
    else:
        bot.edit_message_text(
            "❌ Произошла ошибка при обновлении профиля. Пожалуйста, попробуйте позже.",
            chat_id,
            call.message.message_id
        )

def process_manual_norms(message):
    """Обработчик ручного ввода норм КБЖУ"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Разбираем введенные значения
    try:
        values = message.text.strip().split()
        if len(values) != 4:
            raise ValueError("Нужно ввести ровно 4 числа")
        
        calories = float(values[0])
        proteins = float(values[1])
        fats = float(values[2])
        carbs = float(values[3])
        
        # Проверяем диапазоны значений
        if calories < 500 or calories > 10000:
            raise ValueError("Калории должны быть от 500 до 10000")
        if proteins < 10 or proteins > 500:
            raise ValueError("Белки должны быть от 10 до 500")
        if fats < 10 or fats > 500:
            raise ValueError("Жиры должны быть от 10 до 500")
        if carbs < 10 or carbs > 1000:
            raise ValueError("Углеводы должны быть от 10 до 1000")
    except ValueError as e:
        bot.send_message(
            chat_id,
            f"❌ Ошибка: {str(e)}. Пожалуйста, введите четыре числа через пробел (калории белки жиры углеводы).\n"
            "Например: `2000 150 70 200`",
            parse_mode="Markdown"
        )
        return
    
    # Обновляем нормы пользователя
    norms = DatabaseManager.update_user_profile(
        user_id,
        daily_calories=calories,
        daily_proteins=proteins,
        daily_fats=fats,
        daily_carbs=carbs
    )
    
    if norms:
        # Отображаем результаты
        result_text = (
            "✅ *Ваши нормы КБЖУ успешно установлены:*\n\n"
            f"• Калории: {norms['daily_calories']} ккал\n"
            f"• Белки: {norms['daily_proteins']} г\n"
            f"• Жиры: {norms['daily_fats']} г\n"
            f"• Углеводы: {norms['daily_carbs']} г\n\n"
            "Теперь ваша статистика будет отображаться с указанием прогресса относительно этих норм."
        )
        
        bot.send_message(
            chat_id,
            result_text,
            parse_mode="Markdown"
        )
    else:
        bot.send_message(
            chat_id,
            "❌ Произошла ошибка при обновлении норм. Пожалуйста, попробуйте позже."
        )

def send_payment_invoice(chat_id, title, description, amount, months, user_id=None):
    """
    Отправляет счет на оплату через Telegram Payments API
    
    Args:
        chat_id (int): ID чата пользователя
        title (str): Название товара/услуги
        description (str): Описание товара/услуги
        amount (float): Сумма к оплате
        months (int): Количество месяцев подписки
        user_id (int, optional): ID пользователя (если None, то берется chat_id)
    """
    try:
        # Если user_id не передан, используем chat_id (для личных чатов они совпадают)
        if user_id is None:
            user_id = chat_id
        
        # Создаем уникальный идентификатор платежа
        payload = f"subscription_{user_id}_{months}_{int(time.time())}"
        
        # Преобразуем сумму в копейки (минимальные единицы валюты)
        price_amount = int(amount * 100)  # Например, 100.50 рублей = 10050 копеек
        
        # Создаем массив цен (может содержать несколько позиций)
        prices = [
            types.LabeledPrice(label=title, amount=price_amount)
        ]
        
        # Отправляем счет
        bot.send_invoice(
            chat_id=chat_id,
            title=title,                         # Название товара
            description=description,             # Описание товара
            invoice_payload=payload,             # Полезные данные для идентификации платежа
            provider_token=PAYMENT_PROVIDER_TOKEN,  # Токен от BotFather
            currency="RUB",                      # Валюта
            prices=prices,                       # Массив цен
            start_parameter=f"sub_{months}m"     # Параметр для глубоких ссылок
        )
        logger.info(f"Счет на оплату отправлен пользователю {chat_id}")
        return True
    except Exception as e:
        logger.error(f"Ошибка при отправке счета: {str(e)}")
        return False

# Обработчик команды /help
@bot.message_handler(commands=['help'])
@track_command('help')
def help_command(message):
    """Обработчик команды /help"""
    help_text = (
        "📱 *SnapEat - Помощь*\n\n"
        "Этот бот поможет вам рассчитать КБЖУ (калории, белки, жиры, углеводы) "
        "блюд по фотографии или штрихкоду продукта.\n\n"
        "🔍 *Как использовать:*\n"
        "1. Отправьте фотографию блюда или штрихкод боту\n"
        "2. Дождитесь анализа (обычно занимает несколько секунд)\n"
        "3. Получите детальную информацию о пищевой ценности\n\n"
        "📋 *Команды:*\n"
        "/start - Начать использование бота\n"
        "/help - Показать это сообщение\n"
        "/subscription - Управление подпиской\n"
        "/stats - Ваша статистика использования\n"
        "/setup - Настройка профиля и норм КБЖУ\n\n"
        "💳 *Подписка:*\n"
        f"- Бесплатно: {FREE_REQUESTS_LIMIT} анализов\n"
        f"- Подписка: {SUBSCRIPTION_COST} руб/месяц - неограниченное количество анализов\n\n"
        "❓ *Вопросы и поддержка:*\n"
        "Если у вас возникли вопросы или проблемы, свяжитесь с нашей службой поддержки"
    )
    
    # Путь к изображению для команды help
    help_image_path = os.path.join(os.path.dirname(__file__), 'static', 'help.jpg')
    
    try:
        # Отправляем фото с текстом
        with open(help_image_path, 'rb') as photo:
            bot.send_photo(
                message.chat.id, 
                photo, 
                caption=help_text, 
                parse_mode="Markdown"
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке изображения для команды help: {str(e)}")
        # В случае ошибки отправляем только текст
        bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

# Обработчик команды /subscription
@bot.message_handler(commands=['subscription'])
@track_command('subscription')
def subscription_command(message):
    """Обработчик команды /subscription"""
    user_id = message.from_user.id
    
    # Проверка статуса подписки
    is_subscribed = DatabaseManager.check_subscription_status(user_id)
    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)

    # Путь к изображению для команды subscription
    subscription_image_path = os.path.join(os.path.dirname(__file__), 'static', 'subscription.jpg')
    
    # Формирование сообщения
    if is_subscribed:
        # Получение информации о подписке
        from database.db_manager import Session  # Правильный импорт
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            subscription = session.query(UserSubscription).filter_by(
                user_id=user.id, 
                is_active=True
            ).order_by(UserSubscription.end_date.desc()).first()
            
            end_date = subscription.end_date if subscription else None
            remaining_days = get_remaining_subscription_days(end_date)
            
            subscription_text = (
                "✅ *Ваша подписка активна*\n\n"
                f"Дата окончания: {format_datetime(end_date)}\n"
                f"Осталось дней: {remaining_days}\n\n"
                "С активной подпиской вы можете делать неограниченное количество запросов."
            )
            
            # Кнопки
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Продлить подписку", callback_data="subscribe"))
        finally:
            session.close()
    else:
        subscription_text = (
            "❌ *У вас нет активной подписки*\n\n"
            f"Доступно бесплатных запросов: {remaining_requests} из {FREE_REQUESTS_LIMIT}\n\n"
            f"Стоимость подписки: {SUBSCRIPTION_COST} руб/месяц\n"
            "С подпиской вы получите неограниченное количество запросов для анализа КБЖУ."
        )
        
        # Кнопки
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
    
    try:
        # Отправляем фото с текстом
        with open(subscription_image_path, 'rb') as photo:
            bot.send_photo(
                message.chat.id, 
                photo, 
                caption=subscription_text, 
                parse_mode="Markdown", 
                reply_markup=markup
            )
    except Exception as e:
        logger.error(f"Ошибка при отправке изображения для команды subscription: {str(e)}")
        # В случае ошибки отправляем только текст
        bot.send_message(message.chat.id, subscription_text, parse_mode="Markdown", reply_markup=markup)

# Обработчик команды /stats
@bot.message_handler(commands=['stats'])
@track_command('stats')
def stats_command(message):
    """Обработчик команды /stats с возможностью листать даты"""
    user_id = message.from_user.id
    
    # Устанавливаем текущую дату для статистики (если не установлена)
    if user_id not in user_stats_dates:
        user_stats_dates[user_id] = (datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).date()
    
    # Отображаем статистику за выбранную дату
    show_stats_for_date(message.chat.id, user_id, user_stats_dates[user_id])

def show_stats_for_date(chat_id, user_id, selected_date):
    """
    Показывает компактную статистику за выбранную дату с блюдами
    
    Args:
        chat_id (int): ID чата для отправки сообщения
        user_id (int): Telegram ID пользователя
        selected_date (datetime.date): Выбранная дата для отображения статистики
    """
    # Получение статистики за выбранную дату
    try:
        daily_stats = DatabaseManager.get_nutrition_stats_for_date(user_id, selected_date)
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {str(e)}")
        bot.send_message(chat_id, "Произошла ошибка при получении статистики. Пожалуйста, попробуйте позже.")
        return

    # Форматируем дату для отображения
    date_str = selected_date.strftime("%d.%m.%Y")
    
    # Создаем кнопки для навигации по датам - ВСЕГДА показываем кнопки
    markup = InlineKeyboardMarkup(row_width=3)
    
    # Кнопка для предыдущей даты
    prev_date = selected_date - timedelta(days=1)
    prev_button = InlineKeyboardButton("⬅️ Пред. день", callback_data=f"stats_prev_{prev_date.strftime('%Y-%m-%d')}")
    
    # Кнопка для сегодня
    today_date = (datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).date()
    today_button = InlineKeyboardButton("Сегодня", callback_data=f"stats_today")
    
    # Кнопка для следующей даты
    next_date = selected_date + timedelta(days=1)
    can_show_next = next_date <= today_date
    next_button = InlineKeyboardButton("След. день ➡️", callback_data=f"stats_next_{next_date.strftime('%Y-%m-%d')}")
    
    # Добавляем кнопки (всегда показываем хотя бы кнопку "Сегодня")
    if selected_date == today_date:
        # Если текущий день - показываем только кнопку "Пред. день"
        markup.add(prev_button, today_button)
    elif can_show_next:
        # Стандартный набор с тремя кнопками
        markup.add(prev_button, today_button, next_button)
    else:
        # Если это будущий день или день перед сегодняшним - нет кнопки "След. день"
        markup.add(prev_button, today_button)
    
    # Проверяем, есть ли данные за выбранную дату
    if not daily_stats or daily_stats["total"]["count"] == 0:
        # Даже если данных нет, показываем кнопки навигации
        stats_text = f"📊 Питание за {date_str}\n\nЗа этот день нет данных о питании."
        bot.send_message(chat_id, stats_text, parse_mode="Markdown", reply_markup=markup)
        return
    
    # Формирование компактного сообщения
    stats_text = f"📊 Питание за {date_str}\n\n"
    
    # Завтрак
    if daily_stats["breakfast"]["count"] > 0:
        # Округляем значения
        calories = int(daily_stats['breakfast']['calories'])
        proteins = int(daily_stats['breakfast']['proteins'])
        fats = int(daily_stats['breakfast']['fats'])
        carbs = int(daily_stats['breakfast']['carbs'])
        
        stats_text += f"🍳 Завтрак: {calories} ккал\n"
        stats_text += f"   Б/Ж/У: {proteins}г | {fats}г | {carbs}г\n"
        
        # Добавляем блюда
        for item in daily_stats["breakfast"]["items"]:
            item_calories = int(item['calories'])
            stats_text += f"   • {item['name']} ({item_calories} ккал)\n"
        
        stats_text += "\n"
    
    # Обед
    if daily_stats["lunch"]["count"] > 0:
        # Округляем значения
        calories = int(daily_stats['lunch']['calories'])
        proteins = int(daily_stats['lunch']['proteins'])
        fats = int(daily_stats['lunch']['fats'])
        carbs = int(daily_stats['lunch']['carbs'])
        
        stats_text += f"🍲 Обед: {calories} ккал\n"
        stats_text += f"   Б/Ж/У: {proteins}г | {fats}г | {carbs}г\n"
        
        # Добавляем блюда
        for item in daily_stats["lunch"]["items"]:
            item_calories = int(item['calories'])
            stats_text += f"   • {item['name']} ({item_calories} ккал)\n"
        
        stats_text += "\n"
    
    # Ужин
    if daily_stats["dinner"]["count"] > 0:
        # Округляем значения
        calories = int(daily_stats['dinner']['calories'])
        proteins = int(daily_stats['dinner']['proteins'])
        fats = int(daily_stats['dinner']['fats'])
        carbs = int(daily_stats['dinner']['carbs'])
        
        stats_text += f"🍽 Ужин: {calories} ккал\n"
        stats_text += f"   Б/Ж/У: {proteins}г | {fats}г | {carbs}г\n"
        
        # Добавляем блюда
        for item in daily_stats["dinner"]["items"]:
            item_calories = int(item['calories'])
            stats_text += f"   • {item['name']} ({item_calories} ккал)\n"
        
        stats_text += "\n"
    
    # Перекусы
    if daily_stats["snack"]["count"] > 0:
        # Округляем значения
        calories = int(daily_stats['snack']['calories'])
        proteins = int(daily_stats['snack']['proteins'])
        fats = int(daily_stats['snack']['fats'])
        carbs = int(daily_stats['snack']['carbs'])
        
        stats_text += f"🍪 Перекус: {calories} ккал\n"
        stats_text += f"   Б/Ж/У: {proteins}г | {fats}г | {carbs}г\n"
        
        # Добавляем блюда
        for item in daily_stats["snack"]["items"]:
            item_calories = int(item['calories'])
            stats_text += f"   • {item['name']} ({item_calories} ккал)\n"
        
        stats_text += "\n"
    
    # Итоги за день
    total_calories = int(daily_stats['total']['calories'])
    total_proteins = int(daily_stats['total']['proteins'])
    total_fats = int(daily_stats['total']['fats'])
    total_carbs = int(daily_stats['total']['carbs'])
    
    stats_text += f"🔄 За день: {total_calories} ккал (Б: {total_proteins}г Ж: {total_fats}г У: {total_carbs}г)"
    
    bot.send_message(chat_id, stats_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("stats_"))
def stats_navigation_callback(call):
    """Обработчик кнопок навигации по датам в статистике"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Обрабатываем различные типы команд навигации
    if call.data == "stats_today":
        # Показываем статистику за сегодня
        user_stats_dates[user_id] = (datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).date()
    elif call.data.startswith("stats_prev_"):
        # Показываем статистику за предыдущий день
        date_str = call.data[11:]  # Получаем дату из callback_data
        user_stats_dates[user_id] = datetime.strptime(date_str, "%Y-%m-%d").date()
    elif call.data.startswith("stats_next_"):
        # Показываем статистику за следующий день
        date_str = call.data[11:]  # Получаем дату из callback_data
        user_stats_dates[user_id] = datetime.strptime(date_str, "%Y-%m-%d").date()
    
    # Удаляем оригинальное сообщение для избежания спама
    try:
        bot.delete_message(chat_id, call.message.message_id)
    except Exception as e:
        logger.error(f"Ошибка при удалении сообщения: {str(e)}")
    
    # Показываем статистику за выбранную дату
    show_stats_for_date(chat_id, user_id, user_stats_dates[user_id])

# Обработчик для кнопки ручного ввода
@bot.callback_query_handler(func=lambda call: call.data == "manual_input")
@track_command('manual_input')
def manual_input_callback(call):
    """Обработчик кнопки ручного ввода данных о продукте"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Сохраняем штрихкод из сообщения
    barcode_text = call.message.text
    barcode = None
    for line in barcode_text.split('\n'):
        if 'Штрихкод:' in line:
            barcode = line.replace('Штрихкод:', '').replace('*', '').strip()
            break
    
    if not barcode and user_id in user_data:
        barcode = user_data[user_id].get('barcode')
    
    # Сохраняем данные для последующего использования
    if user_id not in user_data:
        user_data[user_id] = {}
    
    user_data[user_id]['barcode'] = barcode
    user_data[user_id]['message_id'] = call.message.message_id
    
    # Запрашиваем название продукта
    bot.edit_message_text(
        "Введите название продукта:",
        chat_id,
        call.message.message_id
    )
    
    # Устанавливаем состояние ожидания ввода названия продукта
    bot.set_state(user_id, BotStates.waiting_for_product_name, chat_id)

@bot.callback_query_handler(func=lambda call: call.data == "specify_food")
@track_command('specify_food')
def specify_food_callback(call):
    """Обработчик кнопки уточнения блюда"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Сохраняем ID сообщения для обновления
    user_data[user_id] = {
        'message_id': call.message.message_id,
        'last_photo_id': None  # Здесь будет ID последней фотографии
    }
    
    # Устанавливаем состояние ожидания названия блюда
    bot.set_state(user_id, BotStates.waiting_for_food_name, chat_id)
    
    # Запрашиваем уточнение
    bot.edit_message_text(
        "Пожалуйста, введите точное название блюда для более точного расчета КБЖУ:",
        chat_id,
        call.message.message_id,
        reply_markup=None
    )

@bot.callback_query_handler(func=lambda call: call.data == "specify_portion")
@track_command('specify_portion')
def specify_portion_callback(call):
    """Обработчик кнопки указания размера порции"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Сохраняем ID сообщения для обновления
    if user_id not in user_data:
        user_data[user_id] = {}
    
    # ВАЖНО: Сохраняем ID сообщения
    user_data[user_id]['message_id'] = call.message.message_id
    
    # Устанавливаем состояние ожидания ввода размера порции
    bot.set_state(user_id, BotStates.waiting_for_portion_size, chat_id)
    
    # Запрашиваем уточнение
    bot.edit_message_text(
        "Пожалуйста, введите примерный вес порции в граммах (только число):",
        chat_id,
        call.message.message_id,
        reply_markup=None
    )

@bot.callback_query_handler(func=lambda call: call.data == "subscribe")
@track_command('subscribe_menu')
def subscribe_menu_callback(call):
    """Обработчик кнопки 'Оформить подписку' - показывает меню вариантов подписки"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Создаем клавиатуру с вариантами подписки
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("1 месяц", callback_data="subscribe_1"),
        InlineKeyboardButton("3 месяца (-10%)", callback_data="subscribe_3"),
        InlineKeyboardButton("6 месяцев (-15%)", callback_data="subscribe_6"),
        InlineKeyboardButton("12 месяцев (-20%)", callback_data="subscribe_12")
    )
    
    try:
        # Пробуем отредактировать текст сообщения
        bot.edit_message_text(
            "Выберите срок подписки:",
            chat_id,
            call.message.message_id,
            reply_markup=markup
        )
    except Exception as e:
        # Если не получается отредактировать (например, это сообщение с фото),
        # отправляем новое сообщение
        bot.send_message(
            chat_id,
            "Выберите срок подписки:",
            reply_markup=markup
        )

@bot.callback_query_handler(func=lambda call: call.data.startswith("subscribe_"))
@track_command('subscribe_payment')
def subscription_callback(call):
    """Обработчик кнопок подписки"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Получаем количество месяцев
    months = int(call.data.split("_")[1])
    
    # Расчет скидки
    discount = 0
    if months == 3:
        discount = 0.1  # 10%
    elif months == 6:
        discount = 0.15  # 15%
    elif months == 12:
        discount = 0.2  # 20%
    
    # Рассчитываем сумму с учетом скидки
    amount = SUBSCRIPTION_COST * months * (1 - discount)
    amount_rounded = round(amount, 2)  # Округляем до 2 знаков
    
    # Данные для счета
    title = f"Подписка на {months} мес."
    description = f"Подписка на бота для анализа КБЖУ на {months} месяцев"
    
    # Отправляем сообщение, что готовим счет
    bot.edit_message_text(
        f"Подготовка счета на оплату подписки на {months} мес...",
        chat_id,
        call.message.message_id
    )
    
    # Также отслеживаем, какой тип подписки выбран
    metrics_collector.track_command(f'subscribe_{months}m')
    
    # Отправляем счет через Telegram Payments API
    result = send_payment_invoice(chat_id, title, description, amount_rounded, months, user_id)
    
    if not result:
        bot.edit_message_text(
            "Произошла ошибка при создании счета. Пожалуйста, попробуйте позже или обратитесь в поддержку.",
            chat_id,
            call.message.message_id
        )

# Обработчик ввода названия продукта
@bot.message_handler(state=BotStates.waiting_for_product_name)
def process_product_name(message):
    """Обработчик ввода названия продукта"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    product_name = message.text.strip()
    
    # Сохраняем название продукта
    if user_id not in user_data:
        user_data[user_id] = {}
        
    user_data[user_id]['name'] = product_name
    
    # Запрашиваем калории
    sent_message = bot.send_message(
        chat_id,
        f"Название продукта: *{product_name}*\n\n"
        "Введите количество калорий (ккал):",
        parse_mode="Markdown"
    )
    
    # Устанавливаем состояние ожидания ввода калорий
    bot.set_state(user_id, BotStates.waiting_for_product_calories, chat_id)

# Обработчик ввода калорий
@bot.message_handler(state=BotStates.waiting_for_product_calories)
def process_product_calories(message):
    """Обработчик ввода калорий продукта"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    calories_text = message.text.strip()
    
    # Проверяем корректность ввода
    try:
        calories = float(calories_text.replace(',', '.'))
    except ValueError:
        bot.send_message(chat_id, "Пожалуйста, введите число. Попробуйте еще раз:")
        return
    
    # Сохраняем калории
    user_data[user_id]['calories'] = calories
    
    # Запрашиваем БЖУ
    sent_message = bot.send_message(
        chat_id,
        f"Калории: *{calories}* ккал\n\n"
        "Введите БЖУ в формате 'Белки Жиры Углеводы' (числа через пробел):",
        parse_mode="Markdown"
    )
    
    # Устанавливаем состояние ожидания ввода БЖУ
    bot.set_state(user_id, BotStates.waiting_for_product_pfc, chat_id)

# Обработчик ввода БЖУ
@bot.message_handler(state=BotStates.waiting_for_product_pfc)
def process_product_pfc(message):
    """Обработчик ввода БЖУ продукта"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    pfc_text = message.text.strip()
    
    # Разбираем введенные значения
    try:
        values = pfc_text.split()
        if len(values) != 3:
            raise ValueError()
        
        proteins = float(values[0].replace(',', '.'))
        fats = float(values[1].replace(',', '.'))
        carbs = float(values[2].replace(',', '.'))
    except:
        bot.send_message(
            chat_id,
            "Пожалуйста, введите три числа через пробел (например: 10 5 20). Попробуйте еще раз:"
        )
        return
    
    # Сохраняем БЖУ
    user_data[user_id]['proteins'] = proteins
    user_data[user_id]['fats'] = fats
    user_data[user_id]['carbs'] = carbs
    
    # Очистка штрихкода от лишних символов
    barcode = user_data[user_id]['barcode']
    # Удаляем все нецифровые символы
    clean_barcode = ''.join(filter(str.isdigit, barcode))

    # Собираем все данные с очищенным штрихкодом
    product_data = {
        'name': user_data[user_id]['name'],
        'calories': user_data[user_id]['calories'],
        'proteins': proteins,
        'fats': fats,
        'carbs': carbs,
        'barcode': clean_barcode,  # Используем очищенный штрихкод
        'portion_weight': 100,
        'estimated': False,
        'is_barcode': True
    }
    
    try:
        # Форматируем результат
        result_text = format_nutrition_result(product_data, user_id)
        
        # Добавляем информацию о штрихкоде
        result_text = f"🔍 Штрихкод: *{product_data['barcode']}*\n" + result_text
        
        # Проверяем подписку
        is_subscribed = DatabaseManager.check_subscription_status(user_id)
        remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
        
        if not is_subscribed:
            result_text += f"\n\n{get_subscription_info(remaining_requests, is_subscribed)}"
        
        # Создаем клавиатуру
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("Указать вес порции", callback_data="specify_portion"))
        
        if not is_subscribed:
            markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
        
        # Отправляем результат
        bot.send_message(
            chat_id,
            result_text,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
        # Сохраняем продукт в локальную базу данных штрихкодов
        barcode_scanner = BarcodeScanner()
        barcode_scanner._save_to_local_database(product_data['barcode'], product_data)
        
        # Сохраняем в базу данных пользователя
        analysis_time = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        DatabaseManager.save_food_analysis(
            user_id,
            product_data['name'],
            product_data['calories'],
            product_data['proteins'],
            product_data['fats'],
            product_data['carbs'],
            None,  # Не сохраняем фото для штрихкодов
            product_data.get('portion_weight', 100),
            analysis_time
        )
    except Exception as e:
        logger.error(f"Ошибка при обработке данных продукта: {str(e)}")
        bot.send_message(chat_id, "Произошла ошибка при обработке данных. Пожалуйста, попробуйте позже.")
    finally:
        # Очищаем состояние и данные пользователя
        bot.delete_state(user_id, chat_id)
        if user_id in user_data:
            del user_data[user_id]

# Обработчик текстовых сообщений в режиме уточнения блюда
@bot.message_handler(state=BotStates.waiting_for_food_name)
def handle_food_name(message):
    """Обработчик ввода названия блюда пользователем"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    food_name = message.text.strip()
    
    # Сбрасываем состояние
    bot.delete_state(user_id, chat_id)
    
    if food_name.lower() in ['/cancel', 'отмена']:
        bot.send_message(chat_id, "Уточнение отменено.")
        return
    
    # Получаем данные пользователя
    user_info = user_data.get(user_id)
    if not user_info:
        bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, отправьте фото снова.")
        return
    
    # Отправляем сообщение о начале обработки
    processing_message = bot.send_message(chat_id, "🔍 Уточняю информацию о блюде... Пожалуйста, подождите.")
    
    try:
        # Ищем пищевую ценность по указанному названию
        nutrition_data = NutritionCalculator.lookup_nutrition(food_name)
        
        # Если информация найдена, обновляем данные
        if nutrition_data and not nutrition_data.get('estimated', False):
            # Форматирование результатов
            result_text = format_nutrition_result(nutrition_data, user_id)
            
            # Проверка статуса подписки
            is_subscribed = DatabaseManager.check_subscription_status(user_id)
            remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
            
            if not is_subscribed:
                result_text += f"\n\n{get_subscription_info(remaining_requests, is_subscribed)}"
            
            # Обновляем информацию в базе данных
            try:
                # Получаем последнюю запись пользователя
                session = DatabaseManager.Session()
                try:
                    user = session.query(User).filter_by(telegram_id=user_id).first()
                    if user:
                        food_analysis = session.query(FoodAnalysis).filter_by(
                            user_id=user.id
                        ).order_by(FoodAnalysis.analysis_date.desc()).first()
                        
                        if food_analysis:
                            food_analysis.food_name = food_name
                            food_analysis.calories = nutrition_data['calories']
                            food_analysis.proteins = nutrition_data['proteins']
                            food_analysis.fats = nutrition_data['fats']
                            food_analysis.carbs = nutrition_data['carbs']
                            session.commit()
                finally:
                    session.close()
            except Exception as db_error:
                logger.error(f"Ошибка при обновлении БД: {str(db_error)}")
                # Продолжаем выполнение, так как это некритичная ошибка
            
            # Кнопки
            markup = None
            if not is_subscribed:
                markup = InlineKeyboardMarkup()
                markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
            
            # Отправляем обновленные результаты
            bot.edit_message_text(
                result_text,
                chat_id,
                processing_message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            # Если информация не найдена, сообщаем пользователю
            bot.edit_message_text(
                f"К сожалению, не удалось найти точную информацию о блюде '{food_name}'. "
                "Попробуйте указать более распространенное название или отправьте новое фото.",
                chat_id,
                processing_message.message_id
            )
    
    except Exception as e:
        logger.error(f"Ошибка при уточнении блюда: {str(e)}")
        bot.edit_message_text(
            "❌ Произошла ошибка при уточнении блюда. Пожалуйста, попробуйте еще раз позже.",
            chat_id,
            processing_message.message_id
        )
    
    # Удаляем данные пользователя
    if user_id in user_data:
        del user_data[user_id]

# Обработчик для ввода размера порции
@bot.message_handler(state=BotStates.waiting_for_portion_size)
def handle_portion_size(message):
    """Обработчик ввода размера порции пользователем"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    portion_text = message.text.strip()

    logger.info(f"Обработка размера порции. user_id: {user_id}, user_data: {user_data.get(user_id)}")
    
    # Отменяем операцию по команде
    if portion_text.lower() in ['/cancel', 'отмена']:
        bot.delete_state(user_id, chat_id)
        bot.send_message(chat_id, "Уточнение отменено.")
        return
    
    # Проверяем, содержит ли ввод только цифры
    if not portion_text.isdigit():
        bot.send_message(chat_id, "Пожалуйста, введите корректное число для веса порции (только цифры).")
        return  # Сохраняем состояние и ждем нового ввода
    
    # Конвертируем в число и проверяем, что оно положительное
    portion_size = int(portion_text)
    if portion_size <= 0:
        bot.send_message(chat_id, "Пожалуйста, введите положительное число для веса порции.")
        return  # Сохраняем состояние и ждем нового ввода
    
    # Сбрасываем состояние после успешной валидации
    bot.delete_state(user_id, chat_id)
    
    # Отправляем сообщение о начале обработки
    processing_message = bot.send_message(chat_id, "🔍 Пересчитываю КБЖУ для указанного веса порции...")
    
    try:
        # Проверяем, есть ли данные о продукте в user_data
        if user_id in user_data and 'food_data' in user_data[user_id]:
            # Получаем данные о продукте из user_data
            food_data = user_data[user_id]['food_data']

            if user_id in user_data and 'food_data' in user_data[user_id]:
                logger.info(f"Найдены данные food_data: {user_data[user_id]['food_data']}")
            else:
                logger.info(f"Данные food_data не найдены. Пробуем получить из message_id")

            
            # Получаем текущие значения КБЖУ
            old_portion = food_data.get('portion_weight', 100)
            
            # Рассчитываем коэффициент для пересчета
            ratio = portion_size / old_portion
            
            # Пересчитываем и округляем значения
            new_calories = round(food_data['calories'] * ratio, 1)
            new_proteins = round(food_data['proteins'] * ratio, 1)
            new_fats = round(food_data['fats'] * ratio, 1)
            new_carbs = round(food_data['carbs'] * ratio, 1)
            
            # Обновляем данные в user_data
            food_data['calories'] = new_calories
            food_data['proteins'] = new_proteins
            food_data['fats'] = new_fats
            food_data['carbs'] = new_carbs
            food_data['portion_weight'] = portion_size
            
            # Формируем данные для отправки пользователю
            nutrition_data = {
                'name': food_data['name'],
                'calories': new_calories,
                'proteins': new_proteins,
                'fats': new_fats,
                'carbs': new_carbs,
                'portion_weight': portion_size,
                'estimated': food_data.get('estimated', False)
            }
            
            # Проверка статуса подписки
            is_subscribed = DatabaseManager.check_subscription_status(user_id)
            remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
            
            # Форматирование результата
            result_text = format_nutrition_result(nutrition_data, user_id)
            
            if not is_subscribed:
                result_text += f"\n\n{get_subscription_info(remaining_requests, is_subscribed)}"
            
            # Кнопки
            markup = InlineKeyboardMarkup(row_width=1)

            # Создаем уникальный ключ для текущего анализа
            analysis_key = f"{processing_message.message_id}"

            # Добавляем кнопку для добавления в статистику если еще не добавлено
            if not user_data[user_id].get(f'added_to_stats_{analysis_key}', False):
                markup.add(InlineKeyboardButton("➕ Добавить в статистику", callback_data=f"add_stats_{user_id}"))
            else:
                result_text += "\n\n✅ Блюдо добавлено в статистику"

            # Добавляем кнопку для подписки, если пользователь не подписан
            if not is_subscribed:
                markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
            
            # Отправляем обновленные результаты
            bot.edit_message_text(
                result_text,
                chat_id,
                processing_message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
            return
        
        # ЗАПАСНОЙ ВАРИАНТ - Если данных в user_data нет, получаем их из текста сообщения
        # Получаем ID сообщения с результатами из user_data
        message_id = user_data.get(user_id, {}).get('message_id')
        
        if message_id:
            try:
                # Получаем сообщение с результатами
                food_message = bot.get_message(chat_id, message_id)
                message_text = food_message.text
                
                # Извлекаем название блюда
                name_match = re.search(r'🍽️\s*(.+?)(?:\s*\(|$)', message_text)
                food_name = name_match.group(1).strip() if name_match else "Неизвестное блюдо"
                
                # Извлекаем калории
                calories_match = re.search(r'Калории:\s*(\d+\.?\d*)', message_text)
                current_calories = float(calories_match.group(1)) if calories_match else 0
                
                # Извлекаем БЖУ
                pfc_match = re.search(r'Б/Ж/У:\s*(\d+\.?\d*)\s*г\s*\|\s*(\d+\.?\d*)\s*г\s*\|\s*(\d+\.?\d*)', message_text)
                if pfc_match:
                    current_proteins = float(pfc_match.group(1))
                    current_fats = float(pfc_match.group(2))
                    current_carbs = float(pfc_match.group(3))
                else:
                    current_proteins = 0
                    current_fats = 0
                    current_carbs = 0
                
                # Извлекаем текущий вес порции, если есть
                weight_match = re.search(r'\((\d+\.?\d*)\s*г\)', message_text)
                current_portion = float(weight_match.group(1)) if weight_match else 100
                
                # Рассчитываем новые значения
                ratio = portion_size / current_portion
                new_calories = round(current_calories * ratio, 1)
                new_proteins = round(current_proteins * ratio, 1)
                new_fats = round(current_fats * ratio, 1)
                new_carbs = round(current_carbs * ratio, 1)
                
                # Создаем данные о еде и сохраняем в user_data
                food_data = {
                    'name': food_name,
                    'calories': new_calories,
                    'proteins': new_proteins,
                    'fats': new_fats,
                    'carbs': new_carbs,
                    'portion_weight': portion_size,
                    'estimated': False
                }
                
                # Сохраняем данные в user_data
                if user_id not in user_data:
                    user_data[user_id] = {}
                
                user_data[user_id]['food_data'] = food_data
                
                # Проверка статуса подписки
                is_subscribed = DatabaseManager.check_subscription_status(user_id)
                remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
                
                # Форматирование результата
                result_text = format_nutrition_result(food_data, user_id)
                
                if not is_subscribed:
                    result_text += f"\n\n{get_subscription_info(remaining_requests, is_subscribed)}"
                
                # Кнопки
                markup = InlineKeyboardMarkup(row_width=1)
                markup.add(InlineKeyboardButton("➕ Добавить в статистику", callback_data=f"add_stats_{user_id}"))
                
                if not is_subscribed:
                    markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
                
                # Отправляем обновленные результаты
                bot.edit_message_text(
                    result_text,
                    chat_id,
                    processing_message.message_id,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                return
            except Exception as msg_error:
                logger.error(f"Ошибка при извлечении данных из сообщения: {str(msg_error)}")
                logger.error(traceback.format_exc())
        
        # Если ничего не нашли
        bot.edit_message_text(
            "Не удалось найти данные о продукте. Пожалуйста, отправьте фото еды снова.",
            chat_id,
            processing_message.message_id
        )
    
    except Exception as e:
        logger.error(f"Ошибка при пересчете для нового размера порции: {str(e)}")
        logger.error(traceback.format_exc())
        bot.edit_message_text(
            "❌ Произошла ошибка при пересчете КБЖУ. Пожалуйста, попробуйте еще раз позже.",
            chat_id,
            processing_message.message_id
        )

@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    """Обработчик фотографий с кнопкой добавления в статистику"""
    user_id = message.from_user.id
    
    # При каждой новой фотографии сбрасываем данные о текущей еде и флаги "добавлено в статистику"
    if user_id in user_data:
        # Сохраняем только важные данные пользователя, если есть
        temp_data = {}
        for key in user_data[user_id]:
            # Сохраняем все, кроме food_data и added_to_stats_*
            if key != 'food_data' and not key.startswith('added_to_stats_'):
                temp_data[key] = user_data[user_id][key]
        user_data[user_id] = temp_data
    
    # Проверка статуса подписки
    try:
        is_subscribed = DatabaseManager.check_subscription_status(user_id)
        remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
    except Exception as e:
        logger.error(f"Ошибка при проверке подписки: {str(e)}")
        bot.reply_to(message, "Произошла ошибка при проверке вашей подписки. Пожалуйста, попробуйте позже.")
        return

    # Проверка доступности запросов
    if not is_subscribed and remaining_requests <= 0:
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
        
        bot.reply_to(
            message,
            "У вас закончились бесплатные запросы. Для продолжения работы оформите подписку.",
            reply_markup=markup
        )
        return
    
    # Отправка сообщения о начале обработки
    processing_message = bot.reply_to(message, "🔍 Анализирую фотографию... Это может занять до 15 секунд, пожалуйста, подождите.")
    
    photo_path = None
    try:
        # Получение информации о фото
        file_info = bot.get_file(message.photo[-1].file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}"
        
        # Загрузка фото
        photo_path = download_photo(file_url)
        
        if not photo_path:
            bot.edit_message_text(
                "❌ Не удалось загрузить фотографию. Пожалуйста, попробуйте еще раз.",
                message.chat.id,
                processing_message.message_id
            )
            return
        
        # Используем AITunnel для распознавания и расчета КБЖУ
        nutrition_data = aitunnel_adapter.process_image(image_path=photo_path)
        
        if nutrition_data is None:
            # Обработка случая, когда API вернул None
            bot.edit_message_text(
                "❌ Не удалось распознать изображение. Пожалуйста, попробуйте отправить более четкое фото с хорошим освещением.",
                message.chat.id,
                processing_message.message_id
            )
            return

        # Проверка на штрихкод
        if 'is_barcode' in nutrition_data:
            metrics_collector.track_barcode_scan(user_id)
            # Это штрихкод, формируем специальное сообщение
            if nutrition_data.get('estimated', True):
                # Если продукт не найден в базах данных
                result_text = (
                    f"🔍 Штрихкод: *{nutrition_data.get('barcode')}*\n\n"
                    f"Продукт не найден в базе данных. Пожалуйста, введите название продукта и его КБЖУ вручную."
                )
                
                # Создаем клавиатуру для ручного ввода
                markup = InlineKeyboardMarkup(row_width=1)
                markup.add(InlineKeyboardButton("Ввести данные вручную", callback_data="manual_input"))
                
                # Сохраняем штрихкод и ID сообщения для последующего редактирования
                user_data[user_id] = {
                    'barcode': nutrition_data.get('barcode'),
                    'message_id': processing_message.message_id
                }
                
                bot.edit_message_text(
                    result_text,
                    message.chat.id,
                    processing_message.message_id,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                return
            else:
                # Если продукт найден
                result_text = format_nutrition_result(nutrition_data, user_id)
                
                # Добавляем информацию о штрихкоде к результату
                result_text = f"🔍 Штрихкод: *{nutrition_data.get('barcode')}*\n" + result_text
                
                # Создаем клавиатуру
                markup = InlineKeyboardMarkup(row_width=1)
                
                # Сохраняем данные для добавления в статистику без добавления в БД
                food_data = {
                    'name': nutrition_data['name'],
                    'calories': nutrition_data['calories'],
                    'proteins': nutrition_data['proteins'],
                    'fats': nutrition_data['fats'],
                    'carbs': nutrition_data['carbs'],
                    'portion_weight': nutrition_data.get('portion_weight', 100),
                    'photo_path': None  # Для штрихкодов не сохраняем фото
                }
                
                # Сохраняем данные в user_data для использования в callback
                user_data[user_id] = {'food_data': food_data}
                
                # Добавляем кнопку для добавления в статистику первой
                markup.add(InlineKeyboardButton("➕ Добавить в статистику", callback_data=f"add_stats_{user_id}"))
                
                # Добавляем остальные кнопки
                markup.add(InlineKeyboardButton("Указать вес порции", callback_data="specify_portion"))
                
                # Добавляем кнопку для подписки, если пользователь не подписан
                if not is_subscribed:
                    remaining_requests -= 1
                    result_text += f"\n🔄 Осталось запросов: {remaining_requests}\n"
                    markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
                
                # Отправка результатов пользователю
                bot.edit_message_text(
                    result_text,
                    message.chat.id,
                    processing_message.message_id,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                return
        
        # Проверка на случай отсутствия еды на фото
        if not nutrition_data or ('name' in nutrition_data and nutrition_data['name'] == 'Неизвестное блюдо') or ('no_food' in nutrition_data and nutrition_data['no_food']) or ('name' in nutrition_data and nutrition_data['name'] == 'Еда не обнаружена'):
            # Если еда не обнаружена, показываем улучшенное сообщение
            message_text = (
                "🔍 На изображении не обнаружено еды. Пожалуйста, отправьте фотографию, на которой хорошо видно блюдо.\n\n"
                "Для наилучших результатов:\n"
                "• Фотографируйте сверху\n"
                "• Обеспечьте хорошее освещение\n"
                "• Старайтесь, чтобы блюдо занимало большую часть кадра"
            )
            
            bot.edit_message_text(
                message_text,
                message.chat.id,
                processing_message.message_id
            )
            return

        metrics_collector.track_photo_analysis(user_id)
        
        # Форматирование результатов
        result_text = format_nutrition_result(nutrition_data, user_id)

        # Создаем клавиатуру
        markup = InlineKeyboardMarkup(row_width=1)

        # Сохраняем данные для добавления в статистику
        if user_id not in user_data:
            user_data[user_id] = {}

        user_data[user_id]['food_data'] = {
            'name': nutrition_data['name'],
            'calories': nutrition_data['calories'],
            'proteins': nutrition_data['proteins'],
            'fats': nutrition_data['fats'],
            'carbs': nutrition_data['carbs'],
            'portion_weight': nutrition_data.get('portion_weight', 100),
            'photo_path': photo_path,
            'estimated': nutrition_data.get('estimated', False)
        }

        # Добавляем кнопку для добавления в статистику первой
        markup.add(InlineKeyboardButton("➕ Добавить в статистику", callback_data=f"add_stats_{user_id}"))

        if nutrition_data.get('estimated', False):
            # Для неточных результатов предлагаем уточнить
            markup.add(InlineKeyboardButton("Уточнить название блюда", callback_data="specify_food"))

        # Добавляем кнопку для указания веса порции
        markup.add(InlineKeyboardButton("Указать вес порции", callback_data="specify_portion"))

        # Добавляем кнопку для подписки, если пользователь не подписан
        if not is_subscribed:
            remaining_requests -= 1
            result_text += f"\n🔄 Осталось запросов: {remaining_requests}\n"
            markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
        else:
            result_text += "\n✅ Активная подписка\n"

        # Отправка результатов пользователю
        bot.edit_message_text(
            result_text,
            message.chat.id,
            processing_message.message_id,
            parse_mode="Markdown",
            reply_markup=markup
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке фотографии: {str(e)}")
        bot.edit_message_text(
            "❌ Произошла ошибка при анализе фотографии. Пожалуйста, попробуйте еще раз позже.",
            message.chat.id,
            processing_message.message_id
        )
    finally:
        # Мы не удаляем временный файл, так как он может понадобиться для сохранения в БД
        pass

# Обработчик кнопки добавления в статистику
@bot.callback_query_handler(func=lambda call: call.data.startswith("add_stats_"))
def add_stats_callback(call):
    """Обработчик кнопки добавления в статистику"""
    try:
        user_id = int(call.data.split("_")[2])  # Извлекаем user_id из callback_data
        chat_id = call.message.chat.id
        message_id = call.message.message_id
        
        # Проверяем, есть ли данные для сохранения
        if user_id not in user_data or 'food_data' not in user_data[user_id]:
            bot.answer_callback_query(call.id, "Ошибка: данные не найдены. Попробуйте снова отправить фото.")
            return
        
        # Создаем уникальный ключ для текущего анализа - сочетание message_id и user_id
        analysis_key = f"{message_id}"
        
        # Если этот конкретный анализ уже был добавлен в статистику, сообщаем об этом
        if user_data[user_id].get(f'added_to_stats_{analysis_key}', False):
            bot.answer_callback_query(call.id, "Этот анализ уже добавлен в статистику!")
            return
            
        # Получаем данные блюда
        food_data = user_data[user_id]['food_data']
        
        # Сохраняем в базу данных
        analysis_time = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        analysis_id = DatabaseManager.save_food_analysis(
            user_id,
            food_data['name'],
            food_data['calories'],
            food_data['proteins'],
            food_data['fats'],
            food_data['carbs'],
            food_data.get('photo_path'),
            food_data.get('portion_weight', 100),
            analysis_time
        )
        
        # Сохраняем ID анализа и помечаем как добавленный в статистику ТОЛЬКО для текущего анализа
        if analysis_id:
            user_data[user_id]['analysis_id'] = analysis_id
            user_data[user_id][f'added_to_stats_{analysis_key}'] = True
            
            # Отвечаем пользователю
            bot.answer_callback_query(call.id, "✅ Блюдо успешно добавлено в статистику!")
            
            # Обновляем сообщение, убирая кнопку "Добавить в статистику"
            markup = InlineKeyboardMarkup(row_width=1)
            
            # Получаем оригинальную разметку
            original_markup = call.message.reply_markup.to_dict() if call.message.reply_markup else {"inline_keyboard": []}
            
            # Оставляем все кнопки, кроме "Добавить в статистику"
            for row in original_markup.get("inline_keyboard", []):
                for button in row:
                    if "Добавить в статистику" not in button.get("text", ""):
                        markup.add(types.InlineKeyboardButton(
                            text=button.get("text", ""),
                            callback_data=button.get("callback_data", "")
                        ))
            
            # Получаем оригинальный текст и добавляем сообщение о добавлении в статистику
            original_text = call.message.text
            
            if "Блюдо добавлено в статистику" not in original_text:
                updated_text = original_text + "\n\n✅ Блюдо добавлено в статистику"
            else:
                updated_text = original_text
            
            # Обновляем сообщение
            bot.edit_message_text(
                updated_text,
                chat_id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup if len(markup.keyboard) > 0 else None
            )
        else:
            bot.answer_callback_query(call.id, "Ошибка при добавлении в статистику.")
        
    except Exception as e:
        logger.error(f"Ошибка при добавлении в статистику: {str(e)}")
        logger.error(traceback.format_exc())
        bot.answer_callback_query(call.id, "Произошла ошибка при добавлении в статистику.")

# Обработчик текстовых сообщений
@bot.message_handler(func=lambda message: True)
def text_handler(message):
    """Обработчик текстовых сообщений"""
    help_text = (
        "Пожалуйста, отправьте фотографию еды для анализа или воспользуйтесь командами:\n"
        "/start - Начать использование бота\n"
        "/help - Показать справку\n"
        "/subscription - Управление подпиской\n"
        "/stats - Ваша статистика использования\n"
        "/setup - Настройка профиля и норм КБЖУ"
    )
    
    bot.reply_to(message, help_text)

# Обработчик предварительной проверки платежа
@bot.pre_checkout_query_handler(func=lambda query: True)
def process_pre_checkout_query(pre_checkout_query):
    """
    Обрабатывает предварительную проверку платежа
    Telegram отправляет это событие после того, как пользователь 
    нажал кнопку оплаты, но до фактического проведения платежа
    """
    try:
        # На этом этапе можно проверить наличие товара, валидность данных и т.д.
        # Если все в порядке, просто отвечаем ok=True
        bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)
        logger.info(f"Pre-checkout прошел успешно: {pre_checkout_query.id}")
    except Exception as e:
        logger.error(f"Ошибка при обработке pre_checkout_query: {str(e)}")
        bot.answer_pre_checkout_query(
            pre_checkout_query.id, 
            ok=False, 
            error_message="Произошла ошибка при обработке платежа. Пожалуйста, попробуйте позже."
        )

# Обработчик успешного платежа
@bot.message_handler(content_types=['successful_payment'])
def process_successful_payment(message):
    """
    Обрабатывает успешные платежи
    Telegram отправляет это событие после успешного завершения платежа
    """
    try:
        # Получаем информацию о платеже
        payment_info = message.successful_payment
        user_id = message.from_user.id
        
        # Извлекаем данные из payload (наш формат: subscription_[user_id]_[months]_[timestamp])
        payload_parts = payment_info.invoice_payload.split('_')
        months = int(payload_parts[2]) if len(payload_parts) > 2 else 1
        
        # Получаем ID транзакции в ЮKassa
        transaction_id = payment_info.provider_payment_charge_id
        
        # Добавляем подписку в базу данных
        result = DatabaseManager.add_subscription(user_id, months, transaction_id)
        
        if result:
            # Отслеживаем метрику покупки подписки
            metrics_collector.track_subscription_purchase()
            metrics_collector.save_metrics()  # Явно сохраняем метрики после покупки
            
            # Отправляем сообщение об успешной оплате
            success_text = (
                f"✅ *Оплата успешно выполнена!*\n\n"
                f"Ваша подписка активирована на {months} мес.\n"
                f"Теперь вам доступно неограниченное количество запросов."
            )
            
            bot.send_message(
                message.chat.id,
                success_text,
                parse_mode="Markdown"
            )
            logger.info(f"Подписка успешно активирована для пользователя {user_id} на {months} мес.")
        else:
            bot.send_message(
                message.chat.id,
                "Возникла ошибка при активации подписки. Пожалуйста, обратитесь в поддержку."
            )
            logger.error(f"Ошибка при активации подписки для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка при обработке успешного платежа: {str(e)}")
        bot.send_message(
            message.chat.id,
            "Возникла ошибка при обработке платежа. Пожалуйста, обратитесь в поддержку."
        )

# Регистрируем фильтр для работы с состояниями
bot.add_custom_filter(custom_filters.StateFilter(bot))

# Функция для запуска бота в режиме поллинга (для разработки)
def run_polling():
    """Запуск бота в режиме поллинга"""
    logger.info("Запуск бота в режиме поллинга...")
    bot.remove_webhook()
    bot.infinity_polling()

# Точка входа
if __name__ == "__main__":
    run_polling()      