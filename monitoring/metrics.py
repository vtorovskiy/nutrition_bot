import time
import logging
import threading
import json
import os
from datetime import datetime, timedelta
from collections import defaultdict, deque
import traceback
import time

logger = logging.getLogger(__name__)

class MetricsCollector:
    """Коллектор метрик для отслеживания производительности и выявления проблем"""
    
    def __init__(self, save_interval=3600, metrics_file='metrics.json'):
        """
        Инициализация коллектора метрик
        
        Args:
            save_interval (int): Интервал сохранения метрик в секундах (по умолчанию: 1 час)
            metrics_file (str): Имя файла для сохранения метрик
        """
        self.metrics_file = metrics_file
        self.save_interval = save_interval
        self.lock = threading.Lock()
        
        # Инициализация метрик значениями по умолчанию
        self._init_default_metrics()
        
        # Попытка загрузки существующих метрик из файла
        self._load_metrics()
        
        # Запуск фонового потока для периодического сохранения метрик
        self._start_background_save()
    
    def _init_default_metrics(self):
        """Инициализация метрик значениями по умолчанию"""
        current_time = datetime.now().isoformat()
        self.metrics = {
            'api_calls': defaultdict(int),            # Количество вызовов каждого API
            'api_errors': defaultdict(int),           # Количество ошибок каждого API
            'api_response_times': defaultdict(list),  # Время ответа каждого API (последние 100)
            'photo_analyses': 0,                      # Общее количество анализов фотографий
            'barcode_scans': 0,                       # Общее количество сканирований штрихкодов
            'unique_users': set(),                    # Уникальные пользователи
            'user_commands': defaultdict(int),        # Количество выполненных команд
            'subscription_purchases': 0,              # Количество покупок подписок
            'errors': defaultdict(int),               # Количество ошибок по типам
            'start_time': datetime.now().isoformat(),
            'restart_count': 0,                       # Счетчик перезапусков
            'start_times': [current_time]             # История времен запуска # Время запуска коллектора
        }
        self.max_response_times = 100  # Хранить только последние 100 значений времени ответа
        self._last_save = time.time()
    
    def _load_metrics(self):
        """Загрузка метрик из файла, если он существует"""
        try:
            if os.path.exists(self.metrics_file):
                print(f"[DEBUG] Загружаем метрики из файла: {self.metrics_file}")
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    saved_metrics = json.load(f)
                
                print(f"[DEBUG] Содержимое файла метрик: {saved_metrics}")
                logger.info(f"Загружаем метрики из файла: {self.metrics_file}")
                
                # Обновление метрик из сохраненного файла
                with self.lock:
                    # API вызовы - слияние данных, сохраняя накопленные значения
                    for api, count in saved_metrics.get('api_calls', {}).items():
                        self.metrics['api_calls'][api] = count
                    
                    # API ошибки - слияние данных
                    for api, count in saved_metrics.get('api_errors', {}).items():
                        self.metrics['api_errors'][api] = count
                    
                    # Время ответа API - слияние данных
                    for api, times in saved_metrics.get('api_response_times', {}).items():
                        if times:
                            self.metrics['api_response_times'][api] = deque(times, maxlen=self.max_response_times)
                    
                    # Счетчики - прямое присвоение из файла
                    self.metrics['photo_analyses'] = saved_metrics.get('photo_analyses', 0)
                    self.metrics['barcode_scans'] = saved_metrics.get('barcode_scans', 0)
                    self.metrics['subscription_purchases'] = saved_metrics.get('subscription_purchases', 0)
                    
                    # Уникальные пользователи
                    if 'unique_users' in saved_metrics:
                        # Если сохранен список, преобразуем его в множество
                        if isinstance(saved_metrics['unique_users'], list):
                            self.metrics['unique_users'] = set(saved_metrics['unique_users'])
                    
                    # Команды - слияние данных
                    for cmd, count in saved_metrics.get('user_commands', {}).items():
                        self.metrics['user_commands'][cmd] = count
                    
                    # Ошибки - слияние данных
                    for error, count in saved_metrics.get('errors', {}).items():
                        self.metrics['errors'][error] = count
                    
                    # Обновляем счетчик перезапусков
                    self.metrics['restart_count'] = saved_metrics.get('restart_count', 0) + 1
                    
                    # Добавляем историю времен запуска
                    self.metrics['start_times'] = saved_metrics.get('start_times', [])
                    self.metrics['start_times'].append(datetime.now().isoformat())
                    
                    print(f"[DEBUG] Метрики успешно загружены и обновлены")
                    logger.info(f"Метрики успешно загружены и обновлены")
        except Exception as e:
            print(f"[DEBUG] Ошибка при загрузке метрик: {str(e)}")
            print(f"[DEBUG] Полный traceback: {traceback.format_exc()}")
            logger.error(f"Ошибка при загрузке метрик: {str(e)}")
            logger.error(traceback.format_exc())

    
    def _start_background_save(self):
        """Запуск фонового потока для периодического сохранения метрик"""
        def save_periodically():
            while True:
                time.sleep(self.save_interval)
                self.save_metrics()
        
        save_thread = threading.Thread(target=save_periodically, daemon=True)
        save_thread.start()
    
    def track_api_call(self, api_name, response_time=None, error=False):
        """
        Отслеживание вызова API
        
        Args:
            api_name (str): Название API
            response_time (float, optional): Время ответа в секундах
            error (bool): Флаг ошибки вызова
        """
        with self.lock:
            self.metrics['api_calls'][api_name] += 1
            
            if error:
                self.metrics['api_errors'][api_name] += 1
            
            if response_time is not None:
                if api_name not in self.metrics['api_response_times']:
                    self.metrics['api_response_times'][api_name] = deque(maxlen=self.max_response_times)
                self.metrics['api_response_times'][api_name].append(response_time)
    
    def track_photo_analysis(self, user_id):
        """
        Отслеживание анализа фотографии
        
        Args:
            user_id (int): ID пользователя
        """
        with self.lock:
            self.metrics['photo_analyses'] += 1
            self.metrics['unique_users'].add(user_id)
    
    def track_barcode_scan(self, user_id):
        """
        Отслеживание сканирования штрихкода
        
        Args:
            user_id (int): ID пользователя
        """
        with self.lock:
            self.metrics['barcode_scans'] += 1
            self.metrics['unique_users'].add(user_id)
    
    def track_command(self, command):
        """
        Отслеживание выполнения команды
        
        Args:
            command (str): Название команды
        """
        with self.lock:
            self.metrics['user_commands'][command] += 1
    
    def track_subscription_purchase(self):
        """Отслеживание покупки подписки"""
        with self.lock:
            self.metrics['subscription_purchases'] += 1
    
    def track_error(self, error_type):
        """
        Отслеживание ошибки
        
        Args:
            error_type (str): Тип ошибки
        """
        with self.lock:
            self.metrics['errors'][error_type] += 1
    
    def get_metrics_summary(self):
        """
        Получение сводки по метрикам
        
        Returns:
            dict: Сводка по метрикам
        """
        with self.lock:
            # Расчет метрик
            total_api_calls = sum(self.metrics['api_calls'].values())
            total_api_errors = sum(self.metrics['api_errors'].values())
            error_rate = total_api_errors / total_api_calls if total_api_calls > 0 else 0
            
            # Среднее время ответа для каждого API
            avg_response_times = {}
            for api, times in self.metrics['api_response_times'].items():
                if times:
                    avg_response_times[api] = sum(times) / len(times)
            
            # Расчет времени работы
            start_time = datetime.fromisoformat(self.metrics['start_time'])
            uptime_seconds = (datetime.now() - start_time).total_seconds()
            
            return {
                'total_api_calls': total_api_calls,
                'total_api_errors': total_api_errors,
                'error_rate': f"{error_rate:.2%}",
                'avg_response_times': avg_response_times,
                'photo_analyses': self.metrics['photo_analyses'],
                'barcode_scans': self.metrics['barcode_scans'],
                'unique_users_count': len(self.metrics['unique_users']),
                'popular_commands': dict(sorted(
                    self.metrics['user_commands'].items(), 
                    key=lambda x: x[1], 
                    reverse=True
                )[:5]),  # топ-5 команд
                'subscription_purchases': self.metrics['subscription_purchases'],
                'top_errors': dict(sorted(
                    self.metrics['errors'].items(), 
                    key=lambda x: x[1], 
                    reverse=True
                )[:5]),  # топ-5 ошибок
                'uptime': f"{uptime_seconds / 3600:.1f} часов",
                'restart_count': self.metrics.get('restart_count', 0),
                'start_times': self.metrics.get('start_times', [])
            }
    
    def save_metrics(self):
        """Сохранение метрик в файл"""
        try:
            with self.lock:
                # Подробное логирование для отладки
                print(f"[DEBUG] Начинаем сохранение метрик в файл: {self.metrics_file}")
                
                # Создание копии метрик для сериализации
                serializable_metrics = {
                    'api_calls': dict(self.metrics['api_calls']),
                    'api_errors': dict(self.metrics['api_errors']),
                    'api_response_times': {k: list(v) for k, v in self.metrics['api_response_times'].items()},
                    'photo_analyses': self.metrics['photo_analyses'],
                    'barcode_scans': self.metrics['barcode_scans'],
                    'unique_users': list(self.metrics['unique_users']) if isinstance(self.metrics['unique_users'], set) else [],
                    'user_commands': dict(self.metrics['user_commands']),
                    'subscription_purchases': self.metrics['subscription_purchases'],
                    'errors': dict(self.metrics['errors']),
                    'start_time': self.metrics['start_time'],
                    'restart_count': self.metrics.get('restart_count', 0),
                    'start_times': self.metrics.get('start_times', []),
                    'save_time': datetime.now().isoformat()
                }
                
                # Получаем директорию для сохранения
                directory = os.path.dirname(self.metrics_file)
                print(f"[DEBUG] Директория для сохранения: {directory}")
                
                # Проверка существования директории
                dir_exists = os.path.exists(directory)
                print(f"[DEBUG] Директория существует: {dir_exists}")
                
                # Если путь относительный, делаем его абсолютным от текущей директории
                if not os.path.isabs(directory) and directory:
                    abs_directory = os.path.join(os.getcwd(), directory)
                    print(f"[DEBUG] Абсолютный путь к директории: {abs_directory}")
                    directory = abs_directory
                
                # Создаем директорию, если она не существует
                if directory and not os.path.exists(directory):
                    try:
                        print(f"[DEBUG] Пытаемся создать директорию: {directory}")
                        os.makedirs(directory, exist_ok=True)
                        print(f"[DEBUG] Директория успешно создана: {directory}")
                    except Exception as dir_error:
                        print(f"[DEBUG] Ошибка при создании директории: {str(dir_error)}")
                        logger.error(f"Ошибка при создании директории {directory}: {str(dir_error)}")
                        print(f"[DEBUG] Полный traceback: {traceback.format_exc()}")
                
                # Проверяем запись в директорию
                try:
                    test_file_path = os.path.join(directory, 'test_write.tmp')
                    print(f"[DEBUG] Тестируем запись в файл: {test_file_path}")
                    with open(test_file_path, 'w') as f:
                        f.write('test')
                    os.remove(test_file_path)
                    print(f"[DEBUG] Запись и удаление тестового файла успешны")
                except Exception as write_error:
                    print(f"[DEBUG] Ошибка при проверке записи: {str(write_error)}")
                    print(f"[DEBUG] Полный traceback: {traceback.format_exc()}")
                
                # Печатаем полный путь к файлу для отладки
                full_path = os.path.abspath(self.metrics_file)
                print(f"[DEBUG] Полный путь к файлу метрик: {full_path}")
                
                # Сохранение метрик в файл
                try:
                    print(f"[DEBUG] Пытаемся сохранить метрики в файл: {full_path}")
                    with open(self.metrics_file, 'w', encoding='utf-8') as f:
                        json.dump(serializable_metrics, f, ensure_ascii=False, indent=2)
                    print(f"[DEBUG] Метрики успешно сохранены в файл")
                except Exception as save_error:
                    print(f"[DEBUG] Ошибка при сохранении в файл: {str(save_error)}")
                    print(f"[DEBUG] Полный traceback: {traceback.format_exc()}")
                
                logger.info(f"Метрики успешно сохранены в {self.metrics_file}")
                self._last_save = time.time()
        except Exception as e:
            print(f"[DEBUG] Общая ошибка в save_metrics: {str(e)}")
            print(f"[DEBUG] Полный traceback: {traceback.format_exc()}")
            logger.error(f"Ошибка при сохранении метрик: {str(e)}")
            logger.error(traceback.format_exc())


# Создание глобального экземпляра коллектора метрик
metrics_collector = MetricsCollector(
    save_interval=3600,  # Сохранять метрики каждый час
    metrics_file='/home/dq12777/nutrition_bot/data/metrics.json'  # Абсолютный путь
)