# -*- coding: utf-8 -*-

import ipaddress
import socket
import threading
import random
import time
import os
import re
from collections import deque
from urllib.parse import urlparse, unquote, urljoin, parse_qs, urlsplit
import ssl
import gzip
import zlib
import socketserver
import http.client
import queue

try:
    import xbmcvfs
except ImportError:
    xbmcvfs = None

PROXY_PORT_POOL = [57845, 57846, 57847, 57848, 57849, 57850]
PROXY_PORT = PROXY_PORT_POOL[0]
port_state_lock = threading.Lock()

PROXY_HOST = None
host_state_lock = threading.Lock()


def get_device_ip():
    candidates = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            candidates.append(s.getsockname()[0])
        finally:
            s.close()
    except Exception:
        pass
    if not candidates:
        try:
            hostname = socket.gethostname()
            for ip in socket.gethostbyname_ex(hostname)[2]:
                candidates.append(ip)
        except Exception:
            pass
    for ip in candidates:
        if ip and not ip.startswith("127."):
            return ip
    return "127.0.0.1"


def get_active_host():
    global PROXY_HOST
    with host_state_lock:
        if not PROXY_HOST:
            PROXY_HOST = get_device_ip()
        return PROXY_HOST


def set_active_host(host):
    global PROXY_HOST
    with host_state_lock:
        PROXY_HOST = host


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


def is_port_free(port, host=None):
    if host is None:
        host = get_active_host()
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

SEGMENT_INLINE_RETRIES = 3
SEGMENT_INLINE_RETRY_DELAY = 0.3

PLAYLIST_FETCH_TIMEOUT = 10
SEGMENT_FETCH_TIMEOUT = 15

MIN_REFRESH_INTERVAL = 2.0
MAX_WAIT_FOR_NEW_SEGMENTS = 1.0
MAX_STALL_WITHOUT_PROGRESS_SECONDS = 15.0
SERVED_IDS_MAX = 400
TRICKLE_INTERVAL_TARGET = 0.2
TRICKLE_MIN_TS_PACKETS = 8
MAX_PACING_AHEAD_SECONDS = 6.0
MAX_PACING_BEHIND_SECONDS = 2.0

MAX_ACTIVE_CHANNEL_STREAMS = 12
MAX_CONCURRENT_HANDLERS = 20
CHANNEL_STATE_TTL = 300
CACHE_CLEANUP_INTERVAL = 60
SOCKET_IDLE_TIMEOUT = 10

EXTINF_RE = re.compile(r'#EXTINF:\s*([\d.]+)')
TARGETDURATION_RE = re.compile(r'#EXT-X-TARGETDURATION:\s*(\d+(?:\.\d+)?)')
MEDIA_SEQUENCE_RE = re.compile(r'#EXT-X-MEDIA-SEQUENCE:\s*(\d+)')
HOLD_BACK_RE = re.compile(r'(?<!PART-)HOLD-BACK=([\d.]+)')
START_TIME_OFFSET_RE = re.compile(r'TIME-OFFSET=(-?[\d.]+)')

SERVER_HOLD_BACK_MULTIPLIER = 3.0
DEFAULT_LIVE_DELAY_SECONDS = 6.0

AUTH_ERROR_CODES = {401, 403}
NOT_FOUND_CODES = {404, 410}
BLOCKED_CODES = {451}
RATE_LIMIT_CODES = {429}
SERVER_ERROR_CODES = {500, 502, 503, 504}
MAX_CONSECUTIVE_AUTH_FAILURES = 2

NON_RETRYABLE_KINDS = {'auth', 'not_found', 'blocked'}
CIRCUIT_BREAKER_KINDS = {'auth', 'blocked'}


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


def is_ip_literal(host):
    if not host:
        return False
    try:
        ipaddress.ip_address(host.strip('[]'))
        return True
    except ValueError:
        return False


