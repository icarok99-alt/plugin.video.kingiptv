# -*- coding: utf-8 -*-

import os
import re
import sys
import json
import threading
from lib.helper import *
import inputstreamhelper
from lib import xtream, tunein, pluto, imdb, matcher, list_manager
from lib.proxy import UnifiedServer, PROXY_PORT
from lib.db_manager import get_db
from lib.loading_window import loading_manager
_addon = xbmcaddon.Addon()
getString = _addon.getLocalizedString
profile = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.kingiptv')
if not exists(profile):
    try:
        os.makedirs(profile)
    except OSError:
        pass
IPTV_PROBLEM_LOG = translate(os.path.join(profile, 'iptv_problems_log.txt'))
TITULO = '::: KING IPTV :::'
API_CHANNELS = '\x68\x74\x74\x70\x73\x3a\x2f\x2f\x64\x6f\x63\x73\x2e\x67\x6f\x6f\x67\x6c\x65\x2e\x63\x6f\x6d\x2f\x75\x63\x3f\x65\x78\x70\x6f\x72\x74\x3d\x64\x6f\x77\x6e\x6c\x6f\x61\x64\x26\x69\x64\x3d\x31\x67\x52\x53\x61\x72\x30\x49\x79\x32\x6f\x47\x65\x70\x4c\x33\x4c\x6b\x4d\x74\x43\x62\x77\x54\x7a\x67\x53\x67\x68\x41\x73\x77\x36'
API_RADIOS = '\x68\x74\x74\x70\x73\x3a\x2f\x2f\x67\x69\x73\x74\x2e\x67\x69\x74\x68\x75\x62\x75\x73\x65\x72\x63\x6f\x6e\x74\x65\x6e\x74\x2e\x63\x6f\x6d\x2f\x69\x63\x61\x72\x6f\x6b\x39\x39\x2f\x64\x65\x38\x38\x63\x33\x66\x30\x61\x34\x31\x39\x64\x32\x35\x34\x30\x33\x62\x31\x31\x30\x65\x33\x64\x31\x32\x38\x37\x31\x65\x31\x2f\x72\x61\x77\x2f\x62\x65\x33\x32\x64\x65\x32\x37\x65\x63\x33\x36\x34\x39\x36\x30\x34\x37\x66\x30\x61\x33\x35\x64\x63\x31\x38\x65\x62\x34\x34\x65\x66\x37\x39\x65\x38\x66\x63\x33\x2f\x72\x61\x64\x69\x6f\x73\x2e\x6a\x73\x6f\x6e'
VOD_MATCH_MIN_SCORE = 0.62
XTREAM_PLAYER_HEADERS = {'User-Agent': xtream.BROWSER_UA}
proxy_server = None
proxy_lock = threading.Lock()

def start_proxy_if_needed():
    global proxy_server
    if proxy_server is not None:
        return PROXY_PORT
    with proxy_lock:
        if proxy_server is not None:
            return PROXY_PORT
        try:
            server = UnifiedServer(port=PROXY_PORT)
            t = threading.Thread(target=server.start, daemon=True)
            t.start()
            proxy_server = server
        except Exception:
            pass
    return PROXY_PORT

def go_home():
    try:
        xbmcplugin.endOfDirectory(handle, succeeded=False, updateListing=True, cacheToDisc=False)
    except Exception:
        pass
    xbmc.executebuiltin('Container.Update(plugin://plugin.video.kingiptv/, replace)')

def set_episode_property(imdb_id, season, episode, resume_time=None):
    win = xbmcgui.Window(10000)
    data = {
        'imdb_id': imdb_id,
        'season': season,
        'episode': episode,
        'resume_time': resume_time
    }
    win.setProperty('kingiptv_episode', json.dumps(data))

def episode_item_params(imdb_id, season, episode_number, name, img, fanart, description,
                         serie_name, original_name, watched, year=''):
    name_full = '{}x{} - {}'.format(season, str(episode_number).zfill(2), name)
    return {
        'name': name_full,
        'description': description,
        'iconimage': img,
        'fanart': fanart,
        'imdbnumber': imdb_id,
        'season_num': str(season),
        'episode_num': str(episode_number),
        'serie_name': serie_name,
        'original_name': original_name,
        'episode_title': name,
        'year': year,
        'mediatype': 'episode',
        'playable': True,
        'playcount': 1 if int(episode_number) in watched else 0,
    }

