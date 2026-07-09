# -*- coding: utf-8 -*-
import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

from lib.xtream import epg_lookup_current_next, epg_format_range
from lib.loading_window import loading_manager

ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo('path')

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92

HOME = xbmcgui.Window(10000)

_EPG_PROPS = (
    'epg.channel', 'epg.current.title', 'epg.current.desc',
    'epg.current.range', 'epg.current.remaining', 'epg.current.percent',
)
_EPG_CURRENT_PROPS = tuple(p for p in _EPG_PROPS if p != 'epg.channel')

_START_TIMEOUT = 30
_POLL_INTERVAL = 0.3
_TICK_INTERVAL = 20.0
_TICK_STEP = 0.5


class _LiveMonitor(xbmc.Player):
    def __init__(self):
        super(_LiveMonitor, self).__init__()
        self.started = threading.Event()
        self.stopped = threading.Event()

    def reset(self):
        self.started.clear()
        self.stopped.clear()

    def onAVStarted(self):
        self.started.set()

    def onPlayBackStopped(self):
        self.stopped.set()

    def onPlayBackEnded(self):
        self.stopped.set()

    def onPlayBackError(self):
        self.stopped.set()


class _BusySuppressor(object):
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None

    def _run(self):
        while not self._stop.wait(0.1):
            try:
                xbmc.executebuiltin('Dialog.Close(busydialog,true)')
                xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
            except Exception:
                pass

    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        try:
            xbmc.executebuiltin('Dialog.Close(busydialog,true)')
            xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
        except Exception:
            pass


