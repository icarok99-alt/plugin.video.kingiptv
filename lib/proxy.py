# -*- coding: utf-8 -*-

import socket
import threading
import struct
import random
import time
import os
import re
from urllib.parse import urlparse, unquote, urljoin, quote
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
import binascii
import ssl
from collections import deque
import gzip
import zlib
import socketserver
import http.client
from urllib.parse import urlsplit
try:
    import xbmcvfs
except Exception:
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

CACHE_DURATION_SECONDS = 5
CACHE_MAX_CHUNKS = 250
MAX_RETRIES = 7
MAX_RECONNECT_RETRIES = 2
RETRY_DELAY = 0.5
BUFFER_SIZE = 32768
MAX_EOF_RECONNECTS = 40
MAX_TOTAL_RECONNECTS = 60
MAX_STALL_SECONDS = 45
CLIENT_ALIVE_CHECK_EVERY = 1.0
MAX_ACTIVE_CHANNEL_STREAMS = 12
CACHE_ENTRY_TTL = 300
CACHE_CLEANUP_INTERVAL = 60
PREFETCH_SEGMENT_COUNT = 3
SEGMENT_CACHE_TTL = 30
SEGMENT_CACHE_MAX = 60
SOCKET_IDLE_TIMEOUT = 20
SOCKET_STREAM_TIMEOUT = 20
PREFETCH_TIMEOUT = 8
PREFETCH_MAX_RETRIES = 2
MAX_PREFETCH_THREADS = 6
MAX_CONCURRENT_HANDLERS = 40
CHANNEL_IDLE_ABORT_SECONDS = 15
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0.7871.114 Safari/537.36"
)

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.7827.200 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.7692.100 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.7549.90 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.7391.85 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.7827.200 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:152.0) Gecko/20100101 Firefox/152.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Safari/537.36 Edg/150.0.3593.56",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.7759.62 Safari/537.36 Edg/148.0.3479.40",
    "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.7871.114 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14; SM-S928B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.7759.62 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.3 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Safari/605.1.15",
]

def get_origin(url):
    try:
        parsed = urlparse(url)
        if parsed.scheme and parsed.netloc:
            return "{}://{}".format(parsed.scheme, parsed.netloc)
    except Exception:
        pass
    return ''

class SimpleDNS:
    def __init__(self):
        self.cache = {}
        self.dns_servers = ["1.1.1.1", "8.8.8.8", "208.67.222.222"]
        self.original_getaddrinfo = socket.getaddrinfo
        socket.getaddrinfo = self.resolver
    def build_query(self, domain):
        transaction_id = random.randint(0, 65535)
        header = struct.pack(">HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
        qname = b"".join(
            bytes([len(part)]) + part.encode() for part in domain.split(".")
        ) + b"\x00"
        return header + qname + struct.pack(">HH", 1, 1)
    def parse_response(self, data):
        try:
            answer_count = struct.unpack(">H", data[6:8])[0]
            offset = 12
            while data[offset] != 0:
                offset += 1
            offset += 5
            for _ in range(answer_count):
                offset += 2
                rtype, _, _, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
                offset += 10
                if rtype == 1 and rdlength == 4:
                    ip = struct.unpack(">BBBB", data[offset:offset + 4])
                    return ".".join(map(str, ip))
                offset += rdlength
        except Exception:
            pass
        return None
    def resolve(self, domain):
        if domain in self.cache and self.cache[domain]["expires"] > time.time():
            return self.cache[domain]["ip"]
        for dns in self.dns_servers:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(2)
                query = self.build_query(domain)
                sock.sendto(query, (dns, 53))
                data, _ = sock.recvfrom(512)
                sock.close()
                ip = self.parse_response(data)
                if ip:
                    self.cache[domain] = {"ip": ip, "expires": time.time() + 3600}
                    return ip
            except Exception:
                continue
        return None
    def resolver(self, host, port, *args, **kwargs):
        try:
            socket.inet_aton(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port))]
        except Exception:
            ip = self.resolve(host)
            if ip:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]
        return self.original_getaddrinfo(host, port, *args, **kwargs)
dns = SimpleDNS()

