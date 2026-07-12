# -*- coding: utf-8 -*-

import xml.etree.ElementTree as ET
import base64
import hashlib
import json
import datetime
import threading
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from lib.helper import *
import re
import time
from urllib.parse import urlparse, parse_qs
IPTV_PROBLEM_LOG = translate(os.path.join(profile, 'iptv_problems_log.txt'))
REQUEST_TIMEOUT = 10
MAX_RETRIES = 1
CACHE_FAILED_URLS = {}
EPG_XML_TTL = 86400
EPG_XML_INDEX_VERSION = 'kingIPTV_epg'
EPG_INDEX_MEMORY = {}
EPG_INDEX_LOCK = threading.Lock()
EPG_ACTIVE = set()
EPG_ACTIVE_LOCK = threading.Lock()
BROWSER_UA = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/130.0.0.0 Safari/537.36'
)
KINGIPTV_EPG_COLOR = 'gold'
EPG_HEADER_COLOR = 'gold'
EPG_CURRENT_COLOR = 'gold'
EPG_SCHEDULE_COLOR = 'gray'

def color(text, color_name=None):
    if not text:
        return ''
    color_name = color_name or KINGIPTV_EPG_COLOR
    return '[COLOR {0}]{1}[/COLOR]'.format(color_name, text)