def build_series_playlist(imdb_number, season_num, current_episode_num, serie_name, original_name, all_episodes):
    if not all_episodes or not isinstance(all_episodes, list):
        return
    if not isinstance(season_num, int) or not isinstance(current_episode_num, int):
        return
    playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
    playlist.clear()
    watched_set = get_db().get_watched_in_season(imdb_number, season_num)
    for episode_data in all_episodes:
        if not isinstance(episode_data, dict):
            continue
        ep_num = episode_data.get('episode')
        if not ep_num or not isinstance(ep_num, int):
            continue
        name = episode_data.get('episode_title', '')
        img = episode_data.get('thumbnail', '')
        fanart = episode_data.get('fanart', '')
        description = episode_data.get('description', '')
        if ep_num >= current_episode_num:
            params = episode_item_params(
                imdb_id=imdb_number,
                season=season_num,
                episode_number=ep_num,
                name=name,
                img=img,
                fanart=fanart,
                description=description,
                serie_name=serie_name,
                original_name=original_name,
                watched=watched_set,
            )
            exclude_from_url = {'playcount', 'name', 'description', 'iconimage', 'fanart', 'episode_title'}
            url_params = {k: v for k, v in params.items() if k not in exclude_from_url}
            plugin_url = 'plugin://plugin.video.kingiptv/play_resolve_series/{}'.format(urlencode(url_params))
            display_label = name if name else '{}x{}'.format(season_num, str(ep_num).zfill(2))
            list_item = xbmcgui.ListItem(display_label)
            list_item.setArt({'thumb': img, 'icon': img, 'fanart': fanart or img})
            info_tag = list_item.getVideoInfoTag()
            info_tag.setTitle(name)
            info_tag.setTvShowTitle(serie_name)
            info_tag.setPlot(description)
            info_tag.setMediaType('episode')
            info_tag.setSeason(season_num)
            info_tag.setEpisode(ep_num)
            info_tag.setPlaycount(1 if ep_num in watched_set else 0)
            playlist.add(url=plugin_url, listitem=list_item)

def index_iptv():
    active = list_manager.get_active_list()
    addMenuItem({'name': TITULO, 'description': ''}, destiny='')
    if active:
        addMenuItem({'name': getString(32000), 'description': ''}, destiny='/live_categories')
        addMenuItem({'name': getString(32001), 'description': ''}, destiny='/channels_pluto')
        addMenuItem({'name': getString(32002), 'description': ''}, destiny='/radios')
        addMenuItem({'name': getString(32003), 'description': ''}, destiny='/imdb_movies')
        addMenuItem({'name': getString(32004), 'description': ''}, destiny='/imdb_series')
        addMenuItem({'name': getString(32041), 'description': ''}, destiny='/select_list')
        addMenuItem({'name': getString(32005)}, destiny='/settings')
    else:
        addMenuItem({'name': getString(32001), 'description': ''}, destiny='/channels_pluto')
        addMenuItem({'name': getString(32002), 'description': ''}, destiny='/radios')
        addMenuItem({'name': getString(32040), 'description': ''}, destiny='/select_list')
        addMenuItem({'name': getString(32005)}, destiny='/settings')
    end()
    setview('WideList')

def prompt_select_list():
    iptv = xtream.parselist(API_CHANNELS)
    if not iptv:
        notify(getString(32013))
        return
    active = list_manager.get_active_list()
    labels = []
    preselect = -1
    for n, (dns, username, password) in enumerate(iptv):
        label = 'LISTA {0}'.format(n + 1)
        labels.append(label)
        if active and dns == active.get('dns') and str(username) == str(active.get('username')) \
                and str(password) == str(active.get('password')):
            preselect = n
    heading = getString(32041) if active else getString(32040)
    dialog = xbmcgui.Dialog()
    try:
        choice = dialog.select(heading, labels, preselect=preselect)
    except TypeError:
        choice = dialog.select(heading, labels)
    if choice is None or choice < 0:
        return
    dns, username, password = iptv[choice]
    label = labels[choice]
    list_manager.set_active_list(dns, username, password, label)
    notify(getString(32042))
    xtream.refresh_vod_catalogs_background(dns, username, password)

@route('/')
def index():
    if not list_manager.get_active_list():
        prompt_select_list()
    index_iptv()

@route('/settings')
def settings():
    xbmcaddon.Addon().openSettings()

@route('/select_list')
def select_list():
    prompt_select_list()
    go_home()

