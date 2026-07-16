# -*- coding: utf-8 -*-

try:
    from kodi_six import xbmc as xbmc_, xbmcgui as xbmcgui_, xbmcplugin as xbmcplugin_, xbmcaddon as xbmcaddon_, xbmcvfs as xbmcvfs_
except:
    pass
import six as six_
if six_.PY3:
    from urllib.parse import urlparse as urlparse_, parse_qs as parse_qs_, parse_qsl as parse_qsl_, quote as quote_, unquote as unquote_, quote_plus as quote_plus_, unquote_plus as unquote_plus_, urlencode as urlencode_
else:
    from urlparse import urlparse as urlparse_, parse_qs as parse_qs_, parse_qsl as parse_qsl_
    from urllib import quote as quote_, unquote as unquote_, quote_plus as quote_plus_, unquote_plus as unquote_plus_, urlencode as urlencode_
import sys
import os as os_
import requests as rq
try:
    import json as json_
except ImportError:
    import simplejson as json_
from bs4 import BeautifulSoup as bfs
import base64
six = six_
requests = rq
json = json_
BeautifulSoup = bfs
try:
    xbmc = xbmc_
    xbmcgui = xbmcgui_
    xbmcplugin = xbmcplugin_
    xbmcaddon = xbmcaddon_
    xbmcvfs = xbmcvfs_
except:
    pass
urlparse = urlparse_
parse_qs = parse_qs_
parse_qsl = parse_qsl_
quote = quote_
unquote = unquote_
quote_plus = quote_plus_
unquote_plus = unquote_plus_
urlencode = urlencode_
os = os_
if six.PY2:
    reload(sys)
    sys.setdefaultencoding('utf-8')
try:
    class Progress_six:
        dp = xbmcgui.DialogProgress()
        @classmethod
        def create(cls,heading,msg):
            if six.PY3:
                cls.dp.create(str(heading),str(msg))
            else:
                cls.dp.create(str(heading),str(msg), '','')
        @classmethod
        def update(cls,update,heading):
            if six.PY3:
                cls.dp.update(int(update), str(heading))
            else:
                cls.dp.update(int(update), str(heading),'', '')
    class ProgressBG_six:
        dp = xbmcgui.DialogProgressBG()
        @classmethod
        def create(cls,heading,msg):
            if six.PY3:
                cls.dp.create(str(heading),str(msg))
            else:
                cls.dp.create(str(heading),str(msg), '','')
        @classmethod
        def update(cls,update,heading):
            if six.PY3:
                cls.dp.update(int(update), str(heading))
            else:
                cls.dp.update(int(update), str(heading),'', '')
except:
    pass
try:
    addon = xbmcaddon.Addon()
    addonName = addon.getAddonInfo('name')
    addonVersion = addon.getAddonInfo('version')
    homeDir = addon.getAddonInfo('path')
    translate = xbmcvfs.translatePath if six.PY3 else xbmc.translatePath
    addonIcon = translate(os.path.join(homeDir, 'icon.png'))
    addonFanart = translate(os.path.join(homeDir, 'fanart.jpg'))
    profile = translate(addon.getAddonInfo('profile'))
    plugin = sys.argv[0]
    base = plugin
    handle = int(sys.argv[1])
    dialog_ = xbmcgui.Dialog()
    executebuiltin = xbmc.executebuiltin
except:
    pass

def yesno(heading="",message="",nolabel="Nao",yeslabel="Sim"):
    if not heading:
        heading = addonName
    if six.PY2:
        q = dialog_.yesno(heading=heading, line1=message, nolabel=nolabel, yeslabel=yeslabel)
    else:
        q = dialog_.yesno(heading=heading, message=message, nolabel=nolabel, yeslabel=yeslabel)
    return q

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
    return dialog_.yesno(
        heading=addonName,
        message=message,
        nolabel='Não',
        yeslabel='Sim',
    )

def route(r):
    try:
        route_decorator = r.split('/')[1]
        plugin_route = base.split('/')[3:]
        route_sys = plugin_route[0]
        def decorator(f):
            params = {}
            try:
                param_root = plugin_route[1]
                for command in param_root.split('&'):
                    if '=' in command:
                        split_command = command.split('=', 1)
                        key = unquote_plus(split_command[0])
                        value = unquote_plus(split_command[1])
                        params[key] = value
                    else:
                        params[unquote_plus(command)] = ''
            except:
                pass
            if not route_decorator and not route_sys:
                try:
                    f(params)
                except:
                    f()
            elif route_decorator == route_sys:
                try:
                    f(params)
                except:
                    f()
        return decorator
    except:
        def decorator(f):
            return f
        return decorator

