# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import threading
from urllib.parse import parse_qsl, quote_plus
from lib.helper import *
from lib import list_manager
from lib import list_dialog
from lib import loading_nav
from lib import xtream, tunein, pluto, imdb, api_vod
from lib.epg_dialog import open_skin, open_list_playback
from lib.proxy import UnifiedServer, PROXY_PORT_POOL, get_active_port, is_port_free, get_preferred_port
from lib.db_manager import get_db
from lib.loading_window import loading_manager

ADDON = xbmcaddon.Addon()
ADDON_PATH = translate(ADDON.getAddonInfo('path'))
ADDON_ICON = os.path.join(ADDON_PATH, 'icon.png')
ADDON_FANART = os.path.join(ADDON_PATH, 'resources', 'skins', 'Default', 'media', 'fanart.jpg')

API_CHANNELS = '\x68\x74\x74\x70\x73\x3a\x2f\x2f\x64\x6f\x63\x73\x2e\x67\x6f\x6f\x67\x6c\x65\x2e\x63\x6f\x6d\x2f\x75\x63\x3f\x65\x78\x70\x6f\x72\x74\x3d\x64\x6f\x77\x6e\x6c\x6f\x61\x64\x26\x69\x64\x3d\x31\x67\x52\x53\x61\x72\x30\x49\x79\x32\x6f\x47\x65\x70\x4c\x33\x4c\x6b\x4d\x74\x43\x62\x77\x54\x7a\x67\x53\x67\x68\x41\x73\x77\x36'
API_RADIOS = '\x68\x74\x74\x70\x73\x3a\x2f\x2f\x67\x69\x73\x74\x2e\x67\x69\x74\x68\x75\x62\x75\x73\x65\x72\x63\x6f\x6e\x74\x65\x6e\x74\x2e\x63\x6f\x6d\x2f\x69\x63\x61\x72\x6f\x6b\x39\x39\x2f\x64\x65\x38\x38\x63\x33\x66\x30\x61\x34\x31\x39\x64\x32\x35\x34\x30\x33\x62\x31\x31\x30\x65\x33\x64\x31\x32\x38\x37\x31\x65\x31\x2f\x72\x61\x77\x2f\x62\x65\x33\x32\x64\x65\x32\x37\x65\x63\x33\x36\x34\x39\x36\x30\x34\x37\x66\x30\x61\x33\x35\x64\x63\x31\x38\x65\x62\x34\x34\x65\x66\x37\x39\x65\x38\x66\x63\x33\x2f\x72\x61\x64\x69\x6f\x73\x2e\x6a\x73\x6f\x6e'
WELCOME_DESC = (
    'Seja bem vindo ao kingIPTV ao melhor addon com maior variedade de '
    'conteúdo desde filmes e séries, tv ao vivo e pluto tv sua melhor '
    'fonte de entretenimento pra curtir com sua família.'
)

profile = xbmcvfs.translatePath('special://profile/addon_data/plugin.video.kingiptv')
if not exists(profile):
    try:
        os.makedirs(profile)
    except OSError:
        pass
IPTV_PROBLEM_LOG = translate(os.path.join(profile, 'iptv_problems_log.txt'))

proxy_server = None
proxy_lock = threading.Lock()


def start_proxy_if_needed():
    global proxy_server
    if proxy_server is not None and proxy_server.port:
        return proxy_server.port
    with proxy_lock:
        if proxy_server is not None and proxy_server.port:
            return proxy_server.port
        preferred = get_preferred_port()
        check_order = PROXY_PORT_POOL
        if preferred in PROXY_PORT_POOL:
            check_order = [preferred] + [p for p in PROXY_PORT_POOL if p != preferred]
        for candidate in check_order:
            if not is_port_free(candidate):
                return candidate
        try:
            server = UnifiedServer()
            t = threading.Thread(target=server.start, daemon=True)
            t.start()
            for _ in range(50):
                if server.port:
                    break
                time.sleep(0.05)
            proxy_server = server
            if server.port:
                return server.port
        except Exception:
            pass
    return get_active_port()