@route('/live_categories')
def live_categories():
    active = list_manager.get_active_list()
    if not active:
        notify(getString(32044))
        return
    dns = active['dns']
    username = active['username']
    password = active['password']
    xtream._ensure_epg_background(dns, username, password)
    cat = xtream.API(dns, username, password).channels_category()
    if cat:
        for i in cat:
            name, url = i
            name = name.upper()
            addMenuItem({'name': name, 'description': '', 'dns': dns, 'username': str(username), 'password': str(password), 'url': url}, destiny='/open_channels')
        end()
        setview('WideList')
    else:
        url_problem = '{0}/get.php?username={1}&password={2}\n'.format(dns, username, password)
        open_file = lambda filename, mode: open(filename, mode, encoding='utf-8') if six.PY3 else io.open(filename, mode, encoding='utf-8')
        if exists(IPTV_PROBLEM_LOG):
            check = False
            with open(IPTV_PROBLEM_LOG, "r") as arquivo:
                if url_problem in arquivo.read():
                    check = True
        else:
            check = False
        with open_file(IPTV_PROBLEM_LOG, "a") as arquivo:
            if not check:
                arquivo.write(url_problem)
        notify(getString(32014))

@route('/open_channels')
def open_channels(param):
    dns = param['dns']
    username = param['username']
    password = param['password']
    url = param['url']
    open_ = xtream.API(dns, username, password).channels_open(url)
    if open_:
        setcontent('videos')
        for i in open_:
            name, link, thumb, desc = i
            addMenuItem({'name': name, 'description': desc, 'iconimage': thumb, 'url': link, 'playable': 'true'}, destiny='/play_iptv')
        end()
        setview('WideList')
    else:
        notify(getString(32015))

@route('/play_iptv')
def play_iptv(param):
    name = param.get('name', getString(32029))
    description = param.get('description', '')
    iconimage = param.get('iconimage', '')
    url = param.get('url', '')
    proxy_url = 'http://127.0.0.1:{}/?url={}'.format(start_proxy_if_needed(), quote_plus(url))
    play_item = xbmcgui.ListItem(path=proxy_url)
    play_item.setContentLookup(False)
    play_item.setArt({"icon": iconimage or "DefaultVideo.png", "thumb": iconimage or "DefaultVideo.png"})
    play_item.setMimeType("application/vnd.apple.mpegurl")
    info_tag = play_item.getVideoInfoTag()
    info_tag.setTitle(name)
    info_tag.setPlot(description)
    info_tag.setMediaType('video')
    xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, play_item)

@route('/channels_pluto')
def channels_pluto():
    channels = pluto.playlist_pluto()
    if channels:
        setcontent('videos')
        for name, desc, thumb, url in channels:
            addMenuItem({'name': name, 'description': desc, 'iconimage': thumb, 'url': url, 'playable': 'true'}, destiny='/play_pluto')
        end()
        setview('List')
    else:
        notify(getString(32018))

@route('/play_pluto')
def play_pluto(param):
    url = param.get('url', '')
    name = param.get('name', '')
    iconimage = param.get('iconimage', '')
    desc = param.get('description', '')
    if not url:
        notify(getString(32016))
        return
    helper = inputstreamhelper.Helper('hls')
    if not helper.check_inputstream():
        return
    headers = url.split('|')[1] if '|' in url else ''
    url = url.split('|')[0] if '|' in url else url
    li = xbmcgui.ListItem(path=url)
    li.setProperty('inputstream', helper.inputstream_addon)
    li.setProperty('inputstream.adaptive.manifest_type', 'hls')
    li.setProperty('inputstream.adaptive.stream_headers', headers or 'User-Agent=Mozilla/5.0')
    li.setProperty('inputstream.adaptive.manifest_headers', headers or 'User-Agent=Mozilla/5.0')
    li.setMimeType('application/x-mpegURL')
    li.setProperty('inputstream.adaptive.live_delay', '0')
    li.setProperty('inputstream.adaptive.manifest_update_parameter', 'full')
    li.setArt({'icon': iconimage or 'DefaultVideo.png', 'thumb': iconimage or 'DefaultVideo.png'})
    tag = li.getVideoInfoTag()
    tag.setTitle(name)
    tag.setPlot(desc)
    tag.setMediaType('video')
    xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, li)

