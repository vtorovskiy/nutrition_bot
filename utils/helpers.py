import os
import tempfile
import requests
from datetime import datetime
import sys
from database.db_manager import DatabaseManager

# Добавляем корневую директорию проекта в путь для импорта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def download_photo(file_path):
    """
    Загрузка фотографии по URL
    
    Args:
        file_path (str): URL фотографии
        
    Returns:
        str: Путь к временному файлу с фотографией
    """
    try:
        response = requests.get(file_path, stream=True)
        if response.status_code == 200:
            # Создаем временный файл
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.jpg')
            for chunk in response.iter_content(1024):
                temp_file.write(chunk)
            temp_file.close()
            return temp_file.name
        else:
            print(f"Ошибка при загрузке фотографии: {response.status_code}")
            return None
    except Exception as e:
        print(f"Ошибка при загрузке фотографии: {str(e)}")
        return None

def format_nutrition_result(nutrition_data, user_id=None):
    """
    Улучшенное форматирование результатов анализа пищевой ценности с прогресс-барами
    
    Args:
        nutrition_data (dict): Данные о пищевой ценности
        user_id (int, optional): Telegram ID пользователя для получения дневных норм
        
    Returns:
        str: Отформатированный текст с результатами
    """
    if not nutrition_data:
        return "Не удалось определить пищевую ценность продукта."
    
    # Текст о точности определения
    if nutrition_data.get('estimated', False):
        accuracy_text = "⚠️ _Приблизительная оценка_"
    else:
        accuracy_text = "✅ _Точное определение_"
    
    # Основной текст с результатами
    text = f"🍽 *{nutrition_data['name']}*\n{accuracy_text}\n\n"
    
    # Добавляем информацию о весе порции
    portion_text = ""
    if 'portion_weight' in nutrition_data and nutrition_data['portion_weight'] > 0:
        portion_text = f"⚖️ Вес порции: *{nutrition_data['portion_weight']}* г\n"
    text += portion_text
    
    # Получаем дневные нормы пользователя, если указан ID
    daily_norms = None
    if user_id:
        daily_norms = DatabaseManager.get_user_daily_norms(user_id)
    
    # Если есть дневные нормы, добавляем прогресс-бары и индикаторы
    if daily_norms:
        indicators = get_nutrition_indicators(nutrition_data, daily_norms)
        
        if indicators and 'calories' in indicators:
            text += f"{indicators['calories']['indicator']} Калории: *{nutrition_data['calories']}* ккал\n"
            text += f"   {indicators['calories']['bar']} от дневной нормы\n"
        else:
            text += f"🔥 Калории: *{nutrition_data['calories']}* ккал\n"
        
        if indicators and 'proteins' in indicators:
            text += f"{indicators['proteins']['indicator']} Белки: *{nutrition_data['proteins']}* г\n"
            text += f"   {indicators['proteins']['bar']} от дневной нормы\n"
        else:
            text += f"🥩 Белки: *{nutrition_data['proteins']}* г\n"
        
        if indicators and 'fats' in indicators:
            text += f"{indicators['fats']['indicator']} Жиры: *{nutrition_data['fats']}* г\n"
            text += f"   {indicators['fats']['bar']} от дневной нормы\n"
        else:
            text += f"🧈 Жиры: *{nutrition_data['fats']}* г\n"
        
        if indicators and 'carbs' in indicators:
            text += f"{indicators['carbs']['indicator']} Углеводы: *{nutrition_data['carbs']}* г\n"
            text += f"   {indicators['carbs']['bar']} от дневной нормы\n"
        else:
            text += f"🍞 Углеводы: *{nutrition_data['carbs']}* г\n"
    else:
        # Если нет дневных норм, отображаем обычные значения
        text += f"🔥 Калории: *{nutrition_data['calories']}* ккал\n"
        text += f"🥩 Белки: *{nutrition_data['proteins']}* г\n"
        text += f"🧈 Жиры: *{nutrition_data['fats']}* г\n"
        text += f"🍞 Углеводы: *{nutrition_data['carbs']}* г\n"
    
    # Дополнительная информация о пользе/вреде (опционально)
    nutritional_insights = []
    
    if nutrition_data['calories'] < 200:
        nutritional_insights.append("🟢 *Низкокалорийное блюдо* - подходит для снижения веса")
    elif nutrition_data['calories'] > 600:
        nutritional_insights.append("🟠 *Высококалорийное блюдо* - употребляйте с осторожностью при диете")
    
    if nutrition_data['proteins'] > 25:
        nutritional_insights.append("💪 *Высокое содержание белка* - хороший выбор для роста мышц")
    
    if nutrition_data['fats'] > 30:
        nutritional_insights.append("⚠️ *Высокое содержание жиров* - следите за дневной нормой")
    
    if nutrition_data['carbs'] > 60:
        nutritional_insights.append("🍚 *Высокое содержание углеводов* - хороший источник энергии")
    
    # Добавляем инсайты, если они есть
    if nutritional_insights:
        text += "\n" + "\n".join(nutritional_insights)
    
    # Если нет дневных норм, добавляем приглашение настроить профиль
    if not daily_norms and user_id:
        text += "\n\n💡 _Используйте команду /setup для настройки персональных норм КБЖУ и отслеживания прогресса._"
    
    return text

