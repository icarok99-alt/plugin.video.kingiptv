# -*- coding: utf-8 -*-

from __future__ import unicode_literals
import os
import sys
import threading
from urllib.parse import parse_qsl

try:
    from kodi_six import xbmc, xbmcplugin, xbmcgui, xbmcaddon, xbmcvfs
except ImportError:
    import xbmc
    import xbmcplugin
    import xbmcgui
    import xbmcaddon
    import xbmcvfs

from lib import list_manager
from lib.home_dialog import open_home_menu
from lib import list_dialog

ADDON_HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]
ADDON = xbmcaddon.Addon()
TRANSLATE = xbmcvfs.translatePath
HOME_DIR = TRANSLATE(ADDON.getAddonInfo('path'))
ADDON_ICON = os.path.join(HOME_DIR, 'icon.png')
ADDON_FANART = os.path.join(HOME_DIR, 'resources', 'skins', 'Default', 'media', 'fanart.jpg')

PROFILE_DIR = TRANSLATE('special://profile/addon_data/plugin.video.kingiptv')
if not os.path.exists(PROFILE_DIR):
    try:
        os.makedirs(PROFILE_DIR)
    except OSError:
        pass
FIRST_RUN_FLAG = os.path.join(PROFILE_DIR, 'first_run_done.txt')


def _is_first_run():
    return not os.path.exists(FIRST_RUN_FLAG)


def _mark_first_run_done():
    try:
        with open(FIRST_RUN_FLAG, 'w', encoding='utf-8') as f:
            f.write('1')
    except Exception:
        pass


def _maybe_prompt_first_run():
    if not _is_first_run():
        return
    if list_manager.get_active_list():
        _mark_first_run_done()
        return
    try:
        from lib import routes
        from lib.home_dialog import prerender_home
        prerender_home(_build_home_items(None), fanart=ADDON_FANART)
        routes.prompt_select_list()
    finally:
        _mark_first_run_done()

_SPECIAL_ACTIONS = ('play_resolve_movies', 'play_resolve_series', 'open_settings')

WELCOME_DESC = (
    'Seja bem-vindo ao KingIPTV, o melhor addon para entretenimento! '
    'Aqui você encontra uma enorme variedade de conteúdos, incluindo '
    'filmes, séries, TV ao vivo e canais Pluto TV. Seu entretenimento '
    'completo, em um só lugar.'
)


def _end_as_dialog():
    try:
        xbmcplugin.endOfDirectory(ADDON_HANDLE, succeeded=False, updateListing=False, cacheToDisc=False)
    except Exception:
        pass


def _run(query):
    if not query:
        return
    action = query.get('action')
    if action == 'menu_tv':
        menu_tv_fast()
    elif action == 'menu_movies':
        menu_movies_fast()
    elif action == 'menu_series':
        menu_series_fast()
    else:
        from lib import routes
        routes.dispatch_inline(query)


def _active_list_key(active):
    if not active:
        return None
    return (active.get('dns'), str(active.get('username')), str(active.get('password')))


def _start_epg_background_download(active=None):
    if active is None:
        active = list_manager.get_active_list()

    def worker():
        try:
            from lib import xtream, pluto
            if active:
                xtream.ensure_epg_background(active['dns'], active['username'], active['password'])
            pluto.ensure_pluto_epg_background()
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def _build_home_items(active):
    items = []

    items.append({
        'label': 'TV AO VIVO',
        'description': WELCOME_DESC,
        'icon': ADDON_ICON,
        'query': {'action': 'menu_tv'},
    })
    items.append({
        'label': 'FILMES',
        'description': WELCOME_DESC,
        'icon': ADDON_ICON,
        'query': {'action': 'menu_movies'},
    })
    items.append({
        'label': 'SÉRIES',
        'description': WELCOME_DESC,
        'icon': ADDON_ICON,
        'query': {'action': 'menu_series'},
    })
    items.append({
        'label': 'RÁDIOS',
        'description': WELCOME_DESC,
        'icon': ADDON_ICON,
        'query': {'action': 'radios'},
    })

    select_label = 'TROCAR LISTA' if active else 'ESCOLHER LISTA'
    items.append({
        'label': select_label,
        'description': WELCOME_DESC,
        'icon': ADDON_ICON,
        'query': {'action': 'select_list'},
    })
    items.append({
        'label': 'CONFIGURAÇÕES',
        'description': WELCOME_DESC,
        'icon': ADDON_ICON,
        'query': {'action': 'open_settings'},
    })
    return items