@route('/radios')
def radios():
    radios_ = tunein.radios_list(API_RADIOS)
    if radios_:
        for i in radios_:
            name, url = i
            addMenuItem({'name': name, 'description': '', 'url': url}, destiny='/play_radio')
        end()
        setview('List')

@route('/play_radio')
def play_radio(param):
    name = param.get('name', '')
    url = param.get('url', '')
    if url:
        play_item = xbmcgui.ListItem(path=url)
        play_item.setContentLookup(False)
        play_item.setArt({"icon": "DefaultAudio.png", "thumb": "DefaultAudio.png"})
        info_tag = play_item.getVideoInfoTag()
        info_tag.setTitle(name)
        info_tag.setMediaType('song')
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, play_item)

@route('/imdb_movies')
def imdb_movies():
    addMenuItem({'name': getString(32006), 'description': ''}, destiny='/find_movies')
    addMenuItem({'name': getString(32007), 'description': ''}, destiny='/imdb_movies_250')
    addMenuItem({'name': getString(32008), 'description': ''}, destiny='/imdb_movies_popular')
    end()
    setview('List')

@route('/imdb_series')
def imdb_series():
    addMenuItem({'name': getString(32009), 'description': ''}, destiny='/find_series')
    addMenuItem({'name': getString(32010), 'description': ''}, destiny='/imdb_series_250')
    addMenuItem({'name': getString(32011), 'description': ''}, destiny='/imdb_series_popular')
    end()
    setview('List')

@route('/find_movies')
def find_movies():
    keyboard = xbmc.Keyboard('', getString(32027))
    keyboard.doModal()
    if keyboard.isConfirmed():
        query = keyboard.getText()
        if query:
            results = imdb.IMDBScraper().search_movies(query)
            if results:
                setcontent('movies')
                for movie_name, image, url, description, imdb_id, original_name, year in results:
                    addMenuItem({
                        'name': '{} ({})'.format(movie_name, year) if year and year != '0' else movie_name,
                        'description': description,
                        'iconimage': image,
                        'fanart': image,
                        'url': '',
                        'imdbnumber': imdb_id,
                        'movie_name': movie_name,
                        'original_name': original_name,
                        'year': year,
                        'playable': True
                    }, destiny='/play_resolve_movies')
                end()
                setview('List')

@route('/find_series')
def find_series():
    keyboard = xbmc.Keyboard('', getString(32028))
    keyboard.doModal()
    if keyboard.isConfirmed():
        query = keyboard.getText()
        if query:
            results = imdb.IMDBScraper().search_series(query)
            if results:
                setcontent('tvshows')
                for serie_name, image, url, description, imdb_id, original_name, year in results:
                    addMenuItem({
                        'name': '{} ({})'.format(serie_name, year) if year else serie_name,
                        'description': description,
                        'iconimage': image,
                        'url': url,
                        'imdbnumber': imdb_id,
                        'serie_name': serie_name,
                        'original_name': original_name,
                        'year': year,
                    }, destiny='/open_imdb_seasons')
                end()
                setview('List')

@route('/imdb_movies_250')
def movies_250(param=None):
    page = int(param.get('page', 1)) if param else 1
    per_page = 50
    start = (page - 1) * per_page
    end_ = start + per_page
    all_items = imdb.IMDBScraper().movies_250()
    itens = all_items[start:end_]
    if itens:
        setcontent('movies')
        for movie_name, image, url, description, imdb_id, original_name, year in itens:
            addMenuItem({
                'name': '{} ({})'.format(movie_name, year) if year else movie_name,
                'description': description,
                'iconimage': image,
                'fanart': image,
                'url': '',
                'imdbnumber': imdb_id,
                'movie_name': movie_name,
                'original_name': original_name,
                'year': year,
                'playable': True
            }, destiny='/play_resolve_movies')
        if end_ < len(all_items):
            addMenuItem({'name': getString(32012), 'page': page + 1}, destiny='/imdb_movies_250')
        end()
        setview('List')

@route('/imdb_series_250')
def series_250(param=None):
    page = int(param.get('page', 1)) if param else 1
    per_page = 50
    start = (page - 1) * per_page
    end_ = start + per_page
    all_items = imdb.IMDBScraper().series_250()
    itens = all_items[start:end_]
    if itens:
        setcontent('tvshows')
        for serie_name, image, url, description, imdb_id, original_name, year in itens:
            addMenuItem({
                'name': '{} ({})'.format(serie_name, year) if year else serie_name,
                'description': description,
                'iconimage': image,
                'url': url,
                'imdbnumber': imdb_id,
                'serie_name': serie_name,
                'original_name': original_name,
                'year': year
            }, destiny='/open_imdb_seasons')
        if end_ < len(all_items):
            addMenuItem({'name': getString(32012), 'page': page + 1}, destiny='/imdb_series_250')
        end()
        setview('List')