def go_home():
    xbmc.executebuiltin('Container.Update(plugin://plugin.video.kingiptv/, replace)')


def set_episode_property(imdb_id, season, episode, resume_time=None):
    win = xbmcgui.Window(10000)
    win.setProperty('kingiptv_episode', json.dumps({
        'imdb_id': imdb_id, 'season': season, 'episode': episode, 'resume_time': resume_time
    }))


def episode_item_params(imdb_id, season, episode_number, name, img, fanart, description,
                         serie_name, original_name, watched, year=''):
    name_full = '{}x{} - {}'.format(season, str(episode_number).zfill(2), name)
    return {
        'name': name_full, 'description': description, 'iconimage': img, 'fanart': fanart,
        'imdbnumber': imdb_id, 'season_num': str(season), 'episode_num': str(episode_number),
        'serie_name': serie_name, 'original_name': original_name, 'episode_title': name,
        'year': year, 'mediatype': 'episode', 'playable': True,
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
                imdb_id=imdb_number, season=season_num, episode_number=ep_num, name=name,
                img=img, fanart=fanart, description=description, serie_name=serie_name,
                original_name=original_name, watched=watched_set,
            )
            exclude_from_url = {'playcount', 'name', 'description', 'iconimage', 'fanart', 'episode_title'}
            url_params = {k: v for k, v in params.items() if k not in exclude_from_url}
            url_params['via_playlist'] = '1'
            plugin_url = 'plugin://plugin.video.kingiptv/?action=play_resolve_series&{}'.format(urlencode(url_params))
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
            try:
                info_tag.setResumePoint(0, 0)
            except Exception:
                pass
            playlist.add(url=plugin_url, listitem=list_item)


def redact_url_for_log(url):
    if not url:
        return url
    try:
        return re.sub(r'/([^/]+)/([^/]+)/(\d+\.[a-zA-Z0-9]+)$', r'/***/***/\3', url)
    except Exception:
        return url


def build_play_item(stream, sub, title, iconimage, fanart, headers=None):
    stream_lower = stream.lower()
    is_hls = '.m3u8' in stream_lower or 'hls' in stream_lower
    is_mpd = '.mpd' in stream_lower
    if is_hls or is_mpd:
        if headers:
            header_str = '&'.join('{}={}'.format(k, quote_plus(str(v))) for k, v in headers.items())
            path = '{}|{}'.format(stream, header_str)
        else:
            path = stream
    else:
        proxy_port = start_proxy_if_needed()
        path = 'http://127.0.0.1:{}/?url={}'.format(proxy_port, quote_plus(stream))
    play_item = xbmcgui.ListItem(label=title, path=path)
    play_item.setContentLookup(False)
    try:
        play_item.getVideoInfoTag().setResumePoint(0, 0)
    except Exception:
        pass
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


def resolve_movie_stream(imdb_number):
    return api_vod.resolve_movie_stream(imdb_number)


def resolve_series_episode_stream(imdb_number, season_num, episode_num):
    return api_vod.resolve_episode_stream(imdb_number, season_num, episode_num)


def open_settings():
    xbmcaddon.Addon().openSettings()


def _account_has_known_problem(dns, username, password):
    return xtream.is_account_marked_offline(dns, username, password)


def _kick_epg_background(dns, username, password):
    xtream.clear_account_offline(dns, username, password)

    def worker():
        try:
            xtream.ensure_epg_background(dns, username, password)
        except Exception:
            pass
    threading.Thread(target=worker, daemon=True).start()


