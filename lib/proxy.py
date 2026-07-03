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
    import xbmc
except Exception:
    xbmc = None

def log(msg, level=None):
    try:
        if xbmc:
            if level is None:
                level = xbmc.LOGINFO
            xbmc.log("[XC Pro Proxy] {}".format(msg), level)
        else:
            print("[XC Pro Proxy] {}".format(msg))
    except Exception:
        pass
PROXY_PORT = 9097
CACHE_DURATION_SECONDS = 5
CACHE_MAX_CHUNKS = 250
MAX_RETRIES = 7
RETRY_DELAY = 0.5
BUFFER_SIZE = 32768
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

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
        socket.getaddrinfo = self._resolver
    def _build_query(self, domain):
        transaction_id = random.randint(0, 65535)
        header = struct.pack(">HHHHHH", transaction_id, 0x0100, 1, 0, 0, 0)
        qname = b"".join(
            bytes([len(part)]) + part.encode() for part in domain.split(".")
        ) + b"\x00"
        return header + qname + struct.pack(">HH", 1, 1)
    def _parse_response(self, data):
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
                query = self._build_query(domain)
                sock.sendto(query, (dns, 53))
                data, _ = sock.recvfrom(512)
                sock.close()
                ip = self._parse_response(data)
                if ip:
                    self.cache[domain] = {"ip": ip, "expires": time.time() + 3600}
                    return ip
            except Exception:
                continue
        return None
    def _resolver(self, host, port, *args, **kwargs):
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
    def get_random_user_agent(self):
        random_bytes = binascii.b2a_hex(os.urandom(20))[:32].decode('ascii')
        return f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{random_bytes} Safari/537.36"
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
        log(f"EXTRAINDO URL DE: {path}")
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
    def get_channel_cache(self, url):
        clean_url = re.sub(r'(_=\d+|timestamp=\d+|t=\d+|seq=\d+)', '', url)
        with self.stream_lock:
            if clean_url not in self.channel_caches:
                self.channel_caches[clean_url] = CircularBuffer(CACHE_DURATION_SECONDS, CACHE_MAX_CHUNKS)
            return self.channel_caches[clean_url]
    def get_mp4_cache(self, url):
        clean_url = re.sub(r'(_=\d+|timestamp=\d+|t=\d+|seq=\d+)', '', url)
        with self.cache_lock:
            if clean_url not in self.mp4_caches:
                self.mp4_caches[clean_url] = MP4Cache(CACHE_MAX_CHUNKS)
            return self.mp4_caches[clean_url]
    def fetch_channel_with_fallback(self, url, headers=None, range_header=None, cache=None):
        if headers is None:
            headers = {}
        for attempt in range(MAX_RETRIES):
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
                    response = urlopen(req, timeout=15, context=self.ssl_context)
                else:
                    response = urlopen(req, timeout=15)
                status_code = response.getcode()
                content_encoding = response.headers.get('content-encoding', '').lower()
                if status_code not in [200, 206]:
                    return None, status_code, None
                return response, status_code, content_encoding
            except HTTPError as e:
                if attempt < MAX_RETRIES - 1 and e.code in [403, 406, 451, 500, 502, 503, 504, 523]:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return None, e.code, None
            except Exception:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return None, 0, None
        return None, 0, None
    def rewrite_m3u8_urls(self, playlist_content, base_url, proxy_host):
        def proxify(raw_url):
            raw_url = raw_url.strip()
            if not raw_url or raw_url.startswith('#'):
                return raw_url
            try:
                absolute = urljoin(base_url + '/', raw_url)
                if absolute.startswith('http://127.0.0.1') or absolute.startswith('http://localhost'):
                    return absolute
                if absolute.startswith(('http://', 'https://')):
                    return "http://{}/?url={}".format(proxy_host, quote(absolute, safe=''))
            except Exception:
                pass
            return raw_url
        lines = []
        for line in playlist_content.split('\n'):
            line = line.rstrip()
            if line and not line.startswith('#'):
                line = proxify(line)
            lines.append(line)
        return '\n'.join(lines)
    def handle_channel_stream(self, url, headers, wfile):
        cache = self.get_channel_cache(url)
        response = None
        try:
            log("Iniciando stream de canal: {}".format(url[:80]))
            response, status_code, content_encoding = self.fetch_channel_with_fallback(url, headers, None, cache)
            if response is None:
                recovery_chunks = cache.get_recovery_chunks(CACHE_DURATION_SECONDS)
                if recovery_chunks:
                    wfile.write(b"HTTP/1.1 200 OK\r\n")
                    wfile.write(b"Content-Type: video/mp2t\r\n")
                    wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                    wfile.write(b"Cache-Control: no-cache\r\n")
                    wfile.write(b"Connection: keep-alive\r\n\r\n")
                    for chunk in recovery_chunks:
                        try:
                            wfile.write(chunk)
                            time.sleep(0.03)
                        except:
                            break
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
                        proxy_host = "127.0.0.1:{}".format(PROXY_PORT)
                        base_url = content_url.rsplit('/', 1)[0]
                        rewritten = self.rewrite_m3u8_urls(playlist_text, base_url, proxy_host)
                        response.close()
                        wfile.write(b"HTTP/1.1 200 OK\r\n")
                        wfile.write(b"Content-Type: application/vnd.apple.mpegurl\r\n")
                        wfile.write("Content-Length: {}\r\n".format(len(rewritten)).encode())
                        wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                        wfile.write(b"Cache-Control: no-cache\r\n\r\n")
                        wfile.write(rewritten.encode('utf-8'))
                        return
                    except Exception as e:
                        log("Erro M3U8: {}".format(e))
                        return
                wfile.write("HTTP/1.1 {} OK\r\n".format(206 if status_code == 206 else 200).encode())
                wfile.write(b"Content-Type: video/mp2t\r\n")
                wfile.write(b"Access-Control-Allow-Origin: *\r\n")
                wfile.write(b"Cache-Control: no-cache\r\n")
                wfile.write(b"Connection: keep-alive\r\n")
                if 'content-length' in response.headers:
                    wfile.write("Content-Length: {}\r\n".format(response.headers['content-length']).encode())
                wfile.write(b"\r\n")
                cache.stream_started = True
                consecutive_errors = 0
                while True:
                    try:
                        if response:
                            chunk = response.read(BUFFER_SIZE)
                            if chunk:
                                cache.add_chunk(chunk)
                                wfile.write(chunk)
                                consecutive_errors = 0
                            else:
                                break
                        else:
                            cache_chunks = cache.get_continuous_chunks(30)
                            if cache_chunks:
                                for chunk in cache_chunks:
                                    try:
                                        wfile.write(chunk)
                                        time.sleep(0.03)
                                    except:
                                        break
                            try:
                                new_response, new_status, _ = self.fetch_channel_with_fallback(
                                    url, headers, "bytes={}-".format(cache.total_bytes), cache
                                )
                                if new_response and new_status in [200, 206]:
                                    if response:
                                        response.close()
                                    response = new_response
                                    continue
                            except:
                                pass
                            time.sleep(1)
                    except (BrokenPipeError, socket.error):
                        break
                    except Exception as e:
                        consecutive_errors += 1
                        if consecutive_errors >= 3:
                            try:
                                if response:
                                    response.close()
                                    response = None
                                new_response, new_status, _ = self.fetch_channel_with_fallback(
                                    url, headers, "bytes={}-".format(cache.total_bytes), cache
                                )
                                if new_response and new_status in [200, 206]:
                                    response = new_response
                                    consecutive_errors = 0
                                    continue
                            except:
                                pass
                        if cache.get_continuous_chunks(20):
                            for chunk in cache.get_continuous_chunks(20):
                                try:
                                    wfile.write(chunk)
                                    time.sleep(0.03)
                                except:
                                    break
        except Exception as e:
            log("Erro handle_channel_stream: {}".format(e))
        finally:
            if response:
                try:
                    response.close()
                except:
                    pass
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
                log("Erro fetch MP4: {}".format(e))
                return None
    def _parse_range(self, range_header):
        if not range_header:
            return None
        match = re.search(r'bytes=(\d+)-(\d*)', range_header)
        if not match:
            return None
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        return start, end
    def _parse_total_size(self, headers):
        content_range = headers.get('Content-Range', '') or headers.get('content-range', '')
        match = re.search(r"/(\d+)$", content_range)
        if match:
            return int(match.group(1))
        content_length = headers.get('Content-Length') or headers.get('content-length')
        if content_length and content_length.isdigit():
            return int(content_length)
        return None
    def _detect_mp4(self, url):
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
        parsed_range = self._parse_range(range_header)
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
        total_size = self._parse_total_size(upstream.headers)
        if total_size:
            cache.content_length = total_size
        wfile.write("HTTP/1.1 {} OK\r\n".format(status).encode())
        wfile.write("Content-Type: {}\r\n".format(upstream.headers.get('Content-Type', 'video/mp4')).encode())
        wfile.write(b"Accept-Ranges: bytes\r\n")
        if upstream.headers.get('Content-Length'):
            wfile.write("Content-Length: {}\r\n".format(upstream.headers.get('Content-Length')).encode())
        if upstream.headers.get('Content-Range'):
            wfile.write("Content-Range: {}\r\n".format(upstream.headers.get('Content-Range')).encode())
        wfile.write(b"Access-Control-Allow-Origin: *\r\n")
        wfile.write(b"\r\n")
        if method in ('HEAD', 'OPTIONS'):
            upstream.close()
            return
        pos = 0
        if status == 206:
            content_range = upstream.headers.get('Content-Range', '')
            match = re.search(r'bytes\s+(\d+)-', content_range)
            if match:
                pos = int(match.group(1))
            elif parsed_range:
                pos = parsed_range[0]
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
            log("Erro stream MP4: {}".format(e))
        finally:
            upstream.close()