@route('/imdb_movies_popular')
def movies_popular(param=None):
    page = int(param.get('page', 1)) if param else 1
    per_page = 50
    start = (page - 1) * per_page
    end_ = start + per_page
    all_items = imdb.IMDBScraper().movies_popular()
    itens = all_items[start:end_]
    if itens:
        setcontent('movies')
        for movie_name, image, url, description, imdb_id, original_name, year in itens:
            addMenuItem({
                'name': '{} ({})'.format(movie_name, year) if year else movie_name,
                'description': description,
                'iconimage': image,
                'fanart': image,
                'url': '',
                'imdbnumber': imdb_id,
                'movie_name': movie_name,
                'original_name': original_name,
                'year': year,
                'playable': True
            }, destiny='/play_resolve_movies')
        if end_ < len(all_items):
            addMenuItem({'name': getString(32012), 'page': page + 1}, destiny='/imdb_movies_popular')
        end()
        setview('List')

@route('/imdb_series_popular')
def series_popular(param=None):
    page = int(param.get('page', 1)) if param else 1
    per_page = 50
    start = (page - 1) * per_page
    end_ = start + per_page
    all_items = imdb.IMDBScraper().series_popular()
    itens = all_items[start:end_]
    if itens:
        setcontent('tvshows')
        for serie_name, image, url, description, imdb_id, original_name, year in itens:
            addMenuItem({
                'name': '{} ({})'.format(serie_name, year) if year else serie_name,
                'description': description,
                'iconimage': image,
                'url': url,
                'imdbnumber': imdb_id,
                'serie_name': serie_name,
                'original_name': original_name,
                'year': year
            }, destiny='/open_imdb_seasons')
        if end_ < len(all_items):
            addMenuItem({'name': getString(32012), 'page': page + 1}, destiny='/imdb_series_popular')
        end()
        setview('List')

@route('/open_imdb_seasons')
def open_imdb_seasons(param):
    serie_icon = param.get('iconimage', '')
    serie_name = param.get('serie_name', param.get('name', ''))
    original_name = param.get('original_name', '')
    url = param.get('url', '')
    imdb_id = param.get('imdbnumber', '')
    year = param.get('year', '')
    itens = imdb.IMDBScraper().imdb_seasons(url)
    if itens:
        setcontent('tvshows')
        for season_number, name, url_season in itens:
            addMenuItem({
                'name': name,
                'description': '',
                'iconimage': serie_icon,
                'url': url_season,
                'imdbnumber': imdb_id,
                'season': season_number,
                'serie_name': serie_name,
                'original_name': original_name,
                'year': year
            }, destiny='/open_imdb_episodes')
        end()
        setview('List')

@route('/open_imdb_episodes')
def open_imdb_episodes(param):
    serie_icon = param.get('iconimage', '')
    serie_name = param.get('serie_name', '')
    original_name = param.get('original_name', '')
    url = param.get('url', '')
    imdb_id = param.get('imdbnumber', '')
    season = param.get('season', '')
    year = param.get('year', '')
    itens = imdb.IMDBScraper().imdb_episodes(url)
    if itens:
        db = get_db()
        db.save_season_episodes(
            imdb_id=imdb_id,
            season=int(season),
            serie_name=serie_name,
            original_name=original_name,
            episodes_data=itens
        )
        watched_set = db.get_watched_in_season(imdb_id, int(season))
        setcontent('episodes')
        for episode_number, name, img, fanart, description in itens:
            params = episode_item_params(
                imdb_id=imdb_id,
                season=season,
                episode_number=episode_number,
                name=name,
                img=img,
                fanart=fanart,
                description=description,
                serie_name=serie_name,
                original_name=original_name,
                watched=watched_set,
                year=year,
            )
            addMenuItem(
                params,
                destiny='/play_resolve_series',
                exclude_from_url=['name', 'description', 'iconimage', 'fanart', 'episode_title'],
            )
        end()
        setview('List')