def prompt_select_list():
    from lib import loading_nav
    iptv = loading_nav.run_with_loading(
        lambda: xtream.parselist(API_CHANNELS), message='Aguarde...', fanart=ADDON_FANART
    )

    if not iptv:
        notify('Nenhuma lista IPTV')
        return

    active = list_manager.get_active_list()
    items = []
    preselect = 0
    for n, (dns, username, password) in enumerate(iptv):
        is_active = bool(active and dns == active.get('dns')
                          and str(username) == str(active.get('username'))
                          and str(password) == str(active.get('password')))
        if is_active:
            preselect = n
        label = 'LISTA {0}'.format(n + 1)
        display_label = label
        if is_active:
            display_label += ' (ATIVA)'
        if _account_has_known_problem(dns, username, password):
            display_label += ' (LISTA INDISPONÍVEL)'
        items.append({
            'label': display_label,
            'description': WELCOME_DESC,
            'icon': ADDON_ICON,
            'payload': {'dns': dns, 'username': username, 'password': password, 'label': label},
        })

    heading = 'TROCAR LISTA' if active else 'ESCOLHER LISTA'
    idx, item = list_dialog.open_list_menu(heading, items, fanart=ADDON_FANART, start_pos=preselect)
    if item is None:
        notify('Nenhuma lista selecionada')
        return
    payload = item['payload']
    list_manager.set_active_list(payload['dns'], payload['username'], payload['password'], payload['label'])
    _kick_epg_background(payload['dns'], payload['username'], payload['password'])
    notify('Lista IPTV definida com sucesso')


def select_list():
    prompt_select_list()


def _log_offline_list(dns, username, password):
    xtream.mark_account_offline(dns, username, password)


def build_iptv_play_item(name, description, iconimage, url):
    proxy_url = 'http://127.0.0.1:{}/?url={}'.format(start_proxy_if_needed(), quote_plus(url))
    play_item = xbmcgui.ListItem(path=proxy_url)
    play_item.setContentLookup(False)
    play_item.setProperty('IsPlayable', 'true')
    play_item.setProperty('IsLive', 'true')
    play_item.setProperty('inputstream', 'inputstream.adaptive')
    play_item.setProperty('inputstream.adaptive.manifest_type', 'hls')
    play_item.setProperty('inputstream.adaptive.live_delay', '60')
    play_item.setArt({'icon': iconimage or 'DefaultVideo.png', 'thumb': iconimage or 'DefaultVideo.png'})
    play_item.setMimeType('application/x-mpegURL')
    info_tag = play_item.getVideoInfoTag()
    info_tag.setTitle(name)
    info_tag.setPlot(description)
    info_tag.setMediaType('video')
    return proxy_url, play_item


def show_channels_epg(param):
    dns, username, password, url = param['dns'], param['username'], param['password'], param['url']
    channels = loading_nav.run_with_loading(
        lambda: xtream.API(dns, username, password).channels_open_epg(url), message='Aguarde...'
    )
    if not channels:
        notify('Opção indisponível')
        return

    def build(channel):
        return build_iptv_play_item(channel['name'], '', channel.get('icon', ''), channel.get('url', ''))

    open_skin(header='TV AO VIVO', channels=channels, build_listitem=build, fanart=ADDON_FANART)


def open_channels(param):
    show_channels_epg(param)


def live_categories():
    active = list_manager.get_active_list()
    if not active:
        notify('Nenhuma lista IPTV configurada. Escolha uma primeiro.')
        return
    dns, username, password = active['dns'], active['username'], active['password']

    cat = loading_nav.run_with_loading(
        lambda: xtream.API(dns, username, password).channels_category(), message='Aguarde...'
    )
    if not cat:
        _log_offline_list(dns, username, password)
        notify('Lista Offline')
        return

    xtream.clear_account_offline(dns, username, password)
    xtream.ensure_epg_background(dns, username, password)

    items = []
    for name, url in cat:
        items.append({
            'label': name.upper(),
            'description': 'Categoria de canais ao vivo da sua lista IPTV.',
            'icon': ADDON_ICON,
            'query': {'dns': dns, 'username': str(username), 'password': str(password), 'url': url},
        })

    pos = 0
    while True:
        idx, item = list_dialog.open_list_menu('TV AO VIVO - CATEGORIAS', items, fanart=ADDON_FANART, start_pos=pos)
        if item is None:
            return
        pos = idx
        show_channels_epg(item['query'])


