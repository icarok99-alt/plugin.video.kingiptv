# -*- coding: utf-8 -*-

import re
import random
import string
import time
import requests
from urllib.parse import urlparse, quote_plus
from lib import jsunpack


class Resolver:
    FF_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0'
    OPERA_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 OPR/112.0.0.0'
    EDGE_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0'
    CHROME_USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
    SAFARI_USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15'
    ANDROID_USER_AGENT = 'Mozilla/5.0 (Linux; Android 14; SM-S918B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.6478.122 Mobile Safari/537.36'
    IOS_USER_AGENT = 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1'
    USER_AGENTS = [FF_USER_AGENT, OPERA_USER_AGENT, EDGE_USER_AGENT, CHROME_USER_AGENT, SAFARI_USER_AGENT, ANDROID_USER_AGENT, IOS_USER_AGENT]
    _headers = {
        'User-Agent': CHROME_USER_AGENT,
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        'sec-ch-ua': '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1'
    }

    @classmethod
    def rand_ua(cls):
        return random.choice(cls.USER_AGENTS)

    @classmethod
    def append_headers(cls, headers):
        return '|%s' % '&'.join(['%s=%s' % (key, quote_plus(headers[key])) for key in headers])

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
        except:
            pass
        return packed_data

    @classmethod
    def last_url(cls, url, headers):
        stream = ''
        try:
            r = requests.head(url, headers=headers, allow_redirects=True)
            stream = r.url
        except:
            pass
        return stream

    @classmethod
    def verify_stream(cls, url, headers, timeout=10):
        if not url:
            return False
        try:
            h = dict(headers)
            h['Range'] = 'bytes=0-8191'
            r = requests.get(url, headers=h, stream=True, timeout=timeout, allow_redirects=True)
            try:
                status_ok = r.status_code in (200, 206)
                content_type = r.headers.get('Content-Type', '').lower()
                looks_like_html = 'text/html' in content_type
                if not status_ok or looks_like_html:
                    return False
                chunk = next(r.iter_content(chunk_size=1024), b'')
                return len(chunk) > 0
            finally:
                r.close()
        except:
            return False

    @classmethod
    def dood_decode(cls, data):
        t = string.ascii_letters + string.digits
        return data + ''.join([random.choice(t) for _ in range(10)])

    @classmethod
    def resolve_doodstream(cls, url, referer):
        stream = ''
        try:
            try:
                url = url.split('?')[0]
            except:
                pass
            parsed_uri = urlparse(url)
            host = parsed_uri.netloc.replace('www.', '')
            media_id = url.rstrip('/').split('/')[-1]
            keep_hosts = ['doodstream.com', 'myvidplay.com', 'playmogo.com']
            if host not in keep_hosts:
                host = 'playmogo.com'
            web_url = 'https://{0}/d/{1}'.format(host, media_id)
            headers = {
                'User-Agent': cls.rand_ua(),
                'Referer': 'https://{0}/'.format(host)
            }
            r = requests.get(web_url, headers=headers, allow_redirects=True)
            if r.url != web_url:
                host = urlparse(r.url).netloc.replace('www.', '')
                web_url = 'https://{0}/d/{1}'.format(host, media_id)
            headers['Referer'] = web_url
            html = r.text
            match = re.search(r'<iframe\s*src="([^"]+)', html)
            if match:
                iframe_url = match.group(1)
                if iframe_url.startswith('//'):
                    iframe_url = 'https:' + iframe_url
                elif iframe_url.startswith('/'):
                    iframe_url = 'https://{0}{1}'.format(host, iframe_url)
                html = requests.get(iframe_url, headers=headers, allow_redirects=True).text
            else:
                e_url = web_url.replace('/d/', '/e/')
                html = requests.get(e_url, headers=headers, allow_redirects=True).text
            match = re.search(
                r'''dsplayer\.hotkeys[^']+'([^']+).+?function\s*makePlay.+?return[^?]+([^"]+)''',
                html, re.DOTALL
            )
            if match:
                token = match.group(2)
                pass_url = match.group(1)
                if pass_url.startswith('/'):
                    pass_url = 'https://{0}{1}'.format(host, pass_url)
                elif not pass_url.startswith('http'):
                    pass_url = 'https://{0}/{1}'.format(host, pass_url.lstrip('/'))
                html = requests.get(pass_url, headers=headers, allow_redirects=True).text
                if 'cloudflarestorage.' in html:
                    vid_src = html.strip()
                else:
                    vid_src = cls.dood_decode(html) + token + str(int(time.time() * 1000))
                headers.update({'Referer': web_url})
                if cls.verify_stream(vid_src, headers):
                    stream = vid_src + cls.append_headers(headers)
        except:
            pass
        return stream

    @classmethod
    def resolve_mixdrop(cls, url, referer):
        stream = ''
        try:
            url = url.replace('.club', '.co')
            try:
                url = url.split('?')[0]
            except:
                pass
            user_agent = cls.rand_ua()
            try:
                r = requests.head(url, headers={'User-Agent': user_agent}, allow_redirects=True)
                url = r.url
            except:
                pass
            parsed_uri = urlparse(url)
            host = parsed_uri.netloc
            rurl = 'https://{0}/'.format(host)
            if referer:
                rurl = referer
            headers = {
                'Origin': rurl.rstrip('/'),
                'Referer': rurl,
                'User-Agent': user_agent
            }
            html = requests.get(url, headers=headers, allow_redirects=True).text
            r = re.search(r'''location\s*=\s*["']([^'"]+)''', html)
            if r:
                url = 'https://{0}{1}'.format(host, r.group(1))
                html = requests.get(url, headers=headers, allow_redirects=True).text
            if '(p,a,c,k,e,d)' in html:
                html = cls.get_packed_data(html)
            r = re.search(r'(?:vsr|wurl|surl)[^=]*=\s*"([^"]+)', html)
            if r:
                surl = r.group(1)
                if surl.startswith('//'):
                    surl = 'https:' + surl
                headers.pop('Origin', None)
                headers.update({'Referer': url})
                if cls.verify_stream(surl, headers):
                    stream = surl + cls.append_headers(headers)
        except:
            pass
        return stream

    @classmethod
    def resolve_streamtape(cls, url, referer):
        stream = ''
        try:
            correct_url = url.replace('/v/', '/e/')
            try:
                r = requests.head(correct_url, headers=cls._headers, allow_redirects=True)
                correct_url = r.url
            except:
                pass
            parsed_uri = urlparse(correct_url)
            protocol = parsed_uri.scheme
            host = parsed_uri.netloc
            if not referer:
                referer = '{0}://{1}/'.format(protocol, host)
            headers = dict(cls._headers)
            headers.update({
                'User-Agent': cls.rand_ua(),
                'Referer': referer
            })
            data = requests.get(correct_url, headers=headers, allow_redirects=True).text
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
                        subst = re.findall(r'substring\((\d+)', part)
                        for sub in subst:
                            p2 += int(sub)
                    src_url += p1[p2:]
                src_url += '&stream=1'
                if src_url.startswith('//'):
                    src_url = 'https:' + src_url
                last_stream = cls.last_url(src_url, headers=headers)
                candidate_url = last_stream or src_url
                if cls.verify_stream(candidate_url, headers):
                    stream = candidate_url + cls.append_headers(headers)
            else:
                link_part1_re = re.compile(r'<div.+?style="display:none;">(.*?)&token=.+?</div>').findall(data)
                link_part2_re = re.compile(r"&token=(.*?)'").findall(data)
                if link_part1_re and link_part2_re:
                    part1 = link_part1_re[0].replace(' ', '')
                    part2 = link_part2_re[-1]
                    if 'streamtape' in part1:
                        part1 = part1.split('streamtape')[1]
                        final = 'streamtape' + part1 + '&token=' + part2
                        candidate = 'https://' + final + '&stream=1'
                    elif 'get_video' in part1:
                        part1_1 = part1.split('get_video')[0].replace('/', '')
                        part1_2 = part1.split('get_video')[1]
                        final = part1_1 + '/get_video' + part1_2 + '&token=' + part2
                        candidate = 'https://' + final + '&stream=1'
                    else:
                        candidate = ''
                    if candidate:
                        last_stream = cls.last_url(candidate, headers=headers)
                        candidate_url = last_stream or candidate
                        if cls.verify_stream(candidate_url, headers):
                            stream = candidate_url + cls.append_headers(headers)
        except:
            pass
        return stream

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
        if domain in dood_domains or any(d in domain for d in ['dood', 'ds2play', 'ds2video', 'vidply', 'all3do', 'do7go', 'doodcdn', 'doply', 'vide0', 'vvide0', 'd-s.io', 'dsvplay', 'myvidplay', 'playmogo']):
            stream = cls.resolve_doodstream(url, referer)
        elif domain in mixdrop_domains or any(d in domain for d in ['mixdrop', 'mixdrp', 'mxdrop', 'm1xdrop', 'miixdrop']):
            stream = cls.resolve_mixdrop(url, referer)
        elif domain in streamtape_domains or any(d in domain for d in ['streamtape', 'strtape', 'strcloud', 'strtpe', 'scloud', 'stape', 'shavetape', 'adblocktape', 'antiadtape', 'tapeblocker', 'streamnoads', 'tapeadvertisement', 'tapeadsenjoyer', 'watchadsontape', 'tpead', 'advertape']):
            stream = cls.resolve_streamtape(url, referer)
        return stream, sub


def resolveurl(url, referer=''):
    stream, sub = Resolver.resolverurls(url, referer)
    return stream, sub
