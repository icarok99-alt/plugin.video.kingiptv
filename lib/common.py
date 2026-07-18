# -*- coding: utf-8 -*-

from __future__ import unicode_literals

import io
import os
import sys
import time
import json
import threading

import six
import requests

try:
    from kodi_six import xbmc, xbmcgui, xbmcplugin, xbmcaddon, xbmcvfs
except ImportError:
    import xbmc
    import xbmcgui
    import xbmcplugin
    import xbmcaddon
    import xbmcvfs

if six.PY3:
    from urllib.parse import (
        urlparse, parse_qs, parse_qsl, quote, unquote,
        quote_plus, unquote_plus, urlencode,
    )
else:
    from urlparse import urlparse, parse_qs, parse_qsl
    from urllib import quote, unquote, quote_plus, unquote_plus, urlencode

if six.PY2:
    reload(sys)  # noqa: F821
    sys.setdefaultencoding('utf-8')

addon = xbmcaddon.Addon()
addonName = addon.getAddonInfo('name')
addonVersion = addon.getAddonInfo('version')
homeDir = addon.getAddonInfo('path')
translate = xbmcvfs.translatePath if six.PY3 else xbmc.translatePath
addonIcon = translate(os.path.join(homeDir, 'icon.png'))
addonFanart = translate(os.path.join(homeDir, 'fanart.jpg'))
profile = translate(addon.getAddonInfo('profile'))

plugin = sys.argv[0] if len(sys.argv) > 0 else 'plugin://plugin.video.kingiptv'
base = plugin
handle = int(sys.argv[1]) if len(sys.argv) > 1 else -1

dialog_ = xbmcgui.Dialog()
executebuiltin = xbmc.executebuiltin

LOG_FILE = os.path.join(profile, 'kingiptv_debug.log')


def ensure_profile_dir():
    try:
        if not xbmcvfs.exists(profile):
            xbmcvfs.mkdirs(profile)
    except Exception:
        try:
            if not os.path.exists(profile):
                os.makedirs(profile)
        except Exception:
            pass


ensure_profile_dir()


def log(message, level=None):
    if level is None:
        level = xbmc.LOGINFO
    try:
        text = message if isinstance(message, six.text_type) else str(message)
    except Exception:
        text = repr(message)
    formatted = '[KingIPTV] {}'.format(text)
    try:
        xbmc.log(formatted, level=level)
    except Exception:
        pass
    try:
        ensure_profile_dir()
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        with io.open(LOG_FILE, 'a', encoding='utf-8') as fh:
            fh.write(u'{} {}\n'.format(timestamp, text))
    except Exception:
        pass


def opensettings():
    addon.openSettings()


def getsetting(text):
    return addon.getSetting(text)


def setsetting(key, value):
    return addon.setSetting(key, value)


def get_setting_bool(key, default=False):
    try:
        value = addon.getSetting(key)
        if value in (None, ''):
            return default
        return str(value).lower() == 'true'
    except Exception:
        return default


def exists(path):
    return xbmcvfs.exists(path)


def mkdir(path):
    try:
        xbmcvfs.mkdir(path)
    except Exception:
        pass


def yesno(heading="", message="", nolabel="Nao", yeslabel="Sim"):
    if not heading:
        heading = addonName
    if six.PY2:
        return dialog_.yesno(heading=heading, line1=message, nolabel=nolabel, yeslabel=yeslabel)
    return dialog_.yesno(heading=heading, message=message, nolabel=nolabel, yeslabel=yeslabel)


def format_resume_time(seconds):
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    if h > 0:
        return '{:d}:{:02d}:{:02d}'.format(h, m, s)
    return '{:d}:{:02d}'.format(m, s)


def ask_resume(position):
    time_str = format_resume_time(position)
    message = 'Você deseja retornar de onde parou {}?'.format(time_str)
    return dialog_.yesno(heading=addonName, message=message, nolabel='Não', yeslabel='Sim')


def dialog(msg):
    dialog_.ok(addonName, msg)


def select(name, items):
    return dialog_.select(name, items)


def get_search_string(heading='', message=''):
    search_string = None
    keyboard = xbmc.Keyboard(message, heading)
    keyboard.doModal()
    if keyboard.isConfirmed():
        search_string = to_unicode(keyboard.getText())
    return search_string


def input_text(heading='Put text'):
    vq = get_search_string(heading=heading, message="")
    if not vq:
        return False
    return vq


def infoDialog(message, iconimage='', time=3000, sound=False):
    heading = addonName
    if iconimage == '':
        iconimage = addonIcon
    elif iconimage == 'INFO':
        iconimage = xbmcgui.NOTIFICATION_INFO
    elif iconimage == 'WARNING':
        iconimage = xbmcgui.NOTIFICATION_WARNING
    elif iconimage == 'ERROR':
        iconimage = xbmcgui.NOTIFICATION_ERROR
    dialog_.notification(heading, message, iconimage, time, sound=sound)


def notify(msg):
    try:
        infoDialog(msg, iconimage='INFO')
    except Exception:
        pass


def string_utf8(string):
    if isinstance(string, bytes):
        return string
    return string.encode("utf-8", errors="ignore")


def to_unicode(text, encoding='utf-8', errors='strict'):
    if isinstance(text, bytes):
        return text.decode(encoding, errors=errors)
    return text


def build_url(query):
    return '{}?{}'.format(plugin, urlencode(query or {}))