def find_movie_in_list(active, movie_name, original_name, year):
    dns = active['dns']
    username = active['username']
    password = active['password']
    log('[KINGIPTV][MATCH] filme pedido: nome={!r} original={!r} ano={!r}'.format(
        movie_name, original_name, year
    ))
    catalog = xtream.get_movies_catalog(dns, username, password)
    if not catalog:
        log('[KINGIPTV][MATCH] catalogo de filmes vazio/indisponivel para {}'.format(dns))
        return None
    log('[KINGIPTV][MATCH] catalogo de filmes carregado: {} itens'.format(len(catalog)))
    titles = [movie_name, original_name]
    match, score = matcher.best_title_match(
        catalog, 'name', titles, year=year, min_score=VOD_MATCH_MIN_SCORE, debug_log=log
    )
    if not match:
        log('[KINGIPTV][MATCH] NENHUM match aceito para {!r} (score={:.3f})'.format(movie_name, score))
        return None
    stream_url = api_movie_url = xtream.API(dns, username, password).movie_play_url(
        match['stream_id'], match.get('extension')
    )
    log('[KINGIPTV][MATCH] match aceito: catalogo={!r} stream_id={} url={}'.format(
        match.get('name'), match.get('stream_id'), redact_url_for_log(stream_url)
    ))
    return stream_url

def find_series_episode_in_list(active, serie_name, original_name, season_num, episode_num, year=None):
    dns = active['dns']
    username = active['username']
    password = active['password']
    log('[KINGIPTV][MATCH] serie pedida: nome={!r} original={!r} ano={!r} temporada={} episodio={}'.format(
        serie_name, original_name, year, season_num, episode_num
    ))
    catalog = xtream.get_series_catalog(dns, username, password)
    if not catalog:
        log('[KINGIPTV][MATCH] catalogo de series vazio/indisponivel para {}'.format(dns))
        return None
    log('[KINGIPTV][MATCH] catalogo de series carregado: {} itens'.format(len(catalog)))
    titles = [serie_name, original_name]
    match, score = matcher.best_title_match(
        catalog, 'name', titles, year=year, min_score=VOD_MATCH_MIN_SCORE, debug_log=log
    )
    if not match:
        log('[KINGIPTV][MATCH] NENHUM match aceito para {!r} (score={:.3f})'.format(serie_name, score))
        return None
    api = xtream.API(dns, username, password)
    stream_url = api.get_episode_stream(match['series_id'], season_num, episode_num)
    log('[KINGIPTV][MATCH] match aceito: catalogo={!r} series_id={} T{}E{} url={}'.format(
        match.get('name'), match.get('series_id'), season_num, episode_num, redact_url_for_log(stream_url)
    ))
    if not stream_url:
        log('[KINGIPTV][MATCH] serie casou mas episodio T{}E{} nao existe no provedor'.format(
            season_num, episode_num
        ))
    return stream_url

def redact_url_for_log(url):
    if not url:
        return url
    try:
        return re.sub(r'/([^/]+)/([^/]+)/(\d+\.[a-zA-Z0-9]+)$', r'/***/***/\3', url)
    except Exception:
        return url

def build_play_item(stream, sub, title, iconimage, fanart, headers=None):
    log('[KINGIPTV][PLAY] montando item de reproducao para {!r} stream={}'.format(
        title, redact_url_for_log(stream)
    ))
    stream_lower = stream.lower()
    is_hls = '.m3u8' in stream_lower or 'hls' in stream_lower
    is_mpd = '.mpd' in stream_lower
    if is_hls or is_mpd:
        if headers:
            header_str = '&'.join(
                '{}={}'.format(k, quote_plus(str(v)))
                for k, v in headers.items()
            )
            path = '{}|{}'.format(stream, header_str)
        else:
            path = stream
    else:
        proxy_port = start_proxy_if_needed()
        path = 'http://127.0.0.1:{}/?url={}'.format(proxy_port, quote_plus(stream))
    log('[KINGIPTV][PLAY] path final enviado ao Kodi: {}'.format(redact_url_for_log(path)))
    play_item = xbmcgui.ListItem(label=title, path=path)
    play_item.setContentLookup(False)
    if is_hls:
        play_item.setMimeType('application/x-mpegURL')
    elif is_mpd:
        play_item.setMimeType('application/dash+xml')
    elif stream_lower.endswith(('.mp4', '.mkv', '.avi', '.mov', '.webm', '.ts')):
        play_item.setMimeType('video/mp4')
    play_item.setArt({'thumb': iconimage, 'icon': iconimage, 'fanart': fanart or iconimage})
    if sub:
        play_item.setSubtitles([sub])
    return play_item

