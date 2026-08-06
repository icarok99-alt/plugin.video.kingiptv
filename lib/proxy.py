# -*- coding: utf-8 -*-

import socket
import threading
import random
import time
import os
import re
from collections import deque
from urllib.parse import urlparse, unquote, urljoin, parse_qs, urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import ssl
import gzip
import zlib
import socketserver
import http.client
import concurrent.futures
try:
    import xbmcvfs
except ImportError:
    xbmcvfs = None

PROXY_PORT_POOL = [57845, 57846, 57847, 57848, 57849, 57850]
PROXY_PORT = PROXY_PORT_POOL[0]
port_state_lock = threading.Lock()

def get_active_port():
    with port_state_lock:
        return PROXY_PORT

def set_active_port(port):
    global PROXY_PORT
    with port_state_lock:
        PROXY_PORT = port
    persist_port(port)

def port_state_path():
    try:
        if xbmcvfs is not None:
            base = xbmcvfs.translatePath(
                'special://profile/addon_data/plugin.video.kingiptv/'
            )
        else:
            base = os.path.join(os.path.expanduser("~"), ".kingiptv_proxy")
        if base and not os.path.isdir(base):
            os.makedirs(base, exist_ok=True)
        return os.path.join(base, "active_proxy_port.txt")
    except Exception:
        return None

def persist_port(port):
    path = port_state_path()
    if not path:
        return
    try:
        with open(path, "w") as f:
            f.write(str(port))
    except Exception:
        pass

def read_persisted_port():
    path = port_state_path()
    if not path:
        return None
    try:
        with open(path, "r") as f:
            value = f.read().strip()
        return int(value) if value else None
    except Exception:
        return None

def get_preferred_port():
    return read_persisted_port()

def is_port_free(port, host="127.0.0.1"):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    try:
        return s.connect_ex((host, port)) != 0
    except Exception:
        return True
    finally:
        try:
            s.close()
        except Exception:
            pass

MAX_RETRIES = 3
RETRY_DELAY = 0.3
BUFFER_SIZE = 65536

SEGMENT_INLINE_RETRIES = 3
SEGMENT_INLINE_RETRY_DELAY = 0.3

PLAYLIST_FETCH_TIMEOUT = 10
SEGMENT_FETCH_TIMEOUT = 15

DEFAULT_SEGMENT_DURATION = 6.0
MIN_REFRESH_INTERVAL = 2.0
MAX_WAIT_FOR_NEW_SEGMENTS = 1.0
SERVED_IDS_MAX = 400
TRICKLE_INTERVAL_TARGET = 0.1
TRICKLE_MIN_TS_PACKETS = 8
BUFFER_AHEAD_SECONDS = 6.0

REFRESH_LEAD_TIME = 5.0

MAX_ACTIVE_CHANNEL_STREAMS = 12
MAX_CONCURRENT_HANDLERS = 20
CHANNEL_STATE_TTL = 300
CACHE_CLEANUP_INTERVAL = 60
SOCKET_IDLE_TIMEOUT = 10
SOCKET_STREAM_TIMEOUT = 10

EXTINF_RE = re.compile(r'#EXTINF:\s*([\d.]+)')
TARGETDURATION_RE = re.compile(r'#EXT-X-TARGETDURATION:\s*(\d+(?:\.\d+)?)')
MEDIA_SEQUENCE_RE = re.compile(r'#EXT-X-MEDIA-SEQUENCE:\s*(\d+)')


AUTH_ERROR_CODES = {401, 403}
NOT_FOUND_CODES = {404, 410}
BLOCKED_CODES = {451}
RATE_LIMIT_CODES = {429}
SERVER_ERROR_CODES = {500, 502, 503, 504}
NON_RETRYABLE_CODES = AUTH_ERROR_CODES | NOT_FOUND_CODES | BLOCKED_CODES
MAX_CONSECUTIVE_AUTH_FAILURES = 2


def classify_status(status):
    if status is None:
        return 'network'
    if status in AUTH_ERROR_CODES:
        return 'auth'
    if status in NOT_FOUND_CODES:
        return 'not_found'
    if status in BLOCKED_CODES:
        return 'blocked'
    if status in RATE_LIMIT_CODES:
        return 'rate_limit'
    if status in SERVER_ERROR_CODES:
        return 'server_error'
    if status in (200, 206):
        return 'ok'
    return 'unknown'


