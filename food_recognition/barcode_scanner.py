import io
import os
import sys
import requests
import json
from pyzbar.pyzbar import decode
from PIL import Image
from google.cloud import vision

# Добавляем корневую директорию проекта в путь для импорта
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import GOOGLE_APPLICATION_CREDENTIALS
from food_recognition.nutrition_calc import NutritionCalculator

class BarcodeScanner:
    """Класс для сканирования штрихкодов и получения информации о продуктах"""
    
    def __init__(self):
        """Инициализация сканера штрихкодов"""
        self.vision_client = None
        if GOOGLE_APPLICATION_CREDENTIALS:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = GOOGLE_APPLICATION_CREDENTIALS
            self.vision_client = vision.ImageAnnotatorClient()
    
    def detect_barcode(self, image_path=None, image_content=None):
        """
        Распознавание штрихкода на изображении
        
        Args:
            image_path (str, optional): Путь к файлу изображения
            image_content (bytes, optional): Содержимое изображения в байтах
            
        Returns:
            str: Распознанный штрихкод или None, если штрихкод не найден
        """
        try:
            # Подготовка изображения
            if image_path and os.path.exists(image_path):
                with open(image_path, 'rb') as image_file:
                    content = image_file.read()
                pil_image = Image.open(image_path)
            elif image_content:
                content = image_content
                pil_image = Image.open(io.BytesIO(image_content))
            else:
                raise ValueError("Необходимо предоставить либо путь к изображению, либо его содержимое")
            
            # Сначала пробуем использовать pyzbar (быстрее и без API-запросов)
            decoded_objects = decode(pil_image)
            if decoded_objects:
                for obj in decoded_objects:
                    return obj.data.decode('utf-8')
            
            # Если pyzbar не справился, используем Google Vision API
            if self.vision_client:
                image = vision.Image(content=content)
                response = self.vision_client.text_detection(image=image)
                
                # Проверка на ошибки
                if response.error.message:
                    raise Exception(f"Ошибка при распознавании изображения: {response.error.message}")
                
                # Ищем текст, похожий на штрихкод (длинный набор цифр)
                texts = response.text_annotations
                if texts:
                    for text in texts:
                        # Проверяем, похож ли текст на штрихкод (только цифры, длина 8-13)
                        if text.description.isdigit() and 8 <= len(text.description) <= 13:
                            return text.description
            
            return None
        
        except Exception as e:
            print(f"Ошибка при распознавании штрихкода: {str(e)}")
            return None
    
    def get_product_info(self, barcode):
        """
        Получение информации о продукте по штрихкоду
        
        Args:
            barcode (str): Штрихкод продукта
            
        Returns:
            dict: Информация о продукте или None, если продукт не найден
        """
        try:
            # Проверяем, есть ли продукт в локальной базе
            local_product = self._check_local_database(barcode)
            if local_product:
                return local_product
            
            # 1. Сначала пробуем Edadeal (больше российских продуктов)
            try:
                response = requests.get(f"https://api.edadeal.ru/web/v1/product_details?product_id={barcode}", 
                                       timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data and 'product' in data:
                        product = data['product']
                        # Edadeal не всегда предоставляет полные данные о КБЖУ,
                        # поэтому нужно проверять наличие
                        nutrition = product.get('nutrition', {})
                        
                        nutrition_data = {
                            'name': product.get('title', 'Неизвестный продукт'),
                            'calories': nutrition.get('energy', {}).get('value', 0),
                            'proteins': nutrition.get('proteins', {}).get('value', 0),
                            'fats': nutrition.get('fats', {}).get('value', 0),
                            'carbs': nutrition.get('carbohydrates', {}).get('value', 0),
                            'portion_weight': 100,  # По умолчанию на 100г
                            'barcode': barcode,
                            'estimated': False
                        }
                        
                        # Сохраняем продукт в локальную базу
                        self._save_to_local_database(barcode, nutrition_data)
                        return nutrition_data
            except:
                pass
            
            # 2. Если не нашли в Edadeal, пробуем Open Food Facts
            try:
                response = requests.get(f"https://world.openfoodfacts.org/api/v0/product/{barcode}.json", 
                                       timeout=5)
                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") == 1:
                        product = data.get("product", {})
                        nutriments = product.get("nutriments", {})
                        
                        # Получаем название на русском, если доступно
                        product_name = product.get('product_name_ru', product.get('product_name', 'Неизвестный продукт'))
                        
                        nutrition_data = {
                            'name': product_name,
                            'calories': nutriments.get('energy-kcal_100g', 0),
                            'proteins': nutriments.get('proteins_100g', 0),
                            'fats': nutriments.get('fat_100g', 0),
                            'carbs': nutriments.get('carbohydrates_100g', 0),
                            'portion_weight': 100,  # По умолчанию на 100г
                            'barcode': barcode,
                            'estimated': False
                        }
                        
                        # Сохраняем продукт в локальную базу
                        self._save_to_local_database(barcode, nutrition_data)
                        return nutrition_data
            except:
                pass
            
            # 3. Если не нашли нигде, возвращаем заглушку для ручного ввода
            return {
                'name': f'Продукт (штрихкод: {barcode})',
                'calories': 0,
                'proteins': 0,
                'fats': 0,
                'carbs': 0,
                'portion_weight': 100,
                'barcode': barcode,
                'estimated': True
            }
                
        except Exception as e:
            print(f"Ошибка при получении информации о продукте: {str(e)}")
            return None
    
    def _check_local_database(self, barcode):
        """
        Проверка наличия продукта в локальной базе данных
        
        Args:
            barcode (str): Штрихкод продукта
            
        Returns:
            dict: Информация о продукте или None, если продукт не найден
        """
        database_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                                    'data', 'barcodes.json')
        
        if os.path.exists(database_path):
            try:
                with open(database_path, 'r', encoding='utf-8') as f:
                    products = json.load(f)
                    return products.get(barcode)
            except:
                return None
        
        return None
    
    def _save_to_local_database(self, barcode, product_data):
        """
        Сохранение продукта в локальную базу данных
        
        Args:
            barcode (str): Штрихкод продукта
            product_data (dict): Информация о продукте
        """
        # Очистка штрихкода от лишних символов
        clean_barcode = ''.join(filter(str.isdigit, barcode))
        
        # Также очищаем штрихкод внутри product_data
        if 'barcode' in product_data:
            product_data['barcode'] = clean_barcode
        
        data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
        database_path = os.path.join(data_dir, 'barcodes.json')
        
        # Создаем директорию, если она не существует
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)
        
        # Загружаем существующие данные или создаем новый словарь
        products = {}
        if os.path.exists(database_path):
            try:
                with open(database_path, 'r', encoding='utf-8') as f:
                    products = json.load(f)
            except:
                products = {}
        
        # Добавляем новый продукт, используя очищенный штрихкод как ключ
        products[clean_barcode] = product_data
        
        # Сохраняем обновленные данные
        with open(database_path, 'w', encoding='utf-8') as f:
            json.dump(products, f, ensure_ascii=False, indent=2)