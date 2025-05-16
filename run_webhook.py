#!/usr/bin/env python3
import os
import sys
import logging
import logging.handlers
from telebot import apihelper
import telebot

# Добавляем текущую директорию в PYTHONPATH
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(script_dir)

# Импортируем конфигурацию и бота
from config import (
    TELEGRAM_BOT_TOKEN, WEBHOOK_URL, WEBHOOK_PORT,
    WEBHOOK_HOST, WEBHOOK_LISTEN, WEBHOOK_SSL_CERT, WEBHOOK_SSL_PRIV,
    LOG_FILE
)
from bot import bot, logger

# Настройка дополнительного логирования для отладки
file_handler = logging.handlers.RotatingFileHandler(
    LOG_FILE, maxBytes=10*1024*1024, backupCount=5
)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)
logger.addHandler(file_handler)

telebot_logger = logging.getLogger('telebot')
telebot_logger.setLevel(logging.INFO)
telebot_logger.addHandler(file_handler)

def main():
    # Удаляем текущий вебхук, если есть
    logger.info("Удаление текущего вебхука...")
    bot.remove_webhook()
    
    # Параметры для webhook
    webhook_url = f"{WEBHOOK_URL}/{TELEGRAM_BOT_TOKEN}/"
    logger.info(f"Настройка вебхука на {webhook_url}")
    
    # Устанавливаем вебхук
    bot.set_webhook(
        url=webhook_url,
        certificate=open(WEBHOOK_SSL_CERT, 'rb') if WEBHOOK_SSL_CERT else None
    )
    
    # Создаем Flask-приложение для обработки webhook
    from flask import Flask, request, abort
    
    app = Flask(__name__)
    
    @app.route(f'/{TELEGRAM_BOT_TOKEN}/', methods=['POST'])
    def webhook():
        logger.info("Получен webhook запрос")
        try:
            if request.headers.get('content-type') == 'application/json':
                json_string = request.get_data().decode('utf-8')
                logger.info(f"Получены данные webhook: {json_string[:100]}...")  # Только начало для безопасности
                
                # Получаем данные из JSON
                update_dict = apihelper.json.loads(json_string)
                logger.info("JSON успешно разобран")
                
                # Преобразуем словарь в объект Update
                update_obj = telebot.types.Update.de_json(update_dict)
                logger.info("Преобразовано в объект Update")
                
                # Обрабатываем обновление
                bot.process_new_updates([update_obj])
                logger.info("Webhook успешно обработан")
                
                return 'OK'
            else:
                logger.warning(f"Неверный content-type: {request.headers.get('content-type')}")
                abort(403)
        except Exception as e:
            logger.error(f"Ошибка при обработке webhook: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return str(e), 500
    
    @app.route('/status', methods=['GET'])
    def status():
        return 'SnapEat Bot is running'
    
    logger.info(f"Запуск сервера на {WEBHOOK_LISTEN}:{WEBHOOK_PORT}")
    
    # Запускаем Flask-сервер БЕЗ SSL, т.к. Nginx будет обрабатывать SSL
    try:
        app.run(
            host='0.0.0.0',  # Принудительно слушаем на всех интерфейсах
            port=WEBHOOK_PORT,
            debug=False
        )
    except Exception as e:
        logger.error(f"Ошибка при запуске сервера: {e}")
        raise

if __name__ == "__main__":
    main()