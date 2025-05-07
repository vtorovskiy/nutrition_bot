from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from datetime import datetime, timedelta
import sys
import os
from datetime import datetime, time, timedelta
from database.models import User, FoodAnalysis, init_db
from monitoring.decorators import track_api_call
from monitoring.decorators import track_command, track_user_action
from monitoring.metrics import metrics_collector

# Добавляем корневую директорию проекта в путь для импорта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL, FREE_REQUESTS_LIMIT
from database.models import User, UserSubscription, FoodAnalysis, init_db

# Инициализация базы данных
engine = init_db()
session_factory = sessionmaker(bind=engine)
Session = scoped_session(session_factory)

def determine_meal_type(time):
    """
    Определяет тип приема пищи по времени
        
    Args:
        time (datetime): Время приема пищи
            
    Returns:
        str: Тип приема пищи (breakfast, lunch, dinner, snack)
    """
    hour = time.hour
        
    if 5 <= hour < 11:
        return "breakfast"  # Завтрак: 5:00 - 10:59
    elif 11 <= hour < 16:
        return "lunch"      # Обед: 11:00 - 15:59
    elif 16 <= hour < 21:
        return "dinner"     # Ужин: 16:00 - 20:59
    else:
        return "snack"      # Перекус: 21:00 - 4:59


