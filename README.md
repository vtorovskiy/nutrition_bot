# Бот для анализа КБЖУ по фотографии еды

Телеграм-бот, который анализирует пищевую ценность блюд по фотографии и рассчитывает КБЖУ (калории, белки, жиры, углеводы). 
Проект включает в себя распознавание еды с помощью GPT-4o, расчет пищевой ценности и систему подписок с ЮKassa.

## Основные функции

- Анализ фотографий еды и распознавание продуктов с использованием AI
- Компактное отображение калорий, белков, жиров и углеводов
- Персонализированные рекомендации с учетом физических параметров и целей
- Статистика питания с разбивкой по приемам пищи и дням
- Бесплатный лимит запросов для новых пользователей
- Система платных подписок через ЮKassa

## Команды бота

- `/start` - Начало работы с ботом, доступ к функциям
- `/help` - Справка по использованию бота
- `/stats` - Статистика питания с навигацией по дням
- `/setup` - Настройка профиля и персональных норм КБЖУ
- `/subscription` - Управление подпиской

## Особенности интерфейса

- Компактный формат вывода КБЖУ с эмодзи и четкой структурой
- Наглядная статистика с группировкой по приемам пищи
- Удобная навигация по календарю статистики
- Интерактивные кнопки для основных действий

## Технические детали

- Python 3.8+
- Telebot (pyTelegramBotAPI)
- SQLAlchemy для хранения данных
- Интеграция с GPT-4o через AITunnel для анализа изображений
- Резервное распознавание через Google Cloud Vision API
- Система платежей через ЮKassa API

## Установка и запуск

1. Клонировать репозиторий
2. Создать виртуальное окружение и установить зависимости: 
3. Создать .env файл с настройками:
4. Запустить бота:

## Структура проекта
nutrition_bot/
├── bot.py                     # Основной файл бота
├── config.py                  # Конфигурационные параметры
├── requirements.txt           # Зависимости проекта
├── database/                  # Модуль для работы с базой данных
│   ├── db_manager.py          # Управление базой данных
│   └── models.py              # Модели базы данных
├── food_recognition/          # Модуль для распознавания еды
│   ├── aitunnel_adapter.py    # Адаптер для AITunnel
│   ├── aitunnel_vision_api.py # Взаимодействие с GPT-4o
│   ├── nutrition_calc.py      # Расчет пищевой ценности
│   └── vision_api.py          # Google Cloud Vision API
├── payments/                  # Модуль для платежей
│   └── yukassa.py             # Интеграция с ЮKassa
└── utils/                     # Вспомогательные утилиты
└── helpers.py             # Вспомогательные функции

## Дальнейшее развитие

- Расширение базы данных продуктов и блюд
- Добавление аналитики с графиками
- Интеграция с фитнес-трекерами
- Персонализированные рекомендации по питанию
- Распознавание продуктов по текстовому описанию