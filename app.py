from flask import Flask, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
import math
from datetime import datetime, timedelta, timezone
import re
from markupsafe import Markup, escape

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'


@app.template_filter('highlight_mentions')
def highlight_mentions(value):
    """@(文字) の文字部分を強調表示する。"""
    escaped_value = escape(value or '')
    pattern = re.compile(r'@\(([^()]*)\)')
    highlighted = pattern.sub(
        r'<strong class="instruction-highlight">\1</strong>',
        str(escaped_value)
    )
    return Markup(highlighted)

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# 青森市の市区町村コード
AREA_CODE = "0220100"

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
DEMO_LOCATION = {
    'name': '青森市役所',
    'latitude': 40.8220,
    'longitude': 140.7470
}
SHELTER_COORDINATES = {
    1: (40.8227, 140.7428), 2: (40.8127, 140.7556),
    3: (40.7975, 140.7752), 4: (40.8103, 140.7602),
    5: (40.8202, 140.7354)
}

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def save_shelters():
    temporary_file = f'{DATA_FILE}.tmp'
    with open(temporary_file, 'w', encoding='utf-8') as data_file:
        json.dump(shelters, data_file, ensure_ascii=False, indent=2)
    os.replace(temporary_file, DATA_FILE)


def get_shelter_crowding(shelter):
    """避難所の収容状況を表示用に計算する。"""
    capacity = shelter.get('capacity')
    current_capacity = shelter.get('current_capacity')
    try:
        capacity = int(capacity)
        current_capacity = int(current_capacity)
    except (TypeError, ValueError):
        return {
            'available': None,
            'rate': None,
            'status': '未登録'
        }

    available = max(capacity - current_capacity, 0)
    rate = round(current_capacity / capacity * 100) if capacity > 0 else 0
    if capacity > 0 and current_capacity >= capacity:
        status = '満員'
    elif capacity > 0 and current_capacity >= capacity * 0.7:
        status = 'やや混雑'
    else:
        status = '余裕あり'
    return {
        'available': available,
        'rate': rate,
        'status': status
    }


def get_shelter_coordinates(shelter):
    latitude = shelter.get('latitude')
    longitude = shelter.get('longitude')
    if latitude is None or longitude is None:
        latitude, longitude = SHELTER_COORDINATES.get(shelter.get('id'), (None, None))
    try:
        return float(latitude), float(longitude)
    except (TypeError, ValueError):
        return None


