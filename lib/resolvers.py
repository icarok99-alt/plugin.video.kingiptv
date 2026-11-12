# -*- coding: utf-8 -*-

import re
import random
import string
import time
import requests
from urllib.parse import urlparse, quote_plus
from lib import jsunpack

TIMEOUT = 15


class Resolver:
    FF_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'
    OPERA_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 OPR/112.0.0.0'
    EDGE_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0'
    CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    SAFARI_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'
    ANDROID_USER_AGENT = 'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36'
    IOS_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
    # Apenas UAs desktop: UAs mobile podem devolver paginas/players diferentes
    USER_AGENTS = [FF_USER_AGENT, EDGE_USER_AGENT, CHROME_USER_AGENT]

    DOC_ACCEPT = 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7'
    ACCEPT_LANGUAGE = 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7'

    # ------------------------------------------------------------------ headers

    @classmethod
    def rand_ua(cls):
        return random.choice(cls.USER_AGENTS)

    @classmethod
    def build_headers(cls, user_agent=None, referer=None):
        """Headers de navegacao (paginas HTML), coerentes com o User-Agent."""
        ua = user_agent or cls.CHROME_USER_AGENT
        headers = {
            'User-Agent': ua,
            'Accept': cls.DOC_ACCEPT,
            'Accept-Language': cls.ACCEPT_LANGUAGE,
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site' if referer else 'none',
            'Sec-Fetch-User': '?1',
        }
        if referer:
            headers['Referer'] = referer
        # Client hints so existem em navegadores Chromium
        match = re.search(r'Chrome/(\d+)', ua)
        if match:
            version = match.group(1)
            brand = 'Microsoft Edge' if 'Edg/' in ua else 'Google Chrome'
            headers['sec-ch-ua'] = '"Not/A)Brand";v="8", "Chromium";v="{0}", "{1}";v="{0}"'.format(version, brand)
            headers['sec-ch-ua-mobile'] = '?0'
            headers['sec-ch-ua-platform'] = '"Windows"'
        return headers

    @classmethod
    def media_headers(cls, user_agent, referer=None, origin=None):
        """Headers enxutos para o player usar na reproducao do stream."""
        headers = {
            'User-Agent': user_agent or cls.CHROME_USER_AGENT,
            'Accept': '*/*',
            'Accept-Language': cls.ACCEPT_LANGUAGE,
        }
        if referer:
            headers['Referer'] = referer
        if origin:
            headers['Origin'] = origin
        return headers

    @classmethod
    def append_headers(cls, headers):
        return '|%s' % '&'.join(['%s=%s' % (key, quote_plus(headers[key])) for key in headers])

    # ------------------------------------------------------------------ http

    @classmethod
    def get_text(cls, url, headers, timeout=TIMEOUT, retries=2):
        for attempt in range(retries):
            try:
                r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
                if r.status_code >= 400:
                    return '', r
                return r.text, r
            except requests.RequestException:
                if attempt + 1 < retries:
                    time.sleep(0.5)
        return '', None

    @classmethod
    def final_url(cls, url, headers):
        """Segue redirects ate a URL final (tenta HEAD e cai para GET com Range)."""
        try:
            r = requests.head(url, headers=headers, allow_redirects=True, timeout=TIMEOUT)
            if r.status_code < 400 and r.url:
                return r.url
        except requests.RequestException:
            pass
        r = None
        try:
            h = dict(headers)
            h['Range'] = 'bytes=0-0'
            r = requests.get(url, headers=h, stream=True, allow_redirects=True, timeout=TIMEOUT)
            if r.status_code < 400:
                return r.url
        except requests.RequestException:
            pass
        finally:
            if r is not None:
                r.close()
        return ''

    @classmethod
    def last_url(cls, url, headers):
        return cls.final_url(url, headers)

    # ------------------------------------------------------------------ verificacao

    @classmethod
    def _looks_like_media(cls, chunk, content_type):
        start = chunk.lstrip()[:32].lower()
        if start.startswith(b'#extm3u'):
            return True
        if start.startswith((b'<!doctype', b'<html', b'<head', b'<body', b'<?xml', b'<script', b'{', b'[')):
            return False
        if chunk[4:8] in (b'ftyp', b'moov', b'mdat', b'free', b'wide', b'styp', b'skip'):
            return True
        if chunk.startswith((b'\x1a\x45\xdf\xa3', b'FLV', b'ID3', b'OggS', b'RIFF')):
            return True
        if chunk[:1] == b'\x47' and (len(chunk) <= 188 or chunk[188:189] == b'\x47'):
            return True
        media_types = ('video/', 'audio/', 'application/octet-stream', 'binary/octet-stream',
                       'application/vnd.apple.mpegurl', 'application/x-mpegurl', 'application/mp4')
        return any(content_type.startswith(t) for t in media_types)

    @classmethod
    def verify_stream(cls, url, headers, timeout=TIMEOUT):
        """Confirma que a URL entrega midia de verdade (e nao uma pagina de erro/HTML/JSON)."""
        if not url:
            return False
        r = None
        try:
            h = dict(headers)
            h['Range'] = 'bytes=0-8191'
            r = requests.get(url, headers=h, stream=True, timeout=timeout, allow_redirects=True)
            if r.status_code not in (200, 206):
                return False
            content_type = r.headers.get('Content-Type', '').split(';')[0].strip().lower()
            if content_type in ('text/html', 'application/json', 'application/xhtml+xml'):
                return False
            chunk = b''
            for part in r.iter_content(chunk_size=2048):
                chunk += part
                if len(chunk) >= 2048:
                    break
            if not chunk:
                return False
            is_playlist = chunk.lstrip().startswith(b'#EXTM3U')
            length = r.headers.get('Content-Length', '')
            if r.status_code == 200 and length.isdigit() and int(length) < 2048 and not is_playlist:
                return False
            return cls._looks_like_media(chunk, content_type)
        except Exception:
            return False
        finally:
            if r is not None:
                r.close()

    @classmethod
    def finalize(cls, candidate, headers):
        """Retorna 'url|headers' somente se o link estiver reproduzivel."""
        if candidate and cls.verify_stream(candidate, headers):
            return candidate + cls.append_headers(headers)
        return ''

    # ------------------------------------------------------------------ utilitarios

    @classmethod
    def get_packed_data(cls, html):
        packed_data = ''
        try:
            for match in re.finditer(r'''(eval\s*\(function\(p,a,c,k,e,.*?)</script>''', html, re.DOTALL | re.I):
                r = match.group(1)
                t = re.findall(r'(eval\s*\(function\(p,a,c,k,e,)', r, re.DOTALL | re.IGNORECASE)
                if len(t) == 1:
                    if jsunpack.detect(r):
                        packed_data += jsunpack.unpack(r)
                else:
                    t = r.split('eval')
                    t = ['eval' + x for x in t if x]
                    for r in t:
                        if jsunpack.detect(r):
                            packed_data += jsunpack.unpack(r)
        except Exception:
            pass
        return packed_data

    @classmethod
    def dood_decode(cls, data):
        t = string.ascii_letters + string.digits
        return data + ''.join([random.choice(t) for _ in range(10)])

    # ------------------------------------------------------------------ resolvers

    @classmethod
    def resolve_doodstream(cls, url, referer):
        try:
            url = url.split('?')[0]
            parsed_uri = urlparse(url)
            host = parsed_uri.netloc.replace('www.', '')
            media_id = url.rstrip('/').split('/')[-1]
            keep_hosts = ['doodstream.com', 'myvidplay.com', 'playmogo.com']
            if host not in keep_hosts:
                host = 'playmogo.com'
            ua = cls.rand_ua()
            web_url = 'https://{0}/d/{1}'.format(host, media_id)
            headers = cls.build_headers(ua, referer='https://{0}/'.format(host))

            html, r = cls.get_text(web_url, headers)
            if r is None:
                return ''
            if r.url != web_url:
                host = urlparse(r.url).netloc.replace('www.', '')
                web_url = 'https://{0}/d/{1}'.format(host, media_id)
            headers['Referer'] = web_url

            match = re.search(r'<iframe\s*src="([^"]+)', html)
            if match:
                iframe_url = match.group(1)
                if iframe_url.startswith('//'):
                    iframe_url = 'https:' + iframe_url
                elif iframe_url.startswith('/'):
                    iframe_url = 'https://{0}{1}'.format(host, iframe_url)
                html, _ = cls.get_text(iframe_url, headers)
            else:
                html, _ = cls.get_text(web_url.replace('/d/', '/e/'), headers)
            if not html:
                return ''

            match = re.search(
                r'''dsplayer\.hotkeys[^']+'([^']+).+?function\s*makePlay.+?return[^?]+([^"]+)''',
                html, re.DOTALL
            )
            if not match:
                return ''
            token = match.group(2)
            pass_url = match.group(1)
            if pass_url.startswith('/'):
                pass_url = 'https://{0}{1}'.format(host, pass_url)
            elif not pass_url.startswith('http'):
                pass_url = 'https://{0}/{1}'.format(host, pass_url.lstrip('/'))

            html, _ = cls.get_text(pass_url, headers)
            if not html:
                return ''
            if 'cloudflarestorage.' in html:
                vid_src = html.strip()
            else:
                vid_src = cls.dood_decode(html) + token + str(int(time.time() * 1000))

            for ref in (web_url, 'https://{0}/'.format(host)):
                play_headers = cls.media_headers(ua, referer=ref)
                stream = cls.finalize(vid_src, play_headers)
                if stream:
                    return stream
        except Exception:
            pass
        return ''

    @classmethod
    def resolve_mixdrop(cls, url, referer):
        try:
            url = url.replace('.club', '.co').split('?')[0]
            ua = cls.rand_ua()
            try:
                r = requests.head(url, headers={'User-Agent': ua}, allow_redirects=True, timeout=TIMEOUT)
                if r.status_code < 400:
                    url = r.url
            except requests.RequestException:
                pass

            host = urlparse(url).netloc
            rurl = referer or 'https://{0}/'.format(host)
            headers = cls.build_headers(ua, referer=rurl)
            headers['Origin'] = rurl.rstrip('/')

            html, _ = cls.get_text(url, headers)
            if not html:
                return ''
            r = re.search(r'''location\s*=\s*["']([^'"]+)''', html)
            if r:
                url = 'https://{0}{1}'.format(host, r.group(1))
                html, _ = cls.get_text(url, headers)
                if not html:
                    return ''
            if '(p,a,c,k,e,d)' in html:
                html = cls.get_packed_data(html)

            r = re.search(r'(?:vsr|wurl|surl)[^=]*=\s*"([^"]+)', html)
            if not r:
                return ''
            surl = r.group(1)
            if surl.startswith('//'):
                surl = 'https:' + surl
            play_headers = cls.media_headers(ua, referer=url)
            return cls.finalize(surl, play_headers)
        except Exception:
            pass
        return ''

    @classmethod
    def resolve_streamtape(cls, url, referer):
        try:
            ua = cls.rand_ua()
            correct_url = url.replace('/v/', '/e/')
            resolved = cls.final_url(correct_url, cls.build_headers(ua, referer=referer or None))
            if resolved:
                correct_url = resolved

            parsed_uri = urlparse(correct_url)
            if not referer:
                referer = '{0}://{1}/'.format(parsed_uri.scheme, parsed_uri.netloc)
            headers = cls.build_headers(ua, referer=referer)
            play_headers = cls.media_headers(ua, referer=referer)

            data, _ = cls.get_text(correct_url, headers)
            if not data:
                return ''

            candidate = ''
            src = re.findall(r'''ById\('.+?=\s*(["']//[^;<]+)''', data)
            if src:
                src_url = ''
                parts = src[-1].replace("'", '"').split('+')
                for part in parts:
                    p1 = re.findall(r'"([^"]*)', part)
                    if not p1:
                        continue
                    p1 = p1[0]
                    p2 = 0
                    if 'substring' in part:
                        for sub in re.findall(r'substring\((\d+)', part):
                            p2 += int(sub)
                    src_url += p1[p2:]
                src_url += '&stream=1'
                if src_url.startswith('//'):
                    src_url = 'https:' + src_url
                candidate = src_url
            else:
                link_part1_re = re.compile(r'<div.+?style="display:none;">(.*?)&token=.+?</div>').findall(data)
                link_part2_re = re.compile(r"&token=(.*?)'").findall(data)
                if link_part1_re and link_part2_re:
                    part1 = link_part1_re[0].replace(' ', '')
                    part2 = link_part2_re[-1]
                    if 'streamtape' in part1:
                        part1 = part1.split('streamtape')[1]
                        candidate = 'https://streamtape' + part1 + '&token=' + part2 + '&stream=1'
                    elif 'get_video' in part1:
                        part1_1 = part1.split('get_video')[0].replace('/', '')
                        part1_2 = part1.split('get_video')[1]
                        candidate = 'https://' + part1_1 + '/get_video' + part1_2 + '&token=' + part2 + '&stream=1'

            if not candidate:
                return ''
            # Tenta a URL final (apos redirects) e, se falhar, a original
            final = cls.final_url(candidate, play_headers)
            for option in [u for u in (final, candidate) if u]:
                stream = cls.finalize(option, play_headers)
                if stream:
                    return stream
        except Exception:
            pass
        return ''

    # ------------------------------------------------------------------ roteamento

    @classmethod
    def resolverurls(cls, url, referer=''):
        stream = ''
        sub = ''
        parsed_url = urlparse(url)
        domain = parsed_url.netloc
        domain = domain.replace('www.', '').replace('ww3.', '').replace('ww4.', '')
        dood_domains = [
            'dood.watch', 'doodstream.com', 'dood.to', 'dood.so', 'dood.cx', 'dood.la', 'dood.ws',
            'dood.sh', 'doodstream.co', 'dood.pm', 'dood.wf', 'dood.re', 'dood.yt', 'dooood.com',
            'dood.stream', 'ds2play.com', 'doods.pro', 'ds2video.com', 'd0o0d.com', 'do0od.com',
            'd0000d.com', 'd000d.com', 'dood.li', 'dood.work', 'dooodster.com', 'vidply.com',
            'all3do.com', 'do7go.com', 'doodcdn.io', 'doply.net', 'vide0.net', 'vvide0.com',
            'd-s.io', 'dsvplay.com', 'myvidplay.com', 'playmogo.com'
        ]
        mixdrop_domains = [
            'mixdrop.co', 'mixdrop.to', 'mixdrop.sx', 'mixdrop.bz', 'mixdrop.ch',
            'mixdrp.co', 'mixdrp.to', 'mixdrop.gl', 'mixdrop.club', 'mixdroop.bz',
            'mixdroop.co', 'mixdrop.vc', 'mixdrop.ag', 'mdy48tn97.com',
            'md3b0j6hj.com', 'mdbekjwqa.pw', 'mdfx9dc8n.net', 'mixdropjmk.pw',
            'mixdrop21.net', 'mixdrop.is', 'mixdrop.si', 'mixdrop23.net', 'mixdrop.nu',
            'mixdrop.ms', 'mdzsmutpcvykb.net', 'mixdrop.ps', 'mxdrop.to', 'mixdrop.sb',
            'mixdrop.my', 'm1xdrop.net', 'm1xdrop.com', 'm1xdrop.click', 'mxdrop.sx',
            'mixdrop.top', 'mixdrp.click', 'miixdrop.net'
        ]
        streamtape_domains = [
            'streamtape.com', 'strtape.cloud', 'streamtape.net', 'streamta.pe', 'streamtape.site',
            'strcloud.link', 'strcloud.club', 'strtpe.link', 'streamtape.cc', 'scloud.online', 'stape.fun',
            'streamadblockplus.com', 'shavetape.cash', 'streamtape.to', 'streamta.site',
            'streamadblocker.xyz', 'tapewithadblock.org', 'adblocktape.wiki', 'antiadtape.com',
            'streamtape.xyz', 'tapeblocker.com', 'streamnoads.com', 'tapeadvertisement.com',
            'tapeadsenjoyer.com', 'watchadsontape.com', 'tpead.net', 'advertape.net'
        ]
        try:
            if domain in dood_domains or any(d in domain for d in ['dood', 'ds2play', 'ds2video', 'vidply', 'all3do', 'do7go', 'doodcdn', 'doply', 'vide0', 'vvide0', 'd-s.io', 'dsvplay', 'myvidplay', 'playmogo']):
                stream = cls.resolve_doodstream(url, referer)
            elif domain in mixdrop_domains or any(d in domain for d in ['mixdrop', 'mixdrp', 'mxdrop', 'm1xdrop', 'miixdrop']):
                stream = cls.resolve_mixdrop(url, referer)
            elif domain in streamtape_domains or any(d in domain for d in ['streamtape', 'strtape', 'strcloud', 'strtpe', 'scloud', 'stape', 'shavetape', 'adblocktape', 'antiadtape', 'tapeblocker', 'streamnoads', 'tapeadvertisement', 'tapeadsenjoyer', 'watchadsontape', 'tpead', 'advertape']):
                stream = cls.resolve_streamtape(url, referer)
        except Exception:
            stream = ''
        return stream, sub


def resolveurl(url, referer=''):
    stream, sub = Resolver.resolverurls(url, referer)
    return stream, sub
