"""
Telegram-бот для расчёта стоимости доставки мебели
"""
import json
import logging
from typing import Dict, Any, List, Union
from datetime import datetime

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Состояния диалога
(
    MAIN_MENU,  # Главное меню с панелью управления
    ADDRESS,
    DISTANCE_MKAD,
    VOLUME,
    CARRYING_QUESTION,  # Вопрос про пронос мебели
    DOOR_DISTANCE,  # Расстояние до двери (для МО)
    CARRYING_TIMES,  # Количество проносов
    EXTRA_POINT,  # Вопрос о дополнительной точке доставки
    EXTRA_POINT_FROM,  # Выбор откуда (Москва/МО)
    EXTRA_POINT_TO,  # Выбор куда (Москва/МО)
    EXTRA_DISTANCE,  # Расстояние между точками доставки
    EXTRA_POINT_FINISH,  # Завершение добавления точек
    DELIVERY_ONLY,
    MOSCOW_FLOOR,  # Этаж для Москвы (общий для всей мебели)
    MOSCOW_ELEVATOR,  # Лифт для Москвы (общий для всей мебели)
    LIFTING_NEEDED,
    FLOOR,
    ELEVATOR,
    LIFTING_METHOD,  # Способ подъема (все на лифте или смешанный)
    LIFTING_ELEVATOR_COUNT,  # Количество мест по лифту
    LIFTING_STAIRS_COUNT,  # Количество мест по лестнице
    FURNITURE_TYPE,
    PLACES_CONFIRM,
    PLACES_INPUT,
    ASSEMBLY_NEEDED,
    ADD_MORE_FURNITURE,
    STORAGE_NEEDED,
    STORAGE_DAYS,
    STORAGE_VOLUME,
    CALCULATION,
    VIEW_PRICES  # Просмотр прайса
) = range(31)


def format_currency(amount: Union[int, float]) -> str:
    """Форматирует число с разделителями тысяч"""
    # Преобразуем в int для форматирования (отбрасываем дробную часть)
    amount_int = int(amount)
    return f"{amount_int:,}".replace(",", " ")


def format_prices_list(prices: Dict[str, Any]) -> str:
    """Форматирует прайс-лист для отображения"""
    if not prices:
        return "Данные не загружены"
    
    text = ""
    
    # Основные цены
    if 'moscow_ring_road_km' in prices:
        text += f"🚚 <b>МКАД:</b> {format_currency(prices['moscow_ring_road_km'])} руб/км\n"
    if 'carrying_from_parking' in prices:
        text += f"📦 <b>Пронос от парковки:</b> {format_currency(prices['carrying_from_parking'])} руб\n"
    
    # Доставка
    if 'delivery' in prices:
        delivery = prices['delivery']
        text += f"\n<b>Доставка:</b>\n"
        if 'up_to_1m3' in delivery:
            text += f"• До 1 м³: {format_currency(delivery['up_to_1m3'])} руб\n"
        if '1_to_5m3' in delivery:
            text += f"• 1-5 м³: {format_currency(delivery['1_to_5m3'])} руб\n"
        if '5_to_10m3' in delivery:
            text += f"• 5-10 м³: {format_currency(delivery['5_to_10m3'])} руб\n"
        if '10_to_18m3' in delivery:
            text += f"• 10-18 м³: {format_currency(delivery['10_to_18m3'])} руб\n"
    
    # Подъем (примеры основных позиций)
    if 'lifting' in prices:
        lifting = prices['lifting']
        text += f"\n<b>Подъем:</b>\n"
        
        items_to_show = [
            ('sofa_non_disassembled_up_to_2m', 'Диван до 2м'),
            ('sofa_non_disassembled_up_to_3m', 'Диван до 3м'),
            ('sofa_corner', 'Диван угловой'),
            ('armchair', 'Кресло'),
            ('chair_semi_armchair_pouf_coffee_table', 'Стул/Полукресло'),
            ('bed_disassembled_1_seat', 'Кровать разборная'),
        ]
        
        for key, name in items_to_show:
            if key in lifting and isinstance(lifting[key], dict):
                price = lifting[key].get('price_per_place', 0)
                if price:
                    text += f"• {name}: {format_currency(price)} руб/этаж\n"
    
    # Сборка
    if 'assembly' in prices:
        assembly = prices['assembly']
        text += f"\n<b>Сборка:</b>\n"
        if 'bed' in assembly:
            text += f"• Кровать: {format_currency(assembly['bed'])} руб\n"
        if 'sofa_straight' in assembly:
            text += f"• Диван прямой: {format_currency(assembly['sofa_straight'])} руб\n"
        if 'sofa_corner' in assembly:
            text += f"• Диван угловой: {format_currency(assembly['sofa_corner'])} руб\n"
    
    # Хранение
    if 'storage' in prices and 'per_day_per_m3' in prices['storage']:
        text += f"\n<b>Хранение:</b> {format_currency(prices['storage']['per_day_per_m3'])} руб/день за 1 м³\n"
    
    return text


