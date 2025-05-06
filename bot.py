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
        f"Я бот для анализа пищевой ценности блюд по фотографии. "
        f"Просто отправь мне фото еды, и я рассчитаю её КБЖУ "
        f"(калории, белки, жиры, углеводы).\n\n"
        f"🔍 *Доступные команды:*\n"
        f"/help - Показать справку\n"
        f"/subscription - Управление подпиской\n"
        f"/stats - Ваша статистика использования\n\n"
    )
    
    # Добавляем информацию о подписке
    is_subscribed = DatabaseManager.check_subscription_status(user_id)
    remaining_requests = DatabaseManager.get_remaining_free_requests(user_id)
    subscription_info = get_subscription_info(remaining_requests, is_subscribed)
    
    welcome_text += subscription_info
    
    # Кнопки
    markup = InlineKeyboardMarkup()
    if not is_subscribed:
        markup.add(InlineKeyboardButton("Оформить подписку", callback_data="subscribe"))
    
    bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown", reply_markup=markup)

#обработчик команды /setup
@bot.message_handler(commands=['setup'])
def setup_command(message):
    """Обработчик команды /setup для настройки профиля пользователя"""
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

# Обработчики для шагов настройки профиля
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
        f"Выбран пол: {'Мужской' if gender == 'male' else 'Женский'}\n\nТеперь введите ваш возраст (полных лет):",
        chat_id,
        call.message.message_id
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
    except ValueError:
        bot.send_message(chat_id, "Пожалуйста, введите корректный возраст (число от 12 до 100):")
        return
    
    # Сохраняем возраст пользователя
    user_data[user_id]['age'] = age
    
    # Запрашиваем вес
    bot.send_message(chat_id, f"Возраст: {age} лет\n\nТеперь введите ваш вес в килограммах:")
    
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
    except ValueError:
        bot.send_message(chat_id, "Пожалуйста, введите корректный вес (число от 30 до 300):")
        return
    
    # Сохраняем вес пользователя
    user_data[user_id]['weight'] = weight
    
    # Запрашиваем рост
    bot.send_message(chat_id, f"Вес: {weight} кг\n\nТеперь введите ваш рост в сантиметрах:")
    
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
    
    # Явно сбрасываем все возможные состояния
    bot.delete_state(user_id, chat_id)
    
    # Получаем все данные пользователя
    user_profile = user_data[user_id]
    
    # Обновляем профиль пользователя и рассчитываем дневные нормы
    norms = DatabaseManager.update_user_profile(
        user_id,
        gender=user_profile['gender'],
        age=user_profile['age'],
        weight=user_profile['weight'],
        height=user_profile['height'],
        activity_level=activity_level
    )
    
    if norms:
        # Отображаем результаты
        result_text = (
            "✅ *Ваш профиль успешно настроен!*\n\n"
            f"• Пол: {'Мужской' if user_profile['gender'] == 'male' else 'Женский'}\n"
            f"• Возраст: {user_profile['age']} лет\n"
            f"• Вес: {user_profile['weight']} кг\n"
            f"• Рост: {user_profile['height']} см\n"
            f"• Уровень активности: {activity_level}\n\n"
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
        "📱 *FoodNutritionBot - Помощь*\n\n"
        "Этот бот поможет вам рассчитать КБЖУ (калории, белки, жиры, углеводы) "
        "блюд по фотографии.\n\n"
        "🔍 *Как использовать:*\n"
        "1. Отправьте фотографию блюда боту\n"
        "2. Дождитесь анализа (обычно занимает несколько секунд)\n"
        "3. Получите детальную информацию о пищевой ценности\n\n"
        "📋 *Команды:*\n"
        "/start - Начать использование бота\n"
        "/help - Показать это сообщение\n"
        "/subscription - Управление подпиской\n"
        "/stats - Ваша статистика использования\n\n"
        "💳 *Подписка:*\n"
        f"- Бесплатно: {FREE_REQUESTS_LIMIT} анализов\n"
        f"- Подписка: {SUBSCRIPTION_COST} руб/месяц - неограниченное количество анализов\n\n"
        "❓ *Вопросы и поддержка:*\n"
        "Если у вас возникли вопросы или проблемы, свяжитесь с @admin_contact_here"
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
        session = DatabaseManager.Session()
        try:
            user = session.query(User).filter_by(telegram_id=user_id).first()
            subscription = session.query(DatabaseManager.UserSubscription).filter_by(
                user_id=user.id, 
                is_active=True
            ).order_by(DatabaseManager.UserSubscription.end_date.desc()).first()
            
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
    Показывает статистику за выбранную дату с прогресс-барами и индикаторами
    
    Args:
        chat_id (int): ID чата для отправки сообщения
        user_id (int): Telegram ID пользователя
        selected_date (datetime.date): Выбранная дата для отображения статистики
    """
    # Получение статистики за выбранную дату
    daily_stats = DatabaseManager.get_nutrition_stats_for_date(user_id, selected_date)
    
    # Получение общей статистики
    overall_stats = DatabaseManager.get_overall_stats(user_id)
    
    # Получение дневных норм пользователя
    daily_norms = DatabaseManager.get_user_daily_norms(user_id)
    
    if not daily_stats and not overall_stats:
        bot.send_message(chat_id, "У вас пока нет статистики использования.", parse_mode="Markdown")
        return
    
    # Форматируем дату для отображения
    date_str = selected_date.strftime("%d.%m.%Y")
    
    # Формирование сообщения о потреблении за выбранную дату
    stats_text = f"📊 *Ваша статистика питания за {date_str}*\n\n"
    
    # Если есть данные за выбранную дату
    if daily_stats and daily_stats["total"]["count"] > 0:
        # Если у пользователя настроены дневные нормы, добавляем общий прогресс
        if daily_norms:
            # Получаем индикаторы для общей статистики за день
            indicators = get_nutrition_indicators(
                {
                    'calories': daily_stats['total']['calories'],
                    'proteins': daily_stats['total']['proteins'],
                    'fats': daily_stats['total']['fats'],
                    'carbs': daily_stats['total']['carbs']
                },
                daily_norms
            )
            
            if indicators:
                stats_text += "*Дневной прогресс:*\n"
                
                if 'calories' in indicators:
                    stats_text += f"{indicators['calories']['indicator']} Калории: {daily_stats['total']['calories']} ккал\n"
                    stats_text += f"   {indicators['calories']['bar']} от нормы\n"
                
                if 'proteins' in indicators:
                    stats_text += f"{indicators['proteins']['indicator']} Белки: {daily_stats['total']['proteins']} г\n"
                    stats_text += f"   {indicators['proteins']['bar']} от нормы\n"
                
                if 'fats' in indicators:
                    stats_text += f"{indicators['fats']['indicator']} Жиры: {daily_stats['total']['fats']} г\n"
                    stats_text += f"   {indicators['fats']['bar']} от нормы\n"
                
                if 'carbs' in indicators:
                    stats_text += f"{indicators['carbs']['indicator']} Углеводы: {daily_stats['total']['carbs']} г\n"
                    stats_text += f"   {indicators['carbs']['bar']} от нормы\n"
                
                stats_text += "\n"
        
        # Добавляем информацию о приемах пищи
        
        # Завтрак
        if daily_stats["breakfast"]["count"] > 0:
            stats_text += "🍳 *Завтрак:*\n"
            stats_text += f"Калории: {daily_stats['breakfast']['calories']} ккал\n"
            stats_text += f"Белки: {daily_stats['breakfast']['proteins']} г\n"
            stats_text += f"Жиры: {daily_stats['breakfast']['fats']} г\n"
            stats_text += f"Углеводы: {daily_stats['breakfast']['carbs']} г\n"
            
            # Добавляем список блюд
            stats_text += "\nБлюда:\n"
            for i, item in enumerate(daily_stats["breakfast"]["items"], 1):
                portion_info = f" ({item['portion_weight']}г)" if item.get('portion_weight') else ""
                stats_text += f"  {i}. {item['name']}{portion_info} - {item['calories']} ккал ({item['time']})\n"
            
            stats_text += "\n"
        
        # Обед
        if daily_stats["lunch"]["count"] > 0:
            stats_text += "🍲 *Обед:*\n"
            stats_text += f"Калории: {daily_stats['lunch']['calories']} ккал\n"
            stats_text += f"Белки: {daily_stats['lunch']['proteins']} г\n"
            stats_text += f"Жиры: {daily_stats['lunch']['fats']} г\n"
            stats_text += f"Углеводы: {daily_stats['lunch']['carbs']} г\n"
            
            # Добавляем список блюд
            stats_text += "\nБлюда:\n"
            for i, item in enumerate(daily_stats["lunch"]["items"], 1):
                portion_info = f" ({item['portion_weight']}г)" if item.get('portion_weight') else ""
                stats_text += f"  {i}. {item['name']}{portion_info} - {item['calories']} ккал ({item['time']})\n"
            
            stats_text += "\n"
        
        # Ужин
        if daily_stats["dinner"]["count"] > 0:
            stats_text += "🍽 *Ужин:*\n"
            stats_text += f"Калории: {daily_stats['dinner']['calories']} ккал\n"
            stats_text += f"Белки: {daily_stats['dinner']['proteins']} г\n"
            stats_text += f"Жиры: {daily_stats['dinner']['fats']} г\n"
            stats_text += f"Углеводы: {daily_stats['dinner']['carbs']} г\n"
            
            # Добавляем список блюд
            stats_text += "\nБлюда:\n"
            for i, item in enumerate(daily_stats["dinner"]["items"], 1):
                portion_info = f" ({item['portion_weight']}г)" if item.get('portion_weight') else ""
                stats_text += f"  {i}. {item['name']}{portion_info} - {item['calories']} ккал ({item['time']})\n"
            
            stats_text += "\n"
        
        # Перекусы
        if daily_stats["snack"]["count"] > 0:
            stats_text += "🍪 *Перекусы:*\n"
            stats_text += f"Калории: {daily_stats['snack']['calories']} ккал\n"
            stats_text += f"Белки: {daily_stats['snack']['proteins']} г\n"
            stats_text += f"Жиры: {daily_stats['snack']['fats']} г\n"
            stats_text += f"Углеводы: {daily_stats['snack']['carbs']} г\n"
            
            # Добавляем список блюд
            stats_text += "\nБлюда:\n"
            for i, item in enumerate(daily_stats["snack"]["items"], 1):
                portion_info = f" ({item['portion_weight']}г)" if item.get('portion_weight') else ""
                stats_text += f"  {i}. {item['name']}{portion_info} - {item['calories']} ккал ({item['time']})\n"
            
            stats_text += "\n"
        
        # Итого за день (если нет дневных норм)
        if not daily_norms:
            stats_text += "📌 *Итого за день:*\n"
            stats_text += f"Калории: {daily_stats['total']['calories']} ккал\n"
            stats_text += f"Белки: {daily_stats['total']['proteins']} г\n"
            stats_text += f"Жиры: {daily_stats['total']['fats']} г\n"
            stats_text += f"Углеводы: {daily_stats['total']['carbs']} г\n\n"
    else:
        stats_text += f"За этот день нет данных о питании.\n\n"
    
    # Добавляем общую статистику
    if overall_stats:
        stats_text += "📈 *Общая статистика:*\n"
        stats_text += f"Всего анализов: {overall_stats['total_analyses']}\n"
        stats_text += f"Общее количество калорий: {overall_stats['total_calories']} ккал\n"
        stats_text += f"Общее количество белков: {overall_stats['total_proteins']} г\n"
        stats_text += f"Общее количество жиров: {overall_stats['total_fats']} г\n"
        stats_text += f"Общее количество углеводов: {overall_stats['total_carbs']} г\n"
    
    # Если нет дневных норм, добавляем приглашение настроить профиль
    if not daily_norms:
        stats_text += "\n💡 _Используйте команду /setup для настройки персональных норм КБЖУ и отслеживания прогресса._"
    
    # Создаем кнопки для навигации по датам
    markup = InlineKeyboardMarkup(row_width=3)
    
    # Кнопка для предыдущей даты
    prev_date = selected_date - timedelta(days=1)
    has_prev_data = DatabaseManager.has_data_for_date(user_id, prev_date) or prev_date >= DatabaseManager.get_earliest_analysis_date(user_id) or prev_date >= (datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).date() - timedelta(days=7)
    
    prev_button = InlineKeyboardButton("⬅️ Пред. день", callback_data=f"stats_prev_{prev_date.strftime('%Y-%m-%d')}")
    
    # Кнопка для сегодня
    today_button = InlineKeyboardButton("Сегодня", callback_data=f"stats_today")
    
    # Кнопка для следующей даты
    next_date = selected_date + timedelta(days=1)
    has_next_data = DatabaseManager.has_data_for_date(user_id, next_date) or next_date <= (datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET)).date()
    
    next_button = InlineKeyboardButton("След. день ➡️", callback_data=f"stats_next_{next_date.strftime('%Y-%m-%d')}")
    
    # Добавляем кнопки в зависимости от доступности данных
    if has_prev_data:
        if has_next_data:
            markup.add(prev_button, today_button, next_button)
        else:
            markup.add(prev_button, today_button)
    elif has_next_data:
        markup.add(today_button, next_button)
    else:
        markup.add(today_button)
    
    bot.send_message(chat_id, stats_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("stats_"))
def stats_navigation_callback(call):
    """Обработчик кнопок навигации по датам в статистике"""
    user_id = call.from_user.id
    chat_id = call.message.chat.id
    
    # Обрабатываем различные типы команд навигации
    if call.data == "stats_today":
        # Показываем статистику за сегодня
        user_stats_dates[user_id] = datetime.utcnow() + timedelta(hours=TIMEZONE_OFFSET).date()
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

    # Добавление информации о подписке
    if not is_subscribed:
        remaining_requests -= 1
        # Добавляем информацию о запросах
        result_text += f"\n⏳ Осталось {remaining_requests} запросов"
        result_text += f"\nℹ️ Для неограниченного доступа оформите подписку."
    else:
        result_text += "\n✅ Активная подписка"
    
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
        
        # Подготавливаем информацию о распознанных ингредиентах
        detected_items = nutrition_data.get('detected_items', [])
        detected_items_text = ""
        
        if detected_items:
            detected_items_text = "\n\n🍽 *Распознанные ингредиенты:*\n"
            for i, item in enumerate(detected_items, 1):
                detected_items_text += f"{i}. {item}\n"
        
        # Форматирование результатов
        result_text = format_nutrition_result(nutrition_data, user_id)
        
        # Добавление информации о распознанных ингредиентах
        result_text += detected_items_text
        
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
            result_text += f"\n\n{get_subscription_info(remaining_requests, is_subscribed)}"
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