def write_http_error(wfile, code, message=""):
    try:
        body = "{} {}".format(code, message).encode("utf-8", "replace")
        wfile.write("HTTP/1.1 {} {}\r\n".format(code, message).encode())
        wfile.write(b"Content-Type: text/plain; charset=utf-8\r\n")
        wfile.write("Content-Length: {}\r\n".format(len(body)).encode())
        wfile.write(b"Access-Control-Allow-Origin: *\r\n")
        wfile.write(b"Connection: close\r\n\r\n")
        wfile.write(body)
    except Exception:
        pass


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

    def request(self, url, method='GET', headers=None, timeout=None, max_redirects=5,
                auto_origin=True):
        timeout = timeout or self.timeout
        current_url = url
        base_headers = dict(headers or {})
        base_headers['Connection'] = 'keep-alive'
        for _ in range(max_redirects + 1):
            parsed = urlsplit(current_url)
            key = self._key(parsed)
            path = (parsed.path or '/') + (('?' + parsed.query) if parsed.query else '')

            req_headers = dict(base_headers)
            if auto_origin and not is_ip_literal(parsed.hostname):
                hop_origin = get_origin(current_url)
                if hop_origin:
                    req_headers['Origin'] = hop_origin
                    req_headers['Referer'] = hop_origin + '/'

            with self._lock:
                conn = self._conns.pop(key, None)

            for attempt in range(2):
                if conn is None:
                    conn = self._new_conn(parsed, timeout)
                try:
                    conn.timeout = timeout
                    conn.request(method, path, headers=req_headers)
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