class CircularBuffer:
    def __init__(self, max_seconds=5, max_chunks=250):
        self.buffer = deque(maxlen=max_chunks)
        self.timestamps = deque(maxlen=max_chunks)
        self.max_seconds = max_seconds
        self.total_bytes = 0
        self.lock = threading.Lock()
        self.last_update = 0
        self.stream_started = False
    def add_chunk(self, chunk):
        with self.lock:
            self.buffer.append(chunk)
            self.timestamps.append(time.time())
            self.total_bytes += len(chunk)
            self.last_update = time.time()
            cutoff = time.time() - self.max_seconds
            while self.timestamps and self.timestamps[0] < cutoff:
                removed = self.buffer.popleft()
                self.timestamps.popleft()
                self.total_bytes -= len(removed)
    def get_recovery_chunks(self, duration=3):
        with self.lock:
            if not self.buffer:
                return []
            cutoff = time.time() - duration
            recovery = []
            for i, ts in enumerate(self.timestamps):
                if ts >= cutoff:
                    recovery.append(self.buffer[i])
            if not recovery and self.buffer:
                recovery = list(self.buffer)[-20:]
            return recovery
    def get_continuous_chunks(self, count=30):
        with self.lock:
            if not self.buffer:
                return []
            return list(self.buffer)[-count:]
    def clear(self):
        with self.lock:
            self.buffer.clear()
            self.timestamps.clear()
            self.total_bytes = 0
            self.stream_started = False

class MP4Cache:
    def __init__(self, max_chunks=250):
        self.chunks = {}
        self.max_chunks = max_chunks
        self.lock = threading.Lock()
        self.total_size = 0
        self.content_length = None
    def add_chunk(self, start_byte, data):
        if not data:
            return
        with self.lock:
            if start_byte not in self.chunks:
                self.chunks[start_byte] = data
                self.total_size += len(data)
                while len(self.chunks) > self.max_chunks:
                    oldest = min(self.chunks.keys())
                    self.total_size -= len(self.chunks[oldest])
                    del self.chunks[oldest]
    def get_range(self, start, end):
        with self.lock:
            keys = sorted(self.chunks.keys())
            if not keys:
                return None
            result = bytearray()
            pos = start
            while pos < end:
                found = False
                for chunk_start in keys:
                    chunk = self.chunks[chunk_start]
                    chunk_end = chunk_start + len(chunk)
                    if chunk_start <= pos < chunk_end:
                        offset = pos - chunk_start
                        take = min(end - pos, chunk_end - pos)
                        result.extend(chunk[offset:offset + take])
                        pos += take
                        found = True
                        break
                if not found:
                    return None
            return bytes(result)