def addMenuItem(params=None, destiny='', exclude_from_url=None, action=None):
    params = dict(params or {})
    action_name = action if action is not None else destiny.lstrip('/')
    name = params.get('name', '')
    description = params.get("description", "")
    originaltitle = params.get("originaltitle", "")
    try:
        params['name'] = string_utf8(name)
    except Exception:
        pass
    try:
        params['description'] = string_utf8(description)
    except Exception:
        pass
    try:
        params['originaltitle'] = string_utf8(originaltitle)
    except Exception:
        pass

    exclude_keys = {'playcount'}
    if exclude_from_url:
        exclude_keys.update(exclude_from_url)
    url_params = {k: v for k, v in params.items() if k not in exclude_keys and v not in (None, '')}
    url_params['action'] = action_name
    url = build_url(url_params)

    iconimage = params.get("iconimage", "")
    fanart = params.get("fanart", "")
    codec = params.get("codec", "")
    playable = params.get("playable", "")
    duration = params.get("duration", "")
    imdbnumber = params.get("imdbnumber", "") or params.get("imdb", "")
    aired = params.get("aired", "")
    genre = params.get("genre", "")
    season = params.get("season", "")
    episode = params.get("episode", "")
    year = params.get("year", "")
    mediatype = params.get("mediatype", "video")
    tvshowtitle = params.get("tvshowtitle", "")
    serie_name = params.get("serie_name", "")

    li = xbmcgui.ListItem(name)
    iconimage = iconimage if iconimage else addonIcon
    li.setArt({"icon": "DefaultVideo.png", "thumb": iconimage})
    info = li.getVideoInfoTag()
    info.setTitle(name)
    info.setPlot(description)
    if year:
        info.setYear(int(year))
    if codec:
        info.addVideoStream(xbmc.VideoStreamDetail(codec='h264'))
    if duration:
        info.setDuration(int(duration))
    if originaltitle:
        info.setOriginalTitle(str(originaltitle))
    if imdbnumber:
        info.setIMDBNumber(str(imdbnumber))
    if aired:
        info.setFirstAired(str(aired))
    if genre:
        info.setGenres([str(genre)])
    if mediatype:
        info.setMediaType(str(mediatype))
    if tvshowtitle or serie_name:
        info.setTvShowTitle(str(tvshowtitle or serie_name))
    playcount = params.get('playcount', None)
    if playcount is not None:
        info.setPlaycount(int(playcount))
    season_num = params.get('season_num', season)
    episode_num = params.get('episode_num', episode)
    if season_num:
        info.setSeason(int(season_num))
    if episode_num:
        info.setEpisode(int(episode_num))
    is_playable = bool(playable and playable != 'false')
    is_folder = not is_playable
    if is_playable:
        li.setProperty('IsPlayable', 'true')
        try:
            info.setResumePoint(0, 0)
        except Exception:
            pass
    li.setProperty('fanart_image', fanart or addonFanart)
    xbmcplugin.addDirectoryItem(handle=handle, url=url, listitem=li, isFolder=is_folder)


def play_video(params):
    name = params.get('name', '')
    url = params.get('url', '')
    sub = params.get('sub', '')
    description = params.get("description", "")
    originaltitle = params.get("originaltitle", "")
    iconimage = params.get("iconimage", "")
    fanart = params.get("fanart", "")
    codec = params.get("codec", "")
    playable = params.get("playable", "")
    duration = params.get("duration", "")
    imdbnumber = params.get("imdbnumber", "") or params.get("imdb", "")
    aired = params.get("aired", "")
    genre = params.get("genre", "")
    season = params.get("season", "")
    episode = params.get("episode", "")
    year = params.get("year", "")
    mediatype = params.get("mediatype", "video")
    li = xbmcgui.ListItem(name)
    li.setArt({"icon": "DefaultVideo.png", "thumb": iconimage or ''})
    info = li.getVideoInfoTag()
    info.setTitle(name)
    info.setPlot(description)
    if year:
        info.setYear(int(year))
    if codec:
        info.addVideoStream(xbmc.VideoStreamDetail(codec='h264'))
    if duration:
        info.setDuration(int(duration))
    if originaltitle:
        info.setOriginalTitle(str(originaltitle))
    if imdbnumber:
        info.setIMDBNumber(str(imdbnumber))
    if aired:
        info.setFirstAired(str(aired))
    if genre:
        info.setGenres([str(genre)])
    if season:
        info.setSeason(int(season))
    if episode:
        info.setEpisode(int(episode))
    if mediatype:
        info.setMediaType(str(mediatype))
    li.setProperty('fanart_image', fanart or addonFanart)
    li.setPath(url)
    if sub:
        li.setSubtitles([sub])
    if playable and playable != 'false':
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, li)
    else:
        xbmc.Player().play(item=url, listitem=li)


def setcontent(name):
    xbmcplugin.setContent(handle, name)


def end(succeeded=True, update_listing=False, cache_to_disc=True):
    try:
        xbmcplugin.endOfDirectory(handle, succeeded=succeeded, updateListing=update_listing, cacheToDisc=cache_to_disc)
    except TypeError:
        xbmcplugin.endOfDirectory(handle)


_VIEW_IDS = {
    'List': 50,
    'Poster': 51,
    'IconWall': 52,
    'Shift': 53,
    'InfoWall': 54,
    'WideList': 55,
    'Wall': 500,
    'Banner': 501,
    'Fanart': 502,
}


def setview(name):
    view_id = _VIEW_IDS.get(name, 50)
    xbmc.executebuiltin('Container.SetViewMode({})'.format(view_id))


def go_home():
    try:
        xbmcplugin.endOfDirectory(handle, succeeded=False, updateListing=True, cacheToDisc=False)
    except Exception:
        pass
    xbmc.executebuiltin('Container.Update(plugin://plugin.video.kingiptv/, replace)')
