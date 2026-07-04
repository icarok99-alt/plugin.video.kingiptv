# -*- coding: utf-8 -*-

from lib.helper import *
import uuid
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

PLUTO_HEADERS = {
    'User-Agent': USER_AGENT,
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    'Origin': 'https://pluto.tv',
    'Referer': 'https://pluto.tv/',
}

BR_TZ = timezone(timedelta(hours=-3))


def _parse_iso_datetime(s):
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
    try:
        resp = requests.get('https://worldtimeapi.org/api/timezone/America/Sao_Paulo', timeout=6)
        resp.raise_for_status()
        data = resp.json()
        dt_str = data.get('datetime')
        dt = _parse_iso_datetime(dt_str)
        if dt is None:
            raise ValueError
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def _build_program(entry):
    start = _parse_iso_datetime(entry.get('start'))
    stop = _parse_iso_datetime(entry.get('stop'))
    if not start or not stop:
        return None
    ep = entry.get('episode', {})
    return {
        'title': ep.get('name', ''),
        'description': ep.get('description', ''),
        'start': start,
        'stop': stop,
    }


def _get_current_and_next_program(timelines, now):
    programs = []
    for entry in timelines:
        prog = _build_program(entry)
        if prog:
            programs.append(prog)

    if not programs:
        return None, None

    programs.sort(key=lambda p: p['start'])

    current_program = None
    next_program = None

    for idx, prog in enumerate(programs):
        if prog['start'] <= now <= prog['stop']:
            current_program = prog
            if idx + 1 < len(programs):
                next_program = programs[idx + 1]
            break

    if current_program is None:
        future_programs = [p for p in programs if p['start'] >= now]
        if future_programs:
            next_program = future_programs[0]
        else:
            current_program = programs[-1]

    return current_program, next_program


def _format_program_line(program):
    local_time = program['start'].astimezone(BR_TZ)
    title = program['title']
    description = program['description']
    return f"[COLOR yellow][{local_time.strftime('%H:%M')}] {title}[/COLOR]\n({description})\n"


def _build_stream_url(channel, params, session_token):
    stitched_urls = channel.get('stitched', {}).get('urls', [])
    if not stitched_urls:
        return None

    stream_url = stitched_urls[0].get('url')
    if not stream_url:
        return None

    try:
        stream_url = stream_url.split('?')[0].replace('/stitch/hls/', '/v2/stitch/hls/')
        stream_url = f"{stream_url}?{params}&jwt={session_token}&masterJWTPassthrough=true&includeExtendedEvents=true&eventVOD=false&CMCD=mtp=1000,ot=m,sf=h"
        stream_url = stream_url + '|User-Agent=' + quote_plus(USER_AGENT)
    except Exception:
        return None

    return stream_url


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
            r = requests.get(f'https://boot.pluto.tv/v4/start?appName=web&appVersion=9.19.0-7a6c115631d945c4f7327de3e03b7c474b692657&deviceVersion=148.0.0&deviceModel=web&deviceMake=firefox&deviceType=web&clientID=df8c4848-8b94-4323-9ca6-d0b802a9589c&clientModelNumber=1.0.0&channelSlug=5f120e94a5714d00074576a1&serverSideAds=false&drmCapabilities=widevine%3AL3&blockingMode=&notificationVersion=1&appLaunchCount=0&lastAppLaunchDate={from_str}&clientTime={to_str}', headers=PLUTO_HEADERS, timeout=10)
            r.raise_for_status()
            data_api = r.json()
            session_token = data_api.get('sessionToken', '')
            params = data_api.get('stitcherParams', '')
        except Exception as e:
            log(f'playlist_pluto: falha ao obter sessionToken/stitcherParams: {e}')
            params = ''
            session_token = ''

        try:
            resp = requests.get(url, headers=PLUTO_HEADERS, timeout=10)
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
            stream_url = _build_stream_url(channel, params, session_token)

            timelines = channel.get('timelines', [])
            current_program, next_program = _get_current_and_next_program(timelines, time_brazil)

            desc = ''
            if current_program:
                desc += _format_program_line(current_program)
            if next_program:
                desc += _format_program_line(next_program)

            name_for_kodi = channel_name
            if current_program and current_program.get('title'):
                name_for_kodi = f"{channel_name} - [COLOR yellow]{current_program.get('title')}[/COLOR]"

            channels_kodi.append((name_for_kodi, desc, thumb, stream_url))

    except Exception as e:
        log(f'playlist_pluto: erro geral: {e}')

    return channels_kodi