def build_pluto_play_item(name, description, iconimage, url):
    if not url:
        return None, None
    clean_url = url.split('|')[0] if '|' in url else url
    header_str = url.split('|', 1)[1] if '|' in url else 'User-Agent={}'.format(quote_plus(pluto.USER_AGENT))
    li = xbmcgui.ListItem(path=clean_url)
    li.setProperty('IsPlayable', 'true')
    li.setProperty('IsLive', 'true')
    li.setProperty('inputstream', 'inputstream.adaptive')
    li.setProperty('inputstream.adaptive.manifest_type', 'hls')
    li.setProperty('inputstream.adaptive.live_delay', '25')
    li.setProperty('inputstream.adaptive.stream_headers', header_str)
    li.setProperty('inputstream.adaptive.manifest_headers', header_str)
    li.setContentLookup(False)
    li.setMimeType('application/x-mpegURL')
    li.setArt({'icon': iconimage or 'DefaultVideo.png', 'thumb': iconimage or 'DefaultVideo.png'})
    tag = li.getVideoInfoTag()
    tag.setTitle(name)
    tag.setPlot(description)
    tag.setMediaType('video')
    return clean_url, li


def channels_pluto():
    channels = loading_nav.run_with_loading(
        pluto.playlist_pluto_epg, message='Aguarde...'
    )
    if not channels:
        notify('Nenhum canal')
        return

    def build(channel):
        return build_pluto_play_item(channel['name'], '', channel.get('icon', ''), channel.get('url', ''))

    open_skin(header='PLUTO TV', channels=pluto.to_lazy_channels(channels), build_listitem=build, fanart=ADDON_FANART)


def build_radio_play_item(name, iconimage, url):
    if not url:
        return None, None
    li = xbmcgui.ListItem(path=url)
    li.setContentLookup(False)
    li.setProperty('IsPlayable', 'true')
    li.setArt({'icon': iconimage or 'DefaultAudio.png', 'thumb': iconimage or 'DefaultAudio.png'})
    info_tag = li.getVideoInfoTag()
    info_tag.setTitle(name)
    info_tag.setMediaType('song')
    return url, li


def radios():
    radios_ = loading_nav.run_with_loading(
        lambda: tunein.radios_list(API_RADIOS), message='Aguarde...'
    )
    if not radios_:
        notify('Nenhuma rádio disponível')
        return

    items = []
    for channel in radios_:
        items.append({
            'label': channel.get('name', ''),
            'icon': channel.get('icon', '') or ADDON_ICON,
            'secondary': 'Rádio ao vivo',
            'description': WELCOME_DESC,
            'url': channel.get('url', ''),
        })

    def build(entry):
        return build_radio_play_item(entry.get('label', ''), entry.get('icon', ''), entry.get('url', ''))

    open_list_playback(header='RÁDIOS', items=items, build_listitem=build, fanart=ADDON_FANART)


def _movie_entry(movie_name, image, url, description, imdb_id, original_name, year):
    label = '{} ({})'.format(movie_name, year) if year and year != '0' else movie_name
    return {
        'label': label,
        'year': year if year and year != '0' else '',
        'description': description or 'Sem sinopse disponível.',
        'poster': image,
        'payload': {
            'movie_name': movie_name, 'iconimage': image, 'fanart': image,
            'imdbnumber': imdb_id, 'description': description, 'year': year,
            'original_name': original_name,
        },
    }


