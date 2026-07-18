# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import sys
import threading
from urllib.parse import parse_qsl

from lib.common import xbmc, xbmcaddon, xbmcplugin, handle, addMenuItem, end, setview, log
from lib import list_manager

TITULO = '::: KING IPTV :::'


def _paramstring():
    if len(sys.argv) > 2 and sys.argv[2]:
        raw = sys.argv[2]
        return raw[1:] if raw.startswith('?') else raw
    return ''


def _start_epg_background(active):
    def worker():
        try:
            from lib import xtream, pluto
            xtream.ensure_epg_background(active['dns'], active['username'], active['password'])
            pluto.ensure_pluto_epg_background()
        except Exception as exc:
            log('[KINGIPTV][EPG] falha ao atualizar EPG em background: {}'.format(exc))
    threading.Thread(target=worker, daemon=True).start()


def index_iptv():
    active = list_manager.get_active_list()
    addMenuItem({'name': TITULO, 'description': ''}, action='noop')

    if active:
        _start_epg_background(active)
        addMenuItem({'name': 'TV AO VIVO', 'description': ''}, destiny='/live_categories')
    addMenuItem({'name': 'PLUTO TV', 'description': ''}, destiny='/channels_pluto')
    addMenuItem({'name': 'RÁDIOS', 'description': ''}, destiny='/radios')
    if active:
        addMenuItem({'name': 'IMDB FILMES', 'description': ''}, destiny='/imdb_movies')
        addMenuItem({'name': 'IMDB SÉRIES', 'description': ''}, destiny='/imdb_series')
    select_list_label = 'TROCAR LISTA' if active else 'ESCOLHER LISTA'
    addMenuItem({'name': select_list_label, 'description': ''}, destiny='/select_list')
    addMenuItem({'name': 'CONFIGURAÇÕES'}, destiny='/settings')
    end()
    setview('WideList')


def _first_run_select_list_then_home():
    from lib import routes
    routes.prompt_select_list()
    index_iptv()


def main():
    params = dict(parse_qsl(_paramstring(), keep_blank_values=True))
    action = params.get('action') or None

    if action in (None, 'noop'):
        if not list_manager.get_active_list():
            _first_run_select_list_then_home()
        else:
            index_iptv()
        return

    if action == 'settings':
        xbmcaddon.Addon().openSettings()
        return

    from lib import routes
    routes.router(action, params)


if __name__ == '__main__':
    main()
