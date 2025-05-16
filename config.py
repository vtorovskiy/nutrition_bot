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
PAYMENT_PROVIDER_TOKEN = os.getenv('PAYMENT_PROVIDER_TOKEN')

# Стоимость подписки (в рублях)
SUBSCRIPTION_COST = 299

# Лимиты для бесплатного использования
FREE_REQUESTS_LIMIT = 10

# Настройки базы данных
DATABASE_URL = os.getenv('DATABASE_URL', 'sqlite:///nutrition_bot.db')

# Режим работы бота (polling или webhook)
BOT_MODE = os.getenv('BOT_MODE', 'polling').lower()
IS_WEBHOOK_MODE = BOT_MODE == 'webhook'

# Webhook и URL сервера (для продакшена)
WEBHOOK_URL = os.getenv('WEBHOOK_URL')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_PORT = int(os.getenv('WEBHOOK_PORT', 8443))
WEBHOOK_LISTEN = os.getenv('WEBHOOK_LISTEN', '0.0.0.0')

# Путь к SSL-сертификатам (для продакшена)
WEBHOOK_SSL_CERT = os.getenv('WEBHOOK_SSL_CERT')
WEBHOOK_SSL_PRIV = os.getenv('WEBHOOK_SSL_PRIV')

# Настройки логирования
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', 'logs/bot.log')

# Директория для резервного копирования
BACKUP_DIR = os.getenv('BACKUP_DIR', 'backups')

# ID администраторов через запятую в .env
ADMIN_IDS_STR = os.getenv('ADMIN_IDS', '931190875')
ADMIN_IDS = [int(admin_id.strip()) for admin_id in ADMIN_IDS_STR.split(',') if admin_id.strip()]

# Создание необходимых директорий
log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    os.makedirs(log_dir, exist_ok=True)
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR, exist_ok=True)