class EPGDialog(xbmcgui.WindowXMLDialog):

    def __new__(cls, xml_filename, script_path, header='', channels=None,
                fanart='', start_pos=0, busy_suppressor=None):
        return super(EPGDialog, cls).__new__(cls, xml_filename, script_path, 'Default', '1080i')

    def __init__(self, xml_filename, script_path, header='', channels=None,
                 fanart='', start_pos=0, busy_suppressor=None):
        super(EPGDialog, self).__init__()
        self.header = header or ''
        self.channels = channels or []
        self.fanart = fanart or ''
        self.start_pos = start_pos
        self._last_pos = -1
        self.selected_channel = None
        self.back_requested = False
        self.video_reclaimed = False
        self._active = False
        self._tick_thread = None
        self._video_watch_thread = None
        self._opened_at = 0
        self._busy_suppressor = busy_suppressor

    def onInit(self):
        HOME.setProperty('epg.header', self.header)
        HOME.setProperty('epg.fanart', self.fanart)

        container = self.getControl(3001)
        container.reset()
        now = int(time.time())

        items = []
        for ch in self.channels:
            li = xbmcgui.ListItem(label=ch.get('name', ''))
            icon = ch.get('icon') or ''
            li.setArt({'icon': icon, 'thumb': icon})
            current, _next = epg_lookup_current_next(ch.get('programs') or [])
            if current:
                li.setProperty('current', current.get('title', '') or '')
                start = int(current.get('start') or 0)
                end = int(current.get('end') or 0)
                pct = 0
                if end > start:
                    pct = max(0, min(100, int((now - start) * 100 / (end - start))))
                li.setProperty('percent', str(pct))
            items.append(li)

        if items:
            container.addItems(items)

        start_pos = self.start_pos if 0 <= self.start_pos < len(self.channels) else 0
        if items:
            container.selectItem(start_pos)
        self.setFocusId(3001)
        self._last_pos = start_pos

        self._active = True
        self._opened_at = time.time()
        self._update_details(start_pos)

        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()
        self._video_watch_thread = threading.Thread(target=self._video_watch_loop, daemon=True)
        self._video_watch_thread.start()

        if self._busy_suppressor is not None:
            self._busy_suppressor.stop()
            self._busy_suppressor = None

    def _tick_loop(self):
        monitor = xbmc.Monitor()
        elapsed = 0.0
        while self._active:
            if monitor.waitForAbort(_TICK_STEP):
                return
            if not self._active:
                return
            elapsed += _TICK_STEP
            if elapsed >= _TICK_INTERVAL:
                elapsed = 0.0
                try:
                    self._update_details(self._last_pos)
                except Exception:
                    pass

    def _video_watch_loop(self):
        monitor = xbmc.Monitor()
        player = xbmc.Player()
        if monitor.waitForAbort(1.0):
            return
        while self._active:
            try:
                if player.isPlayingVideo():
                    self.video_reclaimed = True
                    self.close()
                    return
            except Exception:
                pass
            if monitor.waitForAbort(0.4):
                return

    def onAction(self, action):
        xbmcgui.WindowXMLDialog.onAction(self, action)
        if action.getId() in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            if time.time() - self._opened_at < 0.6:
                return
            self.back_requested = True
            self.close()
            return
        if self.getFocusId() == 3001:
            pos = self.getControl(3001).getSelectedPosition()
            if pos != self._last_pos:
                self._last_pos = pos
                self._update_details(pos)

    def onClick(self, control_id):
        if control_id == 3001:
            pos = self.getControl(3001).getSelectedPosition()
            if 0 <= pos < len(self.channels):
                self.selected_channel = self.channels[pos]
                self.close()
        elif control_id == 3010:
            self.back_requested = True
            self.close()

    def _update_details(self, pos):
        if not self._active:
            return
        if pos < 0 or pos >= len(self.channels):
            HOME.clearProperty('epg.channel')
            for prop in _EPG_CURRENT_PROPS:
                HOME.clearProperty(prop)
            return

        channel = self.channels[pos]
        programs = channel.get('programs') or []
        current, nextp = epg_lookup_current_next(programs)
        now = int(time.time())

        HOME.setProperty('epg.channel', channel.get('name', '') or '')

        try:
            progress_ctrl = self.getControl(3003)
        except Exception:
            progress_ctrl = None

        if current:
            start = int(current.get('start') or 0)
            end = int(current.get('end') or 0)
            remaining = max(0, (end - now) // 60) if end else 0
            pct = 0
            if end > start:
                pct = max(0, min(100, int((now - start) * 100 / (end - start))))
            HOME.setProperty('epg.current.title', current.get('title', '') or '')
            HOME.setProperty('epg.current.desc', current.get('desc') or 'Sem descricao disponivel.')
            HOME.setProperty('epg.current.range', epg_format_range(current))
            HOME.setProperty('epg.current.remaining', str(remaining))
            HOME.setProperty('epg.current.percent', str(pct))
            if progress_ctrl is not None:
                progress_ctrl.setPercent(pct)
        else:
            HOME.clearProperty('epg.current.title')
            HOME.clearProperty('epg.current.range')
            HOME.clearProperty('epg.current.remaining')
            HOME.setProperty('epg.current.percent', '0')
            if nextp:
                next_title = str(nextp.get('title') or '').strip()
                next_range = epg_format_range(nextp)
                if next_title and next_range:
                    desc = 'Sem informacao da programacao atual para este canal.\nA seguir: {} ({})'.format(
                        next_title, next_range)
                elif next_title:
                    desc = 'Sem informacao da programacao atual para este canal.\nA seguir: {}'.format(next_title)
                else:
                    desc = 'Sem informacao da programacao atual para este canal.'
            else:
                desc = 'Programacao nao disponivel para este canal no momento.'
            HOME.setProperty('epg.current.desc', desc)
            if progress_ctrl is not None:
                progress_ctrl.setPercent(0)

        if not self._active:
            return
        try:
            upcoming_container = self.getControl(3002)
        except Exception:
            return
        upcoming_container.reset()
        upcoming = [p for p in programs if int(p.get('start') or 0) > now]
        upcoming_items = []
        for p in upcoming[:16]:
            li = xbmcgui.ListItem(label=p.get('title', '') or '')
            li.setProperty('range', epg_format_range(p))
            upcoming_items.append(li)
        if upcoming_items:
            upcoming_container.addItems(upcoming_items)

    def close(self):
        self._active = False
        for prop in _EPG_PROPS + ('epg.header', 'epg.fanart'):
            HOME.clearProperty(prop)
        xbmcgui.WindowXMLDialog.close(self)


_GUIDE_ACTIVE_PROP = 'kingiptv_epg_guide_active'


def open_epg(header, channels, build_listitem, fanart=''):
    if HOME.getProperty(_GUIDE_ACTIVE_PROP) == 'true':
        return
    HOME.setProperty(_GUIDE_ACTIVE_PROP, 'true')
    try:
        _open_epg_impl(header, channels, build_listitem, fanart)
    finally:
        HOME.clearProperty(_GUIDE_ACTIVE_PROP)


def _open_epg_impl(header, channels, build_listitem, fanart=''):
    channels = [c for c in (channels or []) if c.get('name')]
    for ch in channels:
        ch['programs'] = sorted(ch.get('programs') or [], key=lambda p: p.get('start') or 0)

    if not channels:
        return

    monitor = xbmc.Monitor()
    live_monitor = _LiveMonitor()
    pos = 0
    reopen_suppressor = None

    while True:
        dlg = EPGDialog('DialogEPG.xml', ADDON_PATH, header=header,
                         channels=channels, fanart=fanart, start_pos=pos,
                         busy_suppressor=reopen_suppressor)
        reopen_suppressor = None
        dlg.doModal()
        selected = dlg.selected_channel
        back_requested = dlg.back_requested
        video_reclaimed = dlg.video_reclaimed
        try:
            pos = channels.index(selected) if selected else dlg._last_pos
        except ValueError:
            pos = dlg._last_pos
        del dlg

        if video_reclaimed:
            live_monitor.started.set()
            live_monitor.stopped.clear()
            while not live_monitor.stopped.is_set():
                if monitor.waitForAbort(0.2):
                    return
            reopen_suppressor = _BusySuppressor()
            reopen_suppressor.start()
            continue

        if back_requested or not selected:
            break

        url, listitem = build_listitem(selected)
        if not url or not listitem:
            xbmcgui.Dialog().notification(header, 'Nao foi possivel abrir este canal', xbmcgui.NOTIFICATION_ERROR, 3000)
            continue

        live_monitor.reset()
        loading_manager.show(fanart_path=fanart or None, xml_filename='DialogLoadingLive.xml')
        live_monitor.play(url, listitem)

        waited = 0.0
        while not live_monitor.started.is_set() and not live_monitor.stopped.is_set():
            if monitor.waitForAbort(0.1):
                loading_manager.force_close()
                return
            waited += 0.1
            if waited >= _START_TIMEOUT:
                try:
                    live_monitor.stop()
                except Exception:
                    pass
                break
        loading_manager.close(max_wait=0.1)

        if monitor.abortRequested():
            return

        if not live_monitor.started.is_set():
            xbmcgui.Dialog().notification(header, 'Nao foi possivel iniciar a reproducao deste canal', xbmcgui.NOTIFICATION_ERROR, 3000)
            reopen_suppressor = _BusySuppressor()
            reopen_suppressor.start()
            continue

        while not live_monitor.stopped.is_set():
            if monitor.waitForAbort(0.2):
                return

        if monitor.abortRequested():
            return

        reopen_suppressor = _BusySuppressor()
        reopen_suppressor.start()