@route('/play_resolve_movies')
def play_resolve_movies(param):
    movie_name = param.get('movie_name', param.get('name', ''))
    iconimage = param.get('iconimage', '')
    fanart = param.get('fanart', '')
    imdb_number = param.get('imdbnumber', '')
    description = param.get('description', '')
    year = param.get('year', '')
    original_name = param.get('original_name', '')
    active = list_manager.get_active_list()
    if not active:
        notify(getString(32044))
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
        return
    loading_manager.show()
    try:
        stream = find_movie_in_list(active, movie_name, original_name, year)
        if not stream:
            loading_manager.force_close()
            notify(getString(32043))
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            return
        loading_manager.set_phase2()
        play_item = build_play_item(stream, None, movie_name, iconimage, fanart, headers=XTREAM_PLAYER_HEADERS)
        info_tag = play_item.getVideoInfoTag()
        info_tag.setTitle(movie_name)
        info_tag.setPlot(description)
        info_tag.setIMDBNumber(imdb_number)
        info_tag.setMediaType('movie')
        info_tag.setOriginalTitle(original_name)
        if year:
            info_tag.setYear(int(year))
        set_episode_property(imdb_number, 0, 0)
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, play_item)
        loading_manager.close()
    except Exception:
        loading_manager.force_close()
        notify(getString(32016))
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())

@route('/play_resolve_series')
def play_resolve_series(param):
    serie_name = param.get('serie_name', '')
    original_name = param.get('original_name', '')
    season = param.get('season_num', '')
    episode = param.get('episode_num', '')
    imdb_number = param.get('imdbnumber', '')
    year = param.get('year', '')
    if not episode or not season:
        notify(getString(32021))
        return
    if not str(episode).isdigit() or not str(season).isdigit():
        notify(getString(32022))
        return
    current_episode_num = int(episode)
    season_num = int(season)
    if current_episode_num <= 0 or season_num <= 0:
        notify(getString(32022))
        return
    active = list_manager.get_active_list()
    if not active:
        notify(getString(32044))
        return
    db = get_db()
    episode_meta = db.get_episode_metadata(imdb_number, season_num, current_episode_num) or {}
    episode_title = episode_meta.get('episode_title', '')
    iconimage = episode_meta.get('thumbnail', '')
    fanart = episode_meta.get('fanart', '')
    description = episode_meta.get('description', '')
    resume_data = db.get_resume_time(imdb_number, season_num, current_episode_num)
    resume_time = None
    if resume_data and resume_data[0] > 0:
        if ask_resume(resume_data[0]):
            resume_time = resume_data[0]
        else:
            db.clear_resume_time(imdb_number, season_num, current_episode_num)
    loading_manager.show()
    try:
        stream = find_series_episode_in_list(active, serie_name, original_name, season_num, current_episode_num, year=year)
        if not stream:
            loading_manager.force_close()
            notify(getString(32043))
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            xbmc.PlayList(xbmc.PLAYLIST_VIDEO).clear()
            return
        loading_manager.set_phase2()
        play_item = build_play_item(stream, None, episode_title, iconimage, fanart, headers=XTREAM_PLAYER_HEADERS)
        info_tag = play_item.getVideoInfoTag()
        info_tag.setTitle(episode_title)
        info_tag.setTvShowTitle(serie_name)
        info_tag.setOriginalTitle(original_name)
        info_tag.setPlot(description)
        info_tag.setIMDBNumber(imdb_number)
        info_tag.setMediaType('episode')
        info_tag.setSeason(season_num)
        info_tag.setEpisode(current_episode_num)
        set_episode_property(imdb_number, season_num, current_episode_num, resume_time)
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, play_item)
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        if playlist.size() <= 1:
            all_episodes = db.get_season_episodes(imdb_number, season_num)
            if all_episodes:
                build_series_playlist(
                    imdb_number=imdb_number,
                    season_num=season_num,
                    current_episode_num=current_episode_num,
                    serie_name=serie_name,
                    original_name=original_name,
                    all_episodes=all_episodes,
                )
        loading_manager.close()
    except Exception:
        loading_manager.force_close()
        notify(getString(32016))
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
        xbmc.PlayList(xbmc.PLAYLIST_VIDEO).clear()