def _series_entry(serie_name, image, url, description, imdb_id, original_name, year):
    label = '{} ({})'.format(serie_name, year) if year else serie_name
    return {
        'label': label,
        'year': year or '',
        'description': description or 'Sem sinopse disponível.',
        'poster': image,
        'payload': {
            'serie_name': serie_name, 'iconimage': image, 'url': url,
            'imdbnumber': imdb_id, 'original_name': original_name, 'year': year,
        },
    }


def _prepare_movie_play_item(movie_name, iconimage, fanart, imdb_number, description, year, original_name):
    try:
        stream = resolve_movie_stream(imdb_number)
        if not stream:
            notify('Stream Indisponível')
            return None

        play_item = build_play_item(stream, None, movie_name, iconimage, fanart, headers=api_vod.PLAYER_HEADERS)
        info_tag = play_item.getVideoInfoTag()
        info_tag.setTitle(movie_name)
        info_tag.setPlot(description)
        info_tag.setIMDBNumber(imdb_number)
        info_tag.setMediaType('movie')
        info_tag.setOriginalTitle(original_name)
        if year:
            try:
                info_tag.setYear(int(year))
            except (TypeError, ValueError):
                pass
        set_episode_property(imdb_number, 0, 0)
        return play_item
    except Exception:
        notify('Stream Indisponível')
        return None


def build_movie_play_url(payload):
    url_params = {
        'movie_name': payload.get('movie_name', ''),
        'iconimage': payload.get('iconimage', ''),
        'fanart': payload.get('fanart', '') or payload.get('iconimage', ''),
        'imdbnumber': payload.get('imdbnumber', ''),
        'description': payload.get('description', ''),
        'year': payload.get('year', ''),
        'original_name': payload.get('original_name', ''),
    }
    return 'plugin://plugin.video.kingiptv/?action=play_resolve_movies&{}'.format(urlencode(url_params))


def _play_movie_now(payload):
    movie_name = payload.get('movie_name', '')
    plugin_url = build_movie_play_url(payload)

    list_item = xbmcgui.ListItem(movie_name)
    list_item.setProperty('IsPlayable', 'true')
    iconimage = payload.get('iconimage', '')
    fanart = payload.get('fanart', '') or iconimage
    list_item.setArt({'thumb': iconimage, 'icon': iconimage, 'fanart': fanart})
    info_tag = list_item.getVideoInfoTag()
    info_tag.setTitle(movie_name)
    info_tag.setMediaType('movie')
    year = payload.get('year', '')
    if year:
        try:
            info_tag.setYear(int(year))
        except (TypeError, ValueError):
            pass

    ok = loading_nav.play_and_release(
        lambda player: player.play(plugin_url, list_item),
        message='Aguarde...',
    )
    if not ok:
        notify('Stream Indisponível')


def play_resolve_movies(param):
    loading_manager.start_busy_suppressor()
    try:
        play_item = _prepare_movie_play_item(
            movie_name=param.get('movie_name', param.get('name', '')), iconimage=param.get('iconimage', ''),
            fanart=param.get('fanart', ''), imdb_number=param.get('imdbnumber', ''),
            description=param.get('description', ''), year=param.get('year', ''),
            original_name=param.get('original_name', ''),
        )
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), bool(play_item), play_item or xbmcgui.ListItem())
    finally:
        loading_manager.stop_busy_suppressor()


def _show_movies_screen(header, all_movies):
    per_page = 50
    page = 1
    pos = 0
    while True:
        start = (page - 1) * per_page
        chunk = all_movies[start:start + per_page]
        if not chunk:
            if page == 1:
                notify('Nenhum resultado')
            return
        items = [_movie_entry(*m) for m in chunk]
        if start + per_page < len(all_movies):
            items.append({'label': 'PRÓXIMA PÁGINA', 'description': 'Ver mais resultados.', 'icon': ADDON_ICON, 'next_page': True})
        idx, item = list_dialog.open_list_menu(header, items, fanart=ADDON_FANART, start_pos=pos, playable=True)
        if item is None:
            return
        if item.get('next_page'):
            page += 1
            pos = 0
            continue
        pos = idx
        _play_movie_now(item['payload'])
        continue