class UnifiedProxy:
    def __init__(self):
        self.channel_caches = {}
        self.mp4_caches = {}
        self.ssl_context = ssl.create_default_context()
        self.ssl_context.check_hostname = False
        self.ssl_context.verify_mode = ssl.CERT_NONE
        self.stream_lock = threading.Lock()
        self.cache_lock = threading.Lock()
        self.segment_cache = {}
        self.segment_cache_lock = threading.Lock()
        self.prefetching = set()
        self.prefetching_lock = threading.Lock()
        self.active_streams = 0
        self.active_streams_lock = threading.Lock()
        self.channel_cache_last_used = {}
        self.mp4_cache_last_used = {}
        self.maintenance_started = False
        self.maintenance_lock = threading.Lock()
        self.channel_warmed_up = set()
        self.warmup_lock = threading.Lock()
        self.prefetch_threads_active = 0
        self.prefetch_threads_lock = threading.Lock()
        self.active_handlers = 0
        self.active_handlers_lock = threading.Lock()

    def start_maintenance(self):
        with self.maintenance_lock:
            if self.maintenance_started:
                return
            self.maintenance_started = True
        t = threading.Thread(target=self.cleanup_loop, daemon=True)
        t.start()
        pass

    def cleanup_loop(self):
        while True:
            time.sleep(CACHE_CLEANUP_INTERVAL)
            now = time.time()
            try:
                removed = 0
                with self.stream_lock:
                    stale = [k for k, ts in self.channel_cache_last_used.items()
                             if now - ts > CACHE_ENTRY_TTL]
                    for k in stale:
                        self.channel_caches.pop(k, None)
                        self.channel_cache_last_used.pop(k, None)
                        removed += 1
                if stale:
                    with self.warmup_lock:
                        for k in stale:
                            self.channel_warmed_up.discard(k)
                with self.cache_lock:
                    stale = [k for k, ts in self.mp4_cache_last_used.items()
                             if now - ts > CACHE_ENTRY_TTL]
                    for k in stale:
                        self.mp4_caches.pop(k, None)
                        self.mp4_cache_last_used.pop(k, None)
                        removed += 1
                if removed:
                    pass
            except Exception as e:
                pass

    def acquire_handler_slot(self):
        with self.active_handlers_lock:
            self.active_handlers += 1
            over_limit = self.active_handlers > MAX_CONCURRENT_HANDLERS
            if over_limit:
                pass
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

    def get_random_user_agent(self):
        return random.choice(UA_POOL)

    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        except Exception:
            ip = "127.0.0.1"
        finally:
            s.close()
        return ip

    def extract_url_from_path(self, path):
        pass
        if '/?url=' in path:
            url_part = path.split('/?url=', 1)[1]
            if '&' in url_part:
                url_part = url_part.split('&', 1)[0]
            if ' ' in url_part:
                url_part = url_part.split(' ', 1)[0]
            return unquote(url_part)
        if '/tsdownloader' in path and '?url=' in path:
            params = path.split('?', 1)[1]
            for param in params.split('&'):
                if param.startswith('url='):
                    return unquote(param[4:])
        if path.startswith('/http://') or path.startswith('/https://'):
            return unquote(path[1:])
        if path.startswith('http://') or path.startswith('https://'):
            return unquote(path)
        return None

    def channel_key(self, url):
        return re.sub(r'(_=\d+|timestamp=\d+|t=\d+|seq=\d+)', '', url)

    def get_channel_cache(self, url):
        clean_url = self.channel_key(url)
        with self.stream_lock:
            if clean_url not in self.channel_caches:
                self.channel_caches[clean_url] = CircularBuffer(CACHE_DURATION_SECONDS, CACHE_MAX_CHUNKS)
            self.channel_cache_last_used[clean_url] = time.time()
            return self.channel_caches[clean_url]

    def get_mp4_cache(self, url):
        clean_url = re.sub(r'(_=\d+|timestamp=\d+|t=\d+|seq=\d+)', '', url)
        with self.cache_lock:
            if clean_url not in self.mp4_caches:
                self.mp4_caches[clean_url] = MP4Cache(CACHE_MAX_CHUNKS)
            self.mp4_cache_last_used[clean_url] = time.time()
            return self.mp4_caches[clean_url]

    def fetch_channel_with_fallback(self, url, headers=None, range_header=None, cache=None,
                                     is_alive=None, max_retries=None, timeout=15):
        if headers is None:
            headers = {}
        retries = max_retries if max_retries is not None else MAX_RETRIES
        for attempt in range(retries):
            if is_alive is not None and not is_alive():
                pass
                return None, 0, None
            if attempt == 0:
                user_agent = CHROME_UA
            else:
                user_agent = self.get_random_user_agent()
            origin = get_origin(url)
            req_headers = {
                'User-Agent': user_agent,
                'Accept': '*/*',
                'Accept-Language': 'pt-BR,pt;q=0.9',
                'Connection': 'keep-alive'
            }
            if origin:
                req_headers['Origin'] = origin
                req_headers['Referer'] = origin + '/'
            for key, value in headers.items():
                if key.lower() not in ['host', 'connection', 'content-length', 'range', 'user-agent', 'accept-encoding']:
                    req_headers[key] = value
            if range_header:
                req_headers['Range'] = range_header
            try:
                req = Request(url, headers=req_headers)
                if url.startswith('https'):
                    response = urlopen(req, timeout=timeout, context=self.ssl_context)
                else:
                    response = urlopen(req, timeout=timeout)
                status_code = response.getcode()
                content_encoding = response.headers.get('content-encoding', '').lower()
                if status_code not in [200, 206]:
                    return None, status_code, None
                return response, status_code, content_encoding
            except HTTPError as e:
                if attempt < retries - 1 and e.code in [403, 406, 451, 500, 502, 503, 504, 523]:
                    if is_alive is not None and not is_alive():
                        return None, 0, None
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return None, e.code, None
            except Exception:
                if attempt < retries - 1:
                    if is_alive is not None and not is_alive():
                        return None, 0, None
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return None, 0, None
        return None, 0, None

    def segment_key(self, url):
        return re.sub(r'(_=\d+|timestamp=\d+|t=\d+|seq=\d+)', '', url)

    def get_cached_segment(self, url):
        key = self.segment_key(url)
        with self.segment_cache_lock:
            entry = self.segment_cache.get(key)
            if not entry:
                return None
            data, ts = entry
            if time.time() - ts > SEGMENT_CACHE_TTL:
                del self.segment_cache[key]
                return None
            return data

    def store_segment(self, url, data):
        if not data:
            return
        if data[0] != 0x47:
            pass
            return
        key = self.segment_key(url)
        with self.segment_cache_lock:
            self.segment_cache[key] = (data, time.time())
            if len(self.segment_cache) > SEGMENT_CACHE_MAX:
                oldest_key = min(self.segment_cache, key=lambda k: self.segment_cache[k][1])
                if oldest_key != key:
                    del self.segment_cache[oldest_key]

    def download_complete_segment(self, url, headers, timeout=20):
        try:
            response, status, content_encoding = self.fetch_channel_with_fallback(
                url, headers, max_retries=MAX_RETRIES, timeout=timeout
            )
            if response and status in (200, 206):
                data = response.read()
                response.close()
                if content_encoding == 'gzip':
                    data = gzip.decompress(data)
                elif content_encoding == 'deflate':
                    data = zlib.decompress(data)
                if len(data) > 0 and data[0] == 0x47:
                    return data
                else:
                    pass
                    return None
        except Exception as e:
            pass
        return None

    def download_segment_to_cache(self, url, headers):
        key = self.segment_key(url)
        response = None
        try:
            response, status_code, content_encoding = self.fetch_channel_with_fallback(
                url, headers, max_retries=PREFETCH_MAX_RETRIES, timeout=PREFETCH_TIMEOUT
            )
            if response and status_code in [200, 206]:
                data = response.read()
                try:
                    if content_encoding == 'gzip':
                        data = gzip.decompress(data)
                    elif content_encoding == 'deflate':
                        data = zlib.decompress(data)
                except Exception:
                    pass
                self.store_segment(url, data)
        except Exception as e:
            pass
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            with self.prefetching_lock:
                self.prefetching.discard(key)
            with self.prefetch_threads_lock:
                if self.prefetch_threads_active > 0:
                    self.prefetch_threads_active -= 1

    def extract_segments_with_duration(self, playlist_content, base_url):
        segments = []
        pending_duration = None
        for line in playlist_content.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('#EXTINF'):
                try:
                    pending_duration = float(line.split(':', 1)[1].split(',', 1)[0])
                except Exception:
                    pending_duration = None
                continue
            if line.startswith('#'):
                continue
            try:
                absolute = urljoin(base_url + '/', line)
                if absolute.startswith(('http://', 'https://')):
                    segments.append((absolute, pending_duration or 6.0))
            except Exception:
                pass
            pending_duration = None
        return segments

    def prefetch_segments(self, urls, headers, channel_key=None):
        to_fetch = []
        for seg_url in urls[:PREFETCH_SEGMENT_COUNT]:
            key = self.segment_key(seg_url)
            with self.prefetching_lock:
                if self.get_cached_segment(seg_url):
                    continue
                if key in self.prefetching:
                    continue
                self.prefetching.add(key)
            to_fetch.append(seg_url)
        if not to_fetch:
            return []
        with self.prefetch_threads_lock:
            if self.prefetch_threads_active >= MAX_PREFETCH_THREADS:
                with self.prefetching_lock:
                    for seg_url in to_fetch:
                        self.prefetching.discard(self.segment_key(seg_url))
                pass
                return []
            self.prefetch_threads_active += 1
        t = threading.Thread(target=self.download_segments_sequentially, args=(to_fetch, headers, channel_key))
        t.daemon = True
        t.start()
        return [t]

    def download_segments_sequentially(self, urls, headers, channel_key=None):
        for seg_url in urls:
            if channel_key is not None:
                last_used = self.channel_cache_last_used.get(channel_key)
                if last_used is not None and (time.time() - last_used) > CHANNEL_IDLE_ABORT_SECONDS:
                    with self.prefetching_lock:
                        self.prefetching.discard(self.segment_key(seg_url))
                    continue
            self.download_segment_to_cache(seg_url, headers)

    def rewrite_m3u8_urls(self, playlist_content, base_url, proxy_host, headers=None, prefetch=True, channel_key=None):
        segment_urls = []
        def to_proxy_url(raw_url):
            raw_url = raw_url.strip()
            if not raw_url:
                return raw_url
            try:
                absolute = urljoin(base_url + '/', raw_url)
                if absolute.startswith('http://127.0.0.1') or absolute.startswith('http://localhost'):
                    return absolute
                if absolute.startswith(('http://', 'https://')):
                    return absolute, "http://{}/?url={}".format(proxy_host, quote(absolute, safe=''))
            except Exception:
                pass
            return None
        def proxify_line(raw_url):
            result = to_proxy_url(raw_url)
            if not result:
                return raw_url
            absolute, proxied = result
            segment_urls.append(absolute)
            return proxied
        def proxify_attr(match):
            raw_url = match.group(1)
            result = to_proxy_url(raw_url)
            if not result:
                return match.group(0)
            _, proxied = result
            return 'URI="{}"'.format(proxied)
        uri_attr_re = re.compile(r'URI="([^"]+)"')
        lines = []
        for line in playlist_content.split('\n'):
            line = line.rstrip()
            if not line:
                lines.append(line)
                continue
            if line.startswith('#'):
                if 'URI="' in line:
                    line = uri_attr_re.sub(proxify_attr, line)
            else:
                line = proxify_line(line)
            lines.append(line)
        if prefetch and segment_urls:
            self.prefetch_segments(segment_urls, headers, channel_key=channel_key)
        return '\n'.join(lines)

    def _send_segment(self, wfile, data):
        try:
            wfile.write(b"HTTP/1.1 200 OK\r\n")
            wfile.write(b"Content-Type: video/mp2t\r\n")
            wfile.write(b"Access-Control-Allow-Origin: *\r\n")
            wfile.write(b"Cache-Control: no-cache\r\n")
            wfile.write(f"Content-Length: {len(data)}\r\n".encode())
            wfile.write(b"\r\n")
            wfile.write(data)
        except Exception as e:
            pass

    def _send_error(self, wfile, code, message=""):
        try:
            body = f"{code} {message}".encode()
            wfile.write(f"HTTP/1.1 {code} {message}\r\n".encode())
            wfile.write(b"Content-Type: text/plain\r\n")
            wfile.write(f"Content-Length: {len(body)}\r\n".encode())
            wfile.write(b"Connection: close\r\n\r\n")
            wfile.write(body)
        except Exception:
            pass

    def handle_channel_stream(self, url, headers, wfile, client_sock=None, method='GET'):
        if method in ('HEAD', 'OPTIONS'):
            content_type = 'application/vnd.apple.mpegurl' if '.m3u8' in url.lower() else 'video/mp2t'
            try:
                wfile.write("HTTP/1.1 200 OK\r\n".encode())
                wfile.write("Content-Type: {}\r\n".format(content_type).encode())
                wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                wfile.write(b"Cache-Control: no-cache\r\n")
                wfile.write(b"Content-Length: 0\r\n")
                wfile.write(b"\r\n")
            except Exception:
                pass
            return

        is_ts_segment = '.ts' in url.lower() or '/segment/' in url.lower()

        if is_ts_segment:
            cached = self.get_cached_segment(url)
            if cached:
                self._send_segment(wfile, cached)
                return
            segment_data = self.download_complete_segment(url, headers)
            if segment_data is None:
                self._send_error(wfile, 503, "Segmento indisponivel")
                return
            self.store_segment(url, segment_data)
            self._send_segment(wfile, segment_data)
            return

        cache = self.get_channel_cache(url)
        is_playlist_url = '.m3u8' in url.lower()
        if not is_playlist_url:
            cached_segment = self.get_cached_segment(url)
            if cached_segment:
                try:
                    wfile.write(b"HTTP/1.1 200 OK\r\n")
                    wfile.write(b"Content-Type: video/mp2t\r\n")
                    wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                    wfile.write(b"Cache-Control: no-cache\r\n")
                    wfile.write(b"Connection: keep-alive\r\n")
                    wfile.write("Content-Length: {}\r\n".format(len(cached_segment)).encode())
                    wfile.write(b"\r\n")
                    wfile.write(cached_segment)
                    cache.add_chunk(cached_segment)
                    return
                except (BrokenPipeError, socket.error):
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

        last_alive_check = [0.0]
        def is_client_alive():
            if client_gone[0]:
                return False
            if client_sock is None:
                return True
            now = time.time()
            if now - last_alive_check[0] < 0.25:
                return True
            last_alive_check[0] = now
            try:
                client_sock.settimeout(0)
                peek = client_sock.recv(1, socket.MSG_PEEK)
                if peek == b'':
                    client_gone[0] = True
                    return False
                return True
            except BlockingIOError:
                return True
            except (ConnectionResetError, ConnectionAbortedError, OSError):
                client_gone[0] = True
                return False
            except Exception:
                return True
            finally:
                try:
                    client_sock.settimeout(SOCKET_STREAM_TIMEOUT)
                except Exception:
                    pass

        response = None
        stream_slot_acquired = self.acquire_stream_slot()
        if not stream_slot_acquired:
            pass
            return
        try:
            pass
            response, status_code, content_encoding = self.fetch_channel_with_fallback(
                url, headers, None, cache, is_alive=is_client_alive
            )
            if response is None:
                recovery_chunks = cache.get_recovery_chunks(CACHE_DURATION_SECONDS)
                if recovery_chunks:
                    if not safe_write(b"HTTP/1.1 200 OK\r\n"
                                      b"Content-Type: video/mp2t\r\n"
                                      b"Access-Control-Allow-Origin: *\r\n"
                                      b"Cache-Control: no-cache\r\n"
                                      b"Connection: keep-alive\r\n\r\n"):
                        return
                    for chunk in recovery_chunks:
                        if not safe_write(chunk):
                            return
                        time.sleep(0.03)
                else:
                    return
            if response and status_code in [200, 206]:
                content_type = response.headers.get('content-type', '').lower()
                content_url = response.geturl()
                if 'mpegurl' in content_type or '.m3u8' in content_url.lower():
                    raw_content = response.read()
                    try:
                        if content_encoding == 'gzip':
                            content = gzip.decompress(raw_content)
                        elif content_encoding == 'deflate':
                            content = zlib.decompress(raw_content)
                        else:
                            content = raw_content
                    except:
                        content = raw_content
                    try:
                        playlist_text = content.decode('utf-8', errors='ignore')
                        proxy_host = "127.0.0.1:{}".format(get_active_port())
                        base_url = content_url.rsplit('/', 1)[0]
                        rewritten = self.rewrite_m3u8_urls(playlist_text, base_url, proxy_host, headers,
                                                            channel_key=self.channel_key(url))
                        response.close()
                        safe_write(b"HTTP/1.1 200 OK\r\n"
                                   b"Content-Type: application/vnd.apple.mpegurl\r\n" +
                                   "Content-Length: {}\r\n".format(len(rewritten)).encode() +
                                   b"Access-Control-Allow-Origin: *\r\n"
                                   b"Cache-Control: no-cache\r\n\r\n" +
                                   rewritten.encode('utf-8'))
                        return
                    except Exception as e:
                        pass
                        return
                content_length_header = response.headers.get('content-length')
                try:
                    expected_length = int(content_length_header) if content_length_header else None
                except (TypeError, ValueError):
                    expected_length = None

                bytes_received = 0

                header_bytes = ("HTTP/1.1 {} OK\r\n".format(206 if status_code == 206 else 200) +
                                "Content-Type: video/mp2t\r\n"
                                "Access-Control-Allow-Origin: *\r\n"
                                "Cache-Control: no-cache\r\n"
                                "Connection: keep-alive\r\n")
                if content_length_header:
                    header_bytes += "Content-Length: {}\r\n".format(content_length_header)
                header_bytes += "\r\n"
                if not safe_write(header_bytes.encode()):
                    return
                cache.stream_started = True

                consecutive_errors = 0
                eof_reconnects = 0
                total_reconnects = 0
                last_progress = time.time()
                while not client_gone[0]:
                    if not is_client_alive():
                        pass
                        break
                    if time.time() - last_progress > MAX_STALL_SECONDS:
                        pass
                        break
                    if total_reconnects > MAX_TOTAL_RECONNECTS:
                        pass
                        break
                    try:
                        if response:
                            chunk = response.read(BUFFER_SIZE)
                            if chunk:
                                cache.add_chunk(chunk)
                                if not safe_write(chunk):
                                    break
                                bytes_received += len(chunk)
                                consecutive_errors = 0
                                eof_reconnects = 0
                                last_progress = time.time()
                            else:
                                try:
                                    response.close()
                                except:
                                    pass
                                response = None
                                if expected_length is not None and bytes_received >= expected_length:
                                    break
                                eof_reconnects += 1
                                if eof_reconnects > MAX_EOF_RECONNECTS:
                                    break
                        else:
                            cache_chunks = cache.get_continuous_chunks(30)
                            if cache_chunks:
                                wrote_any = False
                                for chunk in cache_chunks:
                                    if not safe_write(chunk):
                                        break
                                    wrote_any = True
                                    time.sleep(0.03)
                                if client_gone[0]:
                                    break
                                if wrote_any:
                                    last_progress = time.time()
                            total_reconnects += 1
                            if not is_client_alive():
                                break
                            try:
                                already_sent_before_reconnect = bytes_received
                                resume_range = "bytes={}-".format(bytes_received) if bytes_received > 0 else None
                                new_response, new_status, _ = self.fetch_channel_with_fallback(
                                    url, headers, resume_range, cache,
                                    is_alive=is_client_alive, max_retries=MAX_RECONNECT_RETRIES
                                )
                                if new_response and new_status == 200 and already_sent_before_reconnect > 0:
                                    pass
                                    new_response.close()
                                    break
                                if new_response and new_status in [200, 206]:
                                    if response:
                                        response.close()
                                    response = new_response
                                    if new_status == 200:
                                        new_content_length = response.headers.get('content-length')
                                        try:
                                            expected_length = int(new_content_length) if new_content_length else None
                                        except (TypeError, ValueError):
                                            expected_length = None
                                        bytes_received = 0
                                    consecutive_errors = 0
                                    eof_reconnects = 0
                                    last_progress = time.time()
                                    continue
                            except:
                                pass
                            if not is_client_alive():
                                break
                            time.sleep(1)
                    except (BrokenPipeError, socket.error, ConnectionResetError, ConnectionAbortedError):
                        client_gone[0] = True
                        break
                    except Exception as e:
                        consecutive_errors += 1
                        if consecutive_errors >= 3:
                            total_reconnects += 1
                            if not is_client_alive():
                                break
                            try:
                                if response:
                                    response.close()
                                    response = None
                                already_sent_before_reconnect = bytes_received
                                resume_range = "bytes={}-".format(bytes_received) if bytes_received > 0 else None
                                new_response, new_status, _ = self.fetch_channel_with_fallback(
                                    url, headers, resume_range, cache,
                                    is_alive=is_client_alive, max_retries=MAX_RECONNECT_RETRIES
                                )
                                if new_response and new_status == 200 and already_sent_before_reconnect > 0:
                                    pass
                                    new_response.close()
                                    break
                                if new_response and new_status in [200, 206]:
                                    response = new_response
                                    if new_status == 200:
                                        new_content_length = response.headers.get('content-length')
                                        try:
                                            expected_length = int(new_content_length) if new_content_length else None
                                        except (TypeError, ValueError):
                                            expected_length = None
                                        bytes_received = 0
                                    consecutive_errors = 0
                                    last_progress = time.time()
                                    continue
                            except:
                                pass
                        recovery = cache.get_continuous_chunks(20)
                        if recovery:
                            for chunk in recovery:
                                if not safe_write(chunk):
                                    break
                                time.sleep(0.03)
                            if client_gone[0]:
                                break
        except Exception as e:
            pass
        finally:
            if response:
                try:
                    response.close()
                except:
                    pass
            if stream_slot_acquired:
                self.release_stream_slot()

    def fetch_mp4_with_retry(self, url, range_header=None, method='GET'):
        for attempt in range(MAX_RETRIES):
            try:
                parsed = urlparse(url)
                referer = f"{parsed.scheme}://{parsed.netloc}/"
                headers = {
                    'User-Agent': CHROME_UA,
                    'Accept': 'video/mp4,video/*;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'pt-BR,pt;q=0.9',
                    'Accept-Encoding': 'identity',
                    'Connection': 'keep-alive',
                    'Referer': referer,
                    'Origin': f"{parsed.scheme}://{parsed.netloc}",
                }
                if range_header:
                    headers['Range'] = range_header
                req = Request(url, headers=headers, method=method)
                if url.startswith('https'):
                    response = urlopen(req, timeout=30, context=self.ssl_context)
                else:
                    response = urlopen(req, timeout=30)
                return response
            except Exception as e:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY)
                    continue
                pass
                return None

    def parse_range(self, range_header):
        if not range_header:
            return None
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if not match:
            return None
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        return start, end

    def parse_total_size(self, headers):
        content_range = headers.get('Content-Range', '') or headers.get('content-range', '')
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
        content_length = headers.get('Content-Length') or headers.get('content-length')
        if content_length and content_length.isdigit():
            return int(content_length)
        return None

    def detect_mp4(self, url):
        lower = url.lower()
        if any(ext in lower for ext in ['.mp4', '.mkv', '.webm', '.f4v', '.mov', '.avi']):
            return True
        if '/play/' in lower:
            return True
        if 'xtream' in lower and ('/movie/' in lower or '/series/' in lower):
            return True
        return False

    def handle_mp4_stream(self, url, method, req_headers, wfile):
        cache = self.get_mp4_cache(url)
        range_header = req_headers.get('range')
        parsed_range = self.parse_range(range_header)
        if parsed_range and parsed_range[1] is not None:
            start, end = parsed_range
            cached = cache.get_range(start, end + 1)
            if cached:
                total = cache.content_length or '*'
                wfile.write("HTTP/1.1 206 Partial Content\r\n".encode())
                wfile.write(b"Content-Type: video/mp4\r\n")
                wfile.write(b"Accept-Ranges: bytes\r\n")
                wfile.write("Content-Length: {}\r\n".format(len(cached)).encode())
                wfile.write("Content-Range: bytes {}-{}/{}\r\n".format(start, start + len(cached) - 1, total).encode())
                wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                wfile.write(b"\r\n")
                wfile.write(cached)
                return
        upstream = self.fetch_mp4_with_retry(url, range_header=range_header, method=method)
        if not upstream and range_header:
            upstream = self.fetch_mp4_with_retry(url, range_header='bytes=0-', method=method)
        if not upstream:
            return
        status = upstream.getcode()
        total_size = self.parse_total_size(upstream.headers)
        if total_size:
            cache.content_length = total_size
        pos = 0
        if status == 206:
            content_range = upstream.headers.get('Content-Range', '')
            match = re.search(r'bytes\s+(\d+)-', content_range)
            if match:
                pos = int(match.group(1))
            elif parsed_range:
                pos = parsed_range[0]

        if method in ('HEAD', 'OPTIONS'):
            wfile.write("HTTP/1.1 {} OK\r\n".format(status).encode())
            wfile.write("Content-Type: {}\r\n".format(upstream.headers.get('Content-Type', 'video/mp4')).encode())
            wfile.write(b"Accept-Ranges: bytes\r\n")
            if upstream.headers.get('Content-Length'):
                wfile.write("Content-Length: {}\r\n".format(upstream.headers.get('Content-Length')).encode())
            if upstream.headers.get('Content-Range'):
                wfile.write("Content-Range: {}\r\n".format(upstream.headers.get('Content-Range')).encode())
            wfile.write(b"Access-Control-Allow-Origin: *\r\n")
            wfile.write(b"\r\n")
            upstream.close()
            return

        wfile.write("HTTP/1.1 {} OK\r\n".format(status).encode())
        wfile.write("Content-Type: {}\r\n".format(upstream.headers.get('Content-Type', 'video/mp4')).encode())
        wfile.write(b"Accept-Ranges: bytes\r\n")
        if upstream.headers.get('Content-Length'):
            wfile.write("Content-Length: {}\r\n".format(upstream.headers.get('Content-Length')).encode())
        if upstream.headers.get('Content-Range'):
            wfile.write("Content-Range: {}\r\n".format(upstream.headers.get('Content-Range')).encode())
        wfile.write(b"Access-Control-Allow-Origin: *\r\n")
        wfile.write(b"\r\n")
        try:
            while True:
                chunk = upstream.read(BUFFER_SIZE)
                if not chunk:
                    break
                cache.add_chunk(pos, chunk)
                pos += len(chunk)
                wfile.write(chunk)
        except (BrokenPipeError, socket.error):
            pass
        except Exception as e:
            pass
        finally:
            upstream.close()

