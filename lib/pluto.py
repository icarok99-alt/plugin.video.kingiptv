
from lib.helper import *

import uuid
import re
from datetime import datetime, timedelta, timezone
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

def playlist_pluto():
    channels_kodi = []
    try:
        deviceid = str(uuid.uuid4())
        time_brazil = get_current_time()
        from_utc = time_brazil.astimezone(timezone.utc)
        to_utc = (time_brazil + timedelta(days=1)).astimezone(timezone.utc)
        from_str = from_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        to_str = to_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        url = f'https://api.pluto.tv/v2/channels?start={from_str}&stop={to_str}'
        try:
            r = SESSION.get(f'https://boot.pluto.tv/v4/start?appName=web&appVersion=9.19.0-7a6c115631d945c4f7327de3e03b7c474b692657&deviceVersion=148.0.0&deviceModel=web&deviceMake=firefox&deviceType=web&clientID=df8c4848-8b94-4323-9ca6-d0b802a9589c&clientModelNumber=1.0.0&channelSlug=5f120e94a5714d00074576a1&serverSideAds=false&drmCapabilities=widevine%3AL3&blockingMode=&notificationVersion=1&appLaunchCount=0&lastAppLaunchDate={from_str}&clientTime={to_str}', headers=PLUTO_HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data_api = r.json()
            session_token = data_api.get('sessionToken', '')
            params = data_api.get('stitcherParams', '')
        except Exception as e:
            log(f'playlist_pluto: falha ao obter sessionToken/stitcherParams: {e}')
            params = ''
            session_token = ''

        try:
            resp = SESSION.get(url, headers=PLUTO_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            channels = resp.json()
        except Exception as e:
            log(f'playlist_pluto: falha ao obter lista de canais: {e}')
            return channels_kodi

        for channel in channels:
            number = channel.get('number', 0)
            if not number or int(number) <= 0:
                continue

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
                    except:
                        pass


            timelines = channel.get('timelines', [])
            current_program = None
            next_program = None
            for idx, t in enumerate(timelines):
                start = parse_iso_datetime(t.get('start'))
                stop = parse_iso_datetime(t.get('stop'))
                if not start or not stop:
                    continue
                if start <= time_brazil <= stop:
                    ep = t.get('episode', {})
                    current_program = {
                        'title': ep.get('name', ''),
                        'description': ep.get('description', ''),
                        'start': start,
                        'stop': stop
                    }
                    if idx + 1 < len(timelines):
                        nt = timelines[idx + 1]
                        ns = parse_iso_datetime(nt.get('start'))
                        ne = parse_iso_datetime(nt.get('stop'))
                        nep = nt.get('episode', {})
                        next_program = {
                            'title': nep.get('name', ''),
                            'description': nep.get('description', ''),
                            'start': ns,
                            'stop': ne
                        }
                    break

            desc = ''
            if current_program:
                local_now = current_program['start'].astimezone(timezone(timedelta(hours=-3)))
                desc += f"[COLOR yellow][{local_now.strftime('%H:%M')}] {current_program['title']}[/COLOR]\n({current_program['description']})\n"
            if next_program:
                local_next = next_program['start'].astimezone(timezone(timedelta(hours=-3)))
                desc += f"[COLOR yellow][{local_next.strftime('%H:%M')}] {next_program['title']}[/COLOR]\n({next_program['description']})\n"

            name_for_kodi = channel_name
            if current_program and current_program.get('title'):
                name_for_kodi = f"{channel_name} - [COLOR yellow]{current_program.get('title')}[/COLOR]"

            channels_kodi.append((name_for_kodi, desc, thumb, stream_url))

    except Exception as e:
        log(f'playlist_pluto: erro geral: {e}')

    return channels_kodi


def playlist_pluto_epg():
    result = []
    try:
        time_brazil = get_current_time()
        from_utc = time_brazil.astimezone(timezone.utc)
        to_utc = (time_brazil + timedelta(days=1)).astimezone(timezone.utc)
        from_str = from_utc.strftime('%Y-%m-%dT%H:%M:%SZ')
        to_str = to_utc.strftime('%Y-%m-%dT%H:%M:%SZ')

        url = f'https://api.pluto.tv/v2/channels?start={from_str}&stop={to_str}'
        try:
            r = SESSION.get(f'https://boot.pluto.tv/v4/start?appName=web&appVersion=9.19.0-7a6c115631d945c4f7327de3e03b7c474b692657&deviceVersion=148.0.0&deviceModel=web&deviceMake=firefox&deviceType=web&clientID=df8c4848-8b94-4323-9ca6-d0b802a9589c&clientModelNumber=1.0.0&channelSlug=5f120e94a5714d00074576a1&serverSideAds=false&drmCapabilities=widevine%3AL3&blockingMode=&notificationVersion=1&appLaunchCount=0&lastAppLaunchDate={from_str}&clientTime={to_str}', headers=PLUTO_HEADERS, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data_api = r.json()
            session_token = data_api.get('sessionToken', '')
            params = data_api.get('stitcherParams', '')
        except Exception as e:
            log(f'playlist_pluto_epg: falha ao obter sessionToken/stitcherParams: {e}')
            params = ''
            session_token = ''

        try:
            resp = SESSION.get(url, headers=PLUTO_HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            channels = resp.json()
        except Exception as e:
            log(f'playlist_pluto_epg: falha ao obter lista de canais: {e}')
            return result

        for channel in channels:
            number = channel.get('number', 0)
            if not number or int(number) <= 0:
                continue

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
            for t in channel.get('timelines', []) or []:
                start_dt = parse_iso_datetime(t.get('start'))
                stop_dt = parse_iso_datetime(t.get('stop'))
                if not start_dt or not stop_dt:
                    continue
                ep = t.get('episode', {}) or {}
                programs.append({
                    'title': ep.get('name', '') or '',
                    'desc': ep.get('description', '') or '',
                    'start': int(start_dt.timestamp()),
                    'end': int(stop_dt.timestamp()),
                })
            programs.sort(key=lambda p: p.get('start') or 0)

            result.append({
                'name': channel_name,
                'icon': thumb,
                'url': stream_url,
                'programs': programs,
            })

    except Exception as e:
        log(f'playlist_pluto_epg: erro geral: {e}')

    return result
