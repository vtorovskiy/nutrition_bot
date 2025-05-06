import os
from dotenv import load_dotenv

# Загрузка переменных окружения из файла .env
load_dotenv()

# Ключ API AITunnel
AITUNNEL_API_KEY = os.getenv('AITUNNEL_API_KEY')

# Токен бота Telegram
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Ключи Google Cloud Vision API
GOOGLE_APPLICATION_CREDENTIALS = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')

# Настройки ЮKassa
YUKASSA_SHOP_ID = os.getenv('YUKASSA_SHOP_ID')
YUKASSA_SECRET_KEY = os.getenv('YUKASSA_SECRET_KEY')

# Стоимость подписки (в рублях)
SUBSCRIPTION_COST = 299

# Лимиты для бесплатного использования
FREE_REQUESTS_LIMIT = 100

# Настройки базы данных
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///nutrition_bot.db')

# Webhook и URL сервера (для продакшена)
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', 8443))
WEBHOOK_LISTEN = os.getenv('WEBHOOK_LISTEN', '0.0.0.0')

# Путь к SSL-сертификатам (для продакшена)
WEBHOOK_SSL_CERT = os.getenv('WEBHOOK_SSL_CERT')
WEBHOOK_SSL_PRIV = os.getenv('WEBHOOK_SSL_PRIV')