def get_subscription_info(remaining_requests, is_subscribed):
    """
    Формирование информации о подписке
    
    Args:
        remaining_requests (int): Количество оставшихся бесплатных запросов
        is_subscribed (bool): Статус подписки
        
    Returns:
        str: Информация о подписке
    """
    if is_subscribed:
        return "✅ У вас активная подписка. Вы можете делать неограниченное количество запросов."
    else:
        if remaining_requests > 0:
            return f"ℹ️ У вас осталось {remaining_requests} бесплатных запросов. Для неограниченного доступа оформите подписку."
        else:
            return "❗ У вас закончились бесплатные запросы. Для продолжения работы оформите подписку."

def format_datetime(dt):
    """
    Форматирование даты и времени
    
    Args:
        dt (datetime): Дата и время
        
    Returns:
        str: Отформатированная дата и время
    """
    if not dt:
        return "Не указано"
    
    return dt.strftime("%d.%m.%Y %H:%M")

def get_remaining_subscription_days(end_date):
    """
    Расчет оставшихся дней подписки
    
    Args:
        end_date (datetime): Дата окончания подписки
        
    Returns:
        int: Количество оставшихся дней
    """
    if not end_date:
        return 0
    
    delta = end_date - datetime.utcnow()
    return max(0, delta.days)

def generate_progress_bar(current, target, length=10):
    """
    Генерирует текстовый прогресс-бар
    
    Args:
        current (float): Текущее значение
        target (float): Целевое значение (100%)
        length (int): Длина прогресс-бара в символах
        
    Returns:
        str: Текстовый прогресс-бар
    """
    if target <= 0:
        percent = 0
    else:
        percent = min(1.0, current / target)
    
    filled_length = int(length * percent)
    empty_length = length - filled_length
    
    bar = '■' * filled_length + '□' * empty_length
    percent_text = f"{int(percent * 100)}%"
    
    return f"{bar} {percent_text}"

def get_indicator_emoji(percent):
    """
    Возвращает эмодзи-индикатор в зависимости от процента
    
    Args:
        percent (float): Процент от нормы (0-1)
        
    Returns:
        str: Эмодзи-индикатор
    """
    if percent < 0.25:
        return "🔴"  # Очень мало
    elif percent < 0.5:
        return "🟠"  # Мало
    elif percent < 0.75:
        return "🟡"  # Средне
    elif percent < 1.0:
        return "🟢"  # Хорошо
    else:
        return "🔵"  # Достигнуто или превышено

def get_nutrition_indicators(nutrition_values, daily_norms):
    """
    Генерирует индикаторы для основных нутриентов
    
    Args:
        nutrition_values (dict): Текущие значения КБЖУ
        daily_norms (dict): Дневные нормы КБЖУ
        
    Returns:
        dict: Индикаторы для каждого нутриента
    """
    if not daily_norms:
        return None
    
    result = {}
    
    # Калории
    if daily_norms.get('daily_calories'):
        calories_percent = nutrition_values.get('calories', 0) / daily_norms['daily_calories']
        result['calories'] = {
            'percent': calories_percent,
            'bar': generate_progress_bar(nutrition_values.get('calories', 0), daily_norms['daily_calories']),
            'indicator': get_indicator_emoji(calories_percent)
        }
    
    # Белки
    if daily_norms.get('daily_proteins'):
        proteins_percent = nutrition_values.get('proteins', 0) / daily_norms['daily_proteins']
        result['proteins'] = {
            'percent': proteins_percent,
            'bar': generate_progress_bar(nutrition_values.get('proteins', 0), daily_norms['daily_proteins']),
            'indicator': get_indicator_emoji(proteins_percent)
        }
    
    # Жиры
    if daily_norms.get('daily_fats'):
        fats_percent = nutrition_values.get('fats', 0) / daily_norms['daily_fats']
        result['fats'] = {
            'percent': fats_percent,
            'bar': generate_progress_bar(nutrition_values.get('fats', 0), daily_norms['daily_fats']),
            'indicator': get_indicator_emoji(fats_percent)
        }
    
    # Углеводы
    if daily_norms.get('daily_carbs'):
        carbs_percent = nutrition_values.get('carbs', 0) / daily_norms['daily_carbs']
        result['carbs'] = {
            'percent': carbs_percent,
            'bar': generate_progress_bar(nutrition_values.get('carbs', 0), daily_norms['daily_carbs']),
            'indicator': get_indicator_emoji(carbs_percent)
        }
    
    return result