def _show_series_screen(header, all_series):
    per_page = 50
    page = 1
    pos = 0
    while True:
        start = (page - 1) * per_page
        chunk = all_series[start:start + per_page]
        if not chunk:
            if page == 1:
                notify('Nenhum resultado')
            return
        items = [_series_entry(*s) for s in chunk]
        if start + per_page < len(all_series):
            items.append({'label': 'PRÓXIMA PÁGINA', 'description': 'Ver mais resultados.', 'icon': ADDON_ICON, 'next_page': True})
        idx, item = list_dialog.open_list_menu(header, items, fanart=ADDON_FANART, start_pos=pos)
        if item is None:
            return
        if item.get('next_page'):
            page += 1
            pos = 0
            continue
        pos = idx
        _show_seasons_screen(item['payload'])
        continue


def find_movies():
    keyboard = xbmc.Keyboard('', 'Digite o nome do filme')
    keyboard.doModal()
    query = keyboard.getText() if keyboard.isConfirmed() else ''
    results = []
    if query:
        results = loading_nav.run_with_loading(
            lambda: imdb.IMDBScraper().search_movies(query), message='Aguarde...'
        )
    if not results:
        if query:
            notify('Nenhum resultado')
        return
    _show_movies_screen('FILMES - "{}"'.format(query.upper()), results)


def find_series():
    keyboard = xbmc.Keyboard('', 'Digite o nome da série')
    keyboard.doModal()
    query = keyboard.getText() if keyboard.isConfirmed() else ''
    results = []
    if query:
        results = loading_nav.run_with_loading(
            lambda: imdb.IMDBScraper().search_series(query), message='Aguarde...'
        )
    if not results:
        if query:
            notify('Nenhum resultado')
        return
    _show_series_screen('SÉRIES - "{}"'.format(query.upper()), results)


def imdb_movies_250():
    all_items = loading_nav.run_with_loading(
        lambda: imdb.IMDBScraper().movies_250(), message='Aguarde...'
    )
    if not all_items:
        notify('Nenhum resultado')
        return
    _show_movies_screen('TOP 250 FILMES', all_items)


def imdb_series_250():
    all_items = loading_nav.run_with_loading(
        lambda: imdb.IMDBScraper().series_250(), message='Aguarde...'
    )
    if not all_items:
        notify('Nenhum resultado')
        return
    _show_series_screen('TOP 250 SÉRIES', all_items)


def imdb_movies_popular():
    all_items = loading_nav.run_with_loading(
        lambda: imdb.IMDBScraper().movies_popular(), message='Aguarde...'
    )
    if not all_items:
        notify('Nenhum resultado')
        return
    _show_movies_screen('FILMES POPULARES', all_items)


def imdb_series_popular():
    all_items = loading_nav.run_with_loading(
        lambda: imdb.IMDBScraper().series_popular(), message='Aguarde...'
    )
    if not all_items:
        notify('Nenhum resultado')
        return
    _show_series_screen('SÉRIES POPULARES', all_items)


def _show_seasons_screen(payload):
    serie_icon = payload.get('iconimage', '')
    serie_name = payload.get('serie_name', '')
    original_name = payload.get('original_name', '')
    url = payload.get('url', '')
    imdb_id = payload.get('imdbnumber', '')
    year = payload.get('year', '')

    itens = loading_nav.run_with_loading(
        lambda: imdb.IMDBScraper().imdb_seasons(url), message='Aguarde...'
    )
    if not itens:
        notify('Nenhuma temporada encontrada')
        return

    items = []
    for season_number, name, url_season in itens:
        items.append({
            'label': '{} Temporada'.format(season_number),
            'description': '{} Temporada de {}.'.format(season_number, serie_name),
            'poster': serie_icon,
            'year': year,
            'payload': {
                'serie_icon': serie_icon, 'serie_name': serie_name, 'original_name': original_name,
                'imdbnumber': imdb_id, 'season': season_number, 'url_season': url_season, 'year': year,
            },
        })

    pos = 0
    while True:
        idx, item = list_dialog.open_list_menu('{} - TEMPORADAS'.format(serie_name.upper()), items, fanart=ADDON_FANART, start_pos=pos)
        if item is None:
            return
        pos = idx
        _show_episodes_screen(item['payload'])


