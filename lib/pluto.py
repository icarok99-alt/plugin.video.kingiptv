# -*- coding: utf-8 -*-

import re
import threading
import time
from datetime import datetime, timedelta, timezone
from lib.helper import *
from urllib.parse import quote_plus
from requests.adapters import HTTPAdapter
try:
    from urllib3.util.retry import Retry
except Exception:
    from requests.packages.urllib3.util.retry import Retry

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

PLUTO_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Origin': 'https://pluto.tv',
    'Referer': 'https://pluto.tv/',
    'Connection': 'keep-alive',
}

REQUEST_TIMEOUT = 20

def build_session():
    session = requests.Session()
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET', 'HEAD']),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    return session

SESSION = build_session()

epg_fetch_active = threading.Event()

PLUTO_EPG_TTL = 86400
PLUTO_EPG_CACHE_PATH = os.path.join(profile, 'epg_pluto_index.json')


def _pluto_safe_read_json(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _pluto_safe_write_json(path, data):
    try:
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
        os.rename(tmp, path)
        return True
    except Exception:
        return False


def load_pluto_epg_disk():
    today = current_day_key()
    cached = _pluto_safe_read_json(PLUTO_EPG_CACHE_PATH)
    if cached.get('day') != today:
        return None
    generated_at = int(cached.get('generated_at') or 0)
    if not generated_at or (time.time() - generated_at) >= PLUTO_EPG_TTL:
        return None
    channels = cached.get('channels')
    if not isinstance(channels, list) or not channels:
        return None
    return channels


def save_pluto_epg_disk(channels, day):
    _pluto_safe_write_json(PLUTO_EPG_CACHE_PATH, {
        'day': day,
        'generated_at': int(time.time()),
        'channels': channels,
    })


def ensure_pluto_epg_background():
    if load_pluto_epg_disk() is not None:
        return
    if epg_fetch_active.is_set():
        return

    def worker():
        epg_fetch_active.set()
        try:
            playlist_pluto_epg()
        except Exception:
            pass
        finally:
            epg_fetch_active.clear()

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def parse_iso_datetime(s):
    if not s:
        return None
    s = s.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    s = re.sub(r'([+-]\d{2}:\d)(?!\d)', lambda m: m.group(1) + '0', s)
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        for fmt in ('%Y-%m-%dT%H:%M:%S.%f%z', '%Y-%m-%dT%H:%M:%S%z'):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return None

def get_current_time():
    return datetime.now(timezone(timedelta(hours=-3)))

def current_day_key():
    return get_current_time().strftime('%Y-%m-%d')

PLUTO_DNS_SENTINEL = '__pluto__'

PLUTO_PROGRAMS_INDEX_LOCK = threading.Lock()
PLUTO_PROGRAMS_INDEX = {'data': None, 'day': None}


def _pluto_programs_index():
    today = current_day_key()
    with PLUTO_PROGRAMS_INDEX_LOCK:
        if PLUTO_PROGRAMS_INDEX['data'] is not None and PLUTO_PROGRAMS_INDEX['day'] == today:
            return PLUTO_PROGRAMS_INDEX['data']
    channels = load_pluto_epg_disk() or []
    index = {}
    for ch in channels:
        index[ch.get('name') or ''] = ch.get('programs') or []
    with PLUTO_PROGRAMS_INDEX_LOCK:
        PLUTO_PROGRAMS_INDEX['data'] = index
        PLUTO_PROGRAMS_INDEX['day'] = today
    return index


def get_pluto_epg_programs(channel_name, limit=48):
    index = _pluto_programs_index()
    programs = index.get(channel_name) or []
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


def to_lazy_channels(channels):
    lite = []
    for ch in channels or []:
        name = ch.get('name') or ''
        lite.append({
            'name': name,
            'icon': ch.get('icon'),
            'url': ch.get('url'),
            'programs': None,
            'epg_channel_id': name,
            'epg_dns': PLUTO_DNS_SENTINEL,
        })
    return lite


PLUTO_EPG_WINDOW_HOURS = 6


def _pluto_day_windows(time_brazil):
    day_start = time_brazil.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    windows = []
    cursor = day_start
    while cursor < day_end:
        nxt = min(cursor + timedelta(hours=PLUTO_EPG_WINDOW_HOURS), day_end)
        windows.append((cursor, nxt))
        cursor = nxt
    return windows


def playlist_pluto_epg(force_refresh=False):
    today = current_day_key()
    if not force_refresh:
        disk_channels = load_pluto_epg_disk()
        if disk_channels is not None:
            return disk_channels

    result = []
    try:
        time_brazil = get_current_time()
        from_utc = time_brazil.astimezone(timezone.utc)
        to_utc = (time_brazil + timedelta(days=1)).astimezone(timezone.utc)
        from_str = from_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        to_str = to_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        boot_url = (
            'https://boot.pluto.tv/v4/start?appName=web&appVersion=9.19.0-7a6c115631d945c4f7327de3e03b7c474b692657'
            '&deviceVersion=148.0.0&deviceModel=web&deviceMake=firefox&deviceType=web'
            '&clientID=df8c4848-8b94-4323-9ca6-d0b802a9589c&clientModelNumber=1.0.0'
            '&channelSlug=5f120e94a5714d00074576a1&serverSideAds=false&drmCapabilities=widevine%3AL3'
            f'&blockingMode=&notificationVersion=1&appLaunchCount=0&lastAppLaunchDate={from_str}&clientTime={to_str}'
        )

        boot_result = {}
        window_results = {}
        windows = _pluto_day_windows(time_brazil)

        def fetch_boot():
            try:
                r = SESSION.get(boot_url, headers=PLUTO_HEADERS, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                boot_result['data'] = r.json()
            except Exception as e:
                boot_result['error'] = e

        def fetch_window(idx, win_start, win_stop):
            try:
                w_from = win_start.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                w_to = win_stop.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                w_url = f'https://api.pluto.tv/v2/channels?start={w_from}&stop={w_to}'
                resp = SESSION.get(w_url, headers=PLUTO_HEADERS, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                window_results[idx] = resp.json()
            except Exception:
                window_results[idx] = []

        threads = [threading.Thread(target=fetch_boot, daemon=True)]
        for idx, (win_start, win_stop) in enumerate(windows):
            threads.append(threading.Thread(target=fetch_window, args=(idx, win_start, win_stop), daemon=True))
        for th in threads:
            th.start()
        for th in threads:
            th.join(timeout=REQUEST_TIMEOUT + 5)

        if 'error' in boot_result:
            params = ''
            session_token = ''
        else:
            data_api = boot_result.get('data') or {}
            session_token = data_api.get('sessionToken', '')
            params = data_api.get('stitcherParams', '')

        merged = {}
        for idx in range(len(windows)):
            for channel in window_results.get(idx) or []:
                number = channel.get('number', 0)
                if not number or int(number) <= 0:
                    continue
                key = channel.get('_id') or channel.get('slug') or channel.get('name')
                entry = merged.get(key)
                if entry is None:
                    entry = {'meta': channel, 'timelines': []}
                    merged[key] = entry
                entry['timelines'].extend(channel.get('timelines', []) or [])

        if not merged:
            return result

        for entry in merged.values():
            channel = entry['meta']
            number = channel.get('number', 0)
            channel_name = channel.get('name', f'#{number}')
            thumb = channel.get('logo', {}).get('path', '')
            stream_url = None

            stitched_urls = channel.get('stitched', {}).get('urls', [])
            if stitched_urls:
                stream_url = stitched_urls[0].get('url')
                if stream_url:
                    try:
                        stream_url = stream_url.split('?')[0].replace("/stitch/hls/", "/v2/stitch/hls/")
                        stream_url = f"{stream_url}?{params}&jwt={session_token}&masterJWTPassthrough=true&includeExtendedEvents=true&eventVOD=false&CMCD=mtp=1000,ot=m,sf=h"
                        stream_url = (
                            stream_url
                            + '|User-Agent=' + quote_plus(USER_AGENT)
                            + '&Referer=' + quote_plus('https://pluto.tv/')
                            + '&Origin=' + quote_plus('https://pluto.tv')
                        )
                    except Exception:
                        pass

            programs = []
            seen_starts = set()
            for t in entry['timelines']:
                start_dt = parse_iso_datetime(t.get('start'))
                stop_dt = parse_iso_datetime(t.get('stop'))
                if not start_dt or not stop_dt:
                    continue
                start_ts = int(start_dt.timestamp())
                if start_ts in seen_starts:
                    continue
                seen_starts.add(start_ts)
                ep = t.get('episode', {}) or {}
                programs.append({
                    'title': ep.get('name', '') or '',
                    'desc': ep.get('description', '') or '',
                    'start': start_ts,
                    'end': int(stop_dt.timestamp()),
                })
            programs.sort(key=lambda p: p.get('start') or 0)

            result.append({
                'name': channel_name,
                'icon': thumb,
                'url': stream_url,
                'programs': programs,
            })

    except Exception:
        pass

    if result:
        save_pluto_epg_disk(result, today)

    return result
