# -*- coding: utf-8 -*-
import os
import re
import json
import html

try:
    from lib.helper import requests
except Exception:
    from helper import requests

TRAKT_CLIENT_ID = 'e89e30a41bea868c945c33a704277903ee3f9c1e1a2b4daff09be5d4544e04c7'
TRAKT_BASE = 'https://api.trakt.tv'

_BASE_HEADERS = {
    'Content-Type': 'application/json',
    'trakt-api-version': '2',
    'trakt-api-key': TRAKT_CLIENT_ID,
}

_img_cache = {}

def _trakt_images(media_type, trakt_item):
    if not trakt_item or 'images' not in trakt_item:
        return '', ''
    trakt_id = trakt_item.get('ids', {}).get('trakt')
    if trakt_id:
        cache_key = f'{media_type}:{trakt_id}'
        if cache_key in _img_cache:
            return _img_cache[cache_key]
    images = trakt_item.get('images', {})
    poster = ''
    poster_list = images.get('poster') or []
    if poster_list:
        p = poster_list[0]
        poster = f"https://{p}" if not p.startswith('http') else p
    fanart = ''
    fanart_list = images.get('fanart') or []
    if fanart_list:
        f = fanart_list[0]
        fanart = f"https://{f}" if not f.startswith('http') else f
    result = (poster, fanart)
    if trakt_id:
        _img_cache[f'{media_type}:{trakt_id}'] = result
    return result

def _get(path, params=None):
    p = params or {}
    if 'extended' not in p:
        p['extended'] = 'images'
    try:
        r = requests.get(
            TRAKT_BASE + path,
            headers=_BASE_HEADERS,
            params=p,
            timeout=10
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None

def _slug_from(v):
    if '::' in v:
        v = v.split('::', 1)[0]
    m = re.search(r'/(tt\d+)/', v)
    return m.group(1) if m else v

def _to_movie(d):
    title = html.unescape(str(d.get('title', '')).strip())
    year = str(d.get('year', '') or '')
    desc = html.unescape(str(d.get('overview', '') or ''))
    ids = d.get('ids', {})
    imdb_id = ids.get('imdb', '') or ''
    slug = ids.get('slug', '') or imdb_id
    poster, fanart = _trakt_images('movie', d)
    return (title, poster, slug, desc, imdb_id, title, year, fanart)

def _to_show(d):
    title = html.unescape(str(d.get('title', '')).strip())
    year = str(d.get('year', '') or '')
    desc = html.unescape(str(d.get('overview', '') or ''))
    ids = d.get('ids', {})
    imdb_id = ids.get('imdb', '') or ''
    slug = ids.get('slug', '') or imdb_id
    poster, fanart = _trakt_images('show', d)
    return (title, poster, slug, desc, imdb_id, title, year, fanart)

def _valid(t):
    return bool(t[0] and t[2])

def _sort_search(results, key, query):
    q = query.strip().lower()
    def rank(item):
        trakt_score = item.get('score', 0) or 0
        title = str((item.get(key) or {}).get('title', '')).lower()
        return (title == q, title.startswith(q), trakt_score)
    return sorted(results, key=rank, reverse=True)

def search_movies(query):
    out = []
    try:
        raw = _get('/search/movie', {'query': query, 'limit': 30, 'extended': 'images'}) or []
        for r in _sort_search(raw, 'movie', query):
            t = _to_movie(r.get('movie', {}))
            if _valid(t):
                out.append(t)
    except Exception:
        pass
    return out

def search_series(query):
    out = []
    try:
        raw = _get('/search/show', {'query': query, 'limit': 30, 'extended': 'images'}) or []
        for r in _sort_search(raw, 'show', query):
            t = _to_show(r.get('show', {}))
            if _valid(t):
                out.append(t)
    except Exception:
        pass
    return out

def movies_popular(page=1, per_page=50):
    out = []
    try:
        for m in (_get('/movies/popular', {'limit': per_page, 'page': page}) or []):
            t = _to_movie(m)
            if _valid(t):
                out.append(t)
    except Exception:
        pass
    return out

def movies_trending(page=1, per_page=50):
    out = []
    try:
        for item in (_get('/movies/trending', {'limit': per_page, 'page': page}) or []):
            t = _to_movie(item.get('movie', {}))
            if _valid(t):
                out.append(t)
    except Exception:
        pass
    return out

def series_popular(page=1, per_page=50):
    out = []
    try:
        for s in (_get('/shows/popular', {'limit': per_page, 'page': page}) or []):
            t = _to_show(s)
            if _valid(t):
                out.append(t)
    except Exception:
        pass
    return out

def series_trending(page=1, per_page=50):
    out = []
    try:
        for item in (_get('/shows/trending', {'limit': per_page, 'page': page}) or []):
            t = _to_show(item.get('show', {}))
            if _valid(t):
                out.append(t)
    except Exception:
        pass
    return out

def get_seasons(slug_or_url):
    out = []
    try:
        slug = _slug_from(slug_or_url)
        for s in (_get(f'/shows/{slug}/seasons', {'extended': 'full,images'}) or []):
            num = s.get('number', 0)
            if num == 0:
                continue
            poster, _ = _trakt_images('season', s)
            ref = f'{slug}::{num}'
            out.append((str(num), f'Season {num}', poster, ref))
    except Exception:
        pass
    return out

def get_episodes(season_ref):
    out = []
    try:
        slug, snum = season_ref.split('::', 1)
        show = _get(f'/shows/{slug}', {'extended': 'images'}) or {}
        poster, fanart = _trakt_images('show', show)
        for ep in (_get(f'/shows/{slug}/seasons/{snum}/episodes', {'extended': 'full,images'}) or []):
            num = ep.get('number', 0)
            title = html.unescape(str(ep.get('title') or f'Episode {num}').strip())
            desc = html.unescape(str(ep.get('overview', '') or ''))
            images = ep.get('images', {})
            screenshot_list = images.get('screenshot') or []
            thumb = ''
            if screenshot_list:
                t = screenshot_list[0]
                thumb = f"https://{t}" if not t.startswith('http') else t
            if not thumb:
                thumb = poster
            out.append((str(num), title, thumb, fanart or poster, desc))
    except Exception:
        pass
    return out