def create_session():
    session = requests.Session()
    retry = Retry(
        total=MAX_RETRIES,
        read=MAX_RETRIES,
        connect=MAX_RETRIES,
        backoff_factor=0.3,
        status_forcelist=(500, 502, 504),
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount('http://', adapter)
    session.mount('https://', adapter)
    session.headers.update({'User-Agent': BROWSER_UA})
    return session

def log_iptv_problem(url, error_msg=''):
    try:
        with open(IPTV_PROBLEM_LOG, 'a', encoding='utf-8') as f:
            timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
            f.write('{} - {} - {}\n'.format(timestamp, url, error_msg))
    except Exception:
        pass

def decode_b64_safe(text):
    if not text:
        return ''
    try:
        decoded = base64.b64decode(str(text)).decode('utf-8', errors='ignore')
        if decoded and decoded.isprintable():
            return decoded
    except Exception:
        pass
    return str(text)

def clean_text(value):
    if value in (None, ''):
        return ''
    try:
        if isinstance(value, (list, tuple)):
            value = ', '.join(str(v).strip() for v in value if str(v).strip())
        elif isinstance(value, dict):
            value = value.get('name') or value.get('title') or ''
        value = str(value).strip()
        if value.lower() in ('none', 'null', 'n/a', 'na', '0', 'sem informação',
                              'sem informacao', 'undefined', ''):
            return ''
        return value
    except Exception:
        return ''

def first_clean_text(data, *keys):
    if not isinstance(data, dict):
        return ''
    for key in keys:
        value = clean_text(data.get(key))
        if value:
            return value
    return ''
EMOJI_PATTERN = re.compile(
    "["
    "\U0001F1E0-\U0001F1FF"
    "\U0001F300-\U0001F5FF"
    "\U0001F600-\U0001F64F"
    "\U0001F680-\U0001F6FF"
    "\U0001F700-\U0001F77F"
    "\U0001F780-\U0001F7FF"
    "\U0001F800-\U0001F8FF"
    "\U0001F900-\U0001F9FF"
    "\U0001FA00-\U0001FA6F"
    "\U0001FA70-\U0001FAFF"
    "\U00002600-\U000026FF"
    "\U00002700-\U000027BF"
    "\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF"
    "\U00002190-\U000021FF"
    "\U00002300-\U000023FF"
    "\U0000FE00-\U0000FE0F"
    "\U0000200D"
    "]+",
    flags=re.UNICODE,
)

def strip_emoji(text):
    if not text:
        return text
    try:
        cleaned = EMOJI_PATTERN.sub('', text)
        cleaned = re.sub(r'\s+', ' ', cleaned).strip()
        return cleaned
    except Exception:
        return text

def clean_category_name(name):
    name = clean_text(name)
    if not name:
        return ''
    name = strip_emoji(name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

def clean_channel_name(name):
    if not name:
        return name
    name = re.sub(r'\s*\[\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\]', '', name)
    name = re.sub(r'\s*\+\s*\d+\.?\d*\s*min', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    tags_inicio = []
    resto = name
    while True:
        match = re.match(r'^(\[[^\]]+\])\s*', resto)
        if match:
            tags_inicio.append(match.group(1))
            resto = resto[match.end():]
        else:
            break
    if not resto:
        return name
    sufixos_canal = [
        'HD', 'FHD', 'SD', '4K', 'UHD', 'HD+', 'HD¹', 'HD²', 'HD2', 'HD1',
        'FHD¹', 'FHD²', 'SD¹', 'SD²', '4K¹', '4K²', 'UHD¹', 'UHD²',
        'H264', 'H265', 'H264¹', 'H264²', 'H265¹', 'H265²',
        'PLUS', 'PLUS¹', 'PLUS²', 'PREMIUM', 'PREMIUM¹', 'PREMIUM²',
        'MAX', 'MAX¹', 'MAX²',
    ]
    palavras = resto.split()
    ultimo_sufixo_idx = -1
    for idx, palavra in enumerate(palavras):
        palavra_limpa = re.sub(r'[¹²+]', '', palavra.upper())
        if palavra.upper() in sufixos_canal or palavra_limpa in sufixos_canal:
            ultimo_sufixo_idx = idx
    if ultimo_sufixo_idx >= 0:
        canal_str = ' '.join(palavras[:ultimo_sufixo_idx + 1])
        name = (' '.join(tags_inicio) + ' ' + canal_str).strip() if tags_inicio else canal_str
    if '-' in name:
        name = re.sub(r'\s*-\s*', ' - ', name)
    return re.sub(r'\s+', ' ', name).strip()

def ordenar_resolucao(item):
    name = item[0]
    if 'FHD' in name:
        return 1
    elif 'HD' in name:
        return 2
    elif '4K' in name:
        return 3
    elif 'SD' in name:
        return 4
    return 5

def normalize_epg_channel_id(value):
    return str(value or '').strip().lower()

def parse_xmltv_time(value):
    value = str(value or '').strip()
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    parts = value.split()
    dt_part = parts[0]
    tz_part = parts[1] if len(parts) > 1 else ''
    if 'T' in dt_part:
        try:
            clean = value.replace('Z', '+00:00')
            dt = datetime.datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            return int(dt.timestamp())
        except:
            pass
    if len(dt_part) >= 14 and dt_part[:14].isdigit():
        year = int(dt_part[:4])
        month = int(dt_part[4:6])
        day = int(dt_part[6:8])
        hour = int(dt_part[8:10])
        minute = int(dt_part[10:12])
        second = int(dt_part[12:14])
        dt = datetime.datetime(year, month, day, hour, minute, second)
        if tz_part and len(tz_part) >= 5 and tz_part[0] in '+-':
            sign = 1 if tz_part[0] == '+' else -1
            tz_hour = int(tz_part[1:3])
            tz_min = int(tz_part[3:5])
            offset = sign * (tz_hour * 3600 + tz_min * 60)
            tz = datetime.timezone(datetime.timedelta(seconds=offset))
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return int(dt.timestamp())
    return 0

def normalize_epoch_seconds(value):
    if value in (None, ''):
        return 0
    try:
        if isinstance(value, (int, float)):
            return int(value)
        raw = str(value).strip()
        if not raw:
            return 0
        if raw.isdigit():
            return int(raw)
        return parse_xmltv_time(raw)
    except Exception:
        return 0

def get_local_tag(elem):
    if elem is None:
        return ''
    tag = elem.tag
    if tag is None:
        return ''
    if '}' in tag:
        return tag.split('}', 1)[1]
    if ':' in tag:
        return tag.split(':', 1)[1]
    return tag

def xml_child_text(element, child_name):
    try:
        child = element.find(child_name)
        if child is not None and child.text:
            return child.text.strip()
    except Exception:
        pass
    try:
        for child in list(element):
            if get_local_tag(child) == child_name and child.text:
                return child.text.strip()
    except Exception:
        pass
    return ''

def extract_program_title(program):
    if not isinstance(program, dict):
        return ''
    title = (program.get('title') or program.get('name') or
             program.get('event') or program.get('event_name') or '')
    return decode_b64_safe(title).strip()

def extract_program_desc(program):
    if not isinstance(program, dict):
        return ''
    desc = (program.get('description') or program.get('desc') or
            program.get('plot') or '')
    return decode_b64_safe(desc).strip()

def normalize_epg_program(program):
    if not isinstance(program, dict):
        return None
    title = extract_program_title(program)
    desc = extract_program_desc(program)
    start = normalize_epoch_seconds(
        program.get('start_timestamp') or program.get('start') or
        program.get('start_time') or program.get('start_date')
    )
    end = normalize_epoch_seconds(
        program.get('end_timestamp') or program.get('stop_timestamp') or
        program.get('end') or program.get('stop') or program.get('end_time')
    )
    if end <= start and start > 0:
        end = start + 3600
    return {'title': title, 'desc': desc, 'start': start, 'end': end}

def normalize_epg_programs(epg_data):
    programs = []
    for p in epg_data or []:
        norm = normalize_epg_program(p)
        if norm and (norm.get('title') or norm.get('start')):
            programs.append(norm)
    programs.sort(key=lambda x: x.get('start') or 0)
    return programs

def epg_format_range(program):
    if not program:
        return ''
    start = int(program.get('start') or 0)
    end = int(program.get('end') or 0)
    if start <= 0:
        return ''
    if end <= start:
        end = start + 3600
    return '{} - {}'.format(
        datetime.datetime.fromtimestamp(start).strftime('%H:%M'),
        datetime.datetime.fromtimestamp(end).strftime('%H:%M'),
    )

def epg_lookup_current_next(programs):
    now = int(time.time())
    current = None
    nextp = None
    grace = 120
    for idx, program in enumerate(programs or []):
        start = int(program.get('start') or 0)
        end = int(program.get('end') or 0)
        if start and end and (start - grace) <= now < end:
            current = program
            if idx + 1 < len(programs):
                nextp = programs[idx + 1]
            break
        if start and start > now:
            nextp = program
            if idx > 0:
                prev = programs[idx - 1]
                if int(prev.get('end') or 0) >= now:
                    current = prev
            break
    if not current and programs:
        first = programs[0]
        if not first.get('start') or int(first.get('start') or 0) <= now:
            current = first
            if len(programs) > 1:
                nextp = programs[1]
    return current, nextp

def build_epg_desc(current=None, nextp=None, day_schedule=None):
    if not current and not nextp:
        return ''
    parts = []
    now_ts = int(time.time())
    if current:
        title = str(current.get('title') or '').strip()
        rng = epg_format_range(current)
        line = 'Agora: {} | {}'.format(rng, title) if rng else 'Agora: {}'.format(title)
        if line:
            parts.append(color(line, 'gold'))
    if nextp:
        title = str(nextp.get('title') or '').strip()
        rng = epg_format_range(nextp)
        line = 'Próximo: {} | {}'.format(rng, title) if rng else 'Próximo: {}'.format(title)
        if title:
            if parts:
                parts.append('')
            parts.append(color(line, 'gold'))
    upcoming = [
        p for p in (day_schedule or [])
        if int(p.get('start') or 0) > now_ts
        and p is not nextp
    ]
    if upcoming:
        if parts:
            parts.append('')
        parts.append(color('Programação do dia', 'gold'))
        for p in upcoming:
            title = str(p.get('title') or '').strip()
            rng = epg_format_range(p)
            line = '{} | {}'.format(rng, title) if rng else title
            if line:
                parts.append(color(line, 'gold'))
    return '\n'.join(parts).strip()

def epg_server_hash(dns):
    return hashlib.md5(dns.encode('utf-8', 'ignore')).hexdigest()[:12]

def epg_paths(dns):
    h = epg_server_hash(dns)
    return {
        'xml': os.path.join(profile, 'epg_{}.xml'.format(h)),
        'index': os.path.join(profile, 'epg_{}_index.json'.format(h)),
        'meta': os.path.join(profile, 'epg_{}_meta.json'.format(h)),
    }

def safe_read_json(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}

def safe_write_json(path, data):
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        pass
    return False

def epg_fingerprint(dns, username, password):
    return '{}|{}|{}'.format(dns, username, password)

def current_day_key():
    tz = datetime.timezone(datetime.timedelta(hours=-3))
    return datetime.datetime.now(tz).strftime('%Y-%m-%d')

def epg_xml_fresh(dns, username, password):
    paths = epg_paths(dns)
    meta = safe_read_json(paths['meta'])
    if meta.get('fingerprint') != epg_fingerprint(dns, username, password):
        return False
    fetched_at = int(meta.get('fetched_at') or 0)
    if not fetched_at or (time.time() - fetched_at) >= EPG_XML_TTL:
        return False
    if meta.get('day') != current_day_key():
        return False
    try:
        return os.path.exists(paths['xml']) and os.path.getsize(paths['xml']) > 128
    except Exception:
        return False

def epg_index_fresh(dns, username, password):
    paths = epg_paths(dns)
    index = safe_read_json(paths['index'])
    if index.get('version') != EPG_XML_INDEX_VERSION:
        return False
    if index.get('fingerprint') != epg_fingerprint(dns, username, password):
        return False
    generated_at = int(index.get('generated_at') or 0)
    if not generated_at or (time.time() - generated_at) >= EPG_XML_TTL:
        return False
    if index.get('day') != current_day_key():
        return False
    channels = index.get('channels')
    if not isinstance(channels, dict) or not channels:
        return False
    now_ts = int(time.time())
    window_end = int(index.get('window_end') or 0)
    if window_end and now_ts > window_end:
        return False
    return True

def download_epg_xml(dns, username, password):
    paths = epg_paths(dns)
    urls = [
        '{}/xmltv.php?username={}&password={}'.format(dns, username, password),
        '{}/epg.php?username={}&password={}'.format(dns, username, password),
        '{}/api.php?username={}&password={}&type=m3u_plus&output=ts'.format(dns, username, password),
    ]
    headers = {
        'User-Agent': BROWSER_UA,
        'Accept': 'application/xml,text/xml,*/*;q=0.9',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8',
        'Connection': 'close',
    }
    for url in urls:
        try:
            response = requests.get(url, timeout=60, headers=headers, allow_redirects=True, verify=False, stream=True)
            if response.status_code != 200:
                continue
            content = response.content
            if len(content) < 256:
                continue
            sample = content[:512].lower()
            if b'<tv' not in sample and b'<xmltv' not in sample and b'<epg' not in sample:
                continue
            tmp = paths['xml'] + '.tmp'
            with open(tmp, 'wb') as f:
                f.write(content)
            try:
                if os.path.exists(paths['xml']):
                    os.remove(paths['xml'])
            except Exception:
                pass
            os.rename(tmp, paths['xml'])
            safe_write_json(paths['meta'], {
                'fingerprint': epg_fingerprint(dns, username, password),
                'fetched_at': int(time.time()),
                'day': current_day_key(),
                'size': len(content),
            })
            try:
                if os.path.exists(paths['index']):
                    os.remove(paths['index'])
            except Exception:
                pass
            with EPG_INDEX_LOCK:
                EPG_INDEX_MEMORY.pop(dns, None)
            return True
        except Exception:
            continue
    return False

def build_epg_index(dns, username, password):
    paths = epg_paths(dns)
    if not os.path.exists(paths['xml']):
        log_iptv_problem(dns, 'Arquivo XML não encontrado')
        return False
    now_ts = int(time.time())
    start_day = datetime.datetime.fromtimestamp(now_ts).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    window_start = int(start_day.timestamp()) - 86400
    window_end = int((start_day + datetime.timedelta(days=2)).timestamp()) + 86400
    log_iptv_problem(dns, f'Janela: {window_start} a {window_end} (now={now_ts})')
    channels = {}
    total_programmes = 0
    total_with_cid = 0
    total_in_window = 0
    total_saved = 0
    logged_first = False
    try:
        for event, elem in ET.iterparse(paths['xml'], events=('end',)):
            tag = get_local_tag(elem)
            if tag != 'programme':
                if tag not in ('title', 'desc'):
                    try:
                        elem.clear()
                    except Exception:
                        pass
                continue
            total_programmes += 1
            cid = normalize_epg_channel_id(elem.get('channel'))
            if not cid:
                try:
                    elem.clear()
                except Exception:
                    pass
                continue
            total_with_cid += 1
            start = 0
            end = 0
            ts_start = elem.get('start_timestamp')
            if ts_start and ts_start.isdigit():
                start = int(ts_start)
            else:
                start_str = elem.get('start')
                if start_str:
                    start = parse_xmltv_time(start_str)
            ts_end = elem.get('stop_timestamp')
            if ts_end and ts_end.isdigit():
                end = int(ts_end)
            else:
                end_str = elem.get('stop') or elem.get('end')
                if end_str:
                    end = parse_xmltv_time(end_str)
            if end <= start and start > 0:
                end = start + 3600
            if not logged_first and total_programmes <= 10:
                log_iptv_problem(dns, f'Programa #{total_programmes}: cid={cid}, ts_start={ts_start}, start={start}, ts_end={ts_end}, end={end}')
                if total_programmes == 10:
                    logged_first = True
                    log_iptv_problem(dns, '--- Fim dos primeiros 10 programas (logs) ---')
            if start <= 0:
                try:
                    elem.clear()
                except Exception:
                    pass
                continue
            if end < window_start or start > window_end:
                try:
                    elem.clear()
                except Exception:
                    pass
                continue
            total_in_window += 1
            title = xml_child_text(elem, 'title')
            if not title:
                try:
                    elem.clear()
                except Exception:
                    pass
                continue
            desc = xml_child_text(elem, 'desc')
            channels.setdefault(cid, []).append({
                'start': start,
                'end': end,
                'title': title,
                'desc': desc,
            })
            total_saved += 1
            try:
                elem.clear()
            except Exception:
                pass
        log_iptv_problem(dns, f'Resumo: programmes={total_programmes}, com_cid={total_with_cid}, na_janela={total_in_window}, salvos={total_saved}')
        for cid in channels:
            channels[cid].sort(key=lambda x: x.get('start') or 0)
        safe_write_json(paths['index'], {
            'version': EPG_XML_INDEX_VERSION,
            'fingerprint': epg_fingerprint(dns, username, password),
            'generated_at': int(time.time()),
            'day': current_day_key(),
            'window_start': window_start,
            'window_end': window_end,
            'channels': channels,
        })
        log_iptv_problem(dns, f'Indexação concluída: {len(channels)} canais, {total_saved} programas')
        with EPG_INDEX_LOCK:
            EPG_INDEX_MEMORY.pop(dns, None)
        return True
    except Exception as e:
        log_iptv_problem(dns, f'Erro na indexação: {e}')
        return False

def ensure_epg_background(dns, username, password):
    if epg_index_fresh(dns, username, password):
        return
    with EPG_ACTIVE_LOCK:
        if dns in EPG_ACTIVE:
            return
        EPG_ACTIVE.add(dns)
    def worker():
        try:
            xml_fresh = epg_xml_fresh(dns, username, password)
            index_fresh = epg_index_fresh(dns, username, password)
            if not index_fresh:
                if not xml_fresh:
                    if download_epg_xml(dns, username, password):
                        build_epg_index(dns, username, password)
                else:
                    build_epg_index(dns, username, password)
        except Exception as e:
            log_iptv_problem(dns, f'Worker EPG erro: {e}')
        finally:
            with EPG_ACTIVE_LOCK:
                EPG_ACTIVE.discard(dns)
    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()
VOD_CACHE_TTL = 86400
SERIES_CACHE_TTL = 86400

def vod_cache_paths(dns):
    h = epg_server_hash(dns)
    return {
        'movies': os.path.join(profile, 'vod_movies_{}.json'.format(h)),
        'series': os.path.join(profile, 'vod_series_{}.json'.format(h)),
    }

def load_catalog_cache(cache_path, dns, username, password, ttl):
    cached = safe_read_json(cache_path)
    fp = epg_fingerprint(dns, username, password)
    if cached.get('fingerprint') != fp:
        return None, cached
    fetched_at = int(cached.get('fetched_at') or 0)
    items = cached.get('items')
    if not fetched_at or not isinstance(items, list) or not items:
        return None, cached
    if (time.time() - fetched_at) >= ttl:
        return None, cached
    if cached.get('day') != current_day_key():
        return None, cached
    return items, cached

def get_movies_catalog(dns, username, password, force=False):
    paths = vod_cache_paths(dns)
    fp = epg_fingerprint(dns, username, password)
    cached = {}
    if not force:
        items, cached = load_catalog_cache(paths['movies'], dns, username, password, VOD_CACHE_TTL)
        if items:
            return items
    else:
        cached = safe_read_json(paths['movies'])
    api = API(dns, username, password)
    items = api.all_movies()
    if items:
        safe_write_json(paths['movies'], {
            'fingerprint': fp,
            'fetched_at': int(time.time()),
            'day': current_day_key(),
            'items': items,
        })
        return items
    stale = cached.get('items') if isinstance(cached, dict) else None
    return stale if isinstance(stale, list) else []

def get_series_catalog(dns, username, password, force=False):
    paths = vod_cache_paths(dns)
    fp = epg_fingerprint(dns, username, password)
    cached = {}
    if not force:
        items, cached = load_catalog_cache(paths['series'], dns, username, password, SERIES_CACHE_TTL)
        if items:
            return items
    else:
        cached = safe_read_json(paths['series'])
    api = API(dns, username, password)
    items = api.all_series()
    if items:
        safe_write_json(paths['series'], {
            'fingerprint': fp,
            'fetched_at': int(time.time()),
            'day': current_day_key(),
            'items': items,
        })
        return items
    stale = cached.get('items') if isinstance(cached, dict) else None
    return stale if isinstance(stale, list) else []

def refresh_vod_catalogs_background(dns, username, password):
    key = 'vod|{}'.format(dns)
    with EPG_ACTIVE_LOCK:
        if key in EPG_ACTIVE:
            return
        EPG_ACTIVE.add(key)
    def worker():
        try:
            get_movies_catalog(dns, username, password)
            get_series_catalog(dns, username, password)
        except Exception:
            pass
        finally:
            with EPG_ACTIVE_LOCK:
                EPG_ACTIVE.discard(key)
    t = threading.Thread(target=worker)
    t.daemon = True
    t.start()

def load_epg_index(dns):
    with EPG_INDEX_LOCK:
        cached = EPG_INDEX_MEMORY.get(dns)
    if cached and (time.time() - cached[1]) < 300:
        return cached[0]
    paths = epg_paths(dns)
    index = safe_read_json(paths['index'])
    if isinstance(index.get('channels'), dict) and index.get('version') == EPG_XML_INDEX_VERSION:
        with EPG_INDEX_LOCK:
            EPG_INDEX_MEMORY[dns] = (index, time.time())
        return index
    return {}

def get_epg_programs(channel_id, dns, limit=12):
    index = load_epg_index(dns)
    if not index:
        return []
    cid = normalize_epg_channel_id(channel_id)
    channels = index.get('channels') if isinstance(index.get('channels'), dict) else {}
    programs = channels.get(cid) or []
    if not programs:
        for key, value in channels.items():
            if key.startswith(cid) or cid.startswith(key):
                programs = value or []
                break
    now_ts = int(time.time())
    out = []
    for p in programs:
        if (p.get('end') or 0) > now_ts - 300:
            out.append(p)
        if len(out) >= limit:
            break
    return out

def extract_info(url):
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return None, None, None
        port = parsed.port or (80 if parsed.scheme == 'http' else 443)
        params = parse_qs(parsed.query)
        username = params.get('username', [None])[0]
        password = params.get('password', [None])[0]
        if not username or not password:
            log_iptv_problem(url, 'URL sem username ou password válidos')
            return None, None, None
        dns = '{}://{}:{}'.format(parsed.scheme, host, port)
        return dns, username, password
    except Exception as e:
        log_iptv_problem(url, 'Erro ao extrair info: {}'.format(e))
        return None, None, None

def check_iptv(url_iptv):
    current_time = time.time()
    if url_iptv in CACHE_FAILED_URLS:
        if current_time - CACHE_FAILED_URLS[url_iptv] < 300:
            return False
    cond = True
    if exists(IPTV_PROBLEM_LOG):
        try:
            with open(IPTV_PROBLEM_LOG, 'r', encoding='utf-8') as f:
                urls = f.read().split('\n')
                for i in urls:
                    if 'http' in i and i in url_iptv:
                        cond = False
                        break
        except Exception:
            pass
    return cond

def parselist(url):
    iptv = []
    session = create_session()
    try:
        response = session.get(url, timeout=REQUEST_TIMEOUT)
        url = response.json()['url']
    except Exception:
        pass
    try:
        if 'paste.kodi.tv' in url and 'documents' not in url and 'raw' not in url:
            try:
                key = url.split('/')[-1]
                url = 'https://paste.kodi.tv/documents/' + key
                src = session.get(url, timeout=REQUEST_TIMEOUT).json()['data']
                for i in src.split('\n'):
                    i = i.replace(' ', '')
                    if 'http' in i and check_iptv(i):
                        dns, username, password = extract_info(i)
                        if dns and username and password:
                            iptv.append((dns, username, password))
            except Exception as e:
                log_iptv_problem(url, 'Erro paste.kodi.tv: {}'.format(e))
        else:
            src = session.get(url, timeout=REQUEST_TIMEOUT).text
            for i in src.split('\n'):
                i = i.replace(' ', '')
                if 'http' in i and check_iptv(i):
                    dns, username, password = extract_info(i)
                    if dns and username and password:
                        iptv.append((dns, username, password))
    except Exception as e:
        log_iptv_problem(url, 'Erro parselist: {}'.format(e))
    return iptv

class API:
    def __init__(self, dns, username, password, hide_adult='true'):
        if not username or not password:
            raise ValueError('Username e password são obrigatórios')
        self.dns = dns
        self.username = username
        self.password = password
        self.player_api = '{}/player_api.php?username={}&password={}'.format(dns, username, password)
        self.play_url = '{}/live/{}/{}/'.format(dns, username, password)
        self.play_movies = '{}/movie/{}/{}/'.format(dns, username, password)
        self.play_series = '{}/series/{}/{}/'.format(dns, username, password)
        self.live_url = '{}/enigma2.php?username={}&password={}&type=get_live_categories'.format(dns, username, password)
        self.vod_url = '{}/enigma2.php?username={}&password={}&type=get_vod_categories'.format(dns, username, password)
        self.series_url = '{}/enigma2.php?username={}&password={}&type=get_series_categories'.format(dns, username, password)
        self.adult_tags = ['xxx', 'xXx', 'XXX', 'adult', 'Adult', 'ADULT',
                           'porn', 'Porn', 'PORN', 'teste', 'TESTE', 'Teste']
        self.hide_adult = hide_adult
        self.server_alive = None
        self.server_format = None
        self.session = create_session()
    def b64(self, obj):
        return decode_b64_safe(obj)
    def check_protocol(self, url):
        try:
            if urlparse(self.live_url).scheme == 'https':
                return url.replace('http://', 'https://')
        except Exception:
            pass
        return url
    def is_adult(self, name):
        return any(s in name for s in self.adult_tags)
    def allow(self, name):
        if self.hide_adult == 'false':
            return True
        return not self.is_adult(name)
    def regex_from_to(self, text, from_string, to_string, excluding=True):
        try:
            if excluding:
                return re.search(r'(?i)' + from_string + r'([\S\s]+?)' + to_string, text).group(1)
            return re.search(r'(?i)(' + from_string + r'[\S\s]+?' + to_string + r')', text).group(1)
        except Exception:
            return ''
    def regex_get_all(self, text, start_with, end_with):
        try:
            return re.findall(r'(?i)(' + start_with + r'[\S\s]+?' + end_with + r')', text)
        except Exception:
            return []
    def check_server_alive(self):
        if self.server_alive is not None:
            return self.server_alive
        try:
            r = self.session.get(self.player_api, timeout=10, allow_redirects=False)
            if r.status_code == 200:
                self.server_alive = True
                self.server_format = 'xtream'
                try:
                    r2 = requests.get(self.live_url, timeout=3,
                                      headers={'User-Agent': BROWSER_UA},
                                      allow_redirects=False)
                    if r2.status_code == 200:
                        self.server_format = 'enigma2'
                except Exception:
                    pass
                return True
        except Exception:
            pass
        try:
            r = self.session.get(self.live_url, timeout=10, allow_redirects=False)
            if r.status_code == 200:
                self.server_alive = True
                self.server_format = 'enigma2'
                return True
        except Exception:
            pass
        self.server_alive = False
        self.server_format = None
        log_iptv_problem(self.dns, 'Servidor não responde')
        CACHE_FAILED_URLS[self.dns] = time.time()
        return False
    def http(self, url='', mode=None):
        if not self.check_server_alive():
            return '' if mode != 'json_url' else None
        try:
            if not mode:
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    raise requests.exceptions.HTTPError('HTTP {}'.format(r.status_code))
                return r.content
            elif mode == 'channels_category':
                if self.server_format != 'enigma2':
                    return ''
                r = self.session.get(self.live_url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    raise requests.exceptions.HTTPError('HTTP {}'.format(r.status_code))
                return r.content
            elif mode == 'json_url':
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    raise requests.exceptions.HTTPError('HTTP {}'.format(r.status_code))
                return r.json()
            elif mode == 'vod':
                if self.server_format != 'enigma2':
                    return ''
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    raise requests.exceptions.HTTPError('HTTP {}'.format(r.status_code))
                return r.text
        except Exception as e:
            log_iptv_problem(url or self.live_url, 'Erro HTTP: {}'.format(e))
            CACHE_FAILED_URLS[url or self.live_url] = time.time()
        return '' if mode != 'json_url' else None
    def channel_id(self, json_data, n):
        if json_data and isinstance(json_data, list):
            try:
                if n < len(json_data):
                    return json_data[n].get('stream_id', '')
            except Exception:
                pass
        return ''
    def channels_category(self):
        itens = []
        if not self.check_server_alive():
            return itens
        ensure_epg_background(self.dns, self.username, self.password)
        if self.server_format == 'enigma2':
            xml_data = self.http('', 'channels_category')
            if not xml_data:
                return itens
            try:
                root = ET.fromstring(xml_data)
                channels = root.findall('channel')
                for channel in channels:
                    try:
                        name_elem = channel.find('title')
                        url_elem = channel.find('playlist_url')
                        if name_elem is None or url_elem is None:
                            continue
                        name = clean_category_name(self.b64(name_elem.text))
                        if not name or 'All' in name or not self.allow(name):
                            continue
                        url = self.check_protocol(
                            url_elem.text.replace('<![CDATA[', '').replace(']]>', '')
                        )
                        itens.append((name, url))
                    except Exception:
                        continue
            except Exception as e:
                log_iptv_problem(self.live_url, 'Erro ao parsear categorias XML: {}'.format(e))
        elif self.server_format == 'xtream':
            url_cat = '{}&action=get_live_categories'.format(self.player_api)
            categories = self.http(url_cat, 'json_url')
            if not categories:
                return itens
            try:
                for cat in categories:
                    try:
                        name = clean_category_name(cat.get('category_name', ''))
                        cat_id = cat.get('category_id', '')
                        if not cat_id or not name or 'All' in name or not self.allow(name):
                            continue
                        url = '{}&action=get_live_streams&category_id={}'.format(
                            self.player_api, cat_id
                        )
                        itens.append((name, url))
                    except Exception:
                        continue
            except Exception as e:
                log_iptv_problem(url_cat, 'Erro ao processar categorias: {}'.format(e))
        return itens
    def channels_open(self, url):
        itens = []
        ensure_epg_background(self.dns, self.username, self.password)
        if 'player_api.php' in url and 'action=get_live_streams' in url:
            json_data = self.http(url, 'json_url')
            if not json_data:
                return itens
            try:
                for stream in json_data:
                    try:
                        name = clean_channel_name(stream.get('name', '') or '')
                        stream_id = stream.get('stream_id')
                        if not stream_id:
                            continue
                        url_ = '{}{}.m3u8'.format(self.play_url, stream_id)
                        thumb = clean_text(stream.get('stream_icon', ''))
                        epg_title_raw = stream.get('epg_title') or ''
                        if epg_title_raw:
                            epg_title_clean = decode_b64_safe(epg_title_raw).strip()
                            if epg_title_clean:
                                display_name = '{} - {}'.format(name, color(epg_title_clean, 'gold'))
                            else:
                                display_name = name
                        else:
                            display_name = name
                        desc = ''
                        epg_channel_id = stream.get('epg_channel_id') or ''
                        if epg_channel_id:
                            programs = get_epg_programs(epg_channel_id, self.dns)
                            if programs:
                                current, nextp = epg_lookup_current_next(programs)
                                epg_desc = build_epg_desc(
                                    current=current,
                                    nextp=nextp,
                                    day_schedule=programs,
                                )
                                if epg_desc:
                                    desc = epg_desc
                                    if current:
                                        t = str(current.get('title') or '').strip()
                                        if t:
                                            display_name = '{} - {}'.format(name, color(t, 'gold'))
                        itens.append((display_name, url_, thumb, desc))
                    except Exception:
                        continue
                if itens:
                    itens = sorted(itens, key=lambda x: x[0].lower())
            except Exception as e:
                log_iptv_problem(url, 'Erro ao processar streams: {}'.format(e))
            return itens
        try:
            chan_id = url.split('cat_id=')[1].split('&')[0]
        except Exception:
            try:
                chan_id = url.split('category_id=')[1].split('&')[0]
            except Exception:
                chan_id = ''
        if not chan_id:
            return itens
        xml_data = self.http(url)
        if not xml_data:
            return itens
        try:
            url_json = '{}&action=get_live_streams&category_id={}'.format(self.player_api, chan_id)
            json_data = self.http(url_json, 'json_url')
            root = ET.fromstring(xml_data)
            channels = root.findall('channel')
            if not channels:
                return itens
            for i, channel in enumerate(channels):
                try:
                    title_elem = channel.find('title')
                    if title_elem is None:
                        continue
                    name = clean_channel_name(self.b64(title_elem.text))
                    stream_id = self.channel_id(json_data, i)
                    if not stream_id:
                        continue
                    url_ = '{}{}.m3u8'.format(self.play_url, stream_id)
                    try:
                        di = channel.find('desc_image')
                        thumb = di.text.replace('<![CDATA[ ', '').replace(' ]]>', '') if di is not None else ''
                    except Exception:
                        thumb = ''
                    display_name = name
                    desc = ''
                    if json_data and i < len(json_data):
                        stream = json_data[i]
                        epg_title_raw = stream.get('epg_title') or ''
                        if epg_title_raw:
                            epg_title_clean = decode_b64_safe(epg_title_raw).strip()
                            if epg_title_clean:
                                display_name = '{} - {}'.format(name, color(epg_title_clean, 'gold'))
                        epg_channel_id = stream.get('epg_channel_id') or ''
                        if epg_channel_id:
                            programs = get_epg_programs(epg_channel_id, self.dns)
                            if programs:
                                current, nextp = epg_lookup_current_next(programs)
                                epg_desc = build_epg_desc(
                                    current=current,
                                    nextp=nextp,
                                    day_schedule=programs,
                                )
                                if epg_desc:
                                    desc = epg_desc
                                if current:
                                    t = str(current.get('title') or '').strip()
                                    if t:
                                        display_name = '{} - {}'.format(name, color(t, 'gold'))
                    itens.append((display_name, url_, thumb, desc))
                except Exception:
                    continue
            if itens:
                itens = sorted(itens, key=lambda x: x[0].lower())
        except Exception as e:
            log_iptv_problem(url, 'Erro ao abrir canais: {}'.format(e))
        return itens

    def channels_open_epg(self, url):
        result = []
        ensure_epg_background(self.dns, self.username, self.password)
        if 'player_api.php' in url and 'action=get_live_streams' in url:
            json_data = self.http(url, 'json_url')
            if not json_data:
                return result
            for stream in json_data:
                try:
                    name = clean_channel_name(stream.get('name', '') or '')
                    stream_id = stream.get('stream_id')
                    if not stream_id:
                        continue
                    url_ = '{}{}.m3u8'.format(self.play_url, stream_id)
                    thumb = clean_text(stream.get('stream_icon', ''))
                    epg_channel_id = stream.get('epg_channel_id') or ''
                    result.append({
                        'name': name, 'icon': thumb, 'url': url_,
                        'programs': None,
                        'epg_channel_id': epg_channel_id,
                        'epg_dns': self.dns,
                    })
                except Exception:
                    continue
            if result:
                result = sorted(result, key=lambda x: x['name'].lower())
            return result

        try:
            chan_id = url.split('cat_id=')[1].split('&')[0]
        except Exception:
            try:
                chan_id = url.split('category_id=')[1].split('&')[0]
            except Exception:
                chan_id = ''
        if not chan_id:
            return result
        xml_data = self.http(url)
        if not xml_data:
            return result
        try:
            url_json = '{}&action=get_live_streams&category_id={}'.format(self.player_api, chan_id)
            json_data = self.http(url_json, 'json_url')
            root = ET.fromstring(xml_data)
            channels = root.findall('channel')
            if not channels:
                return result
            for i, channel in enumerate(channels):
                try:
                    title_elem = channel.find('title')
                    if title_elem is None:
                        continue
                    name = clean_channel_name(self.b64(title_elem.text))
                    stream_id = self.channel_id(json_data, i)
                    if not stream_id:
                        continue
                    url_ = '{}{}.m3u8'.format(self.play_url, stream_id)
                    try:
                        di = channel.find('desc_image')
                        thumb = di.text.replace('<![CDATA[ ', '').replace(' ]]>', '') if di is not None else ''
                    except Exception:
                        thumb = ''
                    programs = None
                    epg_channel_id = ''
                    if json_data and i < len(json_data):
                        stream = json_data[i]
                        epg_channel_id = stream.get('epg_channel_id') or ''
                    result.append({
                        'name': name, 'icon': thumb, 'url': url_,
                        'programs': programs,
                        'epg_channel_id': epg_channel_id,
                        'epg_dns': self.dns,
                    })
                except Exception:
                    continue
            if result:
                result = sorted(result, key=lambda x: x['name'].lower())
        except Exception as e:
            log_iptv_problem(url, 'Erro ao abrir guia de canais: {}'.format(e))
        return result
    def series_cat(self):
        itens = []
        url_ser = '{}&action=get_series_categories'.format(self.player_api)
        vod_cat = self.http(url_ser, 'json_url')
        if not vod_cat:
            return itens
        for cat in vod_cat:
            try:
                name = clean_category_name(cat.get('category_name', ''))
                if not name or not self.allow(name):
                    continue
                url = '{}&action=get_series&category_id={}'.format(
                    self.player_api, cat['category_id']
                )
                itens.append((name, url))
            except Exception:
                continue
        return itens
    def series_list(self, url):
        itens = []
        ser_cat = self.http(url, 'json_url')
        if not ser_cat:
            return itens
        for ser in ser_cat:
            try:
                name = first_clean_text(ser, 'name', 'title')
                series_id = ser.get('series_id', '')
                if not series_id or not name:
                    continue
                url_ = '{}&action=get_series_info&series_id={}'.format(
                    self.player_api, str(series_id)
                )
                thumb = clean_text(ser.get('cover', ''))
                background = clean_text(
                    ser.get('backdrop_path', [''])[0]
                    if isinstance(ser.get('backdrop_path'), list)
                    else ''
                )
                plot = clean_text(first_clean_text(ser, 'plot', 'description', 'overview'))
                releaseDate = clean_text(first_clean_text(ser, 'releaseDate', 'release_date', 'premiered'))
                cast = str(clean_text(ser.get('cast', ''))).split()
                rating_5based = clean_text(str(ser.get('rating_5based', '') or ser.get('rating', '')))
                episode_run_time = clean_text(str(ser.get('episode_run_time', '')))
                genre = clean_text(first_clean_text(ser, 'genre', 'genres'))
                itens.append((name, url_, thumb, background, plot,
                              releaseDate, cast, rating_5based, episode_run_time, genre))
            except Exception:
                continue
        return itens
    def series_seasons(self, url):
        itens = []
        ser_cat = self.http(url, 'json_url')
        if not ser_cat or 'episodes' not in ser_cat:
            return itens
        try:
            info = ser_cat.get('info', {})
            thumb = clean_text(info.get('cover', ''))
            background = clean_text(
                info.get('backdrop_path', [''])[0]
                if isinstance(info.get('backdrop_path'), list)
                else ''
            )
            for ser in ser_cat['episodes']:
                try:
                    name = 'Season - ' + str(ser)
                    url_ = '{}&season_number={}'.format(url, str(ser))
                    itens.append((name, url_, thumb, background))
                except Exception:
                    continue
        except Exception as e:
            log_iptv_problem(url, 'Erro ao obter temporadas: {}'.format(e))
        return itens
    def season_list(self, url):
        itens = []
        ser_cat = self.http(url, 'json_url')
        if not ser_cat or 'episodes' not in ser_cat:
            return itens
        try:
            info = ser_cat.get('info', {})
            episodes = ser_cat['episodes']
            parsed = urlparse(url)
            season_number = str(parse_qs(parsed.query)['season_number'][0])
            if season_number not in episodes:
                return itens
            for ser in episodes[season_number]:
                try:
                    episode_id = ser.get('id', '')
                    extension = clean_text(ser.get('container_extension', 'mp4')) or 'mp4'
                    if not episode_id:
                        continue
                    play_url = '{}{}.{}'.format(self.play_series, str(episode_id), extension)
                    name = first_clean_text(ser, 'title', 'name')
                    ep_info = ser.get('info', {})
                    thumb = clean_text(ep_info.get('movie_image', ''))
                    background = thumb
                    plot = clean_text(first_clean_text(ep_info, 'plot', 'description', 'overview'))
                    releasedate = clean_text(first_clean_text(ep_info, 'releasedate', 'release_date', 'aired'))
                    cast = str(clean_text(info.get('cast', ''))).split()
                    rating = clean_text(str(info.get('rating_5based', '') or info.get('rating', '')))
                    duration = clean_text(str(ep_info.get('duration', '') or ep_info.get('duration_secs', '')))
                    genre = clean_text(first_clean_text(info, 'genre', 'genres'))
                    itens.append((name, play_url, thumb, background, plot,
                                  releasedate, cast, rating, duration, genre))
                except Exception:
                    continue
        except Exception as e:
            log_iptv_problem(url, 'Erro ao listar episódios: {}'.format(e))
        return itens
    def all_movies(self):
        itens = []
        if not self.check_server_alive():
            return itens
        url = '{}&action=get_vod_streams'.format(self.player_api)
        data = self.http(url, 'json_url')
        if not isinstance(data, list) or not data:
            data = []
            try:
                url_cat = '{}&action=get_vod_categories'.format(self.player_api)
                cats = self.http(url_cat, 'json_url') or []
                for cat in cats:
                    try:
                        cid = cat.get('category_id')
                        if not cid:
                            continue
                        url_s = '{}&action=get_vod_streams&category_id={}'.format(self.player_api, cid)
                        part = self.http(url_s, 'json_url')
                        if isinstance(part, list):
                            data.extend(part)
                    except Exception:
                        continue
            except Exception:
                data = []
        if not data:
            return itens
        for m in data:
            try:
                name = first_clean_text(m, 'name', 'title')
                stream_id = m.get('stream_id')
                if not name or not stream_id:
                    continue
                if not self.allow(name):
                    continue
                ext = clean_text(m.get('container_extension', '')) or 'mp4'
                icon = clean_text(m.get('stream_icon', '') or m.get('cover', ''))
                year = clean_text(str(m.get('year', '') or '')) or None
                rating = clean_text(str(m.get('rating', '') or ''))
                itens.append({
                    'stream_id': stream_id,
                    'name': name,
                    'icon': icon,
                    'extension': ext,
                    'year': year,
                    'rating': rating,
                })
            except Exception:
                continue
        return itens
    def all_series(self):
        itens = []
        if not self.check_server_alive():
            return itens
        url = '{}&action=get_series'.format(self.player_api)
        data = self.http(url, 'json_url')
        if not isinstance(data, list) or not data:
            data = []
            try:
                url_cat = '{}&action=get_series_categories'.format(self.player_api)
                cats = self.http(url_cat, 'json_url') or []
                for cat in cats:
                    try:
                        cid = cat.get('category_id')
                        if not cid:
                            continue
                        url_s = '{}&action=get_series&category_id={}'.format(self.player_api, cid)
                        part = self.http(url_s, 'json_url')
                        if isinstance(part, list):
                            data.extend(part)
                    except Exception:
                        continue
            except Exception:
                data = []
        if not data:
            return itens
        for s in data:
            try:
                name = first_clean_text(s, 'name', 'title')
                series_id = s.get('series_id')
                if not name or not series_id:
                    continue
                if not self.allow(name):
                    continue
                cover = clean_text(s.get('cover', ''))
                release = clean_text(first_clean_text(s, 'releaseDate', 'release_date') or '')
                year = release[:4] if release else None
                itens.append({
                    'series_id': series_id,
                    'name': name,
                    'cover': cover,
                    'year': year,
                })
            except Exception:
                continue
        return itens
    def movie_play_url(self, stream_id, extension):
        ext = extension or 'mp4'
        return self.check_protocol('{}{}.{}'.format(self.play_movies, str(stream_id), ext))
    def get_episode_stream(self, series_id, season, episode):
        if not self.check_server_alive():
            return None
        url = '{}&action=get_series_info&series_id={}'.format(self.player_api, str(series_id))
        data = self.http(url, 'json_url')
        if not data or 'episodes' not in data:
            return None
        try:
            episodes_by_season = data['episodes']
            season_key = str(int(season))
            eps = episodes_by_season.get(season_key)
            if not eps:
                return None
            target = int(episode)
            for ep in eps:
                try:
                    ep_num = int(ep.get('episode_num') or ep.get('episode') or 0)
                except Exception:
                    continue
                if ep_num != target:
                    continue
                episode_id = ep.get('id')
                if not episode_id:
                    continue
                extension = clean_text(ep.get('container_extension', 'mp4')) or 'mp4'
                return self.check_protocol(
                    '{}{}.{}'.format(self.play_series, str(episode_id), extension)
                )
        except Exception as e:
            log_iptv_problem(self.dns, 'Erro ao obter episódio: {}'.format(e))
        return None
    def vod(self, url=''):
        itens = []
        open_data = self.http(url or self.vod_url, 'vod' if url else None)
        if not open_data:
            return itens
        try:
            all_cats = self.regex_get_all(open_data, '<channel>', '</channel>')
            if not all_cats:
                return itens
            for a in all_cats:
                try:
                    if '<playlist_url>' in open_data:
                        name = clean_text(self.b64(self.regex_from_to(a, '<title>', '</title>')))
                        vod_url = self.check_protocol(
                            self.regex_from_to(a, '<playlist_url>', '</playlist_url>')
                            .replace('<![CDATA[', '').replace(']]>', '')
                        )
                        if not name or 'All' in name or not self.allow(name):
                            continue
                        itens.append(('dir', name, vod_url))
                    else:
                        name = clean_text(self.b64(self.regex_from_to(a, '<title>', '</title>')))
                        thumb = self.regex_from_to(a, '<desc_image>', '</desc_image>').replace('<![CDATA[', '').replace(']]>', '')
                        vod_url = self.check_protocol(
                            self.regex_from_to(a, '<stream_url>', '</stream_url>')
                            .replace('<![CDATA[', '').replace(']]>', '')
                        )
                        desc = self.b64(self.regex_from_to(a, '<description>', '</description>'))
                        plot = clean_text(self.regex_from_to(desc, 'PLOT:', '\n'))
                        cast_s = self.regex_from_to(desc, 'CAST:', '\n')
                        ratin = clean_text(self.regex_from_to(desc, 'RATING:', '\n'))
                        year_s = self.regex_from_to(desc, 'RELEASEDATE:', '\n').replace(' ', '-')
                        ym = re.compile(r'-.*?-.*?-(.*?)-', re.DOTALL).findall(year_s)
                        year = str(ym).replace("['", '').replace("']", '') if ym else ''
                        runt = clean_text(self.regex_from_to(desc, 'DURATION_SECS:', '\n'))
                        genre = clean_text(self.regex_from_to(desc, 'GENRE:', '\n'))
                        background = ''
                        cast_list = str(cast_s).split() if cast_s else []
                        itens.append(('play', name, vod_url, thumb, background,
                                      plot, year, cast_list, ratin, genre))
                except Exception:
                    continue
        except Exception as e:
            log_iptv_problem(url or self.vod_url, 'Erro ao processar VOD: {}'.format(e))
        return itens