class DatabaseManager:
    """Класс для управления базой данных"""

    @staticmethod
    def get_user_profile(telegram_id):
        """
        Получает профиль пользователя
        
        Args:
            telegram_id (int): Telegram ID пользователя
            
        Returns:
            dict: Данные профиля пользователя
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            return {
                'gender': user.gender,
                'age': user.age,
                'weight': user.weight,
                'height': user.height,
                'activity_level': user.activity_level,
                'daily_calories': user.daily_calories,
                'daily_proteins': user.daily_proteins,
                'daily_fats': user.daily_fats,
                'daily_carbs': user.daily_carbs
            }
        finally:
            session.close()
    
    @staticmethod
    def calculate_daily_norms(gender, age, weight, height, activity_level, goal='maintenance'):
        """
        Рассчитывает рекомендуемые дневные нормы КБЖУ по формуле Миффлина-Сан Жеора с учетом цели
        
        Args:
            gender (str): Пол ('male' или 'female')
            age (int): Возраст
            weight (float): Вес в кг
            height (float): Рост в см
            activity_level (float): Уровень активности (1.2 - 1.9)
            goal (str): Цель ('weight_loss', 'maintenance', 'weight_gain')
                
        Returns:
            dict: Рекомендуемые дневные нормы КБЖУ
        """
        # Расчет базового метаболизма (BMR) по формуле Миффлина-Сан Жеора
        if gender == 'male':
            bmr = 10 * weight + 6.25 * height - 5 * age + 5
        else:  # female
            bmr = 10 * weight + 6.25 * height - 5 * age - 161
        
        # Расчет суточной потребности в калориях с учетом уровня активности
        daily_calories = bmr * activity_level
        
        # Применяем корректировку в зависимости от цели
        if goal == 'weight_loss':
            # Для похудения: дефицит 20%
            daily_calories *= 0.8
        elif goal == 'weight_gain':
            # Для набора массы: профицит 15%
            daily_calories *= 1.15
        # Для поддержания веса оставляем без изменений
        
        # Расчет макронутриентов с учетом цели
        if goal == 'weight_loss':
            # Для похудения: больше белка, меньше жиров и углеводов
            protein_ratio = 0.35  # 35% калорий из белка
            fat_ratio = 0.30      # 30% калорий из жиров
            carb_ratio = 0.35     # 35% калорий из углеводов
        elif goal == 'weight_gain':
            # Для набора массы: больше белка и углеводов
            protein_ratio = 0.30  # 30% калорий из белка
            fat_ratio = 0.25      # 25% калорий из жиров
            carb_ratio = 0.45     # 45% калорий из углеводов
        else:  # maintenance
            # Для поддержания: стандартное соотношение
            protein_ratio = 0.30  # 30% калорий из белка
            fat_ratio = 0.30      # 30% калорий из жиров
            carb_ratio = 0.40     # 40% калорий из углеводов
        
        # Расчет граммов макронутриентов
        daily_proteins = (daily_calories * protein_ratio) / 4  # 4 ккал/г белка
        daily_fats = (daily_calories * fat_ratio) / 9         # 9 ккал/г жира
        daily_carbs = (daily_calories * carb_ratio) / 4       # 4 ккал/г углеводов
        
        # Округляем значения
        daily_calories = round(daily_calories, 1)
        daily_proteins = round(daily_proteins, 1)
        daily_fats = round(daily_fats, 1)
        daily_carbs = round(daily_carbs, 1)
        
        return {
            'daily_calories': daily_calories,
            'daily_proteins': daily_proteins,
            'daily_fats': daily_fats,
            'daily_carbs': daily_carbs
        }

    @staticmethod
    def update_user_profile(telegram_id, gender=None, age=None, weight=None, height=None, 
                            activity_level=None, goal=None,
                            daily_calories=None, daily_proteins=None, daily_fats=None, daily_carbs=None):
        """
        Обновляет профиль пользователя и, при необходимости, пересчитывает дневные нормы
        
        Args:
            telegram_id (int): Telegram ID пользователя
            gender (str, optional): Пол ('male' или 'female')
            age (int, optional): Возраст
            weight (float, optional): Вес в кг
            height (float, optional): Рост в см
            activity_level (float, optional): Уровень активности (1.2 - 1.9)
            goal (str, optional): Цель ('weight_loss', 'maintenance', 'weight_gain')
            daily_calories (float, optional): Дневная норма калорий (если указана вручную)
            daily_proteins (float, optional): Дневная норма белков (если указана вручную)
            daily_fats (float, optional): Дневная норма жиров (если указана вручную)
            daily_carbs (float, optional): Дневная норма углеводов (если указана вручную)
                
        Returns:
            dict: Обновленные дневные нормы КБЖУ
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            # Обновляем данные пользователя
            if gender is not None:
                user.gender = gender
            if age is not None:
                user.age = age
            if weight is not None:
                user.weight = weight
            if height is not None:
                user.height = height
            if activity_level is not None:
                user.activity_level = activity_level
            if goal is not None:
                user.goal = goal
                
            # Если указаны нормы вручную, используем их
            if daily_calories is not None:
                user.daily_calories = daily_calories
            if daily_proteins is not None:
                user.daily_proteins = daily_proteins
            if daily_fats is not None:
                user.daily_fats = daily_fats
            if daily_carbs is not None:
                user.daily_carbs = daily_carbs
            
            # Если указаны все необходимые параметры, но не указаны нормы вручную,
            # пересчитываем нормы автоматически
            if (user.gender and user.age and user.weight and user.height and user.activity_level and
                daily_calories is None and daily_proteins is None and daily_fats is None and daily_carbs is None):
                
                goal_to_use = user.goal or 'maintenance'  # Если цель не указана, считаем для поддержания веса
                
                norms = DatabaseManager.calculate_daily_norms(
                    user.gender, user.age, user.weight, user.height, user.activity_level, goal_to_use
                )
                
                user.daily_calories = norms['daily_calories']
                user.daily_proteins = norms['daily_proteins']
                user.daily_fats = norms['daily_fats']
                user.daily_carbs = norms['daily_carbs']
            
            session.commit()
            
            # Возвращаем текущие нормы пользователя
            return {
                'daily_calories': user.daily_calories,
                'daily_proteins': user.daily_proteins,
                'daily_fats': user.daily_fats,
                'daily_carbs': user.daily_carbs
            }
        finally:
            session.close()

    @staticmethod
    def get_user_daily_norms(telegram_id):
        """
        Получает дневные нормы КБЖУ пользователя
        
        Args:
            telegram_id (int): Telegram ID пользователя
            
        Returns:
            dict: Дневные нормы КБЖУ или None, если они не установлены
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user or not user.daily_calories:
                return None
            
            return {
                'daily_calories': user.daily_calories,
                'daily_proteins': user.daily_proteins,
                'daily_fats': user.daily_fats,
                'daily_carbs': user.daily_carbs,
                'has_full_profile': bool(user.gender and user.age and user.weight and user.height and user.activity_level)
            }
        finally:
            session.close()

    @staticmethod
    def get_or_create_user(telegram_id, username=None, first_name=None, last_name=None):
        """Получить или создать пользователя"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                user = User(
                    telegram_id=telegram_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name
                )
                session.add(user)
                session.commit()
            return user
        finally:
            session.close()
        
    @staticmethod
    def get_daily_nutrition_stats(telegram_id):
        """
        Получить статистику питания за текущий день с разбивкой по приемам пищи
        
        Args:
            telegram_id (int): Telegram ID пользователя
        
        Returns:
            dict: Статистика питания за день
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            # Получаем начало и конец текущего дня (в UTC)
            today = datetime.utcnow().date()
            start_of_day = datetime.combine(today, datetime.min.time())
            end_of_day = datetime.combine(today, datetime.max.time())
            
            # Получаем все анализы за текущий день
            analyses = session.query(FoodAnalysis).filter(
                FoodAnalysis.user_id == user.id,
                FoodAnalysis.analysis_date >= start_of_day,
                FoodAnalysis.analysis_date <= end_of_day
            ).all()
            
            # Инициализируем статистику по приемам пищи
            meal_stats = {
                "breakfast": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "lunch": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "dinner": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "snack": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "total": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0}
            }
            
            # Заполняем статистику
            for analysis in analyses:
                meal_type = analysis.meal_type or "snack"  # По умолчанию считаем перекусом
                
                meal_stats[meal_type]["count"] += 1
                meal_stats[meal_type]["calories"] += analysis.calories or 0
                meal_stats[meal_type]["proteins"] += analysis.proteins or 0
                meal_stats[meal_type]["fats"] += analysis.fats or 0
                meal_stats[meal_type]["carbs"] += analysis.carbs or 0
                meal_stats[meal_type]["items"].append({
                    "name": analysis.food_name,
                    "calories": analysis.calories,
                    "time": analysis.analysis_date
                })
                
                # Обновляем общую статистику
                meal_stats["total"]["count"] += 1
                meal_stats["total"]["calories"] += analysis.calories or 0
                meal_stats["total"]["proteins"] += analysis.proteins or 0
                meal_stats["total"]["fats"] += analysis.fats or 0
                meal_stats["total"]["carbs"] += analysis.carbs or 0
            
            # Округляем значения для лучшей читаемости
            for meal_type in meal_stats:
                meal_stats[meal_type]["calories"] = round(meal_stats[meal_type]["calories"], 1)
                meal_stats[meal_type]["proteins"] = round(meal_stats[meal_type]["proteins"], 1)
                meal_stats[meal_type]["fats"] = round(meal_stats[meal_type]["fats"], 1)
                meal_stats[meal_type]["carbs"] = round(meal_stats[meal_type]["carbs"], 1)
            
            return meal_stats
            
        finally:
            session.close()
        
    @staticmethod
    def get_overall_stats(telegram_id):
        """
        Получить общую статистику использования для пользователя
        
        Args:
            telegram_id (int): Telegram ID пользователя
        
        Returns:
            dict: Общая статистика использования
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            # Получаем все анализы пользователя
            analyses = session.query(FoodAnalysis).filter_by(user_id=user.id).all()
            
            # Рассчитываем общую статистику
            total_calories = sum(a.calories or 0 for a in analyses)
            total_proteins = sum(a.proteins or 0 for a in analyses)
            total_fats = sum(a.fats or 0 for a in analyses)
            total_carbs = sum(a.carbs or 0 for a in analyses)
            
            # Округляем значения
            total_calories = round(total_calories, 1)
            total_proteins = round(total_proteins, 1)
            total_fats = round(total_fats, 1)
            total_carbs = round(total_carbs, 1)
            
            return {
                "total_analyses": len(analyses),
                "total_calories": total_calories,
                "total_proteins": total_proteins,
                "total_fats": total_fats,
                "total_carbs": total_carbs
            }
        finally:
            session.close()

    @staticmethod
    def get_nutrition_stats_for_date(telegram_id, date):
        """
        Получить статистику питания за конкретную дату с разбивкой по приемам пищи
        
        Args:
            telegram_id (int): Telegram ID пользователя
            date (datetime.date): Дата, за которую нужно получить статистику
        
        Returns:
            dict: Статистика питания за день
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            # Получаем начало и конец указанного дня (в UTC)
            start_of_day = datetime.combine(date, time.min)
            end_of_day = datetime.combine(date, time.max)
            
            # Получаем все анализы за указанный день
            analyses = session.query(FoodAnalysis).filter(
                FoodAnalysis.user_id == user.id,
                FoodAnalysis.analysis_date >= start_of_day,
                FoodAnalysis.analysis_date <= end_of_day
            ).order_by(FoodAnalysis.analysis_date).all()
            
            # Инициализируем статистику по приемам пищи
            meal_stats = {
                "breakfast": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "lunch": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "dinner": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "snack": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []},
                "total": {"count": 0, "calories": 0, "proteins": 0, "fats": 0, "carbs": 0, "items": []}
            }
            
            # Заполняем статистику
            for analysis in analyses:
                meal_type = analysis.meal_type or "snack"  # По умолчанию считаем перекусом
                
                meal_stats[meal_type]["count"] += 1
                meal_stats[meal_type]["calories"] += analysis.calories or 0
                meal_stats[meal_type]["proteins"] += analysis.proteins or 0
                meal_stats[meal_type]["fats"] += analysis.fats or 0
                meal_stats[meal_type]["carbs"] += analysis.carbs or 0
                
                # Добавляем информацию о блюде
                item_info = {
                    "name": analysis.food_name,
                    "calories": analysis.calories,
                    "proteins": analysis.proteins,
                    "fats": analysis.fats,
                    "carbs": analysis.carbs,
                    "time": analysis.analysis_date.strftime("%H:%M"),
                    "portion_weight": analysis.portion_weight
                }
                meal_stats[meal_type]["items"].append(item_info)
                meal_stats["total"]["items"].append(item_info)
                
                # Обновляем общую статистику
                meal_stats["total"]["count"] += 1
                meal_stats["total"]["calories"] += analysis.calories or 0
                meal_stats["total"]["proteins"] += analysis.proteins or 0
                meal_stats["total"]["fats"] += analysis.fats or 0
                meal_stats["total"]["carbs"] += analysis.carbs or 0
            
            # Округляем значения для лучшей читаемости
            for meal_type in meal_stats:
                meal_stats[meal_type]["calories"] = round(meal_stats[meal_type]["calories"], 1)
                meal_stats[meal_type]["proteins"] = round(meal_stats[meal_type]["proteins"], 1)
                meal_stats[meal_type]["fats"] = round(meal_stats[meal_type]["fats"], 1)
                meal_stats[meal_type]["carbs"] = round(meal_stats[meal_type]["carbs"], 1)
            
            return meal_stats
            
        finally:
            session.close()
            
    @staticmethod
    def has_data_for_date(telegram_id, date):
        """
        Проверяет, есть ли данные для указанной даты
        
        Args:
            telegram_id (int): Telegram ID пользователя
            date (datetime.date): Дата для проверки
        
        Returns:
            bool: True, если есть данные, False - если нет
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return False
            
            # Получаем начало и конец указанного дня (в UTC)
            start_of_day = datetime.combine(date, time.min)
            end_of_day = datetime.combine(date, time.max)
            
            # Проверяем наличие записей за указанный день
            count = session.query(FoodAnalysis).filter(
                FoodAnalysis.user_id == user.id,
                FoodAnalysis.analysis_date >= start_of_day,
                FoodAnalysis.analysis_date <= end_of_day
            ).count()
            
            return count > 0
        finally:
            session.close()
            
    @staticmethod
    def get_earliest_analysis_date(telegram_id):
        """
        Получить дату самого раннего анализа пользователя
        
        Args:
            telegram_id (int): Telegram ID пользователя
        
        Returns:
            datetime.date: Дата самого раннего анализа или None, если анализов нет
        """
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            earliest_analysis = session.query(FoodAnalysis).filter(
                FoodAnalysis.user_id == user.id
            ).order_by(FoodAnalysis.analysis_date.asc()).first()
            
            if earliest_analysis:
                return earliest_analysis.analysis_date.date()
            return None
        finally:
            session.close()

    
    @track_api_call('db_save_food_analysis')
    @staticmethod
    def save_food_analysis(telegram_id, food_name, calories, proteins, fats, carbs, image_path=None, portion_weight=None, analysis_time=None):
        """Сохранить результаты анализа пищи"""
        session = Session()
        try:
            user = DatabaseManager.get_or_create_user(telegram_id)
            
            # Если время не указано, используем текущее время
            if analysis_time is None:
                analysis_time = datetime.utcnow()
            
            # Определяем тип приема пищи по времени
            meal_type = determine_meal_type(analysis_time)
            
            food_analysis = FoodAnalysis(
                user_id=user.id,
                food_name=food_name,
                calories=calories,
                proteins=proteins,
                fats=fats,
                carbs=carbs,
                image_path=image_path,
                portion_weight=portion_weight,
                analysis_date=analysis_time,
                meal_type=meal_type
            )
            session.add(food_analysis)
            session.commit()
            return food_analysis
        finally:
            session.close()
    
    @staticmethod
    def check_subscription_status(telegram_id):
        """Проверка статуса подписки пользователя"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return False
            
            active_subscription = session.query(UserSubscription).filter_by(
                user_id=user.id, 
                is_active=True
            ).filter(UserSubscription.end_date > datetime.utcnow()).first()
            
            return bool(active_subscription)
        finally:
            session.close()
    
    @track_api_call('db_add_subscription')
    @staticmethod
    def add_subscription(telegram_id, months=1, payment_id=None):
        """Добавить подписку пользователю"""
        session = Session()
        try:
            user = DatabaseManager.get_or_create_user(telegram_id)
            end_date = datetime.utcnow() + timedelta(days=30 * months)
            
            subscription = UserSubscription(
                user_id=user.id,
                end_date=end_date,
                payment_id=payment_id
            )
            
            session.add(subscription)
            session.commit()
            return subscription
        finally:
            session.close()
    
    @staticmethod
    def get_remaining_free_requests(telegram_id):
        """Получить количество оставшихся бесплатных запросов"""
        if DatabaseManager.check_subscription_status(telegram_id):
            return float('inf')  # Бесконечное количество запросов для подписчиков
            
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return FREE_REQUESTS_LIMIT
            
            used_requests = session.query(FoodAnalysis).filter_by(user_id=user.id).count()
            remaining = max(0, FREE_REQUESTS_LIMIT - used_requests)
            
            return remaining
        finally:
            session.close()
    
    @staticmethod
    def get_user_statistics(telegram_id):
        """Получить статистику использования для пользователя"""
        session = Session()
        try:
            user = session.query(User).filter_by(telegram_id=telegram_id).first()
            if not user:
                return None
            
            analyses = session.query(FoodAnalysis).filter_by(user_id=user.id).all()
            total_calories = sum(a.calories or 0 for a in analyses)
            total_proteins = sum(a.proteins or 0 for a in analyses)
            total_fats = sum(a.fats or 0 for a in analyses)
            total_carbs = sum(a.carbs or 0 for a in analyses)
            
            return {
                "total_analyses": len(analyses),
                "total_calories": total_calories,
                "total_proteins": total_proteins,
                "total_fats": total_fats,
                "total_carbs": total_carbs
            }
        finally:
            session.close()