def _show_episodes_screen(payload):
    serie_icon = payload.get('serie_icon', '')
    serie_name = payload.get('serie_name', '')
    original_name = payload.get('original_name', '')
    url_season = payload.get('url_season', '')
    imdb_id = payload.get('imdbnumber', '')
    season = payload.get('season', '')
    year = payload.get('year', '')

    itens = loading_nav.run_with_loading(
        lambda: imdb.IMDBScraper().imdb_episodes(url_season), message='Aguarde...'
    )
    if not itens:
        notify('Nenhum episódio encontrado')
        return

    db = get_db()
    db.save_season_episodes(imdb_id=imdb_id, season=int(season), serie_name=serie_name,
                             original_name=original_name, episodes_data=itens)
    watched_set = db.get_watched_in_season(imdb_id, int(season))

    items = []
    for episode_number, name, img, fanart, description in itens:
        items.append({
            'label': '{}x{} - {}'.format(season, str(episode_number).zfill(2), name),
            'secondary': 'Assistido' if int(episode_number) in watched_set else '',
            'description': description or 'Sem sinopse disponível.',
            'poster': img or serie_icon,
            'year': year,
            'payload': {
                'serie_name': serie_name, 'original_name': original_name, 'imdbnumber': imdb_id,
                'season': season, 'episode': episode_number, 'episode_title': name,
                'iconimage': img or serie_icon, 'fanart': fanart, 'description': description, 'year': year,
            },
        })

    pos = 0
    while True:
        idx, item = list_dialog.open_list_menu('{} - T{}'.format(serie_name.upper(), season), items, fanart=ADDON_FANART, start_pos=pos, playable=True, content_kind='episodes')
        if item is None:
            return
        pos = idx
        _play_series_episode_now(item['payload'])


def _play_series_episode_now(payload):
    imdb_number = payload.get('imdbnumber', '')
    season = payload.get('season', '')
    episode = payload.get('episode', '')
    serie_name = payload.get('serie_name', '')
    original_name = payload.get('original_name', '')

    if not str(episode).isdigit() or not str(season).isdigit():
        notify('Erro: Número de episódio/temporada inválido')
        return
    season_num, episode_num = int(season), int(episode)

    db = get_db()
    all_episodes = db.get_season_episodes(imdb_number, season_num)
    if not all_episodes:
        all_episodes = [{
            'episode': episode_num,
            'episode_title': payload.get('episode_title', ''),
            'thumbnail': payload.get('iconimage', ''),
            'fanart': payload.get('fanart', ''),
            'description': payload.get('description', ''),
        }]
    build_series_playlist(
        imdb_number=imdb_number, season_num=season_num, current_episode_num=episode_num,
        serie_name=serie_name, original_name=original_name, all_episodes=all_episodes,
    )
    playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
    if playlist.size() == 0:
        notify('Stream Indisponível')
        return

    ok = loading_nav.play_and_release(
        lambda player: player.play(playlist),
        message='Aguarde...',
    )
    if not ok:
        notify('Stream Indisponível')


def open_imdb_seasons(param):
    _show_seasons_screen({
        'iconimage': param.get('iconimage', ''),
        'serie_name': param.get('serie_name', param.get('name', '')),
        'original_name': param.get('original_name', ''),
        'url': param.get('url', ''),
        'imdbnumber': param.get('imdbnumber', ''),
        'year': param.get('year', ''),
    })


