# -*- coding: utf-8 -*-

import sys

try:
    from urllib.parse import unquote_plus
except ImportError:
    from urllib import unquote_plus

try:
    raw = sys.argv[1].lstrip('?') if len(sys.argv) > 1 else ''
    params = {}
    for part in raw.split('&'):
        if '=' in part:
            k, v = part.split('=', 1)
            params[unquote_plus(k)] = unquote_plus(v)

    imdb_id = params.get('imdbnumber', '')
    season  = params.get('season_num', '')
    episode = params.get('episode_num', '')

    if imdb_id and season and episode:
        from lib.database import KingDatabase
        db = KingDatabase()
        if db.is_watched(imdb_id, int(season), int(episode)):
            db.unmark_watched(imdb_id, int(season), int(episode))
        else:
            db.mark_watched(imdb_id, int(season), int(episode))

    import xbmc
    xbmc.executebuiltin('Container.Refresh')

except Exception:
    pass