def calculate_distance_km(latitude, longitude, target_latitude, target_longitude):
    radius = 6371.0
    lat1, lat2 = math.radians(latitude), math.radians(target_latitude)
    delta_lat = math.radians(target_latitude - latitude)
    delta_lon = math.radians(target_longitude - longitude)
    value = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            next_path = request.path
            if request.query_string:
                next_path = f'{next_path}?{request.query_string.decode()}'
            return redirect(url_for('login', next=next_path))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [s for s in shelters if not district or s.get('district') == district]


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    is_logged_in = bool(session.get('logged_in'))
    resident_notices = [
        i for i in instructions
        if i.get('target') in ('住民', 'general')
        and i.get('audience', 'all') not in ('logged_in', 'anonymous')
    ][:3]
    staff_notices = [i for i in instructions if i.get('target') in ('職員', 'staff')]
    map_shelters = []
    for shelter in shelters:
        coordinates = get_shelter_coordinates(shelter)
        if coordinates:
            map_shelters.append({
                'id': shelter.get('id'),
                'name': shelter.get('name', '未登録'),
                'status': shelter.get('status') or shelter.get('opening_status', '開設中'),
                'latitude': coordinates[0],
                'longitude': coordinates[1]
            })
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        staff_notices=staff_notices,
        is_logged_in=is_logged_in,
        map_shelters=map_shelters,
        demo_location=DEMO_LOCATION
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（指定がなければホーム画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('index')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    facility_options = ['トイレ', 'Wi-Fi', '発電機', '空調', '充電設備', '給水設備', '医療・救護設備']
    barrier_free_options = ['車椅子対応', 'スロープ', '多目的トイレ', 'エレベーター', '妊婦・乳幼児対応', '授乳室', 'おむつ交換スペース']
    parking_options = ['駐車場あり', '駐車場なし', '大型車対応']
    shelter_types = ['指定避難所', '福祉避難所', '緊急避難場所', 'その他']
    pet_options = ['可', '不可', '専用スペースあり']
    status_options = ['開設', '閉鎖', '準備中']
    hour_options = [f'{hour:02d}:00' for hour in range(24)]

    form_data = request.form.to_dict(flat=False) if request.method == 'POST' else {}

    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        address = request.form.get('address', '').strip()
        shelter_type = request.form.get('shelter_type', '')
        capacity_text = request.form.get('capacity', '').strip()
        current_capacity_text = request.form.get('current_capacity', '').strip()
        errors = []

        if not name:
            errors.append('避難所名を入力してください。')
        if not address:
            errors.append('住所を入力してください。')
        if shelter_type not in shelter_types:
            errors.append('避難所種別を選択してください。')
        try:
            capacity = int(capacity_text)
            if capacity < 0:
                raise ValueError
        except ValueError:
            capacity = 0
            errors.append('最大収容人数は0以上の整数で入力してください。')
        try:
            current_capacity = int(current_capacity_text)
            if current_capacity < 0:
                raise ValueError
        except ValueError:
            current_capacity = 0
            errors.append('現在の収容人数は0以上の整数で入力してください。')
        if current_capacity > capacity:
            errors.append('現在の収容人数は最大収容人数以下にしてください。')

        pets_allowed = request.form.get('pets_allowed', '')
        status = request.form.get('status', '')
        hours_from = request.form.get('hours_from', '')
        hours_to = request.form.get('hours_to', '')
        hours = f'{hours_from}〜{hours_to}' if hours_from and hours_to else ''
        contact_phone = request.form.get('contact_phone', '').strip()
        contact_department = request.form.get('contact_department', '').strip()
        if pets_allowed not in pet_options:
            errors.append('ペットの受け入れを選択してください。')
        if status not in status_options:
            errors.append('開設状況を選択してください。')
        if hours_from not in hour_options or hours_to not in hour_options:
            errors.append('利用可能時間の開始時刻と終了時刻を選択してください。')
        if not re.fullmatch(r'\d+', contact_phone):
            errors.append('電話番号は数字のみで入力してください。')
        for value, label in [(contact_department, '担当部署')]:
            if not value:
                errors.append(f'{label}を入力してください。')

        if errors:
            return render_template(
                'shelter_register.html',
                error=True,
                messages=errors,
                form_data=form_data,
                facility_options=facility_options,
                barrier_free_options=barrier_free_options,
                parking_options=parking_options,
                shelter_types=shelter_types,
                pet_options=pet_options,
                status_options=status_options,
                hour_options=hour_options
            )

        if request.form.get('action') == 'edit':
            return render_template(
                'shelter_register.html',
                form_data=form_data,
                facility_options=facility_options,
                barrier_free_options=barrier_free_options,
                parking_options=parking_options,
                shelter_types=shelter_types,
                pet_options=pet_options,
                status_options=status_options,
                hour_options=hour_options
            )

        if request.form.get('action') != 'save':
            return render_template(
                'shelter_confirm.html',
                form_data=form_data,
                facility_options=facility_options,
                barrier_free_options=barrier_free_options,
                parking_options=parking_options,
                shelter_types=shelter_types,
                pet_options=pet_options,
                status_options=status_options
            )

        try:
            next_id = max((shelter.get('id', 0) for shelter in shelters), default=0) + 1
            updated_shelters = shelters + [{
                'id': next_id,
                'name': name,
                'address': address,
                'shelter_type': shelter_type,
                'capacity': capacity,
                'current_capacity': current_capacity,
                'facilities': [value for value in request.form.getlist('facilities') if value in facility_options],
                'barrier_free': [value for value in request.form.getlist('barrier_free') if value in barrier_free_options],
                'parking': [value for value in request.form.getlist('parking') if value in parking_options],
                'pets_allowed': pets_allowed,
                'notes': request.form.get('notes', '').strip(),
                'status': status,
                'hours': hours,
                'hours_from': hours_from,
                'hours_to': hours_to,
                'contact_phone': contact_phone,
                'contact_department': contact_department
            }]
            with open(DATA_FILE, 'w', encoding='utf-8') as data_file:
                json.dump(updated_shelters, data_file, ensure_ascii=False, indent=2)
            shelters[:] = updated_shelters
        except Exception:
            return render_template(
                'shelter_register.html',
                error=True,
                message='避難所情報を保存できませんでした。',
                form_data=form_data,
                facility_options=facility_options,
                barrier_free_options=barrier_free_options,
                parking_options=parking_options,
                shelter_types=shelter_types,
                pet_options=pet_options,
                status_options=status_options,
                hour_options=hour_options
            )

        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所情報の登録が完了しました。',
            facility_options=facility_options,
            barrier_free_options=barrier_free_options,
            parking_options=parking_options,
            shelter_types=shelter_types,
            pet_options=pet_options,
            status_options=status_options
        )

    return render_template(
        'shelter_register.html',
        facility_options=facility_options,
        barrier_free_options=barrier_free_options,
        parking_options=parking_options,
        shelter_types=shelter_types,
        pet_options=pet_options,
        status_options=status_options,
        hour_options=hour_options
    )

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    keyword = request.args.get('keyword', '').strip()
    try:
        reference_latitude = float(request.args.get('latitude', DEMO_LOCATION['latitude']))
        reference_longitude = float(request.args.get('longitude', DEMO_LOCATION['longitude']))
    except (TypeError, ValueError):
        reference_latitude = DEMO_LOCATION['latitude']
        reference_longitude = DEMO_LOCATION['longitude']
    facility_options = ['トイレ', 'Wi-Fi', '発電機', '空調', '充電設備', '給水設備', '医療・救護設備']
    barrier_free_options = ['車椅子対応', 'スロープ', '多目的トイレ', 'エレベーター', '妊婦・乳幼児対応', '授乳室', 'おむつ交換スペース']
    parking_options = ['駐車場あり', '駐車場なし', '大型車対応']
    shelter_types = ['指定避難所', '福祉避難所', '緊急避難場所', 'その他']
    status_options = ['開設', '閉鎖', '準備中']
    filters = {
        'pet_allowed': request.args.get('pet_allowed') == '1',
        'barrier_free': request.args.get('barrier_free') == '1',
        'has_toilet': request.args.get('has_toilet') == '1',
        'has_wifi': request.args.get('has_wifi') == '1',
        'pregnant_infant_support': request.args.get('pregnant_infant_support') == '1',
        'facilities': [value for value in request.args.getlist('facility') if value in facility_options],
        'barrier_free_items': [value for value in request.args.getlist('barrier_free_item') if value in barrier_free_options],
        'parking': [value for value in request.args.getlist('parking') if value in parking_options],
        'shelter_types': [value for value in request.args.getlist('shelter_type') if value in shelter_types],
        'statuses': [value for value in request.args.getlist('status') if value in status_options]
    }

    def values(shelter, key):
        value = shelter.get(key, [])
        if isinstance(value, list):
            return value
        if not value:
            return []
        return [part.strip() for part in str(value).replace('・', '・').split('・') if part.strip()]

    def matches(shelter):
        searchable = ' '.join(str(shelter.get(key, '')) for key in ('name', 'district', 'address'))
        if keyword and keyword.casefold() not in searchable.casefold():
            return False
        facilities = values(shelter, 'facilities')
        barriers = values(shelter, 'barrier_free')
        parking = values(shelter, 'parking')
        if filters['pet_allowed'] and shelter.get('pets_allowed') not in ('可', '専用スペースあり'):
            return False
        if filters['barrier_free'] and not barriers:
            return False
        if filters['has_toilet'] and 'トイレ' not in facilities:
            return False
        if filters['has_wifi'] and 'Wi-Fi' not in facilities:
            return False
        if filters['pregnant_infant_support'] and not any(
            item in barriers for item in ('妊婦・乳幼児対応', '授乳室', 'おむつ交換スペース')
        ):
            return False
        if filters['facilities'] and not all(item in facilities for item in filters['facilities']):
            return False
        if filters['barrier_free_items'] and not all(item in barriers for item in filters['barrier_free_items']):
            return False
        if filters['parking'] and not all(item in parking for item in filters['parking']):
            return False
        if filters['shelter_types'] and shelter.get('shelter_type') not in filters['shelter_types']:
            return False
        if filters['statuses'] and (shelter.get('status') or shelter.get('opening_status')) not in filters['statuses']:
            return False
        return True

    results = [shelter for shelter in shelters if matches(shelter)]
    map_shelters = []
    distances = {}
    for shelter in results:
        coordinates = get_shelter_coordinates(shelter)
        if coordinates:
            latitude, longitude = coordinates
            distances[shelter.get('id')] = calculate_distance_km(
                latitude, longitude, reference_latitude, reference_longitude
            )
            map_shelters.append({
                'id': shelter.get('id'),
                'name': shelter.get('name', '未登録'),
                'latitude': latitude,
                'longitude': longitude
            })
        else:
            distances[shelter.get('id')] = None
    results.sort(key=lambda shelter: distances.get(shelter.get('id')) is None,)
    results.sort(key=lambda shelter: distances.get(shelter.get('id')) or float('inf'))

    return render_template(
        'shelter_search.html',
        shelters=results,
        crowding={shelter.get('id'): get_shelter_crowding(shelter) for shelter in results},
        distances=distances,
        map_shelters=map_shelters,
        keyword=keyword,
        reference_location={'latitude': reference_latitude, 'longitude': reference_longitude},
        filters=filters,
        facility_options=facility_options,
        barrier_free_options=barrier_free_options,
        parking_options=parking_options,
        shelter_types=shelter_types,
        status_options=status_options
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    return render_template('search_results.html', results=shelters)

# 避難所詳細ページ
@app.route('/shelter/<int:shelter_id>')
def shelter_detail(shelter_id):
    shelter = next((s for s in shelters if s.get('id') == shelter_id), None)
    if shelter is None:
        return '避難所が見つかりませんでした', 404
    return render_template(
        'shelter_detail.html',
        shelter=shelter,
        crowding=get_shelter_crowding(shelter),
        facility_options=['トイレ', 'Wi-Fi', '発電機', '空調', '充電設備', '給水設備', '医療・救護設備'],
        barrier_free_options=['車椅子対応', 'スロープ', '多目的トイレ', 'エレベーター', '妊婦・乳幼児対応', '授乳室', 'おむつ交換スペース'],
        parking_options=['駐車場あり', '駐車場なし', '大型車対応']
    )


@app.route('/evacuee_counts', methods=['GET', 'POST'])
@login_required
def evacuee_counts():
    selected_id = request.values.get('shelter_id', type=int)
    message = None
    error = None
    if selected_id is None and shelters:
        selected_id = shelters[0].get('id')
    selected_shelter = next((item for item in shelters if item.get('id') == selected_id), None)

    if request.method == 'POST':
        if selected_shelter is None:
            error = '避難所を選択してください。'
        else:
            try:
                new_capacity = int(request.form.get('current_capacity', ''))
                maximum = int(selected_shelter.get('capacity', 0))
                if new_capacity < 0 or new_capacity > maximum:
                    raise ValueError
            except (TypeError, ValueError):
                error = f'現在の避難者数は0人以上、最大{selected_shelter.get("capacity", 0)}人以下で入力してください。'
            if error is None:
                original_capacity = selected_shelter.get('current_capacity')
                selected_shelter['current_capacity'] = new_capacity
                try:
                    save_shelters()
                    message = '避難者数を更新しました。'
                except Exception:
                    selected_shelter['current_capacity'] = original_capacity
                    error = '避難者数を更新できませんでした。'

    return render_template(
        'evacuee_counts.html',
        shelters=shelters,
        selected_shelter=selected_shelter,
        selected_id=selected_id,
        crowding=get_shelter_crowding(selected_shelter) if selected_shelter else None,
        message=message,
        error=error
    )


# 指示ボード：住民向けの指示を検索・発信・確認する
@app.route('/board', methods=['GET', 'POST'])
def board():
    if request.method == 'POST':
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        content = request.form.get('content', '').strip()
        if content:
            target_type = request.form.get('target_type', 'general')
            target = '職員' if target_type == 'staff' else '住民'
            next_id = max((instruction.get('id', 0) for instruction in instructions), default=0) + 1
            now = get_japan_time()
            instructions.insert(0, {
                'id': next_id,
                'target': target,
                'content': content,
                'shelter': request.form.get('shelter', '').strip(),
                'status': '発信中',
                'created_at': now,
                'updated_at': now,
            })
            save_instructions()
            return redirect(url_for('board'))

    search_word = request.args.get('q', '').strip()
    is_logged_in = bool(session.get('logged_in'))

    def is_visible(instruction):
        if instruction.get('target') in ('職員', 'staff'):
            return is_logged_in
        if instruction.get('audience') == 'logged_in':
            return is_logged_in
        if instruction.get('audience') == 'anonymous':
            return not is_logged_in
        return instruction.get('target') in ('住民', 'general')

    resident_instructions = [i for i in instructions if is_visible(i)]
    if search_word:
        resident_instructions = [
            instruction for instruction in resident_instructions
            if search_word in instruction.get('content', '')
            or search_word in instruction.get('shelter', '')
        ]
    return render_template(
        'board.html',
        instructions=resident_instructions,
        search_word=search_word,
        is_logged_in=is_logged_in
    )


@app.route('/board/delete/<int:instruction_id>', methods=['POST'])
def delete_instruction(instruction_id):
    if not session.get('logged_in'):
        return redirect(url_for('login', next=url_for('board')))

    instruction = next((item for item in instructions if item.get('id') == instruction_id), None)
    if instruction is not None:
        instructions.remove(instruction)
        save_instructions()
    return redirect(url_for('board'))

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    results = filter_shelters(request.args.get('district'))
    return render_template('search_results.html', results=results)

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    results = filter_shelters(request.args.get('district'))

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