def open_imdb_episodes(param):
    _show_episodes_screen({
        'serie_icon': param.get('iconimage', ''),
        'serie_name': param.get('serie_name', ''),
        'original_name': param.get('original_name', ''),
        'url_season': param.get('url', ''),
        'imdbnumber': param.get('imdbnumber', ''),
        'season': param.get('season', ''),
        'year': param.get('year', ''),
    })


def play_resolve_series(param):
    loading_manager.start_busy_suppressor()
    try:
        serie_name = param.get('serie_name', '')
        original_name = param.get('original_name', '')
        season = param.get('season_num', '')
        episode = param.get('episode_num', '')
        imdb_number = param.get('imdbnumber', '')
        year = param.get('year', '')
        via_playlist = param.get('via_playlist') == '1'

        if not episode or not season or not str(episode).isdigit() or not str(season).isdigit():
            notify('Erro: Número de episódio/temporada inválido')
            if not via_playlist:
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            return
        current_episode_num, season_num = int(episode), int(season)
        if current_episode_num <= 0 or season_num <= 0:
            notify('Erro: Número de episódio/temporada inválido')
            if not via_playlist:
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            return

        if not via_playlist:
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            db = get_db()
            all_episodes = db.get_season_episodes(imdb_number, season_num)
            if not all_episodes:
                all_episodes = [{
                    'episode': current_episode_num,
                    'episode_title': param.get('episode_title', ''),
                    'thumbnail': param.get('iconimage', ''),
                    'fanart': param.get('fanart', ''),
                    'description': param.get('description', ''),
                }]
            build_series_playlist(
                imdb_number=imdb_number, season_num=season_num, current_episode_num=current_episode_num,
                serie_name=serie_name, original_name=original_name, all_episodes=all_episodes,
            )
            playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
            if playlist.size() == 0:
                notify('Stream Indisponível')
                return
            xbmc.Player().play(playlist)
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
        try:
            stream = resolve_series_episode_stream(imdb_number, season_num, current_episode_num)
            if not stream:
                notify('Stream Indisponível')
                xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
                xbmc.PlayList(xbmc.PLAYLIST_VIDEO).clear()
                return
            play_item = build_play_item(stream, None, episode_title, iconimage, fanart, headers=api_vod.PLAYER_HEADERS)
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
        except Exception:
            notify('Stream Indisponível')
            xbmcplugin.setResolvedUrl(int(sys.argv[1]), False, xbmcgui.ListItem())
            xbmc.PlayList(xbmc.PLAYLIST_VIDEO).clear()
    finally:
        loading_manager.stop_busy_suppressor()


def dispatch_inline(params):
    action = params.get('action')

    if action == 'open_settings':
        open_settings()
    elif action == 'select_list':
        select_list()
    elif action == 'live_categories':
        live_categories()
    elif action == 'open_channels':
        open_channels(params)
    elif action == 'channels_pluto':
        channels_pluto()
    elif action == 'radios':
        radios()
    elif action == 'find_movies':
        find_movies()
    elif action == 'find_series':
        find_series()
    elif action == 'imdb_movies_250':
        imdb_movies_250()
    elif action == 'imdb_series_250':
        imdb_series_250()
    elif action == 'imdb_movies_popular':
        imdb_movies_popular()
    elif action == 'imdb_series_popular':
        imdb_series_popular()
    elif action == 'open_imdb_seasons':
        open_imdb_seasons(params)
    elif action == 'open_imdb_episodes':
        open_imdb_episodes(params)
    elif action == 'play_resolve_movies':
        play_resolve_movies(params)
    elif action == 'play_resolve_series':
        play_resolve_series(params)
    else:
        go_home()


def router(paramstring):
    params = dict(parse_qsl(paramstring, keep_blank_values=True))
    if params.get('action') is None:
        go_home()
        return
    dispatch_inline(params)


if __name__ == '__main__':
    router(sys.argv[2][1:] if len(sys.argv) > 2 else '')
