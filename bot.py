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
from telebot.handler_backends import State, StatesGroup
from utils.helpers import get_nutrition_indicators
from database.models import User, FoodAnalysis, UserSubscription
from food_recognition.barcode_scanner import BarcodeScanner

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
        f"Я твой помощник для анализа пищевой ценности блюд по фотографии или штрихкоду. "
        f"Просто отправь мне фото еды или штрихкод продукта, и я рассчитаю её КБЖУ "
        f"(калории, белки, жиры, углеводы).\n\n"
        f"🔍 *Доступные команды:*\n"
        f"/help - Показать справку\n"
        f"/subscription - Управление подпиской\n"
        f"/stats - Ваша статистика использования\n"
        f"/setup - Настройка профиля и норм КБЖУ\n\n"
    )
    
    # Добавляем информацию о подписке
    is_subscribed = DatabaseManager.check_subscription_status(user_id)
    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
    subscription_info = get_subscription_info(remaining_requests, is_subscribed)
    
    welcome_text += subscription_info
    
    # Кнопки
    markup = InlineKeyboardMarkup(row_width=1)
    if not is_subscribed:
        markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
    
    # Добавляем кнопку для расчета КБЖУ
    markup.add(InlineKeyboardButton("Рассчитать норму КБЖУ", callback_data="setup_profile"))
    
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown", reply_markup=markup)

@bot.message_handler(commands=['setup'])
def setup_command(message):
    """Обработчик команды /setup для настройки профиля пользователя"""
    user_id = message.from_user.id
    
    # Получаем текущий профиль пользователя
    user_profile = DatabaseManager.get_user_profile(user_id)
    
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
        
        bot.send_message(message.chat.id, setup_text, parse_mode="Markdown", reply_markup=markup)

# Добавьте обработчик для кнопок настройки профиля
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
    bot.delete_message(chat_id, message.message_id-1)
    
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
    bot.delete_message(chat_id, message.message_id-1)
    
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
    except ValueError:
        bot.send_message(chat_id, "Пожалуйста, введите корректный рост (число от 100 до 250):")
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

# Обработчик команды /help
@bot.message_handler(commands=['help'])
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
        "/status - Ваш профиль\n"
        "/stats - Ваша статистика использования\n\n"
        "💳 *Подписка:*\n"
        f"- Бесплатно: {FREE_REQUESTS_LIMIT} анализов\n"
        f"- Подписка: {SUBSCRIPTION_COST} руб/месяц - неограниченное количество анализов\n\n"
        "❓ *Вопросы и поддержка:*\n"
        "Если у вас возникли вопросы или проблемы, свяжитесь с нашей службой поддержки"
    )
    
    bot.send_message(message.chat.id, help_text, parse_mode="Markdown")

# Обработчик команды /subscription
@bot.message_handler(commands=['subscription'])
def subscription_command(message):
    """Обработчик команды /subscription"""
    user_id = message.from_user.id
    
    # Проверка статуса подписки
    is_subscribed = DatabaseManager.check_subscription_status(user_id)
    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
    
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
    
    bot.send_message(message.chat.id, subscription_text, parse_mode="Markdown", reply_markup=markup)

# Обработчик команды /stats
@bot.message_handler(commands=['stats'])
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
    daily_stats = DatabaseManager.get_nutrition_stats_for_date(user_id, selected_date)

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
    bot.delete_message(chat_id, call.message.message_id)
    
    # Показываем статистику за выбранную дату
    show_stats_for_date(chat_id, user_id, user_stats_dates[user_id])

# Добавить обработчик для кнопки ручного ввода
@bot.callback_query_handler(func=lambda call: call.data == "manual_input")
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

