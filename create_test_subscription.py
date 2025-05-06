from datetime import datetime, timedelta
from database.db_manager import DatabaseManager

# Ваш Telegram ID
telegram_id = 931190875  # Замените на ваш ID

# Создание подписки на 1 месяц с текущей даты
DatabaseManager.add_subscription(
    telegram_id=telegram_id,
    months=1,
    payment_id="test_manual_payment"
)

print("Подписка успешно создана")