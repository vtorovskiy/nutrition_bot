from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import sys
import os

# Добавляем корневую директорию проекта в путь для импорта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DATABASE_URL

Base = declarative_base()

class User(Base):
    """Модель пользователя"""
    __tablename__ = 'users'
    
    id = Column(Integer, primary_key=True)
    telegram_id = Column(Integer, unique=True, nullable=False)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    registration_date = Column(DateTime, default=datetime.utcnow)
    
    # Добавляем поля для дневных норм КБЖУ
    gender = Column(String, nullable=True)  # 'male' или 'female'
    age = Column(Integer, nullable=True)
    weight = Column(Float, nullable=True)  # в кг
    height = Column(Float, nullable=True)  # в см
    activity_level = Column(Float, nullable=True)  # коэффициент активности (1.2 - 1.9)
    goal = Column(String, nullable=True)  # 'weight_loss', 'maintenance', 'weight_gain'
    
    # Расчетные нормы (могут быть заданы вручную или расчитаны)
    daily_calories = Column(Float, nullable=True)
    daily_proteins = Column(Float, nullable=True)
    daily_fats = Column(Float, nullable=True)
    daily_carbs = Column(Float, nullable=True)
    
    # Отношение один-ко-многим с моделью UserSubscription
    subscriptions = relationship("UserSubscription", back_populates="user")
    
    # Отношение один-ко-многим с моделью FoodAnalysis
    food_analyses = relationship("FoodAnalysis", back_populates="user")
    
    def __repr__(self):
        return f"<User(telegram_id={self.telegram_id}, username={self.username})>"

class UserSubscription(Base):
    """Модель подписки пользователя"""
    __tablename__ = 'user_subscriptions'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=False)
    is_active = Column(Boolean, default=True)
    payment_id = Column(String, nullable=True)
    
    # Отношение многие-к-одному с моделью User
    user = relationship("User", back_populates="subscriptions")
    
    def __repr__(self):
        return f"<UserSubscription(user_id={self.user_id}, active={self.is_active}, end_date={self.end_date})>"

class FoodAnalysis(Base):
    """Модель анализа пищи"""
    __tablename__ = 'food_analyses'
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    analysis_date = Column(DateTime, default=datetime.utcnow)
    food_name = Column(String, nullable=True)
    calories = Column(Float, nullable=True)
    proteins = Column(Float, nullable=True)
    fats = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    image_path = Column(String, nullable=True)
    portion_weight = Column(Float, nullable=True)  # Добавляем поле для веса порции

    # Добавляем поле для хранения приема пищи
    meal_type = Column(String, nullable=True)  # 'breakfast', 'lunch', 'dinner', 'snack'
    
    # Отношение многие-к-одному с моделью User
    user = relationship("User", back_populates="food_analyses")
    
    def __repr__(self):
        return f"<FoodAnalysis(id={self.id}, food_name={self.food_name}, calories={self.calories})>"

# Инициализация базы данных
def init_db():
    engine = create_engine(DATABASE_URL)
    Base.metadata.create_all(engine)
    return engine