ERROR_KIND_TO_CLIENT_STATUS = {
    'auth': 401,
    'not_found': 404,
    'blocked': 451,
    'rate_limit': 429,
    'server_error': 503,
    'network': 503,
    'timeout': 503,
    'unknown': 503,
}

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.7827.200 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.7827.200 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
]

def get_origin(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return "{}://{}".format(parsed.scheme, parsed.netloc)
    except Exception:
        pass
    return ''

class ConnectionPool:
    def __init__(self, ssl_context, timeout=15):
        self.ssl_context = ssl_context
        self.timeout = timeout
        self._lock = threading.Lock()
        self._conns = {}

    @staticmethod
    def _key(parsed):
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        return (parsed.scheme, parsed.hostname, port)

    def _new_conn(self, parsed, timeout):
        if parsed.scheme == 'https':
            return http.client.HTTPSConnection(
                parsed.hostname, parsed.port, timeout=timeout, context=self.ssl_context
            )
        return http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=timeout)

    def request(self, url, method='GET', headers=None, timeout=None, max_redirects=5):
        timeout = timeout or self.timeout
        current_url = url
        headers = dict(headers or {})
        headers['Connection'] = 'keep-alive'
        for _ in range(max_redirects + 1):
            parsed = urlsplit(current_url)
            key = self._key(parsed)
            path = (parsed.path or '/') + (('?' + parsed.query) if parsed.query else '')
            
            with self._lock:
                conn = self._conns.pop(key, None)
                
            for attempt in range(2):
                if conn is None:
                    conn = self._new_conn(parsed, timeout)
                try:
                    conn.timeout = timeout
                    conn.request(method, path, headers=headers)
                    resp = conn.getresponse()
                    body = resp.read()
                    status = resp.status
                    resp_headers = {k.lower(): v for k, v in resp.getheaders()}
                    
                    if resp_headers.get('connection', '').lower() != 'close':
                        with self._lock:
                            self._conns[key] = conn
                    else:
                        try:
                            conn.close()
                        except Exception:
                            pass

                    if status in (301, 302, 303, 307, 308) and 'location' in resp_headers:
                        current_url = urljoin(current_url, resp_headers['location'])
                        break
                    return status, resp_headers, body, current_url
                except Exception:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                    if attempt == 1:
                        raise
            else:
                continue
        raise ConnectionError("Excesso de redirecionamentos para {}".format(url))

    def discard(self, url):
        try:
            parsed = urlsplit(url)
            key = self._key(parsed)
            with self._lock:
                conn = self._conns.pop(key, None)
            if conn is not None:
                conn.close()
        except Exception:
            pass