def home():
    epg_started_for = set()

    def _ensure_epg_for(active):
        key = _active_list_key(active)
        if key in epg_started_for:
            return
        epg_started_for.add(key)
        _start_epg_background_download(active)

    while True:
        active = list_manager.get_active_list()
        _ensure_epg_for(active)

        items = _build_home_items(active)

        selected_query = open_home_menu(items, fanart=ADDON_FANART)
        if not selected_query:
            return
        _run(selected_query)
        _ensure_epg_for(list_manager.get_active_list())


def menu_tv_fast():
    _start_epg_background_download(list_manager.get_active_list())
    while True:
        active = list_manager.get_active_list()
        items = []
        if active:
            items.append({
                'label': 'TV Ao Vivo',
                'description': WELCOME_DESC,
                'icon': ADDON_ICON,
                'query': {'action': 'live_categories'},
            })
        items.append({
            'label': 'Pluto TV',
            'description': WELCOME_DESC,
            'icon': ADDON_ICON,
            'query': {'action': 'channels_pluto'},
        })

        idx, item = list_dialog.open_list_menu('TV AO VIVO', items, fanart=ADDON_FANART)
        if not item:
            return
        _run(item['query'])


def menu_movies_fast():
    items = [
        {'label': 'Pesquisar Filme', 'description': WELCOME_DESC, 'icon': ADDON_ICON, 'query': {'action': 'find_movies'}},
        {'label': 'Top 250 Filmes', 'description': WELCOME_DESC, 'icon': ADDON_ICON, 'query': {'action': 'imdb_movies_250'}},
        {'label': 'Filmes Populares', 'description': WELCOME_DESC, 'icon': ADDON_ICON, 'query': {'action': 'imdb_movies_popular'}},
    ]
    while True:
        idx, item = list_dialog.open_list_menu('FILMES', items, fanart=ADDON_FANART)
        if not item:
            return
        _run(item['query'])


def menu_series_fast():
    items = [
        {'label': 'Pesquisar Série', 'description': WELCOME_DESC, 'icon': ADDON_ICON, 'query': {'action': 'find_series'}},
        {'label': 'Top 250 Séries', 'description': WELCOME_DESC, 'icon': ADDON_ICON, 'query': {'action': 'imdb_series_250'}},
        {'label': 'Séries Populares', 'description': WELCOME_DESC, 'icon': ADDON_ICON, 'query': {'action': 'imdb_series_popular'}},
    ]
    while True:
        idx, item = list_dialog.open_list_menu('SÉRIES', items, fanart=ADDON_FANART)
        if not item:
            return
        _run(item['query'])


def _dispatch_to_routes(paramstring):
    from lib import routes
    routes.router(paramstring)


def main():
    paramstring = sys.argv[2][1:] if len(sys.argv) > 2 and sys.argv[2].startswith('?') else (sys.argv[2] if len(sys.argv) > 2 else '')
    params = dict(parse_qsl(paramstring, keep_blank_values=True))
    action = params.get('action')

    if action in _SPECIAL_ACTIONS:
        _dispatch_to_routes(paramstring)
        return

    _end_as_dialog()

    if action == 'menu_tv':
        menu_tv_fast()
        return
    if action == 'menu_movies':
        menu_movies_fast()
        return
    if action == 'menu_series':
        menu_series_fast()
        return

    if action is None:
        _maybe_prompt_first_run()
        home()
        return

    _dispatch_to_routes(paramstring)


if __name__ == '__main__':
    main()