class _LiveFeeder:
    def __init__(self, proxy, channel_key, playlist_url, headers, state, is_client_alive,
                 client_sock=None, client_gone=None):
        self.proxy = proxy
        self.channel_key = channel_key
        self.playlist_url = playlist_url
        self.headers = headers
        self.state = state
        self.is_client_alive = is_client_alive
        self.client_sock = client_sock
        self.client_gone = client_gone

        self.stop_event = threading.Event()
        self.ready = queue.Queue(maxsize=6)
        self.error_kind = [None]

        self.served_urls = set()
        self.served_urls_deque = deque()
        self.last_served_seq = None

        self.last_segment_list = None

    def stop(self):
        self.stop_event.set()

    def _peek_disconnected(self):
        if self.client_sock is None or self.client_gone is None:
            return False
        original_timeout = None
        try:
            original_timeout = self.client_sock.gettimeout()
            self.client_sock.settimeout(0.01)
            peek = self.client_sock.recv(1, socket.MSG_PEEK)
            if peek == b'':
                self.client_gone[0] = True
                return True
        except (BlockingIOError, socket.timeout):
            pass
        except (ConnectionResetError, ConnectionAbortedError, OSError):
            self.client_gone[0] = True
            return True
        finally:
            try:
                self.client_sock.settimeout(original_timeout)
            except Exception:
                pass
        return False

    def _mark_served(self, seg_url, seq):
        self.served_urls.add(seg_url)
        self.served_urls_deque.append(seg_url)
        if len(self.served_urls_deque) > SERVED_IDS_MAX:
            old = self.served_urls_deque.popleft()
            self.served_urls.discard(old)
        if seq is not None and (self.last_served_seq is None or seq > self.last_served_seq):
            self.last_served_seq = seq

    def _refresh(self):
        new_state, kind = self.proxy.get_or_refresh_playlist(
            self.channel_key, self.playlist_url, self.headers
        )
        return new_state, kind

    def _new_segments(self, candidate_state):
        segments = self.proxy._segments_with_seq(candidate_state)
        if not segments:
            return []

        reset_detected = False
        if self.last_segment_list is not None:
            old_seq = self.last_segment_list[0][2] if self.last_segment_list else None
            new_seq = segments[0][2]
            if old_seq is not None and new_seq is not None:
                if new_seq < old_seq:
                    reset_detected = True
            if not reset_detected and old_seq is None and new_seq is None:
                old_urls = {seg[0] for seg in self.last_segment_list[:3]}
                new_urls = {seg[0] for seg in segments[:3]}
                if not old_urls.intersection(new_urls):
                    reset_detected = True
        else:
            pass

        if reset_detected:
            self.served_urls.clear()
            self.served_urls_deque.clear()
            self.last_served_seq = None
            return self.proxy._segments_from_live_edge(candidate_state)

        new_segments = []
        for url, dur, seq in segments:
            if seq is not None:
                if self.last_served_seq is not None and seq <= self.last_served_seq:
                    continue
                if url in self.served_urls:
                    continue
                new_segments.append((url, dur, seq))
            else:
                if url not in self.served_urls:
                    new_segments.append((url, dur, seq))

        self.last_segment_list = segments[:]
        return new_segments

    def _fetch_loop(self):
        self.last_segment_list = self.proxy._segments_with_seq(self.state)

        pending = deque(self.proxy._segments_from_live_edge(self.state))
        stall_since = None
        empty_streak = 0
        auth_failures = 0

        while not self.stop_event.is_set() and self.is_client_alive():
            if not pending:
                if self._peek_disconnected():
                    return

                new_state, kind = self._refresh()
                if kind in CIRCUIT_BREAKER_KINDS:
                    auth_failures += 1
                    if auth_failures > MAX_CONSECUTIVE_AUTH_FAILURES:
                        self.error_kind[0] = kind
                        self.stop_event.set()
                        return
                else:
                    auth_failures = 0

                candidate_state = new_state or self.state
                new_segments = self._new_segments(candidate_state)

                if not new_segments:
                    now = time.time()
                    if stall_since is None:
                        stall_since = now
                    elif now - stall_since > MAX_STALL_WITHOUT_PROGRESS_SECONDS:
                        self.served_urls.clear()
                        self.served_urls_deque.clear()
                        self.last_served_seq = None
                        self.last_segment_list = None
                        pending = deque(self.proxy._segments_from_live_edge(candidate_state))
                        stall_since = None
                        empty_streak = 0
                        self.state = candidate_state
                        continue
                    empty_streak += 1
                    wait_s = min(MAX_WAIT_FOR_NEW_SEGMENTS * (1 + 0.5 * (empty_streak - 1)), 3.0)
                    self.stop_event.wait(wait_s)
                    continue

                stall_since = None
                empty_streak = 0
                self.state = candidate_state
                pending = deque(new_segments)
                continue

            seg_url, seg_dur, seg_seq = pending.popleft()
            data, seg_kind = self.proxy.download_segment(seg_url, self.headers)

            if not data and seg_kind not in NON_RETRYABLE_KINDS:
                for _ in range(SEGMENT_INLINE_RETRIES):
                    if self.stop_event.is_set() or not self.is_client_alive():
                        return
                    time.sleep(SEGMENT_INLINE_RETRY_DELAY)
                    data, seg_kind = self.proxy.download_segment(seg_url, self.headers)
                    if data or seg_kind in NON_RETRYABLE_KINDS:
                        break

            if not data:
                if seg_kind in CIRCUIT_BREAKER_KINDS:
                    auth_failures += 1
                    if auth_failures > MAX_CONSECUTIVE_AUTH_FAILURES:
                        self.error_kind[0] = seg_kind
                        self.stop_event.set()
                        return
                continue

            auth_failures = 0
            self._mark_served(seg_url, seg_seq)

            while not self.stop_event.is_set() and self.is_client_alive():
                try:
                    self.ready.put((seg_dur, data), timeout=1.0)
                    break
                except queue.Full:
                    continue

    def run(self, safe_write):
        fetch_thread = threading.Thread(
            target=self._fetch_loop, name='iptvproxy-fetch', daemon=True
        )
        fetch_thread.start()

        pacing = {'session_start': None, 'duration_sent': 0.0}
        try:
            while self.is_client_alive():
                self.proxy.channel_last_active[self.channel_key] = time.time()
                try:
                    seg_dur, data = self.ready.get(timeout=1.0)
                except queue.Empty:
                    if not fetch_thread.is_alive():
                        break
                    continue

                duration = seg_dur or self.state.get('target_duration')
                if pacing['session_start'] is None:
                    ok = safe_write(memoryview(data))
                    pacing['session_start'] = time.time()
                    pacing['duration_sent'] = 0.0
                else:
                    ok = self.proxy._trickle_write(
                        safe_write, data, duration, self.is_client_alive, pacing
                    )
                if not ok:
                    return True, 'ok'

            if self.error_kind[0]:
                return False, self.error_kind[0]
            return True, 'ok'
        finally:
            self.stop_event.set()
            fetch_thread.join(timeout=2)


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
            req_headers = {
                'User-Agent': ua,
                'Accept': '*/*',
                'Accept-Language': 'pt-BR,pt;q=0.9',
            }
            for k, v in (headers or {}).items():
                if k.lower() not in ('host', 'connection', 'content-length', 'range',
                                     'user-agent', 'accept-encoding'):
                    req_headers[k] = v
            auto_origin = attempt == 0
            try:
                status, resp_headers, data, final_url = self.conn_pool.request(
                    url, method='GET', headers=req_headers, timeout=timeout,
                    auto_origin=auto_origin
                )
                if status not in (200, 206):
                    kind = classify_status(status)
                    last_status, last_kind = status, kind
                    if kind in NON_RETRYABLE_KINDS:
                        if auto_origin and attempt < max_retries - 1:
                            continue
                        return None, None, status, kind
                    if attempt >= max_retries - 1:
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
        hold_back = None
        start_offset = None
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
            if line.startswith('#EXT-X-SERVER-CONTROL'):
                m = HOLD_BACK_RE.search(line)
                if m:
                    try:
                        hold_back = float(m.group(1))
                    except Exception:
                        pass
                continue
            if line.startswith('#EXT-X-START'):
                m = START_TIME_OFFSET_RE.search(line)
                if m:
                    try:
                        start_offset = float(m.group(1))
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
        return segments, target_duration, media_sequence, hold_back, start_offset

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
            seg_duration = self._latest_segment_duration(state) or MIN_REFRESH_INTERVAL
            cache_ttl = max(state.get('target_duration') or seg_duration, seg_duration)
            if elapsed < cache_ttl:
                return state, 'cached'

        text, final_url, kind = self.fetch_playlist(url, headers)
        if text is None:
            return state, kind

        base_url = (final_url or url).rsplit('/', 1)[0]
        segments, target_duration, media_sequence, hold_back, start_offset = \
            self._parse_playlist(text, base_url)
        if not segments:
            return state, 'empty'

        total_duration = sum(d for _, d in segments if d is not None)
        live_delay = self._resolve_live_delay(target_duration, hold_back, start_offset, total_duration)

        new_state = {
            'segments': segments,
            'target_duration': target_duration,
            'total_duration': total_duration,
            'media_sequence': media_sequence,
            'hold_back': hold_back,
            'start_offset': start_offset,
            'live_delay': live_delay,
            'last_fetch_ts': time.time(),
        }
        with self.playlist_lock:
            self.playlist_state[channel_key] = new_state
        return new_state, 'ok'

    @staticmethod
    def _resolve_live_delay(target_duration, hold_back, start_offset, total_duration):
        if hold_back is not None and hold_back > 0:
            delay = hold_back
        elif start_offset is not None and start_offset < 0:
            delay = abs(start_offset)
        elif target_duration:
            delay = target_duration * SERVER_HOLD_BACK_MULTIPLIER
        else:
            delay = DEFAULT_LIVE_DELAY_SECONDS
        if total_duration:
            delay = min(delay, total_duration)
        return max(delay, 0.0)

    def _segments_from_live_edge(self, state):
        all_segments = self._segments_with_seq(state)
        if not all_segments:
            return all_segments

        live_delay = state.get('live_delay')
        if not live_delay or live_delay <= 0:
            return all_segments[-1:]

        target_duration = state.get('target_duration') or MIN_REFRESH_INTERVAL
        acc = 0.0
        start_idx = len(all_segments) - 1
        for i in range(len(all_segments) - 1, -1, -1):
            _, dur, _ = all_segments[i]
            acc += dur or target_duration
            start_idx = i
            if acc >= live_delay:
                break
        return all_segments[start_idx:]

    @staticmethod
    def _segments_with_seq(state):
        media_seq = state.get('media_sequence')
        segments = state.get('segments') or []
        if media_seq is None:
            return [(u, d, None) for u, d in segments]
        return [(u, d, media_seq + i) for i, (u, d) in enumerate(segments)]

    @staticmethod
    def _latest_segment_duration(state):
        segments = state.get('segments') or []
        for _, dur in reversed(segments):
            if dur:
                return dur
        return None

    def _trickle_write(self, safe_write, data, duration, is_client_alive, pacing):
        total_len = len(data)
        if total_len == 0:
            return True
        if not duration or duration <= 0:
            duration = MIN_REFRESH_INTERVAL

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

        elapsed = time.time() - pacing['session_start']
        ahead = pacing['duration_sent'] - elapsed
        if ahead > MAX_PACING_AHEAD_SECONDS:
            pacing['duration_sent'] = elapsed + MAX_PACING_AHEAD_SECONDS
        elif ahead < -MAX_PACING_BEHIND_SECONDS:
            pacing['duration_sent'] = elapsed - MAX_PACING_BEHIND_SECONDS

        for start, end in offsets:
            if not is_client_alive():
                return False
            if not safe_write(view[start:end]):
                return False
            pacing['duration_sent'] += chunk_duration
            elapsed = time.time() - pacing['session_start']
            ahead = pacing['duration_sent'] - elapsed
            if ahead > 0:
                time.sleep(min(ahead, chunk_duration * 4))
        return True

    def serve_live_channel(self, playlist_url, headers, safe_write, is_client_alive,
                           client_sock=None, client_gone=None):
        channel_key = self.channel_key(playlist_url)
        self.channel_last_active[channel_key] = time.time()

        state, kind = self.get_or_refresh_playlist(channel_key, playlist_url, headers)
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
            return True, 'ok'

        feeder = _LiveFeeder(
            self, channel_key, playlist_url, headers, state, is_client_alive,
            client_sock=client_sock, client_gone=client_gone,
        )
        try:
            return feeder.run(safe_write)
        finally:
            feeder.stop()

    def _send_error(self, wfile, code, message=""):
        write_http_error(wfile, code, message)

    def handle_channel_stream(self, url, headers, wfile, client_sock=None, method='GET'):
        if method == 'HEAD':
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
        write_http_error(self.wfile, code, message)

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
<h2>KingIPTV Proxy Active</h2>
<p>Proxy funcionando em {}:{}</p>
</body></html>""".format(get_active_host(), get_active_port()).encode("utf-8")
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
                location = "http://{}:{}{}".format(get_active_host(), target_port, target)
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
        host = get_active_host()
        last_err = None
        for p in ordered:
            try:
                server = ThreadedTCPServer((host, p), ProxyHandler)
                return server, p
            except OSError as e:
                last_err = e
                continue
        raise NoPortAvailableError(
            "Nenhuma porta livre no pool de rotacao: {}".format(ordered)
        ) from last_err

    def start_backup_redirects(self):
        handler_cls = make_redirect_handler(self.port)
        host = get_active_host()
        for p in self.ports:
            if p == self.port:
                continue
            try:
                srv = ThreadedTCPServer((host, p), handler_cls)
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