def opensettings():
    addon.openSettings()

def getsetting(text):
    return addon.getSetting(text)

def setsetting(key,value):
    return addon.setSetting(key, value)

def exists(path):
    return xbmcvfs.exists(path)

def mkdir(path):
    try:
        xbmcvfs.mkdir(path)
    except:
        pass

def dialog(msg):
    dialog = xbmcgui.Dialog()
    dialog.ok(addonName, msg)

def progress_six():
    dp = Progress_six()
    return dp

def progressBG_six():
    pDialog = ProgressBG_six()
    return pDialog

def select(name,items):
    op = dialog_.select(name, items)
    return op

def string_utf8(string):
    if isinstance(string, bytes):
        return string
    return string.encode("utf-8", errors="ignore")

def to_unicode(text, encoding='utf-8', errors='strict'):
    if isinstance(text, bytes):
        return text.decode(encoding, errors=errors)
    return text

def get_search_string(heading='', message=''):
    search_string = None
    keyboard = xbmc.Keyboard(message, heading)
    keyboard.doModal()
    if keyboard.isConfirmed():
        search_string = to_unicode(keyboard.getText())
    return search_string

def input_text(heading='Put text'):
    vq = get_search_string(heading=heading, message="")
    if ( not vq ): return False
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
        infoDialog(msg,iconimage='INFO')
    except:
        pass

def addMenuItem(params={}, destiny='', exclude_from_url=None):
    try:
        destiny = destiny.split('/')[1]
    except:
        pass
    name = params.get('name', '')
    description = params.get("description", "")
    originaltitle = params.get("originaltitle", "")
    try:
        params.update({'name': string_utf8(name)})
    except:
        pass
    try:
        params.update({'description': string_utf8(description)})
    except:
        pass
    try:
        params.update({'originaltitle': string_utf8(originaltitle)})
    except:
        pass
    exclude_keys = {'playcount'}
    if exclude_from_url:
        exclude_keys.update(exclude_from_url)
    url_params = {k: v for k, v in params.items() if k not in exclude_keys}
    u = 'plugin://%s/%s/%s'%(base.split("/")[2],destiny,urlencode(url_params))
    iconimage = params.get("iconimage", "")
    fanart = params.get("fanart", "")
    codec = params.get("codec", "")
    playable = params.get("playable", "")
    duration = params.get("duration", "")
    imdbnumber = params.get("imdbnumber", "")
    if not imdbnumber:
        imdbnumber = params.get("imdb", "")
    aired = params.get("aired", "")
    genre = params.get("genre", "")
    season = params.get("season", "")
    episode = params.get("episode", "")
    year = params.get("year", "")
    mediatype = params.get("mediatype", "video")
    tvshowtitle = params.get("tvshowtitle", "")
    serie_name = params.get("serie_name", "")
    li=xbmcgui.ListItem(name)
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
    if fanart:
        li.setProperty('fanart_image', fanart)
    else:
        li.setProperty('fanart_image', addonFanart)
    xbmcplugin.addDirectoryItem(handle=handle, url=u, listitem=li, isFolder=is_folder)

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
    imdbnumber = params.get("imdbnumber", "")
    if not imdbnumber:
        imdbnumber = params.get("imdb", "")
    aired = params.get("aired", "")
    genre = params.get("genre", "")
    season = params.get("season", "")
    episode = params.get("episode", "")
    year = params.get("year", "")
    mediatype = params.get("mediatype", "video")
    li=xbmcgui.ListItem(name)
    iconimage = iconimage if iconimage else ''
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
    if season:
        info.setSeason(int(season))
    if episode:
        info.setEpisode(int(episode))
    if mediatype:
        info.setMediaType(str(mediatype))
    if fanart:
        li.setProperty('fanart_image', fanart)
    else:
        li.setProperty('fanart_image', addonFanart)
    li.setPath(url)
    if sub:
        li.setSubtitles([sub])
    if playable and not playable == 'false':
        xbmcplugin.setResolvedUrl(int(sys.argv[1]), True, li)
    else:
        xbmc.Player().play(item=url, listitem=li)

def setcontent(name):
    xbmcplugin.setContent(handle, name)

def end():
    xbmcplugin.endOfDirectory(handle)

def setview(name):
    views = {
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
    view_id = views.get(name, 50)
    xbmc.executebuiltin(f'Container.SetViewMode({view_id})')