class UnifiedProxy:
    def __init__(self):
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.conn_pool = ConnectionPool(self.ssl_context)

        self.channel_ua_cache = {}
        self.channel_ua_lock = threading.Lock()

        self.playlist_lock = threading.Lock()
        self.playlist_state = {}
        self.channel_last_active = {}

        self.active_streams = 0
        self.active_streams_lock = threading.Lock()
        self.active_handlers = 0
        self.active_handlers_lock = threading.Lock()

        self.maintenance_started = False
        self.maintenance_lock = threading.Lock()

    def start_maintenance(self):
        with self.maintenance_lock:
            if self.maintenance_started:
                return
            self.maintenance_started = True
        t = threading.Thread(target=self.cleanup_loop, daemon=True)
        t.start()

    def cleanup_loop(self):
        while True:
            time.sleep(CACHE_CLEANUP_INTERVAL)
            now = time.time()
            try:
                stale = [k for k, ts in self.channel_last_active.items()
                         if now - ts > CHANNEL_STATE_TTL]
                for k in stale:
                    self.channel_last_active.pop(k, None)
                    with self.playlist_lock:
                        self.playlist_state.pop(k, None)
                    with self.channel_ua_lock:
                        self.channel_ua_cache.pop(k, None)
            except Exception:
                pass

    def acquire_handler_slot(self):
        with self.active_handlers_lock:
            self.active_handlers += 1
            over_limit = self.active_handlers > MAX_CONCURRENT_HANDLERS
        return not over_limit

    def release_handler_slot(self):
        with self.active_handlers_lock:
            if self.active_handlers > 0:
                self.active_handlers -= 1

    def acquire_stream_slot(self):
        with self.active_streams_lock:
            if self.active_streams >= MAX_ACTIVE_CHANNEL_STREAMS:
                return False
            self.active_streams += 1
            return True

    def release_stream_slot(self):
        with self.active_streams_lock:
            if self.active_streams > 0:
                self.active_streams -= 1

    def channel_key(self, url):
        return re.sub(r'(_=\d+|timestamp=\d+|t=\d+|seq=\d+)', '', url)

    def get_user_agent_for_channel(self, url):
        key = self.channel_key(url)
        with self.channel_ua_lock:
            if key not in self.channel_ua_cache:
                self.channel_ua_cache[key] = random.choice(UA_POOL)
            return self.channel_ua_cache[key]

    def extract_url_from_path(self, path):
        if path.startswith('/http://') or path.startswith('/https://'):
            return unquote(path[1:])
        if path.startswith('http://') or path.startswith('https://'):
            return unquote(path)
        if '?' in path:
            query_part = path.split('?', 1)[1]
            params = parse_qs(query_part)
            url_list = params.get('url', [])
            if url_list:
                return unquote(url_list[0])
        return None

    def _fetch_url(self, url, headers=None, timeout=10, max_retries=MAX_RETRIES, is_alive=None):
        fixed_ua = self.get_user_agent_for_channel(url)
        last_status = None
        last_kind = 'network'
        for attempt in range(max_retries):
            if is_alive is not None and not is_alive():
                return None, None, None, 'aborted'
            ua = fixed_ua if attempt == 0 else random.choice(UA_POOL)
            origin = get_origin(url)
            req_headers = {
                'User-Agent': ua,
                'Accept': '*/*',
                'Accept-Language': 'pt-BR,pt;q=0.9',
            }
            if origin:
                req_headers['Origin'] = origin
                req_headers['Referer'] = origin + '/'
            for k, v in (headers or {}).items():
                if k.lower() not in ('host', 'connection', 'content-length', 'range',
                                     'user-agent', 'accept-encoding'):
                    req_headers[k] = v
            try:
                status, resp_headers, data, final_url = self.conn_pool.request(
                    url, method='GET', headers=req_headers, timeout=timeout
                )
                if status not in (200, 206):
                    kind = classify_status(status)
                    last_status, last_kind = status, kind
                    if kind in ('auth', 'not_found', 'blocked') or attempt >= max_retries - 1:
                        return None, None, status, kind
                    delay = RETRY_DELAY * (attempt + 1)
                    if kind == 'rate_limit':
                        delay *= 2
                    time.sleep(delay)
                    continue
                encoding = resp_headers.get('content-encoding', '').lower()
                try:
                    if encoding == 'gzip':
                        data = gzip.decompress(data)
                    elif encoding == 'deflate':
                        data = zlib.decompress(data)
                except Exception:
                    pass
                return data, final_url, status, 'ok'
            except (socket.timeout, TimeoutError):
                last_kind = 'timeout'
                self.conn_pool.discard(url)
                if attempt >= max_retries - 1:
                    return None, None, None, 'timeout'
                time.sleep(RETRY_DELAY * (attempt + 1))
            except Exception:
                last_kind = 'network'
                self.conn_pool.discard(url)
                if attempt >= max_retries - 1:
                    return None, None, None, 'network'
                time.sleep(RETRY_DELAY * (attempt + 1))
        return None, None, last_status, last_kind

    def download_segment(self, url, headers):
        data, _final_url, _status, kind = self._fetch_url(
            url, headers, timeout=SEGMENT_FETCH_TIMEOUT, max_retries=MAX_RETRIES
        )
        if not data:
            return None, kind
        if len(data) < 188 or data[0] != 0x47:
            return None, 'corrupt'
        return data, 'ok'

    def _parse_playlist(self, playlist_text, base_url):
        segments = []
        target_duration = None
        media_sequence = None
        pending_duration = None
        for raw_line in playlist_text.split('\n'):
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith('#EXT-X-TARGETDURATION'):
                m = TARGETDURATION_RE.search(line)
                if m:
                    try:
                        target_duration = float(m.group(1))
                    except Exception:
                        pass
                continue
            if line.startswith('#EXT-X-MEDIA-SEQUENCE'):
                m = MEDIA_SEQUENCE_RE.search(line)
                if m:
                    try:
                        media_sequence = int(m.group(1))
                    except Exception:
                        pass
                continue
            if line.startswith('#EXTINF'):
                m = EXTINF_RE.search(line)
                if m:
                    try:
                        pending_duration = float(m.group(1))
                    except Exception:
                        pending_duration = None
                continue
            if line.startswith('#'):
                continue
            absolute = urljoin(base_url + '/', line)
            if absolute.startswith(('http://', 'https://')):
                segments.append((absolute, pending_duration))
            pending_duration = None
        return segments, target_duration, media_sequence

    def _compute_refresh_interval(self, total_duration, last_segment_duration, target_duration):
        if not total_duration or total_duration <= 0:
            return max(target_duration or DEFAULT_SEGMENT_DURATION, MIN_REFRESH_INTERVAL)
        interval = total_duration - REFRESH_LEAD_TIME
        return max(interval, MIN_REFRESH_INTERVAL)

    def fetch_playlist(self, url, headers):
        data, final_url, _status, kind = self._fetch_url(
            url, headers, timeout=PLAYLIST_FETCH_TIMEOUT, max_retries=2
        )
        if data is None:
            return None, None, kind
        try:
            text = data.decode('utf-8', errors='ignore')
        except Exception:
            text = data.decode('latin-1', errors='ignore')
        return text, (final_url or url), 'ok'

    def get_or_refresh_playlist(self, channel_key, url, headers, force=False):
        with self.playlist_lock:
            state = self.playlist_state.get(channel_key)
        if state and not force:
            elapsed = time.time() - state['last_fetch_ts']
            if elapsed < state['refresh_interval']:
                return state, 'cached'

        text, final_url, kind = self.fetch_playlist(url, headers)
        if text is None:
            return state, kind

        base_url = (final_url or url).rsplit('/', 1)[0]
        segments, target_duration, media_sequence = self._parse_playlist(text, base_url)
        if not segments:
            return state, 'empty'

        total_duration = sum(d for _, d in segments if d is not None)
        last_dur = segments[-1][1]
        refresh_interval = self._compute_refresh_interval(total_duration, last_dur, target_duration)

        new_state = {
            'segments': segments,
            'target_duration': target_duration,
            'total_duration': total_duration,
            'media_sequence': media_sequence,
            'refresh_interval': refresh_interval,
            'last_fetch_ts': time.time(),
        }
        with self.playlist_lock:
            self.playlist_state[channel_key] = new_state
        return new_state, 'ok'

    @staticmethod
    def _segment_id(url):
        return url.split('/')[-1].split('?')[0]

    def _trickle_write(self, safe_write, data, duration, is_client_alive, pacing):
        total_len = len(data)
        if total_len == 0:
            return True
        if not duration or duration <= 0:
            duration = DEFAULT_SEGMENT_DURATION

        target_chunks = max(1, int(duration / TRICKLE_INTERVAL_TARGET))
        raw_chunk_size = max(1, total_len // target_chunks)
        packets = max(TRICKLE_MIN_TS_PACKETS, raw_chunk_size // 188)
        chunk_size = packets * 188

        view = memoryview(data)
        if chunk_size <= 0 or chunk_size >= total_len:
            offsets = [(0, total_len)]
        else:
            offsets = [(i, min(i + chunk_size, total_len)) for i in range(0, total_len, chunk_size)]

        n = len(offsets)
        chunk_duration = duration / n
        for start, end in offsets:
            if not is_client_alive():
                return False
            if not safe_write(view[start:end]):
                return False
            pacing['duration_sent'] += chunk_duration
            elapsed = time.time() - pacing['session_start']
            ahead = pacing['duration_sent'] - elapsed
            if ahead > BUFFER_AHEAD_SECONDS:
                time.sleep(min(ahead - BUFFER_AHEAD_SECONDS, chunk_duration * 4))
        return True

    @staticmethod
    def _queue_remaining_duration(queue_items, default_duration):
        total = 0.0
        for _, dur, _ in queue_items:
            total += dur if dur else (default_duration or DEFAULT_SEGMENT_DURATION)
        return total

    @staticmethod
    def _segments_with_seq(state):
        media_seq = state.get('media_sequence')
        segments = state.get('segments') or []
        if media_seq is None:
            return [(u, d, None) for u, d in segments]
        return [(u, d, media_seq + i) for i, (u, d) in enumerate(segments)]

    def serve_live_channel(self, playlist_url, headers, safe_write, is_client_alive, client_sock=None, client_gone=None):
        channel_key = self.channel_key(playlist_url)
        self.channel_last_active[channel_key] = time.time()

        state, kind = self.get_or_refresh_playlist(channel_key, playlist_url, headers, force=True)
        if not state or not state.get('segments'):
            return False, kind

        header = (
            "HTTP/1.1 200 OK\r\n"
            "Content-Type: video/mp2t\r\n"
            "Access-Control-Allow-Origin: *\r\n"
            "Cache-Control: no-cache\r\n"
            "Connection: keep-alive\r\n"
            "\r\n"
        )
        if not safe_write(header.encode()):
            return True

        session_start = time.time()
        pacing = {'session_start': session_start, 'duration_sent': 0.0}

        queue = deque(self._segments_with_seq(state))
        served_ids = deque()
        served_set = set()
        last_served_seq = [None]
        segment_index = [0]

        refresh_box = {'future': None, 'triggered_at': None}
        next_holder = {'url': None, 'future': None}
        empty_refresh_streak = [0]
        consecutive_auth_failures = [0]

        def start_prefetch(seg_url):
            next_holder['url'] = seg_url
            next_holder['future'] = executor.submit(self.download_segment, seg_url, headers)

        def mark_served(seg_url, seq):
            sid = self._segment_id(seg_url)
            served_set.add(sid)
            served_ids.append(sid)
            if len(served_ids) > SERVED_IDS_MAX:
                old = served_ids.popleft()
                served_set.discard(old)
            if seq is not None:
                if last_served_seq[0] is None or seq > last_served_seq[0]:
                    last_served_seq[0] = seq

        def filter_new(candidate_state):
            candidates = self._segments_with_seq(candidate_state)
            has_seq = last_served_seq[0] is not None and any(s is not None for _, _, s in candidates)
            if has_seq:
                max_candidate_seq = max(
                    (c[2] for c in candidates if c[2] is not None), default=None
                )
                if max_candidate_seq is not None and max_candidate_seq <= last_served_seq[0]:
                    last_served_seq[0] = None
                    served_set.clear()
                    served_ids.clear()
                    return candidates
                return [c for c in candidates if c[2] is not None and c[2] > last_served_seq[0]]
            return [c for c in candidates if self._segment_id(c[0]) not in served_set]

        executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix='iptvproxy'
        )

        try:
            if queue:
                start_prefetch(queue[0][0])

            while is_client_alive():
                self.channel_last_active[channel_key] = time.time()

                if refresh_box['future'] is None:
                    elapsed_since_fetch = time.time() - state.get('last_fetch_ts', time.time())
                    refresh_interval = state.get('refresh_interval', 0.0)
                    
                    default_dur = state.get('target_duration') or DEFAULT_SEGMENT_DURATION
                    remaining_buffered = self._queue_remaining_duration(queue, default_dur)
                    queue_critical = remaining_buffered <= REFRESH_LEAD_TIME
                    if elapsed_since_fetch >= refresh_interval or queue_critical:
                        if client_sock is not None and client_gone is not None:
                            original_timeout = None
                            try:
                                original_timeout = client_sock.gettimeout()
                                client_sock.settimeout(0.01)
                                peek = client_sock.recv(1, socket.MSG_PEEK)
                                if peek == b'':
                                    client_gone[0] = True
                                    break
                            except (BlockingIOError, socket.timeout):
                                pass
                            except (ConnectionResetError, ConnectionAbortedError, OSError):
                                client_gone[0] = True
                                break
                            finally:
                                try:
                                    client_sock.settimeout(original_timeout)
                                except Exception:
                                    pass

                        refresh_box['triggered_at'] = time.time()
                        refresh_box['future'] = executor.submit(
                            self.get_or_refresh_playlist, channel_key, playlist_url, headers, True
                        )

                if not queue:
                    new_state = None
                    refresh_kind = 'pending'
                    if refresh_box['future'] is not None:
                        try:
                            new_state, refresh_kind = refresh_box['future'].result(timeout=5)
                        except Exception:
                            new_state, refresh_kind = None, 'pending'
                        refresh_box['future'] = None
                    if new_state is None and refresh_kind not in ('auth', 'blocked'):
                        new_state, refresh_kind = self.get_or_refresh_playlist(
                            channel_key, playlist_url, headers, force=True
                        )

                    if refresh_kind in ('auth', 'blocked'):
                        consecutive_auth_failures[0] += 1
                        if consecutive_auth_failures[0] > MAX_CONSECUTIVE_AUTH_FAILURES:
                            return False, refresh_kind
                    else:
                        consecutive_auth_failures[0] = 0

                    candidate_state = new_state or state
                    new_segments = filter_new(candidate_state)
                    if not new_segments:
                        if not is_client_alive():
                            break
                        empty_refresh_streak[0] += 1
                        wait_s = min(
                            MAX_WAIT_FOR_NEW_SEGMENTS * (1 + 0.5 * (empty_refresh_streak[0] - 1)),
                            3.0,
                        )
                        time.sleep(wait_s)
                        continue
                    empty_refresh_streak[0] = 0
                    state = candidate_state
                    queue = deque(new_segments)
                    start_prefetch(queue[0][0])
                    continue

                seg_url, seg_dur, seg_seq = queue.popleft()

                if next_holder['url'] == seg_url and next_holder['future'] is not None:
                    try:
                        data, seg_kind = next_holder['future'].result(timeout=SEGMENT_FETCH_TIMEOUT)
                    except Exception:
                        data, seg_kind = None, 'timeout'
                else:
                    data, seg_kind = self.download_segment(seg_url, headers)
                next_holder['url'] = None
                next_holder['future'] = None

                if not data:
                    if seg_kind in ('auth', 'blocked'):
                        consecutive_auth_failures[0] += 1
                        new_state, refresh_kind = self.get_or_refresh_playlist(
                            channel_key, playlist_url, headers, force=True
                        )
                        if refresh_kind in ('auth', 'blocked') and \
                                consecutive_auth_failures[0] > MAX_CONSECUTIVE_AUTH_FAILURES:
                            return False, refresh_kind
                        if new_state:
                            state = new_state
                            new_segments = filter_new(state)
                            if new_segments:
                                queue = deque(new_segments)
                                start_prefetch(queue[0][0])
                                consecutive_auth_failures[0] = 0
                                continue
                    elif seg_kind == 'not_found':
                        pass
                    else:
                        consecutive_auth_failures[0] = 0
                        for _extra_attempt in range(SEGMENT_INLINE_RETRIES):
                            if not is_client_alive():
                                break
                            time.sleep(SEGMENT_INLINE_RETRY_DELAY)
                            data, seg_kind = self.download_segment(seg_url, headers)
                            if data:
                                break
                            if seg_kind in ('auth', 'not_found', 'blocked'):
                                break
                else:
                    consecutive_auth_failures[0] = 0

                if queue:
                    start_prefetch(queue[0][0])

                if data:
                    duration = seg_dur or state.get('target_duration') or DEFAULT_SEGMENT_DURATION
                    ok = self._trickle_write(safe_write, data, duration, is_client_alive, pacing)
                    if not ok:
                        return True, 'ok'
                    mark_served(seg_url, seg_seq)
                    segment_index[0] += 1

            return True, 'ok'
        finally:
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)

    def _send_error(self, wfile, code, message=""):
        try:
            body = "{} {}".format(code, message).encode("utf-8", "replace")
            wfile.write("HTTP/1.1 {} {}\r\n".format(code, message).encode())
            wfile.write(b"Content-Type: text/plain\r\n")
            wfile.write("Content-Length: {}\r\n".format(len(body)).encode())
            wfile.write(b"Access-Control-Allow-Origin: *\r\n")
            wfile.write(b"Connection: close\r\n\r\n")
            wfile.write(body)
        except Exception:
            pass

    def handle_channel_stream(self, url, headers, wfile, client_sock=None, method='GET'):
        if method in ('HEAD', 'OPTIONS'):
            try:
                wfile.write(b"HTTP/1.1 200 OK\r\n")
                wfile.write(b"Content-Type: video/mp2t\r\n")
                wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                wfile.write(b"Cache-Control: no-cache\r\n")
                wfile.write(b"Content-Length: 0\r\n\r\n")
            except Exception:
                pass
            return

        client_gone = [False]

        def safe_write(data):
            if client_gone[0]:
                return False
            try:
                wfile.write(data)
                return True
            except (BrokenPipeError, socket.error, ConnectionResetError, ConnectionAbortedError):
                client_gone[0] = True
                return False
            except Exception:
                client_gone[0] = True
                return False

        def is_client_alive():
            return not client_gone[0]

        stream_slot_acquired = self.acquire_stream_slot()
        if not stream_slot_acquired:
            self._send_error(wfile, 503, "Muitos streams ativos")
            return
        try:
            ok, kind = self.serve_live_channel(
                url, headers, safe_write, is_client_alive, client_sock=client_sock, client_gone=client_gone
            )
            if not ok:
                status = ERROR_KIND_TO_CLIENT_STATUS.get(kind, 503)
                if kind == 'auth':
                    msg = "Credencial/token invalido ou expirado"
                elif kind == 'blocked':
                    msg = "Acesso bloqueado pelo servidor de origem"
                elif kind == 'not_found':
                    msg = "Conteudo nao encontrado na origem"
                else:
                    msg = "Canal indisponivel (instabilidade momentanea)"
                self._send_error(wfile, status, msg)
        finally:
            self.release_stream_slot()