class ProxyHandler(socketserver.StreamRequestHandler):
    proxy = UnifiedProxy()
    def send_response(self, code, message=None):
        if message is None:
            message = http.client.responses.get(code, "OK")
        self._resp_statusline = f"HTTP/1.1 {code} {message}\r\n"
        self._resp_headers = []
    def send_header(self, key, value):
        self._resp_headers.append((key, value))
    def end_headers(self):
        try:
            has_conn = any(k.lower() == "connection" for k, _ in self._resp_headers)
            if not has_conn:
                self._resp_headers.append(("Connection", "close"))
            data = self._resp_statusline
            for k, v in self._resp_headers:
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
            log("RAW socket handler erro: {}".format(e))
            try:
                self.send_error(500, "Internal Server Error")
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
            log("Requisição: {} {}".format(self.command, self.path))
            url = self.proxy.extract_url_from_path(self.path)
            if not url:
                html = """<html><body>
<h2>XC Pro Proxy Active</h2>
<p>Proxy funcionando na porta {}</p>
</body></html>""".format(PROXY_PORT).encode("utf-8")
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return
            log("Proxy para URL: {}".format(url))
            headers = {}
            for key, value in self.headers.items():
                headers[key.lower()] = value
            if self.proxy._detect_mp4(url):
                self.proxy.handle_mp4_stream(url, self.command, headers, self.wfile)
            else:
                self.proxy.handle_channel_stream(url, headers, self.wfile)
        except Exception as e:
            log("Erro handle_request: {}".format(e))
            try:
                self.send_error(500, str(e))
            except Exception:
                pass

class ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

class UnifiedServer:
    def __init__(self, port=9090):
        global PROXY_PORT
        self.port = int(port)
        PROXY_PORT = self.port
        self.server = None
        self.running = False
        self.monitor = None
    def start(self, monitor=None):
        self.monitor = monitor
        self.running = True
        try:
            self.server = ThreadedTCPServer(("127.0.0.1", self.port), ProxyHandler)
            self.server.timeout = 1
            log("=== PROXY INICIADO em 127.0.0.1:{} ===".format(self.port))
            while self.running:
                if self.monitor and self.monitor.abortRequested():
                    log("abortRequested detectado, parando proxy...")
                    break
                try:
                    self.server.handle_request()
                except OSError as e:
                    log("Socket error: {}".format(e))
                except Exception as e:
                    log("Erro processando request: {}".format(e))
        except Exception as e:
            log("Erro no servidor: {}".format(e))
        finally:
            self.stop()
    def stop(self):
        self.running = False
        try:
            if self.server:
                try:
                    self.server.server_close()
                except Exception:
                    pass
                self.server = None
        except Exception as e:
            log("Erro ao fechar servidor: {}".format(e))
        log("Proxy finalizado")
    def is_running(self):
        return (
            self.running and
            self.server is not None
        )
if __name__ == '__main__':
    server = UnifiedServer(port=PROXY_PORT)
    try:
        print("Iniciando Proxy Unificado na porta {}".format(PROXY_PORT))
        server.start()
    except KeyboardInterrupt:
        print("Parando Proxy Unificado...")
        server.stop()