class ProxyHandler(socketserver.StreamRequestHandler):
    proxy = UnifiedProxy()
    def send_response(self, code, message=None):
        if message is None:
            message = http.client.responses.get(code, "OK")
        self.resp_statusline = f"HTTP/1.1 {code} {message}\r\n"
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
                data += f"{k}: {v}\r\n"
            data += "\r\n"
            self.wfile.write(data.encode("utf-8", "replace"))
        except Exception:
            pass
    def send_error(self, code, message=""):
        body = (f"{code} {message}").encode("utf-8", "replace")
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
        except Exception as e:
            pass
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
            pass
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
            pass
            headers = {}
            for key, value in self.headers.items():
                headers[key.lower()] = value
            if self.proxy.detect_mp4(url):
                self.proxy.handle_mp4_stream(url, self.command, headers, self.wfile)
            else:
                self.proxy.handle_channel_stream(url, headers, self.wfile, getattr(self, 'connection', None), method=self.command)
        except Exception as e:
            pass
            try:
                self.send_error(500, str(e))
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
                pass
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
        if self.redirect_servers:
            active_ports = [s.server_address[1] for s, _t in self.redirect_servers]
            pass
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
            pass
            while self.running:
                if self.monitor and self.monitor.abortRequested():
                    pass
                    break
                try:
                    self.server.handle_request()
                except OSError as e:
                    pass
                except Exception as e:
                    pass
        except NoPortAvailableError as e:
            pass
        except Exception as e:
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
        except Exception as e:
            pass
        pass
    def is_running(self):
        return (
            self.running and
            self.server is not None
        )
if __name__ == '__main__':
    server = UnifiedServer()
    try:
        server.start()
    except KeyboardInterrupt:
        server.stop()