class ProxyHandler(socketserver.StreamRequestHandler):
    proxy = UnifiedProxy()

    def send_response(self, code, message=None):
        if message is None:
            message = http.client.responses.get(code, "OK")
        self.resp_statusline = "HTTP/1.1 {} {}\r\n".format(code, message)
        self.resp_headers = []

    def send_header(self, key, value):
        self.resp_headers.append((key, value))

    def end_headers(self):
        try:
            has_conn = any(k.lower() == "connection" for k, _ in self.resp_headers)
            if not has_conn:
                self.resp_headers.append(("Connection", "close"))
            data = self.resp_statusline
            for k, v in self.resp_headers:
                data += "{}: {}\r\n".format(k, v)
            data += "\r\n"
            self.wfile.write(data.encode("utf-8", "replace"))
        except Exception:
            pass

    def send_error(self, code, message=""):
        body = "{} {}".format(code, message).encode("utf-8", "replace")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def handle(self):
        try:
            self.connection.settimeout(SOCKET_IDLE_TIMEOUT)
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except Exception:
            pass
        slot_ok = True
        try:
            slot_ok = self.proxy.acquire_handler_slot()
        except Exception:
            slot_ok = True
        try:
            if not slot_ok:
                try:
                    self.send_error(503, "Too Many Connections")
                except Exception:
                    pass
                return
            raw = self.rfile.readline(65537)
            if not raw:
                return
            if raw.startswith(b"\x16\x03") or raw.startswith(b"PRI * HTTP/2.0"):
                self.send_error(400, "Bad Request")
                return
            line = raw.decode("iso-8859-1", "replace").rstrip("\r\n")
            parts = line.split(" ")
            if len(parts) < 2:
                self.send_error(400, "Bad Request")
                return
            self.command = parts[0].upper()
            if len(parts) >= 3 and parts[-1].startswith("HTTP/"):
                self.request_version = parts[-1]
                target = " ".join(parts[1:-1])
            else:
                self.request_version = "HTTP/1.1"
                target = " ".join(parts[1:])
            if target.startswith("http://") or target.startswith("https://"):
                try:
                    u = urlsplit(target)
                    target = (u.path or "/") + (("?" + u.query) if u.query else "")
                except Exception:
                    pass
            self.path = target
            headers = {}
            while True:
                h = self.rfile.readline(65537)
                if not h or h in (b"\r\n", b"\n"):
                    break
                hs = h.decode("iso-8859-1", "replace")
                if ":" in hs:
                    k, v = hs.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            self.headers = headers
            if self.command == "OPTIONS":
                self.do_OPTIONS()
            elif self.command == "HEAD":
                self.do_HEAD()
            else:
                self.do_GET()
        except Exception:
            try:
                self.send_error(500, "Internal Server Error")
            except Exception:
                pass
        finally:
            try:
                self.proxy.release_handler_slot()
            except Exception:
                pass

    def do_GET(self):
        self.process_request()

    def do_HEAD(self):
        self.process_request()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Range, Origin, Content-Type, Accept')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def process_request(self):
        try:
            url = self.proxy.extract_url_from_path(self.path)
            if not url:
                html = """<html><body>
<h2>XC Pro Proxy Active</h2>
<p>Proxy funcionando na porta {}</p>
</body></html>""".format(get_active_port()).encode("utf-8")
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            headers = {}
            for key, value in self.headers.items():
                headers[key.lower()] = value
            self.proxy.handle_channel_stream(
                url, headers, self.wfile, getattr(self, 'connection', None), method=self.command
            )
        except Exception:
            try:
                self.send_error(500, "Internal Server Error")
            except Exception:
                pass

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def make_redirect_handler(target_port):
    class RedirectHandler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                raw = self.rfile.readline(65537)
                if not raw:
                    return
                line = raw.decode("iso-8859-1", "replace").rstrip("\r\n")
                parts = line.split(" ")
                target = parts[1] if len(parts) >= 2 else "/"
                if target.startswith("http://") or target.startswith("https://"):
                    try:
                        u = urlsplit(target)
                        target = (u.path or "/") + (("?" + u.query) if u.query else "")
                    except Exception:
                        pass
                while True:
                    h = self.rfile.readline(65537)
                    if not h or h in (b"\r\n", b"\n"):
                        break
                location = "http://127.0.0.1:{}{}".format(target_port, target)
                resp = (
                    "HTTP/1.1 302 Found\r\n"
                    "Location: {}\r\n"
                    "Content-Length: 0\r\n"
                    "Connection: close\r\n\r\n"
                ).format(location).encode("utf-8")
                self.wfile.write(resp)
            except Exception:
                pass
    return RedirectHandler

class RedirectTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class NoPortAvailableError(Exception):
    pass

class UnifiedServer:
    def __init__(self, ports=None):
        self.ports = list(ports) if ports else list(PROXY_PORT_POOL)
        self.port = None
        self.server = None
        self.running = False
        self.monitor = None
        self.redirect_servers = []

    def bind_with_rotation(self):
        remaining = list(self.ports)
        preferred = read_persisted_port()
        ordered = []
        if preferred in remaining:
            ordered.append(preferred)
            remaining.remove(preferred)
        random.shuffle(remaining)
        ordered.extend(remaining)
        last_err = None
        for p in ordered:
            try:
                server = ThreadedTCPServer(("127.0.0.1", p), ProxyHandler)
                return server, p
            except OSError as e:
                last_err = e
                continue
        raise NoPortAvailableError(
            "Nenhuma porta livre no pool de rotacao: {}".format(ordered)
        ) from last_err

    def start_backup_redirects(self):
        handler_cls = make_redirect_handler(self.port)
        for p in self.ports:
            if p == self.port:
                continue
            try:
                srv = RedirectTCPServer(("127.0.0.1", p), handler_cls)
                srv.timeout = 1
            except OSError:
                continue
            th = threading.Thread(target=self.serve_redirects, args=(srv,), daemon=True)
            th.start()
            self.redirect_servers.append((srv, th))

    def serve_redirects(self, srv):
        while self.running:
            try:
                srv.handle_request()
            except Exception:
                break

    def stop_backup_redirects(self):
        for srv, _th in self.redirect_servers:
            try:
                srv.server_close()
            except Exception:
                pass
        self.redirect_servers = []

    def start(self, monitor=None):
        self.monitor = monitor
        self.running = True
        try:
            self.server, self.port = self.bind_with_rotation()
            set_active_port(self.port)
            self.server.timeout = 1
            self.start_backup_redirects()
            ProxyHandler.proxy.start_maintenance()
            while self.running:
                if self.monitor and self.monitor.abortRequested():
                    break
                try:
                    self.server.handle_request()
                except OSError:
                    pass
                except Exception:
                    pass
        except NoPortAvailableError:
            pass
        except Exception:
            pass
        finally:
            self.stop()

    def stop(self):
        self.running = False
        self.stop_backup_redirects()
        try:
            if self.server:
                try:
                    self.server.server_close()
                except Exception:
                    pass
                self.server = None
        except Exception:
            pass

    def is_running(self):
        return self.running and self.server is not None