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

CATEGORY_CACHE = {}
CATEGORY_CACHE_TTL = 3600
CATEGORY_CACHE_LOCK = threading.Lock()

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
    sufixos_res = [
        'HD', 'FHD', 'SD', '4K', 'UHD', 'HD+', 'HD¹', 'HD²', 'HD2', 'HD1',
        'FHD¹', 'FHD²', 'SD¹', 'SD²', '4K¹', '4K²', 'UHD¹', 'UHD²',
        'H264', 'H265', 'H264¹', 'H264²', 'H265¹', 'H265²',
        'PLUS', 'PLUS¹', 'PLUS²', 'PREMIUM', 'PREMIUM¹', 'PREMIUM²',
        'MAX', 'MAX¹', 'MAX²',
    ]
    pattern = r'\[(' + '|'.join(re.escape(s) for s in sufixos_res) + r')\]'
    name = re.sub(pattern, r'\1', name)
    codecs = ['H264', 'H265', 'HEVC', 'AVC', 'X264', 'X265']
    pattern2 = r'\((' + '|'.join(re.escape(c) for c in codecs) + r')\)'
    name = re.sub(pattern2, r'\1', name)
    name = re.sub(r'\s*\[\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}\]', '', name)
    name = re.sub(r'\s*\+\s*\d+\.?\d*\s*min', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name

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

    deduped = []
    last_start = None
    for p in programs:
        start = p.get('start') or 0
        if start > 0 and start == last_start:
            continue
        deduped.append(p)
        last_start = start
    return deduped

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
            response = requests.get(url, timeout=20, headers=headers, allow_redirects=True, verify=False, stream=True)
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
    window_start = now_ts - 21600
    window_end = now_ts + 129600
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
        total_dedup_removed = 0
        for cid in channels:
            channels[cid].sort(key=lambda x: x.get('start') or 0)
            deduped = []
            seen = set()
            for prog in channels[cid]:
                key = (prog.get('start') or 0, (prog.get('title') or '').strip().lower())
                if key in seen:
                    total_dedup_removed += 1
                    continue
                seen.add(key)
                deduped.append(prog)
            channels[cid] = deduped
        if total_dedup_removed:
            log_iptv_problem(dns, f'Deduplicação: {total_dedup_removed} programas repetidos removidos')
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

OFFLINE_ACCOUNTS_FILE = os.path.join(profile, 'offline_accounts.json')

def _offline_marker(dns, username, password):
    return '{0}/get.php?username={1}&password={2}'.format(dns, username, password)

def mark_account_offline(dns, username, password):
    marker = _offline_marker(dns, username, password)
    data = safe_read_json(OFFLINE_ACCOUNTS_FILE)
    if not isinstance(data, dict):
        data = {}
    data[marker] = True
    safe_write_json(OFFLINE_ACCOUNTS_FILE, data)

def clear_account_offline(dns, username, password):
    marker = _offline_marker(dns, username, password)
    data = safe_read_json(OFFLINE_ACCOUNTS_FILE)
    if isinstance(data, dict) and marker in data:
        data.pop(marker, None)
        safe_write_json(OFFLINE_ACCOUNTS_FILE, data)

def is_account_marked_offline(dns, username, password):
    marker = _offline_marker(dns, username, password)
    data = safe_read_json(OFFLINE_ACCOUNTS_FILE)
    return bool(isinstance(data, dict) and marker in data)

def _account_marked_offline(dns, username, password):
    return is_account_marked_offline(dns, username, password)

def ensure_epg_background(dns, username, password):
    if epg_index_fresh(dns, username, password):
        return
    if is_account_marked_offline(dns, username, password):
        return
    lock_key = epg_fingerprint(dns, username, password)
    with EPG_ACTIVE_LOCK:
        if lock_key in EPG_ACTIVE:
            return
        EPG_ACTIVE.add(lock_key)
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
                EPG_ACTIVE.discard(lock_key)
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
    seen = set()
    for p in programs:
        dedup_key = (p.get('start') or 0, (p.get('title') or '').strip().lower())
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
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
                    if 'http' in i:
                        dns, username, password = extract_info(i)
                        if dns and username and password:
                            iptv.append((dns, username, password))
            except Exception as e:
                log_iptv_problem(url, 'Erro paste.kodi.tv: {}'.format(e))
        else:
            src = session.get(url, timeout=REQUEST_TIMEOUT).text
            for i in src.split('\n'):
                i = i.replace(' ', '')
                if 'http' in i:
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
        self.adult_tags = ['xxx', 'xXx', 'XXX', 'adult', 'Adult', 'ADULT',
                           'porn', 'Porn', 'PORN', 'teste', 'TESTE', 'Teste']
        self.hide_adult = hide_adult
        self.session = create_session()
    def b64(self, obj):
        return decode_b64_safe(obj)
    def check_protocol(self, url):
        try:
            if urlparse(self.player_api).scheme == 'https':
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
    def http(self, url='', mode=None):
        try:
            if not mode:
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    raise requests.exceptions.HTTPError('HTTP {}'.format(r.status_code))
                return r.content
            elif mode == 'json_url':
                r = self.session.get(url, timeout=REQUEST_TIMEOUT)
                if r.status_code != 200:
                    raise requests.exceptions.HTTPError('HTTP {}'.format(r.status_code))
                return r.json()
        except Exception as e:
            log_iptv_problem(url or self.player_api, 'Erro HTTP: {}'.format(e))
            CACHE_FAILED_URLS[url or self.player_api] = time.time()
        return '' if mode != 'json_url' else None
    def channels_category(self):
        cache_key = '{}|{}|{}'.format(self.dns, self.username, self.password)
        with CATEGORY_CACHE_LOCK:
            cached = CATEGORY_CACHE.get(cache_key)
            if cached and (time.time() - cached['timestamp']) < CATEGORY_CACHE_TTL:
                return cached['data']
        itens = []
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
        if itens:
            with CATEGORY_CACHE_LOCK:
                CATEGORY_CACHE[cache_key] = {
                    'data': itens,
                    'timestamp': time.time()
                }
        return itens
    def channels_open_epg(self, url):
        result = []
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