def compare_prices(old_prices: Dict[str, Any], new_prices: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Сравнивает старые и новые цены, возвращает список изменений"""
    changes = []
    
    # Сравниваем основные цены
    for key in ['moscow_ring_road_km', 'carrying_from_parking']:
        if key in old_prices and key in new_prices:
            if old_prices[key] != new_prices[key]:
                changes.append({
                    'name': 'МКАД' if key == 'moscow_ring_road_km' else 'Пронос от парковки',
                    'old': old_prices[key],
                    'new': new_prices[key],
                    'key': key
                })
    
    # Сравниваем доставку
    if 'delivery' in old_prices and 'delivery' in new_prices:
        old_delivery = old_prices['delivery']
        new_delivery = new_prices['delivery']
        for key in ['up_to_1m3', '1_to_5m3', '5_to_10m3', '10_to_18m3']:
            if key in old_delivery and key in new_delivery:
                if old_delivery[key] != new_delivery[key]:
                    volume_name = {
                        'up_to_1m3': 'До 1 м³',
                        '1_to_5m3': '1-5 м³',
                        '5_to_10m3': '5-10 м³',
                        '10_to_18m3': '10-18 м³'
                    }.get(key, key)
                    changes.append({
                        'name': f'Доставка {volume_name}',
                        'old': old_delivery[key],
                        'new': new_delivery[key],
                        'key': f'delivery.{key}'
                    })
    
    # Сравниваем подъем (основные позиции)
    if 'lifting' in old_prices and 'lifting' in new_prices:
        old_lifting = old_prices['lifting']
        new_lifting = new_prices['lifting']
        
        items_to_check = [
            ('sofa_non_disassembled_up_to_2m', 'Диван до 2м'),
            ('sofa_non_disassembled_up_to_3m', 'Диван до 3м'),
            ('sofa_corner', 'Диван угловой'),
            ('armchair', 'Кресло'),
            ('chair_semi_armchair_pouf_coffee_table', 'Стул/Полукресло'),
            ('bed_disassembled_1_seat', 'Кровать разборная'),
        ]
        
        for key, name in items_to_check:
            if key in old_lifting and key in new_lifting:
                old_price = old_lifting[key].get('price_per_place', 0) if isinstance(old_lifting[key], dict) else 0
                new_price = new_lifting[key].get('price_per_place', 0) if isinstance(new_lifting[key], dict) else 0
                if old_price != new_price:
                    changes.append({
                        'name': f'Подъем {name}',
                        'old': old_price,
                        'new': new_price,
                        'key': f'lifting.{key}.price_per_place'
                    })
    
    # Сравниваем сборку
    if 'assembly' in old_prices and 'assembly' in new_prices:
        old_assembly = old_prices['assembly']
        new_assembly = new_prices['assembly']
        for key in ['bed', 'sofa_straight', 'sofa_corner']:
            if key in old_assembly and key in new_assembly:
                if old_assembly[key] != new_assembly[key]:
                    name_map = {
                        'bed': 'Кровать',
                        'sofa_straight': 'Диван прямой',
                        'sofa_corner': 'Диван угловой'
                    }
                    changes.append({
                        'name': f'Сборка {name_map.get(key, key)}',
                        'old': old_assembly[key],
                        'new': new_assembly[key],
                        'key': f'assembly.{key}'
                    })
    
    return changes


def format_price_changes(changes: List[Dict[str, Any]]) -> str:
    """Форматирует список изменений для отображения"""
    if not changes:
        return "Изменений не обнаружено"
    
    text = ""
    for change in changes:
        text += f"• <b>{change['name']}:</b> {format_currency(change['old'])} → {format_currency(change['new'])} руб\n"
    
    return text


# ИСТОЧНИК ДАННЫХ: Google Sheets (рекомендуется) или Google Docs
# 
# ВАРИАНТ 1: Google Sheets (РЕКОМЕНДУЕТСЯ)
# Создайте Google Sheets таблицу, сделайте её публичной (Файл -> Доступ -> Доступен всем)
# Скопируйте ID из URL: https://docs.google.com/spreadsheets/d/{SHEET_ID}/edit
GOOGLE_SHEET_ID = "1JXlSt3t8j_4GF5bZaMvq5a8GLfEEVi4Jc8OSeOSIERI"  # ID вашей таблицы
GOOGLE_SHEET_GID = "0"  # ID листа (обычно 0 для первого листа)

# ВАРИАНТ 2: Google Docs (альтернатива)
# Документ должен содержать валидный JSON (можно с дополнительным текстом вокруг)
GOOGLE_DOC_URL = "https://docs.google.com/document/d/1ULtC7ZqyGpo0fptLy9X_TdRKm2JEi4tOCTA93hZw-Ls/edit?tab=t.0"

# ID администратора (замените на свой Telegram ID)
# Чтобы узнать свой ID: напишите @userinfobot в Telegram
ADMIN_USER_ID = 1019677560  # ID администратора бота

# ID группы, где бот работает
ADMIN_GROUP_ID = -5135562876  # ID группы, где только администратор может обновлять прайс и просматривать его


def is_admin(user_id: int) -> bool:
    """Проверяет, является ли пользователь администратором"""
    return user_id == ADMIN_USER_ID


def can_access_admin_features(update: Update) -> bool:
    """Проверяет, может ли пользователь использовать функции администратора (обновление прайса, просмотр)
    
    Кнопки видны ТОЛЬКО администратору, независимо от того, в группе он или в приватном чате.
    Группа с ADMIN_GROUP_ID используется только для удобства, но не влияет на доступ.
    """
    user_id = update.effective_user.id if update.effective_user else None
    
    if not user_id:
        return False
    
    # Только администратор может видеть и использовать административные функции
    # Проверка по ID пользователя, не по группе
    return is_admin(user_id)


class DeliveryCalculator:
    """Класс для расчёта стоимости доставки"""
    
    def __init__(self):
        """
        Автоматически загружает актуальный прайс-лист из Google Docs.
        
        При каждом создании экземпляра калькулятора данные загружаются заново,
        что гарантирует использование самых свежих цен.
        
        ОЖИДАНИЕ: содержимое документа — валидный JSON с тем же форматом,
        который раньше был в файле prices.json.
        """
        self.prices: Dict[str, Any] = {}
        self._load_prices_from_google_doc()

    def _build_export_url(self) -> str:
        """
        Формирует ссылку экспорта Google Docs в виде обычного текста.
        Документ должен содержать валидный JSON.
        """
        base = GOOGLE_DOC_URL.split("/edit")[0]
        export_url = f"{base}/export?format=txt"
        return export_url

    def _load_prices_from_google_doc(self) -> None:
        """Загружает прайс-лист из Google Sheets или Google Docs (автоматически актуальные данные)"""
        # Сначала пробуем загрузить из Google Sheets (рекомендуется)
        if GOOGLE_SHEET_ID and GOOGLE_SHEET_ID.strip():
            try:
                logger.info(f"🔍 Пробуем загрузить из Google Sheets (ID: {GOOGLE_SHEET_ID})")
                self._try_load_from_google_sheets()
                logger.info("✅ Успешно загружено из Google Sheets")
                return
            except Exception as sheet_error:
                logger.warning(f"⚠️ Не удалось загрузить из Google Sheets: {sheet_error}", exc_info=True)
                logger.info("Пробуем загрузить из Google Docs...")
        else:
            logger.info("⚠️ GOOGLE_SHEET_ID не задан, пробуем Google Docs")
        
        # Если не получилось, пробуем Google Docs
        try:
            self._try_load_from_google_doc()
            return
        except Exception as doc_error:
            logger.warning(f"Не удалось загрузить из Google Docs: {doc_error}")
            logger.warning("Используем fallback на локальный файл prices.json")
        
        # Если ничего не сработало, используем fallback на локальный файл
        try:
            with open("prices.json", 'r', encoding='utf-8') as f:
                self.prices = json.load(f)
            logger.warning("⚠️ Используется локальный файл prices.json (данные могут быть устаревшими)")
            if 'moscow_ring_road_km' in self.prices:
                logger.warning(f"Цена за МКАД из файла: {self.prices['moscow_ring_road_km']} руб/км")
        except FileNotFoundError:
            logger.error("Локальный файл prices.json также не найден")
            raise Exception("Не удалось загрузить прайс ни из Google Sheets, ни из Google Docs, ни из локального файла")
    
    def _try_load_from_google_sheets(self) -> None:
        """Пробует загрузить данные из Google Sheets (JSON разбит по строкам в колонке A)"""
        import time
        timestamp = int(time.time() * 1000)
        
        # Используем CSV формат для загрузки данных
        url = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/export?format=csv&gid={GOOGLE_SHEET_GID}&_={timestamp}"
        logger.info(f"Пробуем загрузить из Google Sheets: {url}")
        
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            })
            response.raise_for_status()
            
            csv_text = response.text
            logger.info(f"Получен CSV от Google Sheets (длина: {len(csv_text)} символов)")
            
            # Парсим CSV и собираем JSON из колонки A
            import csv
            import io
            
            csv_reader = csv.reader(io.StringIO(csv_text))
            json_lines = []
            
            for row in csv_reader:
                if row and len(row) > 0:
                    # Берем первую колонку (A)
                    cell_value = row[0].strip()
                    # Игнорируем пустые строки и комментарии (начинающиеся с //)
                    if cell_value and not cell_value.startswith('//'):
                        json_lines.append(cell_value)
            
            # Собираем все строки в один JSON
            json_text = '\n'.join(json_lines)
            logger.info(f"Собрано {len(json_lines)} строк JSON из Google Sheets")
            logger.info(f"JSON начинается со: {json_text[:200]}...")
            
            # Пробуем распарсить JSON
            try:
                self.prices = json.loads(json_text)
            except json.JSONDecodeError as first_error:
                # Если не удалось распарсить, пробуем добавить кавычки вокруг ключей
                # Это нужно, если Google Sheets удалил кавычки при вставке
                logger.warning(f"Первая попытка парсинга не удалась: {first_error}")
                logger.info("Пробуем добавить кавычки вокруг ключей...")
                
                import re
                # Добавляем кавычки вокруг ключей (слова перед двоеточием)
                # Паттерн: ключ: значение -> "ключ": значение
                fixed_json = re.sub(r'(\w+)(\s*):(\s*)', r'"\1"\2:\3', json_text)
                # Убираем лишние кавычки вокруг чисел и булевых значений
                fixed_json = re.sub(r':\s*"(\d+)"', r': \1', fixed_json)
                fixed_json = re.sub(r':\s*"(true|false)"', r': \1', fixed_json)
                fixed_json = re.sub(r':\s*"(null)"', r': \1', fixed_json)
                
                try:
                    self.prices = json.loads(fixed_json)
                    logger.info("✅ JSON успешно распарсен после добавления кавычек")
                except json.JSONDecodeError as second_error:
                    logger.error(f"❌ Ошибка парсинга JSON из Google Sheets: {second_error}")
                    logger.error(f"Проблемный JSON (первые 500 символов): {json_text[:500]}")
                    raise ValueError(
                        f"Ошибка парсинга JSON из Google Sheets: {second_error.msg}. "
                        "Убедитесь, что JSON правильно разбит по строкам в колонке A."
                    )
                logger.info(f"✅ Прайс-лист успешно загружен из Google Sheets")
                logger.info(f"Загружено ключей: {len(self.prices)}")
                
                # Логируем все ключевые цены для проверки
                if 'moscow_ring_road_km' in self.prices:
                    logger.info(f"✅ Цена за МКАД: {self.prices['moscow_ring_road_km']} руб/км")
                if 'carrying_from_parking' in self.prices:
                    logger.info(f"✅ Пронос от парковки: {self.prices['carrying_from_parking']} руб")
                if 'delivery' in self.prices:
                    delivery = self.prices['delivery']
                    logger.info(f"✅ Доставка: до 1м³={delivery.get('up_to_1m3')}, 1-5м³={delivery.get('1_to_5m3')}, 5-10м³={delivery.get('5_to_10m3')}, 10-18м³={delivery.get('10_to_18m3')}")
                if 'lifting' in self.prices:
                    lifting = self.prices['lifting']
                    logger.info(f"✅ Подъем (примеры): диван до 2м={lifting.get('sofa_non_disassembled_up_to_2m', {}).get('price_per_place', 'N/A')}, кресло={lifting.get('armchair', {}).get('price_per_place', 'N/A')}")
            except json.JSONDecodeError as json_err:
                logger.error(f"❌ Ошибка парсинга JSON из Google Sheets: {json_err}")
                logger.error(f"Проблемный JSON (первые 500 символов): {json_text[:500]}")
                raise ValueError(
                    f"Ошибка парсинга JSON из Google Sheets: {json_err.msg}. "
                    "Убедитесь, что JSON правильно разбит по строкам в колонке A."
                )
    
    def _try_load_from_google_doc(self) -> None:
        """Пробует загрузить данные из Google Docs (ожидается чистый JSON)"""
        import time
        timestamp = int(time.time() * 1000)
        
        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            # Пробуем формат txt
            url = f"{GOOGLE_DOC_URL.split('/edit')[0]}/export?format=txt&_={timestamp}"
            logger.info(f"Пробуем загрузить из Google Docs: {url}")
            
            response = client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache"
            })
            response.raise_for_status()
            
            raw_text = response.text.strip().lstrip("\ufeff")
            logger.info(f"Получен ответ от Google Docs (длина: {len(raw_text)} символов)")
            logger.info(f"Первые 200 символов: {raw_text[:200]}")
            
            # Умный поиск JSON в тексте - пробуем несколько стратегий
            json_text = None
            json_parsed = None
            
            # Стратегия 1: Ищем самый большой JSON объект (от первой { до последней })
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            
            if start != -1 and end != -1 and end > start:
                candidate = raw_text[start : end + 1]
                try:
                    json_parsed = json.loads(candidate)
                    json_text = candidate
                    logger.info(f"✅ Стратегия 1: Найден JSON объект (длина: {len(json_text)} символов)")
                except json.JSONDecodeError:
                    logger.debug("Стратегия 1: JSON объект найден, но невалидный")
            
            # Стратегия 2: Если не сработало, ищем все JSON объекты и пробуем каждый
            if json_parsed is None:
                import re
                # Ищем все возможные JSON объекты в тексте
                json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                matches = re.finditer(json_pattern, raw_text, re.DOTALL)
                
                for match in matches:
                    candidate = match.group(0)
                    try:
                        parsed = json.loads(candidate)
                        # Проверяем, что это похоже на наш прайс (есть ключевые поля)
                        if isinstance(parsed, dict) and ('delivery' in parsed or 'moscow_ring_road_km' in parsed):
                            json_parsed = parsed
                            json_text = candidate
                            logger.info(f"✅ Стратегия 2: Найден валидный JSON объект (длина: {len(json_text)} символов)")
                            break
                    except json.JSONDecodeError:
                        continue
            
            # Стратегия 3: Если все еще не нашли, пробуем найти JSON в многострочном формате
            if json_parsed is None:
                # Ищем блок, который начинается с { и заканчивается на }
                lines = raw_text.split('\n')
                json_lines = []
                in_json = False
                brace_count = 0
                
                for line in lines:
                    if '{' in line:
                        in_json = True
                        brace_count += line.count('{') - line.count('}')
                    if in_json:
                        json_lines.append(line)
                        brace_count += line.count('{') - line.count('}')
                        if brace_count == 0 and '}' in line:
                            candidate = '\n'.join(json_lines)
                            try:
                                parsed = json.loads(candidate)
                                if isinstance(parsed, dict):
                                    json_parsed = parsed
                                    json_text = candidate
                                    logger.info(f"✅ Стратегия 3: Найден JSON в многострочном формате")
                                    break
                            except json.JSONDecodeError:
                                pass
                            json_lines = []
                            in_json = False
                            brace_count = 0
            
            # Если все стратегии не сработали
            if json_parsed is None:
                logger.error(f"❌ Не удалось найти валидный JSON в документе!")
                logger.error(f"Документ должен содержать валидный JSON (можно с дополнительным текстом вокруг)")
                logger.error(f"Полученный текст начинается так: {raw_text[:1000]}")
                raise ValueError(
                    "Не удалось найти валидный JSON в документе Google Docs. "
                    "Убедитесь, что документ содержит валидный JSON (можно с дополнительным текстом). "
                    "Пример: скопируйте содержимое файла prices.json в документ Google Docs."
                )
            
            # Используем найденный JSON
            self.prices = json_parsed
            logger.info(f"✅ Прайс-лист успешно загружен из Google Docs")
            logger.info(f"Загружено ключей: {len(self.prices)}")
            if 'moscow_ring_road_km' in self.prices:
                logger.info(f"✅ Цена за МКАД: {self.prices['moscow_ring_road_km']} руб/км")
            else:
                logger.warning(f"⚠️ Ключ 'moscow_ring_road_km' не найден в загруженных данных")
    
    
    def calculate(self, data: Dict[str, Any]) -> tuple[int, str]:
        """
        Рассчитывает стоимость доставки
        
        Returns:
            tuple: (итоговая стоимость, детализированный расчёт)
        """
        total = 0
        details = []
        
        # Проверяем, это расчет только хранения или расчет доставки
        # Используем явный флаг storage_only, если он есть, иначе проверяем отсутствие адреса
        is_storage_only = data.get('storage_only', False) or not data.get('address')
        
        # 1. Доставка (только если это не расчет только хранения)
        if not is_storage_only:
            volume = data.get('volume', '')
            delivery_base = 0
            try:
                if volume == 'up_to_1m3':
                    delivery_base = self.prices.get('delivery', {}).get('up_to_1m3', 0)
                elif volume == '1_to_5m3':
                    delivery_base = self.prices.get('delivery', {}).get('1_to_5m3', 0)
                elif volume == '5_to_10m3':
                    delivery_base = self.prices.get('delivery', {}).get('5_to_10m3', 0)
                elif volume == '10_to_18m3':
                    delivery_base = self.prices.get('delivery', {}).get('10_to_18m3', 0)
            except (KeyError, TypeError) as e:
                logger.error(f"Ошибка при получении цены доставки: {e}")
                delivery_base = 0
            
            # Подсчитываем количество адресов доставки
            # Базовый адрес (1) + дополнительные адреса из extra_routes
            extra_routes = data.get('extra_routes', [])
            total_addresses = 1 + len(extra_routes)
            
            # Если есть дополнительные адреса, считаем их как дополнительные адреса доставки
            # Первый адрес - базовая цена, остальные - по тарифу additional_address
            if total_addresses > 1:
                # Определяем цену за дополнительный адрес в зависимости от объема
                if volume == 'up_to_1m3':
                    additional_address_price = self.prices.get('additional_address', {}).get('up_to_1m3', 0)
                elif volume in ['1_to_5m3', '5_to_10m3', '10_to_18m3']:
                    additional_address_price = self.prices.get('additional_address', {}).get('1_to_5m3', 0)
                else:
                    additional_address_price = 0
                
                # Базовая доставка + дополнительные адреса
                additional_addresses_count = total_addresses - 1
                delivery_total = delivery_base + (additional_address_price * additional_addresses_count)
                total += delivery_total
                
                address = data.get('address', 'moscow')
                if address == 'mo':
                    details.append(f"Доставка до двери дома ({total_addresses} адр.): {format_currency(delivery_base)} + {additional_addresses_count} × {format_currency(additional_address_price)} = {format_currency(delivery_total)} руб")
                else:
                    details.append(f"Доставка до подъезда ({total_addresses} адр.): {format_currency(delivery_base)} + {additional_addresses_count} × {format_currency(additional_address_price)} = {format_currency(delivery_total)} руб")
            else:
                # Только один адрес - базовая цена
                total += delivery_base
                address = data.get('address', 'moscow')
                if address == 'mo':
                    details.append(f"Доставка до двери дома: {format_currency(delivery_base)} руб")
                else:
                    details.append(f"Доставка до подъезда: {format_currency(delivery_base)} руб")
        
        # 2. Доплата за МКАД (только если это не расчет только хранения)
        if not is_storage_only and data.get('address') == 'mo' and data.get('distance_mkad', 0) > 0:
            try:
                mkad_price = self.prices.get('moscow_ring_road_km', 0)
                mkad_cost = data['distance_mkad'] * mkad_price
                total += mkad_cost
                details.append(f"Доплата за МКАД ({data['distance_mkad']} км): {format_currency(mkad_cost)} руб")
            except (KeyError, TypeError) as e:
                logger.error(f"Ошибка при расчёте доплаты за МКАД: {e}")
        
        # 3. Подъём на этаж (для всех мебели в списке) - только если это не расчет только хранения
        # Определяем furniture_list заранее, чтобы она была доступна для циклов
        furniture_list = data.get('furniture_list', []) if not is_storage_only else []
        furniture_names = {
            'sofa_non_disassembled_up_to_2m': 'Диван до 2м (неразборный)',
            'sofa_non_disassembled_up_to_3m': 'Диван до 3м (неразборный)',
            'sofa_corner': 'Диван угловой',
            'sofa_disassembled_1_seat': 'Диван разборный',
            'bed_disassembled_1_seat': 'Кровать разборная',
            'dining_table': 'Стол обеденный',
            'desk_console': 'Стол письменный/Консоль',
            'shelf_up_to_1m': 'Стеллаж до 1м',
            'shelf_up_to_2m': 'Стеллаж до 2м',
            'chest_tv_stand_up_to_60kg': 'Комод/ТВ-тумба до 60кг',
            'chest_tv_stand_up_to_90kg': 'Комод/ТВ-тумба до 90кг',
            'chest_tv_stand_up_to_120kg': 'Комод/ТВ-тумба до 120кг',
            'chest_tv_stand_up_to_150kg': 'Комод/ТВ-тумба до 150кг',
            'armchair': 'Кресло',
            'chair_semi_armchair_pouf_coffee_table': 'Стул/Полукресло/Пуф/Столик',
            'ottoman_bedside_table_bench': 'Банкетка/Тумба/Скамья',
            'mirror_picture_up_to_1m': 'Зеркало/Картина до 1м',
            'mirror_picture_over_1m': 'Зеркало/Картина более 1м',
            'decor_light': 'Декор/Свет'
        }
        
        for furniture_item in furniture_list:
            if furniture_item.get('lifting_needed'):
                floor = furniture_item.get('floor', 0)
                furniture_type = furniture_item.get('furniture_type')
                places_count = furniture_item.get('places_count', 1)
                has_elevator = furniture_item.get('elevator', False)
                elevator_places = furniture_item.get('elevator_places', 0)
                stairs_places = furniture_item.get('stairs_places', 0)
                
                # Обработка цокольного этажа/подвала (по тарифу 2-го этажа)
                if floor <= 0:
                    floor = 2
                    floor_text = "цокольный этаж/подвал (по тарифу 2-го этажа)"
                else:
                    floor_text = f"{floor} этаж"
                
                if furniture_type in self.prices.get('lifting', {}):
                    lifting_info = self.prices['lifting'][furniture_type]
                    # price_per_place в прайсе - это уже цена за одно место
                    price_per_place = lifting_info.get('price_per_place', 0)
                    
                    # Проверяем смешанный способ подъема
                    if elevator_places > 0 and stairs_places > 0:
                        # Смешанный способ: часть по лифту, часть по лестнице
                        elevator_cost = int(price_per_place * elevator_places)
                        stairs_cost = int(price_per_place * floor * stairs_places)
                        lifting_cost = elevator_cost + stairs_cost
                        
                        details.append(f"Подъём {furniture_names.get(furniture_type, 'Мебель').lower()} на {floor_text}: {elevator_places} места по лифту × {format_currency(int(price_per_place))} + {stairs_places} места по лестнице × {format_currency(int(price_per_place))} × {floor} = {format_currency(lifting_cost)} руб")
                    elif has_elevator or (elevator_places == places_count and places_count > 0):
                        # Все на лифте: цена за место × места (не умножается на этаж)
                        lifting_cost = int(price_per_place * places_count)
                        elevator_text = "с лифтом"
                        if places_count > 1:
                            details.append(f"Подъём {furniture_names.get(furniture_type, 'Мебель').lower()} на {floor_text} ({elevator_text}): {places_count} места × {format_currency(int(price_per_place))} = {format_currency(lifting_cost)} руб")
                        else:
                            details.append(f"Подъём {furniture_names.get(furniture_type, 'Мебель').lower()} на {floor_text} ({elevator_text}): {format_currency(int(price_per_place))} руб")
                    else:
                        # Все по лестнице: цена за место × этаж × места
                        lifting_cost = int(price_per_place * floor * places_count)
                        elevator_text = "без лифта"
                        if places_count > 1:
                            details.append(f"Подъём {furniture_names.get(furniture_type, 'Мебель').lower()} на {floor_text} ({elevator_text}): {places_count} места × {format_currency(int(price_per_place))} × {floor} = {format_currency(lifting_cost)} руб")
                        else:
                            details.append(f"Подъём {furniture_names.get(furniture_type, 'Мебель').lower()} на {floor_text} ({elevator_text}): {format_currency(int(price_per_place))} × {floor} = {format_currency(lifting_cost)} руб")
                    
                    total += lifting_cost
        
        # 4. Сборка (для всех мебели в списке) - только если это не расчет только хранения
        if not is_storage_only:
            assembly_mapping = {
                'sofa_non_disassembled_up_to_2m': ('sofa_straight', 'Дивана прямого'),
                'sofa_non_disassembled_up_to_3m': ('sofa_straight', 'Дивана прямого'),
                'sofa_corner': ('sofa_corner', 'Дивана углового'),
                'sofa_disassembled_1_seat': ('sofa_straight', 'Дивана прямого'),
                'bed_disassembled_1_seat': ('bed', 'Кровати'),
                'shelf_up_to_1m': ('shelf_up_to_1m', 'Стеллажа до 1м'),
                'shelf_up_to_2m': ('shelf_up_to_2m', 'Стеллажа до 2м'),
                'chest_tv_stand_up_to_60kg': ('tv_stand_chest', 'Комода/ТВ-тумбы'),
                'chest_tv_stand_up_to_90kg': ('tv_stand_chest', 'Комода/ТВ-тумбы'),
                'chest_tv_stand_up_to_120kg': ('tv_stand_chest', 'Комода/ТВ-тумбы'),
                'chest_tv_stand_up_to_150kg': ('tv_stand_chest', 'Комода/ТВ-тумбы'),
                'desk_console': ('table_console_desk_floor_lamp', 'Стола/Консоли'),
                'dining_table': ('dining_table', 'Стола обеденного'),
                'dining_table_marble_up_to_60kg': ('dining_table_marble', 'Стола обеденного мрамор'),
                'dining_table_marble_up_to_90kg': ('dining_table_marble', 'Стола обеденного мрамор'),
                'dining_table_marble_up_to_120kg': ('dining_table_marble', 'Стола обеденного мрамор'),
                'dining_table_marble_up_to_150kg': ('dining_table_marble', 'Стола обеденного мрамор'),
                'dining_table_marble_up_to_200kg': ('dining_table_marble', 'Стола обеденного мрамор'),
                'armchair': ('bench_armchair_chair', 'Кресла'),
                'chair_semi_armchair_pouf_coffee_table': ('bench_armchair_chair', 'Стула/Кресла'),
                'ottoman_bedside_table_bench': ('bench_armchair_chair', 'Банкетки/Скамьи'),
                'mirror_picture_up_to_1m': ('mirror_picture_up_to_1m', 'Зеркала/Картины до 1м'),
                'mirror_picture_over_1m': ('mirror_picture_over_1m', 'Зеркала/Картины более 1м')
            }
        
            for furniture_item in furniture_list:
                if furniture_item.get('assembly_needed'):
                    furniture_type = furniture_item.get('furniture_type')
                    if furniture_type in assembly_mapping:
                        assembly_key, assembly_name = assembly_mapping[furniture_type]
                        if assembly_key in self.prices.get('assembly', {}):
                            assembly_cost = self.prices['assembly'][assembly_key]
                            total += assembly_cost
                            details.append(f"Сборка {assembly_name.lower()}: {format_currency(assembly_cost)} руб")
                    
                    # Сборка по процентам (шкаф-сервант, корпусная мебель)
                    if furniture_item.get('furniture_cost') and furniture_item.get('furniture_cost') > 0:
                        furniture_cost = furniture_item.get('furniture_cost', 0)
                        if furniture_type in ['cabinet_sideboard', 'cabinet_furniture']:
                            percent = self.prices['assembly'].get('cabinet_sideboard_percent', 7)
                            assembly_cost = int(furniture_cost * percent / 100)
                            total += assembly_cost
                            furniture_type_name = 'шкафа-серванта' if furniture_type == 'cabinet_sideboard' else 'корпусной мебели'
                            details.append(f"Сборка {furniture_type_name} (7% от стоимости): {format_currency(assembly_cost)} руб")
        
        # 5. Пронос от парковки - используем количество проносов, указанное пользователем
        if not is_storage_only:
            carrying_times = data.get('carrying_times', 0)
            if carrying_times > 0:
                carrying_price = self.prices.get('carrying_from_parking', 0)
                carrying_cost = carrying_price * carrying_times
                total += carrying_cost
                if carrying_times == 1:
                    details.append(f"Пронос от парковки: {carrying_times} раз ({format_currency(carrying_cost)} руб)")
                elif carrying_times in [2, 3, 4]:
                    details.append(f"Пронос от парковки: {carrying_times} раза ({format_currency(carrying_cost)} руб)")
                else:
                    details.append(f"Пронос от парковки: {carrying_times} раз ({format_currency(carrying_cost)} руб)")
        
        # 6. Дополнительные точки доставки (километраж) - только если это не расчет только хранения
        if not is_storage_only:
            # 6. Дополнительные маршруты (километраж только для МО)
            extra_routes = data.get('extra_routes', [])
            if extra_routes:
                # Используем цену за километр МКАД из прайса
                price_per_km = self.prices.get('moscow_ring_road_km', 70)
                
                for route in extra_routes:
                    distance = route.get('distance', 0)
                    from_point = route.get('from', 'moscow')
                    to_point = route.get('to', 'mo')
                    from_text = "Москва" if from_point == 'moscow' else "МО"
                    to_text = "Москва" if to_point == 'moscow' else "МО"
                    
                    # Километраж учитывается только если маршрут связан с МО (не Москва → Москва)
                    if from_point == 'mo' or to_point == 'mo':
                        route_cost = price_per_km * distance
                        total += route_cost
                        details.append(f"Доп. маршрут {from_text} → {to_text} ({distance} км): {format_currency(route_cost)} руб ({format_currency(price_per_km)} руб/км)")
                    # Москва → Москва: километраж не учитывается (уже учтен в additional_address)
        
        # 7. Время ожидания выгрузки - только если это не расчет только хранения
        if not is_storage_only:
            waiting_time = data.get('waiting_time', '')
            if waiting_time == '15_to_30_min':
                waiting_cost = self.prices.get('waiting_time', {}).get('15_to_30_min', 0)
                total += waiting_cost
                if waiting_cost > 0:
                    details.append(f"Время ожидания (15-30 мин): {format_currency(waiting_cost)} руб")
            elif waiting_time == '30_min_to_1_hour':
                waiting_cost = self.prices.get('waiting_time', {}).get('30_min_to_1_hour', 0)
                if waiting_cost > 0:
                    total += waiting_cost
                    details.append(f"Время ожидания (30 мин - 1 час): {format_currency(waiting_cost)} руб")
        
        # 7. Дополнительные адреса доставки (старая логика - удалена, теперь учитывается в пункте 1)
        
        # 8. Выезд на сборку (если указан) - только если это не расчет только хранения
        if not is_storage_only and data.get('assembly_departure_needed'):
            departure_cost = self.prices.get('assembly_departure', {}).get('base', 0)
            if data.get('address') == 'mo' and data.get('distance_mkad', 0) > 0:
                departure_cost += data['distance_mkad'] * self.prices.get('assembly_departure', {}).get('mkad_km', 0)
            total += departure_cost
            details.append(f"Выезд на сборку: {format_currency(departure_cost)} руб")
        
        # 9. Хранение на складе (если указано)
        # Если это расчет только хранения - показываем хранение
        # Если это расчет доставки - показываем хранение только если оно было выбрано в процессе расчета доставки
        storage_days = data.get('storage_days', 0)
        storage_volume = data.get('storage_volume', 0)
        if storage_days > 0 and storage_volume > 0:
            # Если это расчет только хранения ИЛИ это расчет доставки с хранением (storage_needed был True)
            if is_storage_only or data.get('storage_needed', False):
                storage_price = self.prices.get('storage', {}).get('per_day_per_m3', 0)
                storage_cost = int(storage_days * storage_volume * storage_price)
                if storage_cost > 0:
                    total += storage_cost
                    details.append(f"Хранение на складе ({storage_days} дн., {storage_volume} м³): {format_currency(storage_cost)} руб ({format_currency(storage_price)} руб/день за 1м³)")
        
        # 10. Упаковка пупырчатой плёнкой (если указано)
        packaging_meters = data.get('packaging_meters', 0)
        if packaging_meters > 0:
            packaging_cost = packaging_meters * self.prices.get('packaging', {}).get('bubble_wrap_per_meter', 0)
            total += packaging_cost
            details.append(f"Упаковка пупырчатой плёнкой ({packaging_meters} м): {format_currency(packaging_cost)} руб")
        
        # 11. Скидка для партнёров (если указана)
        if data.get('partner_discount'):
            discount_percent = self.prices.get('partner_discount', 30)
            discount_amount = int(total * discount_percent / 100)
            total -= discount_amount
            details.append(f"Скидка для партнёра ({discount_percent}%): -{format_currency(discount_amount)} руб")
        
        details_text = "\n".join(details)
        details_text += f"\n\n{'='*30}\nИТОГО: {format_currency(total)} руб"
        
        return total, details_text


# Функция для получения данных расчёта из context
def get_calculation_data(context: ContextTypes.DEFAULT_TYPE) -> Dict[str, Any]:
    """Получает данные расчёта из context.user_data"""
    if 'calculation_data' not in context.user_data:
        context.user_data['calculation_data'] = {
            'furniture_list': []
        }
    return context.user_data['calculation_data']


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Начало диалога - показывает главное меню с панелью управления.
    Всегда работает, независимо от состояния диалога.
    """
    return await show_main_menu(update, context)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Показывает главное меню (панель управления) с опциями:
    - Расчет доставки
    - Расчет хранения
    - Просмотр прайса
    """
    try:
        user_id = update.effective_user.id if update.effective_user else "unknown"
        logger.info(f"Показываем главное меню пользователю {user_id}")
        
        # Очищаем все данные пользователя для нового расчёта
        context.user_data.clear()
        calculation_data = get_calculation_data(context)
        calculation_data.clear()
        calculation_data['furniture_list'] = []
        
        user_id = update.effective_user.id if update.effective_user else None
        
        keyboard = [
            [InlineKeyboardButton("🚚 Расчёт доставки (Москва или МО)", callback_data="menu_delivery")],
            [InlineKeyboardButton("📦 Расчёт хранения на складе", callback_data="menu_storage")]
        ]
        
        # Кнопки "Просмотр прайса" и "Обновить данные" только для администратора
        # В группе эти кнопки видны только администратору, другие пользователи их не видят
        if can_access_admin_features(update):
            keyboard.append([InlineKeyboardButton("💰 Просмотр прайс-листа", callback_data="menu_view_prices")])
            keyboard.append([InlineKeyboardButton("🔁 Обновить данные Google Sheets", callback_data="menu_update_prices")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        message_text = (
            "🏠 <b>Панель управления</b>\n\n"
            "Выберите нужную опцию:"
        )
        
        # Обработка через message (команда /start)
        if update.message:
            try:
                await update.message.reply_text(
                    message_text,
                    reply_markup=reply_markup,
                    parse_mode='HTML'
                )
                logger.info(f"Главное меню отправлено пользователю {user_id} через update.message")
            except Exception as e:
                logger.error(f"Ошибка при отправке главного меню пользователю {user_id}: {e}", exc_info=True)
                try:
                    await update.message.reply_text(
                        "🏠 Панель управления\n\nВыберите нужную опцию:",
                        reply_markup=reply_markup
                    )
                except Exception as e2:
                    logger.error(f"Критическая ошибка при отправке сообщения: {e2}", exc_info=True)
        
        # Обработка через callback_query (из кнопки "Назад" или "Новый расчёт")
        elif update.callback_query:
            try:
                await update.callback_query.answer()
                # Пробуем обновить существующее сообщение
                try:
                    await update.callback_query.edit_message_text(
                        message_text,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )
                    logger.info(f"Главное меню обновлено для пользователя {user_id}")
                except Exception:
                    # Если не получилось обновить, отправляем новое
                    await update.callback_query.message.reply_text(
                        message_text,
                        reply_markup=reply_markup,
                        parse_mode='HTML'
                    )
                    logger.info(f"Новое главное меню отправлено пользователю {user_id}")
            except Exception as e:
                logger.error(f"Ошибка при обработке callback_query для пользователя {user_id}: {e}", exc_info=True)
        else:
            logger.warning(f"Неизвестный тип update для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Критическая ошибка в show_main_menu для пользователя {user_id}: {e}", exc_info=True)
        try:
            if update.message:
                await update.message.reply_text(
                    "❌ Произошла ошибка. Попробуйте позже или обратитесь к администратору."
                )
        except:
            pass
    
    return MAIN_MENU


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора опции из главного меню"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("main_menu_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        # Проверка прав для административных функций
        if query.data == "menu_view_prices" or query.data == "menu_update_prices":
            if not can_access_admin_features(update):
                await query.answer("❌ Доступ запрещен. Только администратор может использовать эту функцию.", show_alert=True)
                return MAIN_MENU
        
        await query.answer()
        
        if query.data == "menu_delivery":
            # Переход к расчету доставки
            return await ask_address(update, context)
        elif query.data == "menu_storage":
            # Переход к расчету хранения
            return await start_storage_calculation(update, context)
        elif query.data == "menu_view_prices":
            # Показ прайса (только для администратора)
            return await view_prices(update, context)
        elif query.data == "menu_update_prices":
            # Обновление прайса из Google Docs (только для администратора)
            return await update_prices_from_menu(update, context)
        else:
            return MAIN_MENU
    except Exception as e:
        logger.error(f"Ошибка в main_menu_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос адреса доставки"""
    keyboard = [
        [
            InlineKeyboardButton("Москва", callback_data="address_moscow"),
            InlineKeyboardButton("МО (за МКАД)", callback_data="address_mo")
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    message_text = "🏠 <b>Расчёт стоимости доставки</b>\n\nВыберите адрес доставки:"
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
        except Exception:
            await update.callback_query.message.reply_text(
                message_text,
                reply_markup=reply_markup,
                parse_mode='HTML'
            )
    else:
        await update.message.reply_text(
            message_text,
            reply_markup=reply_markup,
            parse_mode='HTML'
        )
    
    return ADDRESS


async def address_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора адреса"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("address_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        
        if query.data == "address_moscow":
            calculation_data['address'] = 'moscow'
            calculation_data['distance_mkad'] = 0
            await query.edit_message_text("✅ Адрес: Москва")
            return await ask_extra_point(update, context)
        else:  # address_mo
            calculation_data['address'] = 'mo'
            await query.edit_message_text(
                "✅ Адрес: МО (за МКАД)\n\n"
                "📏 Введите расстояние от МКАД в километрах (только число):"
            )
            return DISTANCE_MKAD
    except Exception as e:
        logger.error(f"Ошибка в address_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def distance_mkad_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода расстояния от МКАД"""
    try:
        if not update.message or not update.message.text:
            logger.error("distance_mkad_handler: update.message или text отсутствует")
            return ConversationHandler.END
        
        distance = int(update.message.text)
        if distance < 0:
            await update.message.reply_text("❌ Расстояние не может быть отрицательным. Введите число:")
            return DISTANCE_MKAD
        if distance > 500:
            await update.message.reply_text("❌ Расстояние слишком большое (максимум 500 км). Введите число:")
            return DISTANCE_MKAD
        
        calculation_data = get_calculation_data(context)
        calculation_data['distance_mkad'] = distance
        await update.message.reply_text(f"✅ Расстояние от МКАД: {distance} км")
        return await ask_extra_point(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return DISTANCE_MKAD
    except Exception as e:
        logger.error(f"Ошибка в distance_mkad_handler: {e}")
        if update.message:
            try:
                await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
            except:
                pass
        return ConversationHandler.END


async def ask_extra_point(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о дополнительной точке доставки"""
    calculation_data = get_calculation_data(context)
    address = calculation_data.get('address', 'moscow')
    
    # Инициализируем список маршрутов если его нет
    if 'extra_routes' not in calculation_data:
        calculation_data['extra_routes'] = []
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, добавить точку", callback_data="extra_point_yes"),
            InlineKeyboardButton("❌ Нет", callback_data="extra_point_no")
        ]
    ]
    
    if address == 'mo':
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_address")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📍 <b>Добавить дополнительную точку доставки?</b>"
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return EXTRA_POINT


async def extra_point_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ответа о дополнительной точке"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("extra_point_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        has_extra_point = (query.data == "extra_point_yes")
        
        if has_extra_point:
            await query.edit_message_text("✅ Добавляем точку")
            return await ask_extra_point_from(update, context)
        else:
            await query.edit_message_text("❌ Одна точка")
            if 'extra_routes' not in calculation_data:
                calculation_data['extra_routes'] = []
            return await ask_carrying(update, context)
    except Exception as e:
        logger.error(f"Ошибка в extra_point_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_extra_point_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос откуда (Москва/МО)"""
    keyboard = [
        [
            InlineKeyboardButton("Москва", callback_data="extra_from_moscow"),
            InlineKeyboardButton("МО", callback_data="extra_from_mo")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📍 <b>Откуда едем?</b>"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return EXTRA_POINT_FROM


async def extra_point_from_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора откуда"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("extra_point_from_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        from_point = 'moscow' if query.data == 'extra_from_moscow' else 'mo'
        calculation_data['current_route_from'] = from_point
        
        from_text = "Москва" if from_point == 'moscow' else "МО"
        await query.edit_message_text(f"📍 Откуда: {from_text}")
        
        return await ask_extra_point_to(update, context)
    except Exception as e:
        logger.error(f"Ошибка в extra_point_from_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_extra_point_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос куда (Москва/МО)"""
    keyboard = [
        [
            InlineKeyboardButton("Москва", callback_data="extra_to_moscow"),
            InlineKeyboardButton("МО", callback_data="extra_to_mo")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📍 <b>Куда едем?</b>"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return EXTRA_POINT_TO


async def extra_point_to_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора куда"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("extra_point_to_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        to_point = 'moscow' if query.data == 'extra_to_moscow' else 'mo'
        calculation_data['current_route_to'] = to_point
        
        from_point = calculation_data.get('current_route_from', 'moscow')
        from_text = "Москва" if from_point == 'moscow' else "МО"
        to_text = "Москва" if to_point == 'moscow' else "МО"
        
        await query.edit_message_text(f"📍 Маршрут: {from_text} → {to_text}")
        
        return await ask_extra_distance(update, context)
    except Exception as e:
        logger.error(f"Ошибка в extra_point_to_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_extra_distance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос расстояния между точками доставки (только для маршрутов с МО)"""
    calculation_data = get_calculation_data(context)
    from_point = calculation_data.get('current_route_from', 'moscow')
    to_point = calculation_data.get('current_route_to', 'mo')
    
    # Если маршрут Москва → Москва, километраж не нужен
    if from_point == 'moscow' and to_point == 'moscow':
        # Пропускаем ввод расстояния, сохраняем маршрут с distance = 0
        route = {
            'from': from_point,
            'to': to_point,
            'distance': 0
        }
        
        if 'extra_routes' not in calculation_data:
            calculation_data['extra_routes'] = []
        calculation_data['extra_routes'].append(route)
        
        from_text = "Москва"
        to_text = "Москва"
        
        if update.callback_query:
            try:
                await update.callback_query.message.reply_text(f"✅ Маршрут добавлен: {from_text} → {to_text} (без километража)")
            except:
                pass
        else:
            await update.message.reply_text(f"✅ Маршрут добавлен: {from_text} → {to_text} (без километража)")
        
        # Очищаем временные данные
        if 'current_route_from' in calculation_data:
            del calculation_data['current_route_from']
        if 'current_route_to' in calculation_data:
            del calculation_data['current_route_to']
        
        return await ask_extra_point_finish(update, context)
    
    # Для маршрутов с МО спрашиваем километраж
    text = "📏 <b>Какое расстояние между точками?</b>\n\nВведите расстояние в километрах (число):"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='HTML')
    else:
        await update.message.reply_text(text, parse_mode='HTML')
    
    return EXTRA_DISTANCE


async def extra_distance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода расстояния между точками"""
    try:
        distance = int(update.message.text)
        if distance < 0:
            await update.message.reply_text("❌ Расстояние не может быть отрицательным. Введите число:")
            return EXTRA_DISTANCE
        if distance > 1000:
            await update.message.reply_text("❌ Расстояние слишком большое (максимум 1000 км). Введите число:")
            return EXTRA_DISTANCE
        
        calculation_data = get_calculation_data(context)
        
        # Сохраняем маршрут в список
        from_point = calculation_data.get('current_route_from', 'moscow')
        to_point = calculation_data.get('current_route_to', 'mo')
        
        route = {
            'from': from_point,
            'to': to_point,
            'distance': distance
        }
        
        if 'extra_routes' not in calculation_data:
            calculation_data['extra_routes'] = []
        calculation_data['extra_routes'].append(route)
        
        from_text = "Москва" if from_point == 'moscow' else "МО"
        to_text = "Москва" if to_point == 'moscow' else "МО"
        await update.message.reply_text(f"✅ Маршрут добавлен: {from_text} → {to_text}, {distance} км")
        
        # Очищаем временные данные
        if 'current_route_from' in calculation_data:
            del calculation_data['current_route_from']
        if 'current_route_to' in calculation_data:
            del calculation_data['current_route_to']
        
        return await ask_extra_point_finish(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return EXTRA_DISTANCE
    except Exception as e:
        logger.error(f"Ошибка в extra_distance_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_extra_point_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос завершения добавления точек или продолжения"""
    calculation_data = get_calculation_data(context)
    routes = calculation_data.get('extra_routes', [])
    
    # Формируем список добавленных маршрутов
    routes_text = "\n".join([
        f"  • {('Москва' if r['from'] == 'moscow' else 'МО')} → {('Москва' if r['to'] == 'moscow' else 'МО')}, {r['distance']} км"
        for r in routes
    ])
    
    keyboard = [
        [
            InlineKeyboardButton("➕ Добавить еще", callback_data="extra_point_add_more"),
            InlineKeyboardButton("✅ Завершить", callback_data="extra_point_finish")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"📍 <b>Добавленные маршруты:</b>\n{routes_text}\n\nДобавить еще маршрут?"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return EXTRA_POINT_FINISH


async def extra_point_finish_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка завершения или продолжения добавления точек"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("extra_point_finish_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        if query.data == "extra_point_add_more":
            await query.edit_message_text("➕ Добавляем еще маршрут")
            return await ask_extra_point_from(update, context)
        else:  # extra_point_finish
            calculation_data = get_calculation_data(context)
            routes_count = len(calculation_data.get('extra_routes', []))
            await query.edit_message_text(f"✅ Добавлено маршрутов: {routes_count}")
            return await ask_carrying(update, context)
    except Exception as e:
        logger.error(f"Ошибка в extra_point_finish_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_carrying(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос про пронос мебели (разные формулировки для Москвы и МО)"""
    calculation_data = get_calculation_data(context)
    address = calculation_data.get('address', 'moscow')
    
    keyboard = [
        [
            InlineKeyboardButton("✅ Да", callback_data="carrying_yes"),
            InlineKeyboardButton("❌ Нет", callback_data="carrying_no")
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_address")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if address == 'moscow':
        text = "🚚 <b>Есть ли пронос мебели до подъезда через паркинг или двор?</b>"
    else:  # МО
        text = "🚚 <b>Возможна ли доставка до двери?</b>\n\n(Подъезд к двери подъезда/квартиры)"
    
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return CARRYING_QUESTION


async def carrying_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ответа на вопрос про пронос"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("carrying_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        address = calculation_data.get('address', 'moscow')
        has_carrying = (query.data == "carrying_yes")
        calculation_data['has_carrying'] = has_carrying
        
        carrying_text = "✅ Да" if has_carrying else "❌ Нет"
        await query.edit_message_text(f"{carrying_text}")
        
        # Для МО: если нет доставки до двери, спрашиваем расстояние до двери
        if address == 'mo' and not has_carrying:
            return await ask_door_distance(update, context)
        # Если есть пронос (для Москвы) или доставка до двери (для МО), спрашиваем количество проносов
        elif has_carrying:
            return await ask_carrying_times(update, context)
        else:
            # Нет проноса для Москвы - переходим к объему
            calculation_data['carrying_times'] = 0
            return await ask_volume(update, context)
    except Exception as e:
        logger.error(f"Ошибка в carrying_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_door_distance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос расстояния до двери (для МО, если нет доставки до двери)"""
    text = "📏 <b>Какое расстояние от места разгрузки до двери?</b>\n\nВведите расстояние в метрах (число):"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='HTML')
    else:
        await update.message.reply_text(text, parse_mode='HTML')
    
    return DOOR_DISTANCE


async def door_distance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода расстояния до двери"""
    try:
        distance = float(update.message.text)
        if distance < 0:
            await update.message.reply_text("❌ Расстояние не может быть отрицательным. Введите число:")
            return DOOR_DISTANCE
        if distance > 500:
            await update.message.reply_text("❌ Расстояние слишком большое (максимум 500 м). Введите число:")
            return DOOR_DISTANCE
        
        calculation_data = get_calculation_data(context)
        calculation_data['door_distance'] = distance
        
        # Если расстояние больше 15 метров, спрашиваем количество проносов
        if distance > 15:
            await update.message.reply_text(f"✅ Расстояние до двери: {int(distance)} м")
            return await ask_carrying_times(update, context)
        else:
            # Если расстояние 15 метров или меньше, пронос не нужен
            calculation_data['carrying_times'] = 0
            await update.message.reply_text(f"✅ Расстояние до двери: {int(distance)} м (пронос не требуется)")
            return await ask_volume(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число (можно с десятичной точкой, например: 20.5):")
        return DOOR_DISTANCE
    except Exception as e:
        logger.error(f"Ошибка в door_distance_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_carrying_times(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос количества проносов"""
    text = "📦 <b>Сколько проносов потребуется?</b>\n\nВведите количество проносов (число от 1 до 10):"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='HTML')
    elif update.message:
        await update.message.reply_text(text, parse_mode='HTML')
    
    return CARRYING_TIMES


async def carrying_times_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода количества проносов"""
    try:
        times = int(update.message.text)
        if times < 0:
            await update.message.reply_text("❌ Количество проносов не может быть отрицательным. Введите число:")
            return CARRYING_TIMES
        if times > 10:
            await update.message.reply_text("❌ Количество проносов слишком большое (максимум 10). Введите число:")
            return CARRYING_TIMES
        
        calculation_data = get_calculation_data(context)
        calculation_data['carrying_times'] = times
        
        await update.message.reply_text(f"✅ Количество проносов: {times}")
        return await ask_volume(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите целое число:")
        return CARRYING_TIMES
    except Exception as e:
        logger.error(f"Ошибка в carrying_times_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_volume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос объёма заказа"""
    keyboard = [
        [InlineKeyboardButton("До 1 м³", callback_data="volume_up_to_1m3")],
        [InlineKeyboardButton("1-5 м³", callback_data="volume_1_to_5m3")],
        [InlineKeyboardButton("5-10 м³", callback_data="volume_5_to_10m3")],
        [InlineKeyboardButton("10-18 м³", callback_data="volume_10_to_18m3")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_carrying")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📦 Выберите объём заказа:"
    if update.callback_query:
        await update.callback_query.answer()
        try:
            await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return VOLUME


async def volume_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора объёма"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("volume_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        volume_map = {
            "volume_up_to_1m3": ("До 1 м³", "up_to_1m3"),
            "volume_1_to_5m3": ("1-5 м³", "1_to_5m3"),
            "volume_5_to_10m3": ("5-10 м³", "5_to_10m3"),
            "volume_10_to_18m3": ("10-18 м³", "10_to_18m3")
        }
        
        if query.data not in volume_map:
            logger.error(f"volume_callback: неизвестный volume {query.data}")
            await query.answer("❌ Неизвестный объём. Используйте /start", show_alert=True)
            return ConversationHandler.END
        
        volume_text, volume_key = volume_map[query.data]
        calculation_data = get_calculation_data(context)
        calculation_data['volume'] = volume_key
        await query.edit_message_text(f"✅ Объём: {volume_text}")
        
        # После выбора объёма сразу переходим к выбору типа доставки (хранение убрано из потока доставки)
        return await ask_delivery_only(update, context)
    except Exception as e:
        logger.error(f"Ошибка в volume_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_delivery_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос: нужна только доставка?"""
    keyboard = [
        [
            InlineKeyboardButton("✅ Да, только доставка", callback_data="delivery_only_yes")
        ],
        [
            InlineKeyboardButton("❌ Нет, требуется подъём и сборка", callback_data="delivery_only_no")
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_volume")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    calculation_data = get_calculation_data(context)
    address = calculation_data.get('address', 'moscow')
    
    if address == 'mo':
        text = "🚚 <b>Требуется ли только доставка до двери дома?</b>\n\nВыберите тип доставки:"
    else:
        text = "🚚 <b>Требуется ли только доставка до подъезда?</b>\n\nВыберите тип доставки:"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return DELIVERY_ONLY


async def delivery_only_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора: только доставка или с подъёмом"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("delivery_only_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        
        if query.data == "delivery_only_yes":
            calculation_data['delivery_only'] = True
            calculation_data['furniture_list'] = []  # Нет мебели для подъёма
            calculation_data = get_calculation_data(context)
            address = calculation_data.get('address', 'moscow')
            if address == 'mo':
                await query.edit_message_text("✅ Только доставка до двери дома")
            else:
                await query.edit_message_text("✅ Только доставка до подъезда")
            # Пропускаем все вопросы про мебель, подъём и сборку
            # и сразу переходим к финальному расчёту
            return await calculate_final(update, context)
        else:
            calculation_data['delivery_only'] = False
            await query.edit_message_text("✅ Доставка с подъёмом и сборкой")
            # Для Москвы спрашиваем этаж и лифт один раз для всей мебели
            # Для МО будем спрашивать этаж для каждой мебели отдельно
            if calculation_data.get('address') == 'moscow':
                return await ask_moscow_floor(update, context)
            else:
                # Для МО сразу переходим к выбору мебели
                return await ask_furniture_type(update, context)
    except Exception as e:
        logger.error(f"Ошибка в delivery_only_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_moscow_floor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос этажа для Москвы (общий для всей мебели)"""
    text = "🏢 Введите номер этажа квартиры (только число):\n\n(Этот этаж будет использован для всей мебели)"
    if update.callback_query:
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)
    
    return MOSCOW_FLOOR


async def moscow_floor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода этажа для Москвы"""
    try:
        floor = int(update.message.text)
        if floor < 1:
            await update.message.reply_text("❌ Этаж должен быть не менее 1. Введите число:")
            return MOSCOW_FLOOR
        if floor > 200:
            await update.message.reply_text("❌ Этаж слишком большой (максимум 200). Введите число:")
            return MOSCOW_FLOOR
        
        calculation_data = get_calculation_data(context)
        calculation_data['default_floor'] = floor
        calculation_data['default_elevator'] = None  # Пока не задан
        
        await update.message.reply_text(f"✅ Этаж квартиры: {floor}\n(Будет использован для всей мебели)")
        return await ask_moscow_elevator(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return MOSCOW_FLOOR
    except Exception as e:
        logger.error(f"Ошибка в moscow_floor_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_moscow_elevator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о наличии лифта для Москвы (общий для всей мебели)"""
    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data="moscow_elevator_yes"),
            InlineKeyboardButton("Нет", callback_data="moscow_elevator_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🛗 Есть лифт в доме?\n\n(Это будет использовано для всей мебели)"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return MOSCOW_ELEVATOR


async def moscow_elevator_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора лифта для Москвы"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("moscow_elevator_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        has_elevator = (query.data == "moscow_elevator_yes")
        calculation_data['default_elevator'] = has_elevator
        
        elevator_text = "Да" if has_elevator else "Нет"
        await query.edit_message_text(f"✅ Лифт: {elevator_text}\n(Будет использован для всей мебели)")
        
        # Теперь переходим к выбору мебели
        return await ask_furniture_type(update, context)
    except Exception as e:
        logger.error(f"Ошибка в moscow_elevator_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_lifting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о необходимости подъёма для текущей мебели"""
    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data="lifting_yes"),
            InlineKeyboardButton("Нет", callback_data="lifting_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🚪 Нужен ли подъём на этаж для этой мебели?"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return LIFTING_NEEDED


async def lifting_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора подъёма"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("lifting_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        current_furniture = context.user_data.get('current_furniture', {})
        calculation_data = get_calculation_data(context)
        
        if query.data == "lifting_no":
            current_furniture['lifting_needed'] = False
            current_furniture['floor'] = 0
            current_furniture['elevator'] = False
            context.user_data['current_furniture'] = current_furniture
            await query.edit_message_text("✅ Подъём не требуется")
            return await ask_assembly(update, context)
        else:
            current_furniture['lifting_needed'] = True
            
            # Для Москвы используем сохраненные значения этажа
            if calculation_data.get('address') == 'moscow' and 'default_floor' in calculation_data:
                current_furniture['floor'] = calculation_data['default_floor']
                default_elevator = calculation_data.get('default_elevator', False)
                places_count = current_furniture.get('places_count', 1)
                
                # Если лифта нет, все места по лестнице
                if not default_elevator:
                    current_furniture['elevator'] = False
                    current_furniture['elevator_places'] = 0
                    current_furniture['stairs_places'] = places_count
                    context.user_data['current_furniture'] = current_furniture
                    floor_text = f"{current_furniture['floor']} этаж"
                    await query.edit_message_text(
                        f"✅ Подъём требуется\n"
                        f"🏢 Этаж: {floor_text}\n"
                        f"🛗 Без лифта\n"
                        f"(Используются общие параметры для Москвы)"
                    )
                    return await ask_assembly(update, context)
                
                # Если лифт есть и несколько мест, спрашиваем способ подъема
                if places_count > 1:
                    current_furniture['elevator'] = True  # Предварительно, будет уточнено
                    context.user_data['current_furniture'] = current_furniture
                    await query.edit_message_text("✅ Подъём требуется")
                    return await ask_lifting_method(update, context)
                else:
                    # Для одного места используем сохраненный лифт
                    current_furniture['elevator'] = default_elevator
                    current_furniture['elevator_places'] = 1 if default_elevator else 0
                    current_furniture['stairs_places'] = 0 if default_elevator else 1
                    context.user_data['current_furniture'] = current_furniture
                    floor_text = f"{current_furniture['floor']} этаж"
                    elevator_text = "с лифтом" if default_elevator else "без лифта"
                    await query.edit_message_text(
                        f"✅ Подъём требуется\n"
                        f"🏢 Этаж: {floor_text}\n"
                        f"🛗 {elevator_text.capitalize()}\n"
                        f"(Используются общие параметры для Москвы)"
                    )
                    return await ask_assembly(update, context)
            else:
                # Для МО спрашиваем этаж для каждой мебели отдельно
                context.user_data['current_furniture'] = current_furniture
                await query.edit_message_text("✅ Подъём требуется")
                return await ask_floor(update, context)
    except Exception as e:
        logger.error(f"Ошибка в lifting_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_floor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос этажа"""
    text = "🏢 Введите номер этажа (только число):"
    if update.callback_query:
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)
    
    return FLOOR


async def floor_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода этажа"""
    try:
        floor = int(update.message.text)
        if floor < 1:
            await update.message.reply_text("❌ Этаж должен быть не менее 1. Введите число:")
            return FLOOR
        if floor > 200:
            await update.message.reply_text("❌ Этаж слишком большой (максимум 200). Введите число:")
            return FLOOR
        
        current_furniture = context.user_data.get('current_furniture', {})
        current_furniture['floor'] = floor
        context.user_data['current_furniture'] = current_furniture
        
        await update.message.reply_text(f"✅ Этаж: {floor}")
        return await ask_elevator(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return FLOOR
    except Exception as e:
        logger.error(f"Ошибка в floor_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_elevator(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о наличии лифта (для МО)"""
    current_furniture = context.user_data.get('current_furniture', {})
    places_count = current_furniture.get('places_count', 1)
    
    # Всегда спрашиваем про лифт (для МО)
    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data="elevator_yes"),
            InlineKeyboardButton("Нет", callback_data="elevator_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🛗 Есть лифт?"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return ELEVATOR


async def ask_lifting_method(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос способа подъема (все на лифте или смешанный) - только если лифт есть"""
    current_furniture = context.user_data.get('current_furniture', {})
    places_count = current_furniture.get('places_count', 1)
    
    # Проверяем, есть ли лифт (из current_furniture или из calculation_data для Москвы)
    has_elevator = current_furniture.get('elevator', False)
    if not has_elevator:
        # Проверяем для Москвы
        calculation_data = get_calculation_data(context)
        if calculation_data.get('address') == 'moscow':
            has_elevator = calculation_data.get('default_elevator', False)
    
    # Если лифта нет, все места по лестнице
    if not has_elevator:
        current_furniture['elevator'] = False
        current_furniture['elevator_places'] = 0
        current_furniture['stairs_places'] = places_count
        context.user_data['current_furniture'] = current_furniture
        
        if update.callback_query:
            await update.callback_query.message.reply_text("✅ Без лифта - все места по лестнице")
        else:
            await update.message.reply_text("✅ Без лифта - все места по лестнице")
        
        return await ask_assembly(update, context)
    
    # Если лифт есть, спрашиваем способ подъема
    keyboard = [
        [
            InlineKeyboardButton("✅ Все на лифте", callback_data="lifting_method_all_elevator"),
            InlineKeyboardButton("🔄 Часть по лифту, часть по лестнице", callback_data="lifting_method_mixed")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = f"🛗 <b>Как поднимать мебель?</b>\n\nВсего мест: {places_count}"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
    
    return LIFTING_METHOD


async def lifting_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора способа подъема"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("lifting_method_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        current_furniture = context.user_data.get('current_furniture', {})
        places_count = current_furniture.get('places_count', 1)
        
        if query.data == "lifting_method_all_elevator":
            # Все на лифте
            current_furniture['elevator'] = True
            current_furniture['elevator_places'] = places_count
            current_furniture['stairs_places'] = 0
            context.user_data['current_furniture'] = current_furniture
            await query.edit_message_text("✅ Все на лифте")
            return await ask_assembly(update, context)
        else:
            # Смешанный способ
            await query.edit_message_text("🔄 Смешанный способ")
            return await ask_lifting_elevator_count(update, context)
    except Exception as e:
        logger.error(f"Ошибка в lifting_method_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_lifting_elevator_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос количества мест по лифту"""
    current_furniture = context.user_data.get('current_furniture', {})
    places_count = current_furniture.get('places_count', 1)
    
    text = f"🛗 <b>Сколько мест поднимать по лифту?</b>\n\nВсего мест: {places_count}\nВведите число от 1 до {places_count - 1}:"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='HTML')
    else:
        await update.message.reply_text(text, parse_mode='HTML')
    
    return LIFTING_ELEVATOR_COUNT


async def lifting_elevator_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода количества мест по лифту"""
    try:
        current_furniture = context.user_data.get('current_furniture', {})
        places_count = current_furniture.get('places_count', 1)
        
        elevator_count = int(update.message.text)
        
        if elevator_count < 1:
            await update.message.reply_text("❌ Количество мест по лифту должно быть не менее 1. Введите число:")
            return LIFTING_ELEVATOR_COUNT
        
        if elevator_count >= places_count:
            await update.message.reply_text(f"❌ Количество мест по лифту должно быть меньше общего количества мест ({places_count}). Введите число:")
            return LIFTING_ELEVATOR_COUNT
        
        current_furniture['elevator_places'] = elevator_count
        context.user_data['current_furniture'] = current_furniture
        
        stairs_count = places_count - elevator_count
        await update.message.reply_text(f"✅ По лифту: {elevator_count} мест\nОсталось по лестнице: {stairs_count} мест")
        
        return await ask_lifting_stairs_count(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return LIFTING_ELEVATOR_COUNT
    except Exception as e:
        logger.error(f"Ошибка в lifting_elevator_count_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_lifting_stairs_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос количества мест по лестнице"""
    current_furniture = context.user_data.get('current_furniture', {})
    places_count = current_furniture.get('places_count', 1)
    elevator_count = current_furniture.get('elevator_places', 0)
    stairs_count = places_count - elevator_count
    
    text = f"🪜 <b>Сколько мест поднимать по лестнице?</b>\n\nОсталось мест: {stairs_count}\nВведите число:"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='HTML')
    else:
        await update.message.reply_text(text, parse_mode='HTML')
    
    return LIFTING_STAIRS_COUNT


async def lifting_stairs_count_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода количества мест по лестнице"""
    try:
        current_furniture = context.user_data.get('current_furniture', {})
        places_count = current_furniture.get('places_count', 1)
        elevator_count = current_furniture.get('elevator_places', 0)
        expected_stairs = places_count - elevator_count
        
        stairs_count = int(update.message.text)
        
        if stairs_count != expected_stairs:
            await update.message.reply_text(f"❌ Количество мест по лестнице должно быть {expected_stairs}. Введите число:")
            return LIFTING_STAIRS_COUNT
        
        current_furniture['stairs_places'] = stairs_count
        current_furniture['elevator'] = False  # Смешанный способ
        context.user_data['current_furniture'] = current_furniture
        
        await update.message.reply_text(f"✅ По лестнице: {stairs_count} мест")
        
        return await ask_assembly(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return LIFTING_STAIRS_COUNT
    except Exception as e:
        logger.error(f"Ошибка в lifting_stairs_count_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def elevator_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора лифта (для МО)"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("elevator_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        current_furniture = context.user_data.get('current_furniture', {})
        has_elevator = (query.data == "elevator_yes")
        places_count = current_furniture.get('places_count', 1)
        
        current_furniture['elevator'] = has_elevator
        context.user_data['current_furniture'] = current_furniture
        
        elevator_text = "Да" if has_elevator else "Нет"
        await query.edit_message_text(f"✅ Лифт: {elevator_text}")
        
        # Если лифта нет, все места по лестнице
        if not has_elevator:
            current_furniture['elevator_places'] = 0
            current_furniture['stairs_places'] = places_count
            context.user_data['current_furniture'] = current_furniture
            return await ask_assembly(update, context)
        
        # Если лифт есть и несколько мест, спрашиваем способ подъема
        if places_count > 1:
            return await ask_lifting_method(update, context)
        else:
            # Для одного места с лифтом
            current_furniture['elevator_places'] = 1
            current_furniture['stairs_places'] = 0
            context.user_data['current_furniture'] = current_furniture
            return await ask_assembly(update, context)
    except Exception as e:
        logger.error(f"Ошибка в elevator_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_furniture_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос типа мебели"""
    # Создаём клавиатуру с основными категориями мебели
    keyboard = [
        [InlineKeyboardButton("Диваны", callback_data="furniture_category_sofa")],
        [InlineKeyboardButton("Кровати", callback_data="furniture_category_bed")],
        [InlineKeyboardButton("Столы", callback_data="furniture_category_table")],
        [InlineKeyboardButton("Стеллажи", callback_data="furniture_category_shelf")],
        [InlineKeyboardButton("Комоды/ТВ-тумбы", callback_data="furniture_category_chest")],
        [InlineKeyboardButton("Кресла/Стулья", callback_data="furniture_category_chair")],
        [InlineKeyboardButton("Зеркала/Картины", callback_data="furniture_category_mirror")],
        [InlineKeyboardButton("Другое", callback_data="furniture_category_other")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🛏️ Выберите категорию мебели:"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return FURNITURE_TYPE


async def furniture_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора типа мебели"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("furniture_type_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        # Маппинг категорий на конкретные типы мебели
        furniture_options = {
        "furniture_category_sofa": [
            ("Диван до 2м (неразборный)", "sofa_non_disassembled_up_to_2m"),
            ("Диван до 3м (неразборный)", "sofa_non_disassembled_up_to_3m"),
            ("Диван угловой", "sofa_corner"),
            ("Диван разборный (1 место)", "sofa_disassembled_1_seat")
        ],
        "furniture_category_bed": [
            ("Кровать разборная", "bed_disassembled_1_seat")
        ],
        "furniture_category_table": [
            ("Стол обеденный", "dining_table"),
            ("Стол обеденный мрамор до 60кг", "dining_table_marble_up_to_60kg"),
            ("Стол обеденный мрамор до 90кг", "dining_table_marble_up_to_90kg"),
            ("Стол обеденный мрамор до 120кг", "dining_table_marble_up_to_120kg"),
            ("Стол обеденный мрамор до 150кг", "dining_table_marble_up_to_150kg"),
            ("Стол обеденный мрамор до 200кг", "dining_table_marble_up_to_200kg"),
            ("Стол письменный/Консоль", "desk_console")
        ],
        "furniture_category_shelf": [
            ("Стеллаж до 1м", "shelf_up_to_1m"),
            ("Стеллаж до 2м", "shelf_up_to_2m")
        ],
        "furniture_category_chest": [
            ("Комод/ТВ-тумба до 60кг", "chest_tv_stand_up_to_60kg"),
            ("Комод/ТВ-тумба до 90кг", "chest_tv_stand_up_to_90kg"),
            ("Комод/ТВ-тумба до 120кг", "chest_tv_stand_up_to_120kg"),
            ("Комод/ТВ-тумба до 150кг", "chest_tv_stand_up_to_150kg")
        ],
        "furniture_category_chair": [
            ("Кресло", "armchair"),
            ("Стул/Полукресло/Пуф/Столик журнальный", "chair_semi_armchair_pouf_coffee_table"),
            ("Банкетка/Тумба прикроватная/Скамья", "ottoman_bedside_table_bench")
        ],
        "furniture_category_mirror": [
            ("Зеркало/Картина до 1м", "mirror_picture_up_to_1m"),
            ("Зеркало/Картина более 1м", "mirror_picture_over_1m")
        ],
        "furniture_category_other": [
            ("Декор/Свет", "decor_light")
        ]
    }
    
        if query.data in furniture_options:
            # Показываем варианты для выбранной категории
            options = furniture_options[query.data]
            keyboard = []
            for name, key in options:
                keyboard.append([InlineKeyboardButton(name, callback_data=f"furniture_{key}")])
            keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_to_furniture_category")])
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                "🛏️ Выберите конкретный тип мебели:",
                reply_markup=reply_markup
            )
            return FURNITURE_TYPE
        elif query.data.startswith("furniture_"):
            # Выбран конкретный тип мебели
            furniture_key = query.data.replace("furniture_", "")
            
            # Находим название для отображения
            furniture_name = "Мебель"
            for category, options in furniture_options.items():
                for name, key in options:
                    if key == furniture_key:
                        furniture_name = name
                        break
            
            # Сохраняем текущую мебель во временную переменную
            context.user_data['current_furniture'] = {
                'furniture_type': furniture_key,
                'furniture_name': furniture_name
            }
            
            # Показываем информацию о количестве мест
            return await ask_places_confirmation(update, context)
        
        return FURNITURE_TYPE
    except Exception as e:
        logger.error(f"Ошибка в furniture_type_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_places_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос подтверждения количества мест"""
    current_furniture = context.user_data.get('current_furniture', {})
    furniture_type = current_furniture.get('furniture_type')
    furniture_name = current_furniture.get('furniture_name', 'Мебель')
    
    # Загружаем информацию о мебели из прайса (актуальные данные из Google Docs)
    try:
        calculator = DeliveryCalculator()
    except Exception as e:
        logger.error(f"Ошибка при загрузке прайса в ask_places_confirmation: {e}")
        # Пробуем использовать локальный файл как fallback
        try:
            with open("prices.json", 'r', encoding='utf-8') as f:
                prices = json.load(f)
            calculator = type('Calculator', (), {'prices': prices})()  # Создаём объект с prices
        except Exception as fallback_error:
            logger.error(f"Не удалось загрузить даже fallback файл: {fallback_error}")
            # Если и fallback не сработал, пропускаем эту проверку
            return await ask_assembly(update, context)
    
    if furniture_type in calculator.prices.get('lifting', {}):
        lifting_info = calculator.prices['lifting'][furniture_type]
        places_count = lifting_info.get('places_count', 1)
        # price_per_place в прайсе - это уже цена за одно место
        price_per_place = lifting_info.get('price_per_place', 0)
        hint = lifting_info.get('hint', '')
        # Убираем "обычно" и другие подсказки из hint
        if hint:
            hint = hint.replace('обычно', '').replace('Обычно', '').replace('(обычно', '').replace('обычно)', '').strip()
            hint = hint.replace('  ', ' ').strip()
            # Убираем скобки если hint состоит только из скобок
            if hint.startswith('(') and hint.endswith(')'):
                hint = hint[1:-1].strip()
        
        if places_count > 1:
            if hint:
                text = (
                    f"🛏️ <b>{furniture_name}</b>\n\n"
                    f"📦 {hint}\n\n"
                    f"📊 <b>Стандартно:</b> {places_count} места (цена за место: {format_currency(int(price_per_place))} руб)\n\n"
                    f"Подтвердить количество мест?"
                )
            else:
                text = (
                    f"🛏️ <b>{furniture_name}</b>\n\n"
                    f"📊 <b>Стандартно:</b> {places_count} места (цена за место: {format_currency(int(price_per_place))} руб)\n\n"
                    f"Подтвердить количество мест?"
                )
        else:
            if hint:
                text = (
                    f"🛏️ <b>{furniture_name}</b>\n\n"
                    f"📦 {hint}\n\n"
                    f"📊 <b>Стандартно:</b> 1 место (цена: {format_currency(int(price_per_place))} руб)\n\n"
                    f"Подтвердить количество мест?"
                )
            else:
                text = (
                    f"🛏️ <b>{furniture_name}</b>\n\n"
                    f"📊 <b>Стандартно:</b> 1 место (цена: {format_currency(int(price_per_place))} руб)\n\n"
                    f"Подтвердить количество мест?"
                )
        
        keyboard = [
            [
                InlineKeyboardButton(f"✅ Да, {places_count} места" if places_count > 1 else "✅ Да, 1 место", 
                                   callback_data="places_confirm_yes"),
                InlineKeyboardButton("✏️ Указать другое", callback_data="places_confirm_no")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if update.callback_query:
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        
        return PLACES_CONFIRM
    
    return await ask_assembly(update, context)


async def places_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка подтверждения количества мест"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("places_confirm_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        current_furniture = context.user_data.get('current_furniture', {})
        furniture_type = current_furniture.get('furniture_type')
        
        try:
            calculator = DeliveryCalculator()
        except Exception as e:
            logger.error(f"Ошибка при загрузке прайса в places_confirm_callback: {e}")
            # Пробуем использовать локальный файл как fallback
            try:
                with open("prices.json", 'r', encoding='utf-8') as f:
                    prices = json.load(f)
                calculator = type('Calculator', (), {'prices': prices})()  # Создаём объект с prices
            except Exception as fallback_error:
                logger.error(f"Не удалось загрузить даже fallback файл: {fallback_error}")
                # Если и fallback не сработал, используем значение по умолчанию
                default_places = 1
                current_furniture['places_count'] = default_places
                context.user_data['current_furniture'] = current_furniture
                await query.edit_message_text(f"✅ Количество мест: {default_places}")
                return await ask_lifting(update, context)
        
        if furniture_type in calculator.prices.get('lifting', {}):
            lifting_info = calculator.prices['lifting'][furniture_type]
            default_places = lifting_info.get('places_count', 1)
        else:
            default_places = 1
        
        if query.data == "places_confirm_yes":
            # Используем стандартное количество мест
            current_furniture['places_count'] = default_places
            context.user_data['current_furniture'] = current_furniture
            await query.edit_message_text(f"✅ Количество мест: {default_places}")
            return await ask_lifting(update, context)
        else:
            # Нужно ввести своё количество
            await query.edit_message_text(
                f"✏️ Введите количество мест (число):"
            )
            return PLACES_INPUT
    except Exception as e:
        logger.error(f"Ошибка в places_confirm_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def places_input_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода количества мест"""
    try:
        places_count = int(update.message.text)
        if places_count < 1:
            await update.message.reply_text("❌ Количество мест должно быть не менее 1. Введите число:")
            return PLACES_INPUT
        if places_count > 100:
            await update.message.reply_text("❌ Количество мест слишком большое (максимум 100). Введите число:")
            return PLACES_INPUT
        
        current_furniture = context.user_data.get('current_furniture', {})
        current_furniture['places_count'] = places_count
        context.user_data['current_furniture'] = current_furniture
        
        await update.message.reply_text(f"✅ Количество мест: {places_count}")
        return await ask_lifting(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return PLACES_INPUT
    except Exception as e:
        logger.error(f"Ошибка в places_input_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_assembly(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о необходимости сборки (или автоматическая сборка)"""
    current_furniture = context.user_data.get('current_furniture', {})
    furniture_type = current_furniture.get('furniture_type')
    
    # Проверяем, всегда ли разобран этот тип мебели (актуальные данные из Google Docs)
    try:
        calculator = DeliveryCalculator()
    except Exception as e:
        logger.error(f"Ошибка при загрузке прайса в ask_assembly: {e}")
        # Пробуем использовать локальный файл как fallback
        try:
            with open("prices.json", 'r', encoding='utf-8') as f:
                prices = json.load(f)
            calculator = type('Calculator', (), {'prices': prices})()  # Создаём объект с prices
        except Exception as fallback_error:
            logger.error(f"Не удалось загрузить даже fallback файл: {fallback_error}")
            # Если и fallback не сработал, продолжаем без автоматической сборки
            calculator = type('Calculator', (), {'prices': {}})()
    
    # Всегда спрашиваем про сборку (не автоматически)
    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data="assembly_yes"),
            InlineKeyboardButton("Нет", callback_data="assembly_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "🔧 Нужна ли сборка мебели?"
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return ASSEMBLY_NEEDED


async def assembly_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора сборки"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("assembly_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        current_furniture = context.user_data.get('current_furniture', {})
        current_furniture['assembly_needed'] = (query.data == "assembly_yes")
        context.user_data['current_furniture'] = current_furniture
        
        assembly_text = "Да" if current_furniture['assembly_needed'] else "Нет"
        await query.edit_message_text(f"✅ Сборка: {assembly_text}")
        
        # Добавляем мебель в список
        calculation_data = get_calculation_data(context)
        if 'furniture_list' not in calculation_data:
            calculation_data['furniture_list'] = []
        calculation_data['furniture_list'].append(current_furniture.copy())
        
        # Очищаем текущую мебель
        context.user_data['current_furniture'] = {}
        
        # Спрашиваем: добавить ещё мебель или завершить
        return await ask_add_more_furniture(update, context)
    except Exception as e:
        logger.error(f"Ошибка в assembly_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_add_more_furniture(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о добавлении ещё мебели"""
    keyboard = [
        [
            InlineKeyboardButton("➕ Добавить ещё мебель", callback_data="add_more_yes"),
            InlineKeyboardButton("✅ Завершить расчёт", callback_data="add_more_no")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    calculation_data = get_calculation_data(context)
    furniture_count = len(calculation_data.get('furniture_list', []))
    text = f"📦 Добавлено мебели: {furniture_count}\n\nДобавить ещё мебель или завершить расчёт?"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.message.reply_text(text, reply_markup=reply_markup)
    
    return ADD_MORE_FURNITURE


async def add_more_furniture_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора: добавить ещё мебель или завершить"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("add_more_furniture_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        await query.answer()
        
        if query.data == "add_more_yes":
            await query.edit_message_text("✅ Добавление новой мебели...")
            return await ask_furniture_type(update, context)
        else:
            await query.edit_message_text("✅ Завершение расчёта...")
            # Хранение уже было запрошено ранее, переходим к финальному расчёту
            return await calculate_final(update, context)
    except Exception as e:
        logger.error(f"Ошибка в add_more_furniture_callback: {e}")
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_storage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос о необходимости хранения на складе"""
    logger.info("ask_storage: запрашиваем информацию о хранении")
    keyboard = [
        [
            InlineKeyboardButton("Да", callback_data="storage_yes"),
            InlineKeyboardButton("Нет", callback_data="storage_no")
        ],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_volume")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "📦 Нужно ответственное хранение на складе?\n\n(350 руб/день за 1м³ объёма)"
    
    try:
        if update.callback_query:
            # Если это callback_query, отвечаем на него и отправляем новое сообщение
            await update.callback_query.answer()
            await update.callback_query.message.reply_text(text, reply_markup=reply_markup)
            logger.info("ask_storage: сообщение отправлено через callback_query")
        elif update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)
            logger.info("ask_storage: сообщение отправлено через message")
        else:
            logger.error("ask_storage: неизвестный тип update")
            raise ValueError("Неизвестный тип update в ask_storage")
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения в ask_storage: {e}", exc_info=True)
        raise
    
    logger.info(f"ask_storage: возвращаем состояние STORAGE_NEEDED = {STORAGE_NEEDED}")
    return STORAGE_NEEDED


async def storage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора хранения"""
    try:
        query = update.callback_query
        if not query or not query.data:
            logger.error("storage_callback: query или query.data отсутствует")
            return ConversationHandler.END
        
        logger.info(f"storage_callback: получен callback_data = {query.data}")
        await query.answer()
        
        calculation_data = get_calculation_data(context)
        
        if query.data == "storage_no":
            calculation_data['storage_days'] = 0
            calculation_data['storage_volume'] = 0
            await query.edit_message_text("✅ Хранение не требуется")
            logger.info("storage_callback: хранение не требуется, переходим к выбору типа доставки")
            # После решения по хранению переходим к выбору: только доставка или с подъёмом
            return await ask_delivery_only(update, context)
        else:
            calculation_data['storage_needed'] = True
            await query.edit_message_text("✅ Хранение требуется")
            logger.info("storage_callback: хранение требуется, запрашиваем количество дней")
            return await ask_storage_days(update, context)
    except Exception as e:
        logger.error(f"Ошибка в storage_callback: {e}", exc_info=True)
        if update.callback_query:
            try:
                await update.callback_query.answer("❌ Произошла ошибка. Используйте /start", show_alert=True)
            except:
                pass
        return ConversationHandler.END


async def ask_storage_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос количества дней хранения"""
    text = "📅 Введите количество дней хранения (число):"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)
    
    return STORAGE_DAYS


async def storage_days_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода количества дней"""
    try:
        days = int(update.message.text)
        if days < 1:
            await update.message.reply_text("❌ Количество дней должно быть не менее 1. Введите число:")
            return STORAGE_DAYS
        if days > 3650:  # 10 лет
            await update.message.reply_text("❌ Количество дней слишком большое (максимум 3650 дней). Введите число:")
            return STORAGE_DAYS
        
        calculation_data = get_calculation_data(context)
        calculation_data['storage_days'] = days
        await update.message.reply_text(f"✅ Количество дней: {days}")
        return await ask_storage_volume(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число:")
        return STORAGE_DAYS
    except Exception as e:
        logger.error(f"Ошибка в storage_days_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def ask_storage_volume(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запрос объёма мебели для хранения"""
    text = "📦 Введите объём мебели в м³ (число, например: 2.5):"
    
    if update.callback_query:
        await update.callback_query.message.reply_text(text)
    else:
        await update.message.reply_text(text)
    
    return STORAGE_VOLUME


async def storage_volume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка ввода объёма для хранения"""
    try:
        volume = float(update.message.text)
        if volume <= 0:
            await update.message.reply_text("❌ Объём должен быть больше 0. Введите число:")
            return STORAGE_VOLUME
        if volume > 100:
            await update.message.reply_text("❌ Объём слишком большой (максимум 100 м³). Введите число:")
            return STORAGE_VOLUME
        
        calculation_data = get_calculation_data(context)
        calculation_data['storage_volume'] = volume
        
        await update.message.reply_text(f"✅ Объём мебели: {volume} м³")
        
        # Проверяем, это расчет ТОЛЬКО хранения или хранение в рамках расчета доставки
        is_storage_only = calculation_data.get('storage_only', False)
        
        if is_storage_only:
            # Это расчет ТОЛЬКО хранения - сразу переходим к финальному расчету (без доставки)
            return await calculate_final(update, context)
        else:
            # Это расчет доставки с хранением - переходим к выбору типа доставки
            return await ask_delivery_only(update, context)
    except ValueError:
        await update.message.reply_text("❌ Пожалуйста, введите число (можно с десятичной точкой, например: 2.5):")
        return STORAGE_VOLUME
    except Exception as e:
        logger.error(f"Ошибка в storage_volume_handler: {e}")
        await update.message.reply_text("❌ Произошла ошибка. Попробуйте снова или используйте /start")
        return ConversationHandler.END


async def calculate_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Финальный расчёт (пронос рассчитывается автоматически)"""
    try:
        # Пронос рассчитывается автоматически в DeliveryCalculator по объёму
        # Не нужно спрашивать пользователя
        
        # Выполняем расчёт (каждый раз берём актуальные данные из Google Sheets)
        calculation_data = get_calculation_data(context)
        try:
            calculator = DeliveryCalculator()
            # Логируем загруженные цены для проверки
            if 'moscow_ring_road_km' in calculator.prices:
                logger.info(f"✅ Используется цена за МКАД для расчёта: {calculator.prices['moscow_ring_road_km']} руб/км")
        except Exception as e:
            logger.error(f"Ошибка при загрузке прайса для расчёта: {e}")
            # Пробуем использовать локальный файл как fallback
            try:
                with open("prices.json", 'r', encoding='utf-8') as f:
                    prices_data = json.load(f)
                # Создаём калькулятор с fallback данными
                calculator = DeliveryCalculator.__new__(DeliveryCalculator)
                calculator.prices = prices_data
                logger.warning("Используется fallback файл prices.json для расчёта")
            except Exception as fallback_error:
                logger.error(f"Не удалось загрузить даже fallback файл: {fallback_error}")
                error_text = "❌ Ошибка при загрузке данных для расчёта. Попробуйте позже или используйте /start"
                if update.callback_query:
                    await update.callback_query.message.reply_text(error_text)
                else:
                    await update.message.reply_text(error_text)
                return ConversationHandler.END
        
        total, details = calculator.calculate(calculation_data)
        
        # Отправляем результат
        if update.callback_query:
            await update.callback_query.message.reply_text(
                f"💰 <b>ДЕТАЛИЗИРОВАННЫЙ РАСЧЁТ</b>\n\n{details}",
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(
                f"💰 <b>ДЕТАЛИЗИРОВАННЫЙ РАСЧЁТ</b>\n\n{details}",
                parse_mode='HTML'
            )
        
        # Предлагаем новый расчёт
        keyboard = [
            [InlineKeyboardButton("🔄 Новый расчёт", callback_data="new_calculation")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        if update.callback_query:
            await update.callback_query.message.reply_text(
                "Нажмите кнопку для нового расчёта:",
                reply_markup=reply_markup
            )
        else:
            await update.message.reply_text(
                "Нажмите кнопку для нового расчёта:",
                reply_markup=reply_markup
            )
        
        return CALCULATION
    except Exception as e:
        logger.error(f"Ошибка в calculate_final: {e}")
        error_text = "❌ Произошла ошибка при расчёте. Попробуйте снова или используйте /start для начала нового расчёта."
        if update.callback_query:
            await update.callback_query.message.reply_text(error_text)
        else:
            await update.message.reply_text(error_text)
        return ConversationHandler.END


async def new_calculation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало нового расчёта"""
    query = update.callback_query
    await query.answer()
    
    return await start(update, context)


async def update_prices_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Ручное обновление прайса из Google Docs.
    Только для администратора.
    """
    user_id = update.effective_user.id if update.effective_user else None
    
    if not user_id or not is_admin(user_id):
        await update.message.reply_text("❌ Доступ запрещен. Только администратор может обновлять прайс.")
        return
    
    try:
        DeliveryCalculator()
        context.application.bot_data['prices_last_update'] = datetime.now()
        await update.message.reply_text(
            "✅ Тарифы успешно обновлены из Google Sheets.\n"
            "Все новые расчёты будут использовать актуальные данные."
        )
    except Exception as e:
        logger.error(f"Ошибка при обновлении прайса по команде /update_prices: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Не удалось загрузить прайс из Google Sheets.\n"
            "Проверьте, что таблица доступна и содержит корректный JSON."
        )


async def update_prices_from_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обновление прайса по нажатию кнопки в главном меню.
    Только для администратора.
    Сначала показывает текущий прайс-лист, затем обновляет и показывает изменения.
    """
    query = update.callback_query
    user_id = update.effective_user.id if update.effective_user else None
    
    if not user_id or not is_admin(user_id):
        await query.answer("❌ Доступ запрещен. Только администратор может обновлять прайс.", show_alert=True)
        return MAIN_MENU
    
    await query.answer("📋 Загружаю текущий прайс-лист...")
    
    try:
        # Сначала получаем ТЕКУЩИЕ данные для показа
        try:
            current_calculator = DeliveryCalculator()
            current_prices = current_calculator.prices.copy()
            logger.info("Получены текущие цены для показа")
        except Exception as e:
            logger.warning(f"Не удалось получить текущие цены: {e}")
            current_prices = {}
        
        # Получаем СТАРЫЕ сохраненные цены для сравнения (если есть)
        old_prices_for_comparison = context.application.bot_data.get('last_prices', None)
        
        # Если старых цен нет, используем текущие как базу для сравнения
        if old_prices_for_comparison is None:
            old_prices_for_comparison = current_prices.copy()
            logger.info("Старых цен нет, используем текущие как базу для сравнения")
        
        # Показываем текущий прайс-лист
        current_prices_text = format_prices_list(current_prices)
        await query.message.reply_text(
            f"📋 <b>ТЕКУЩИЙ ПРАЙС-ЛИСТ</b>\n\n{current_prices_text}",
            parse_mode='HTML'
        )
        
        # Небольшая задержка перед обновлением (чтобы дать время Google Sheets обновиться)
        import asyncio
        await asyncio.sleep(2)  # Увеличена задержка до 2 секунд
        await query.message.reply_text("🔄 Обновляю данные из Google Sheets...")
        
        # Загружаем НОВЫЕ данные из Google Sheets
        new_calculator = DeliveryCalculator()
        new_prices = new_calculator.prices.copy()
        context.application.bot_data['prices_last_update'] = datetime.now()
        
        # Сравниваем СТАРЫЕ сохраненные цены с новыми
        # Это позволит увидеть изменения, даже если данные уже обновились в Google Sheets
        changes = compare_prices(old_prices_for_comparison, new_prices)
        
        # Сохраняем новые цены как старые для следующего сравнения
        context.application.bot_data['last_prices'] = new_prices.copy()
        if changes:
            changes_text = format_price_changes(changes)
            await query.message.reply_text(
                f"✅ <b>ДАННЫЕ ОБНОВЛЕНЫ</b>\n\n<b>Изменения:</b>\n{changes_text}\n\n"
                "Все новые расчёты будут использовать актуальные данные.",
                parse_mode='HTML'
            )
        else:
            await query.message.reply_text(
                "✅ <b>Данные обновлены</b>\n\nИзменений не обнаружено.",
                parse_mode='HTML'
            )
        
        # Возвращаемся к главному меню
        return await show_main_menu(update, context)
    except Exception as e:
        logger.error(f"Ошибка при обновлении прайса в главном меню: {e}", exc_info=True)
        await query.message.reply_text(
            "❌ <b>Не удалось загрузить прайс из Google Sheets</b>\n\n"
            "Проверьте, что таблица доступна и содержит корректный JSON.\n\n"
            "Используется резервный файл prices.json (данные могут быть устаревшими).",
            parse_mode='HTML'
        )
        # Возвращаемся к главному меню даже при ошибке
        return await show_main_menu(update, context)


async def update_prices_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обновление прайса по нажатию кнопки (из финального расчёта).
    Работает так же, как /update_prices, но через callback_query.
    """
    query = update.callback_query
    try:
        await query.answer("🔄 Загружаю данные из Google Docs...")
        DeliveryCalculator()
        context.application.bot_data['prices_last_update'] = datetime.now()
        await query.message.reply_text(
            "✅ <b>Тарифы обновлены из Google Docs</b>\n\n"
            "Следующие расчёты будут использовать новые данные.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка при обновлении прайса по кнопке: {e}", exc_info=True)
        await query.message.reply_text(
            "❌ <b>Не удалось загрузить прайс из Google Docs</b>\n\n"
            "Проверьте, что документ доступен и содержит корректный JSON.\n\n"
            "Используется резервный файл prices.json (данные могут быть устаревшими).",
            parse_mode='HTML'
        )
    # Остаёмся в состоянии CALCULATION
    return CALCULATION


async def update_prices_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Обновление прайса по нажатию кнопки в начальном меню.
    После успешного обновления возвращаемся к начальному меню.
    """
    query = update.callback_query
    await query.answer("🔄 Загружаю данные из Google Docs...")
    
    try:
        # Пробуем загрузить данные из Google Docs
        calculator = DeliveryCalculator()
        context.application.bot_data['prices_last_update'] = datetime.now()
        
        # Показываем успешное сообщение
        await query.message.reply_text(
            "✅ <b>Тарифы успешно обновлены из Google Docs</b>\n\n"
            "Все новые расчёты будут использовать актуальные данные.\n\n"
            "Теперь выберите адрес доставки:",
            parse_mode='HTML'
        )
        
        # Возвращаемся к начальному меню
        return await start(update, context)
    except Exception as e:
        logger.error(f"Ошибка при обновлении прайса в начальном меню: {e}", exc_info=True)
        await query.message.reply_text(
            "❌ <b>Не удалось загрузить прайс из Google Docs</b>\n\n"
            "Проверьте, что документ доступен и содержит корректный JSON.\n\n"
            "Используется резервный файл prices.json (данные могут быть устаревшими).\n\n"
            "Вы можете продолжить работу с текущими данными:",
            parse_mode='HTML'
        )
        # Возвращаемся к начальному меню даже при ошибке
        return await start(update, context)

async def start_storage_calculation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало отдельного расчёта хранения"""
    try:
        # Очищаем данные
        context.user_data.clear()
        calculation_data = get_calculation_data(context)
        calculation_data.clear()
        calculation_data['furniture_list'] = []
        calculation_data['storage_only'] = True  # Флаг что это расчет ТОЛЬКО хранения (без доставки)
        calculation_data['delivery_only'] = False  # Не доставка, а хранение
        
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "📦 <b>Расчёт стоимости хранения</b>\n\n" \
               "📅 Введите количество дней хранения (число):"
        
        if update.callback_query:
            await update.callback_query.answer()
            try:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
            except Exception:
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        
        return STORAGE_DAYS
    except Exception as e:
        logger.error(f"Ошибка в start_storage_calculation: {e}")
        return ConversationHandler.END


async def view_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показывает текущий прайс-лист (только для администратора)"""
    try:
        # Проверка прав доступа
        if not can_access_admin_features(update):
            user_id = update.effective_user.id if update.effective_user else "unknown"
            logger.warning(f"Попытка просмотра прайса пользователем {user_id} без прав доступа")
            if update.callback_query:
                await update.callback_query.answer("❌ Доступ запрещен. Только администратор может просматривать прайс.", show_alert=True)
            else:
                await update.message.reply_text("❌ Доступ запрещен. Только администратор может просматривать прайс.")
            return MAIN_MENU
        
        try:
            calculator = DeliveryCalculator()
            prices = calculator.prices
        except Exception as e:
            logger.error(f"Ошибка при загрузке прайса для просмотра: {e}")
            try:
                with open("prices.json", 'r', encoding='utf-8') as f:
                    prices = json.load(f)
            except Exception:
                text = "❌ Не удалось загрузить прайс-лист."
                if update.callback_query:
                    await update.callback_query.answer()
                    await update.callback_query.message.reply_text(text)
                else:
                    await update.message.reply_text(text)
                return MAIN_MENU
        
        # Формируем текст с прайсом (краткая версия основных позиций)
        text_parts = ["💰 <b>ТЕКУЩИЙ ПРАЙС-ЛИСТ</b>\n"]
        
        # Доставка
        if 'delivery' in prices:
            text_parts.append("\n<b>🚚 Доставка:</b>")
            delivery = prices['delivery']
            if 'up_to_1m3' in delivery:
                text_parts.append(f"  До 1 м³: {format_currency(delivery['up_to_1m3'])} руб")
            if '1_to_5m3' in delivery:
                text_parts.append(f"  1-5 м³: {format_currency(delivery['1_to_5m3'])} руб")
            if '5_to_10m3' in delivery:
                text_parts.append(f"  5-10 м³: {format_currency(delivery['5_to_10m3'])} руб")
            if '10_to_18m3' in delivery:
                text_parts.append(f"  10-18 м³: {format_currency(delivery['10_to_18m3'])} руб")
        
        # Хранение
        if 'storage' in prices and 'per_day_per_m3' in prices['storage']:
            text_parts.append(f"\n<b>📦 Хранение:</b>")
            text_parts.append(f"  {format_currency(prices['storage']['per_day_per_m3'])} руб/день за 1м³")
        
        # МКАД
        if 'moscow_ring_road_km' in prices:
            text_parts.append(f"\n<b>📍 МКАД:</b> {format_currency(prices['moscow_ring_road_km'])} руб/км")
        
        text_parts.append("\n<i>Для просмотра полного прайса используйте расчёт доставки.</i>")
        
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад", callback_data="back_to_main_menu")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "\n".join(text_parts)
        
        if update.callback_query:
            await update.callback_query.answer()
            try:
                await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode='HTML')
            except Exception:
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')
        
        return VIEW_PRICES
    except Exception as e:
        logger.error(f"Ошибка в view_prices: {e}")
        return MAIN_MENU


async def back_to_main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Назад' - возврат в главное меню"""
    return await show_main_menu(update, context)


async def back_to_address_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Назад' - возврат к выбору адреса"""
    return await ask_address(update, context)


async def back_to_carrying_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Назад' - возврат к вопросу про пронос"""
    return await ask_carrying(update, context)


async def back_to_volume_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Назад' - возврат к выбору объема"""
    return await ask_volume(update, context)


async def back_to_delivery_only_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка кнопки 'Назад' - возврат к вопросу про тип доставки"""
    return await ask_delivery_only(update, context)


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена диалога"""
    calculation_data = get_calculation_data(context)
    calculation_data.clear()
    context.user_data.clear()
    await update.message.reply_text("❌ Расчёт отменён. Используйте /start для начала нового расчёта.")
    return ConversationHandler.END


def main():
    """Запуск бота"""
    # ВАЖНО: Замените 'YOUR_BOT_TOKEN' на токен вашего бота от @BotFather
    TOKEN = "1914439323:AAESG1BpQwwmVjiRUNGcZOxliI5G9URIiRI"
    
    if TOKEN == "YOUR_BOT_TOKEN":
        print("⚠️  ВНИМАНИЕ: Установите токен бота в переменной TOKEN в файле bot.py")
        print("   Получите токен у @BotFather в Telegram")
        return
    
    # Создаём приложение
    application = Application.builder().token(TOKEN).build()
    
    # Создаём обработчик диалога
    # conversation_timeout: автоматически завершает диалог через 30 минут неактивности
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],  # start как точка входа в диалог
        states={
            MAIN_MENU: [
                CallbackQueryHandler(main_menu_callback, pattern="^menu_")
            ],
            ADDRESS: [
                CallbackQueryHandler(address_callback, pattern="^address_"),
                CallbackQueryHandler(back_to_main_menu_callback, pattern="^back_to_main_menu$")
            ],
            DISTANCE_MKAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, distance_mkad_handler)],
            EXTRA_POINT: [
                CallbackQueryHandler(extra_point_callback, pattern="^extra_point_(yes|no)$"),
                CallbackQueryHandler(back_to_address_callback, pattern="^back_to_address$")
            ],
            EXTRA_POINT_FROM: [
                CallbackQueryHandler(extra_point_from_callback, pattern="^extra_from_"),
                CallbackQueryHandler(back_to_address_callback, pattern="^back_to_address$")
            ],
            EXTRA_POINT_TO: [
                CallbackQueryHandler(extra_point_to_callback, pattern="^extra_to_"),
                CallbackQueryHandler(back_to_address_callback, pattern="^back_to_address$")
            ],
            EXTRA_DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, extra_distance_handler)],
            EXTRA_POINT_FINISH: [
                CallbackQueryHandler(extra_point_finish_callback, pattern="^extra_point_(add_more|finish)$")
            ],
            CARRYING_QUESTION: [
                CallbackQueryHandler(carrying_callback, pattern="^carrying_"),
                CallbackQueryHandler(back_to_address_callback, pattern="^back_to_address$")
            ],
            DOOR_DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, door_distance_handler)],
            CARRYING_TIMES: [MessageHandler(filters.TEXT & ~filters.COMMAND, carrying_times_handler)],
            VOLUME: [
                CallbackQueryHandler(volume_callback, pattern="^volume_"),
                CallbackQueryHandler(back_to_carrying_callback, pattern="^back_to_carrying$")
            ],
            DELIVERY_ONLY: [
                CallbackQueryHandler(delivery_only_callback, pattern="^delivery_only_"),
                CallbackQueryHandler(back_to_volume_callback, pattern="^back_to_volume$")
            ],
            MOSCOW_FLOOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, moscow_floor_handler)],
            MOSCOW_ELEVATOR: [CallbackQueryHandler(moscow_elevator_callback, pattern="^moscow_elevator_")],
            LIFTING_NEEDED: [CallbackQueryHandler(lifting_callback, pattern="^lifting_")],
            FLOOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, floor_handler)],
            ELEVATOR: [CallbackQueryHandler(elevator_callback, pattern="^elevator_")],
            LIFTING_METHOD: [
                CallbackQueryHandler(lifting_method_callback, pattern="^lifting_method_")
            ],
            LIFTING_ELEVATOR_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lifting_elevator_count_handler)],
            LIFTING_STAIRS_COUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, lifting_stairs_count_handler)],
            FURNITURE_TYPE: [
                CallbackQueryHandler(furniture_type_callback, pattern="^furniture_"),
                CallbackQueryHandler(back_to_delivery_only_callback, pattern="^back_to_delivery_only$"),
                CallbackQueryHandler(ask_furniture_type, pattern="^back_to_furniture_category$")
            ],
            PLACES_CONFIRM: [CallbackQueryHandler(places_confirm_callback, pattern="^places_confirm_")],
            PLACES_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, places_input_handler)],
            ASSEMBLY_NEEDED: [CallbackQueryHandler(assembly_callback, pattern="^assembly_")],
            ADD_MORE_FURNITURE: [CallbackQueryHandler(add_more_furniture_callback, pattern="^add_more_")],
            STORAGE_NEEDED: [
                CallbackQueryHandler(storage_callback, pattern="^storage_"),
                CallbackQueryHandler(back_to_volume_callback, pattern="^back_to_volume$")
            ],
            STORAGE_DAYS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, storage_days_handler),
                CallbackQueryHandler(back_to_main_menu_callback, pattern="^back_to_main_menu$")
            ],
            STORAGE_VOLUME: [MessageHandler(filters.TEXT & ~filters.COMMAND, storage_volume_handler)],
            CALCULATION: [
                CallbackQueryHandler(new_calculation_callback, pattern="^new_calculation$")
            ],
            VIEW_PRICES: [
                CallbackQueryHandler(back_to_main_menu_callback, pattern="^back_to_main_menu$")
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CommandHandler("start", start)  # Позволяет перезапустить диалог командой /start в любой момент
        ],
        conversation_timeout=1800,  # 30 минут (в секундах)
        name="delivery_calculation",
        per_chat=True,
        per_user=True
    )
    
    application.add_handler(conv_handler)
    # Команда для ручного обновления прайса из Google Docs
    application.add_handler(CommandHandler("update_prices", update_prices_command))
    
    # Добавляем обработчик ошибок
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Обработчик ошибок с улучшенной обработкой различных типов ошибок"""
        import traceback
        from telegram.error import RetryAfter, TimedOut, NetworkError, Conflict
        
        error = context.error
        logger.error(f"Exception while handling an update: {error}", exc_info=error)
        
        # Обработка специфичных ошибок Telegram API
        if isinstance(error, RetryAfter):
            logger.warning(f"Rate limit exceeded. Retry after {error.retry_after} seconds")
            return
        elif isinstance(error, TimedOut):
            logger.warning("Request timed out")
            return
        elif isinstance(error, NetworkError):
            logger.warning(f"Network error: {error}")
            return
        elif isinstance(error, Conflict):
            logger.warning("Conflict: another bot instance may be running")
            return
        
        # Попытка отправить сообщение об ошибке пользователю
        if update and isinstance(update, Update):
            try:
                if update.effective_message:
                    await update.effective_message.reply_text(
                        "❌ Произошла ошибка. Попробуйте снова или используйте /start для начала нового расчёта."
                    )
                elif update.callback_query:
                    await update.callback_query.answer(
                        "❌ Произошла ошибка. Используйте /start для начала нового расчёта.",
                        show_alert=True
                    )
            except Exception as e:
                logger.error(f"Failed to send error message to user: {e}")
    
    application.add_error_handler(error_handler)
    
    # Запускаем бота
    logger.info("Бот запущен...")
    logger.info("Ожидание команд...")
    print("Bot started...")
    print("Waiting for commands...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == '__main__':
    main()