# Обновляем обработчик callback-запросов
@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    """Обработчик callback-запросов"""
    user_id = call.from_user.id
    
    if call.data == "subscribe":
        # Выбор периода подписки
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("1 месяц", callback_data="subscribe_1"))
        markup.add(InlineKeyboardButton("3 месяца (-10%)", callback_data="subscribe_3"))
        markup.add(InlineKeyboardButton("6 месяцев (-15%)", callback_data="subscribe_6"))
        markup.add(InlineKeyboardButton("12 месяцев (-20%)", callback_data="subscribe_12"))
        
        bot.edit_message_text(
            "Выберите период подписки:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=markup
        )
    
    elif call.data.startswith("subscribe_"):
        # Создание платежа
        months = int(call.data.split("_")[1])
        
        # Расчет скидки
        discount = 0
        if months == 3:
            discount = 0.1  # 10%
        elif months == 6:
            discount = 0.15  # 15%
        elif months == 12:
            discount = 0.2  # 20%
        
        # Создание платежа с учетом скидки
        amount = SUBSCRIPTION_COST * months * (1 - discount)
        description = f"Подписка на бота для анализа КБЖУ на {months} мес."
        
        # Создание платежа в ЮKassa
        payment_data = YuKassaPayment.create_payment(user_id, months, description)
        
        if payment_data and payment_data.get('confirmation_url'):
            # Формирование сообщения
            payment_text = (
                f"💳 *Оплата подписки*\n\n"
                f"Период: {months} мес.\n"
                f"Стоимость: {payment_data['amount']} {payment_data['currency']}\n\n"
                "Для оплаты перейдите по ссылке ниже:"
            )
            
            # Кнопка для оплаты
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("Оплатить", url=payment_data['confirmation_url']))
            
            bot.edit_message_text(
                payment_text,
                call.message.chat.id,
                call.message.message_id,
                parse_mode="Markdown",
                reply_markup=markup
            )
        else:
            bot.answer_callback_query(call.id, "Ошибка при создании платежа. Попробуйте позже.", show_alert=True)
    
    elif call.data == "specify_food":
        # Переход в режим ожидания названия блюда
        # Сохраняем ID сообщения для обновления
        user_data[user_id] = {
            'message_id': call.message.message_id,
            'last_photo_id': None  # Здесь будет ID последней фотографии
        }
        
        # Устанавливаем состояние ожидания названия блюда
        bot.set_state(user_id, BotStates.waiting_for_food_name, call.message.chat.id)
        
        # Запрашиваем уточнение
        bot.edit_message_text(
            "Пожалуйста, введите точное название блюда для более точного расчета КБЖУ:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
        )

    elif call.data == "specify_portion":
        # Сохраняем ID сообщения для обновления
        user_data[user_id] = {
            'message_id': call.message.message_id,
            'last_photo_id': None
        }
        
        # Устанавливаем состояние ожидания ввода размера порции
        bot.set_state(user_id, BotStates.waiting_for_portion_size, call.message.chat.id)
        
        # Запрашиваем уточнение
        bot.edit_message_text(
            "Пожалуйста, введите примерный вес порции в граммах (только число):",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=None
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

# Добавьте новый обработчик для ввода размера порции
@bot.message_handler(state=BotStates.waiting_for_portion_size)
def handle_portion_size(message):
    """Обработчик ввода размера порции пользователем с улучшенной обработкой ошибок"""
    user_id = message.from_user.id
    chat_id = message.chat.id
    portion_text = message.text.strip()
    
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
    
    # Получаем данные пользователя
    user_info = user_data.get(user_id)
    if not user_info:
        bot.send_message(chat_id, "Произошла ошибка. Пожалуйста, отправьте фото снова.")
        return
    
    # Отправляем сообщение о начале обработки
    processing_message = bot.send_message(chat_id, "🔍 Пересчитываю КБЖУ для указанного веса порции...")
    
    try:
        # Получаем последнюю запись пользователя
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            if user:
                food_analysis = session.query(FoodAnalysis).filter_by(
                    user_id=user.id
                ).order_by(FoodAnalysis.analysis_date.desc()).first()
                
                if food_analysis:
                    # Получаем текущие значения КБЖУ
                    old_portion = food_analysis.portion_weight or 100
                    
                    # Рассчитываем коэффициент для пересчета
                    ratio = portion_size / old_portion
                    
                    # Округляем значения до 1 десятичного знака
                    new_calories = round(food_analysis.calories * ratio, 1)
                    new_proteins = round(food_analysis.proteins * ratio, 1)
                    new_fats = round(food_analysis.fats * ratio, 1)
                    new_carbs = round(food_analysis.carbs * ratio, 1)
                    
                    # Обновляем значения в базе данных
                    food_analysis.portion_weight = portion_size
                    food_analysis.calories = new_calories
                    food_analysis.proteins = new_proteins
                    food_analysis.fats = new_fats
                    food_analysis.carbs = new_carbs
                    session.commit()
                    
                    # Формируем новые данные для отправки пользователю
                    nutrition_data = {
                        'name': food_analysis.food_name,
                        'calories': new_calories,
                        'proteins': new_proteins,
                        'fats': new_fats,
                        'carbs': new_carbs,
                        'estimated': False,
                        'portion_weight': portion_size
                    }
                    
                    # Проверка статуса подписки
                    is_subscribed = DatabaseManager.check_subscription_status(user_id)
                    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
                    
                    # Форматирование результата
                    result_text = format_nutrition_result(nutrition_data, user_id)
                    
                    if not is_subscribed:
                        result_text += f"\n\n{get_subscription_info(remaining_requests, is_subscribed)}"
                    
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
                    bot.edit_message_text(
                        "Не найдены данные о последнем анализе. Пожалуйста, отправьте фото еды снова.",
                        chat_id,
                        processing_message.message_id
                    )
        finally:
            session.close()
    
    except Exception as e:
        logger.error(f"Ошибка при пересчете для нового размера порции: {str(e)}")
        bot.edit_message_text(
            "❌ Произошла ошибка при пересчете КБЖУ. Пожалуйста, попробуйте еще раз позже.",
            chat_id,
            processing_message.message_id
        )
    
    # Удаляем данные пользователя
    if user_id in user_data:
        del user_data[user_id]

# Обработчик фотографий
@bot.message_handler(content_types=['photo'])
def photo_handler(message):
    """Обработчик фотографий с использованием AITunnel для точного определения продуктов"""
    user_id = message.from_user.id
    
    # Проверка статуса подписки
    is_subscribed = DatabaseManager.check_subscription_status(user_id)
    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)

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

        # Проверка на штрихкод
        if 'is_barcode' in nutrition_data:
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
                
                # Создаем клавиатуру для уточнения веса
                markup = InlineKeyboardMarkup(row_width=1)
                markup.add(InlineKeyboardButton("Указать вес порции", callback_data="specify_portion"))
                
                # Добавляем кнопку для подписки, если пользователь не подписан
                if not is_subscribed:
                    remaining_requests -= 1
                    result_text += f"\n🔄 Осталось запросов: {remaining_requests}\n"
                    markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
                
                # Сохранение результатов анализа
                analysis_time = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
                DatabaseManager.save_food_analysis(
                    user_id,
                    nutrition_data['name'],
                    nutrition_data['calories'],
                    nutrition_data['proteins'],
                    nutrition_data['fats'],
                    nutrition_data['carbs'],
                    None,  # Не сохраняем фото для штрихкодов
                    nutrition_data.get('portion_weight', 100),
                    analysis_time
                )
                
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
            # Удаление временного файла
            if os.path.exists(photo_path):
                os.remove(photo_path)
            return
        
        # Форматирование результатов (ингредиенты уже включены в результат)
        result_text = format_nutrition_result(nutrition_data, user_id)
        
        # Создаем клавиатуру для уточнения, если результаты неточные
        markup = InlineKeyboardMarkup(row_width=1)
        
        if nutrition_data.get('estimated', False):
            # Для неточных результатов предлагаем уточнить
            markup.add(InlineKeyboardButton("Уточнить название блюда", callback_data="specify_food"))
        
        # Добавляем кнопку для указания веса порции
        markup.add(InlineKeyboardButton("Указать вес порции", callback_data="specify_portion"))
        
        # Добавляем кнопку для подписки, если пользователь не подписан
        if not is_subscribed:
            remaining_requests -= 1
            result_text += f"🔄Осталось запросов: {remaining_requests}\n"
            markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
        else:
            result_text += "✅ Активная подписка\n"
        
        # Сохранение результатов анализа
        analysis_time = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)
        DatabaseManager.save_food_analysis(
            user_id,
            nutrition_data['name'],
            nutrition_data['calories'],
            nutrition_data['proteins'],
            nutrition_data['fats'],
            nutrition_data['carbs'],
            photo_path,
            nutrition_data.get('portion_weight', None),
            analysis_time
        )
        
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
        # Удаление временного файла, если он существует
        if 'photo_path' in locals() and photo_path and os.path.exists(photo_path):
            os.remove(photo_path)

# Обработчик текстовых сообщений
@bot.message_handler(func=lambda message: True)
def text_handler(message):
    """Обработчик текстовых сообщений"""
    help_text = (
        "Пожалуйста, отправьте фотографию еды для анализа или воспользуйтесь командами:\n"
        "/start - Начать использование бота\n"
        "/help - Показать справку\n"
        "/subscription - Управление подпиской\n"
        "/stats - Ваша статистика использования"
    )
    
    bot.reply_to(message, help_text)

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