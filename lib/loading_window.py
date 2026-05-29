# -*- coding: utf-8 -*-

import xbmc
import xbmcgui
import xbmcaddon
import threading
import time
import os


class _PlaybackMonitor(xbmc.Player):

    def __init__(self):
        super().__init__()
        self._event = threading.Event()

    def onPlayBackStarted(self):
        self._event.set()

    def onAVStarted(self):
        self._event.set()

    def onPlayBackError(self):
        self._event.set()

    def onPlayBackStopped(self):
        self._event.set()

    def reset(self):
        self._event.clear()

    def wait_for_playback(self, timeout=20):
        monitor = xbmc.Monitor()
        elapsed = 0
        interval = 0.2

        while elapsed < timeout:
            if self._event.is_set():
                return True
            try:
                if self.isPlaying() and self.getTime() > 0:
                    return True
            except Exception:
                pass
            if monitor.waitForAbort(interval):
                return False
            elapsed += interval

        return False


class LoadingWindow(xbmcgui.WindowXMLDialog):

    PROGRESS_CONTROL = 100

    def __init__(self, *args, **kwargs):
        self.progress = 0
        self.closing = False
        self._progress_thread = None
        self._controls_ready = False

    def onInit(self):
        try:
            self._controls_ready = True
            xbmcgui.Window(10000).setProperty('loading.phase', '1')
            xbmcgui.Window(10000).clearProperty('loading.phase2')
            self.start_progress_animation()
        except Exception:
            pass

    def start_progress_animation(self):
        if self._progress_thread is None or not self._progress_thread.is_alive():
            self.closing = False
            self._progress_thread = threading.Thread(target=self._animate_progress)
            self._progress_thread.daemon = True
            self._progress_thread.start()

    def _animate_progress(self):
        try:
            while not self.closing:
                for i in range(0, 101, 2):
                    if self.closing:
                        break

                    if self._controls_ready:
                        try:
                            self.getControl(self.PROGRESS_CONTROL).setPercent(i)
                        except Exception:
                            pass

                    xbmcgui.Window(10000).setProperty('loading.progress', str(i))
                    time.sleep(0.05)

                if not self.closing:
                    time.sleep(0.2)
        except Exception:
            pass

    def close_dialog(self):
        try:
            self.closing = True

            if self._progress_thread and self._progress_thread.is_alive():
                self._progress_thread.join(timeout=1.0)

            xbmcgui.Window(10000).clearProperty('loading.phase')
            xbmcgui.Window(10000).clearProperty('loading.phase2')
            xbmcgui.Window(10000).clearProperty('loading.progress')
            xbmcgui.Window(10000).clearProperty('loading.fanart')

            self.close()
        except Exception:
            pass


class SourceSelectWindow(xbmcgui.WindowXMLDialog):

    LIST_CONTROL = 200

    def __init__(self, *args, **kwargs):
        self.labels = kwargs.pop('labels', [])
        self.selected_index = -1

    def onInit(self):
        try:
            ctrl = self.getControl(self.LIST_CONTROL)
            ctrl.reset()
            for label in self.labels:
                ctrl.addItem(xbmcgui.ListItem(label=label))
            self.setFocusId(self.LIST_CONTROL)
        except Exception:
            pass

    def onClick(self, control_id):
        if control_id == self.LIST_CONTROL:
            try:
                self.selected_index = self.getControl(self.LIST_CONTROL).getSelectedPosition()
            except Exception:
                self.selected_index = 0
            self.close()

    def onAction(self, action):
        if action.getId() in (
            xbmcgui.ACTION_PREVIOUS_MENU,
            xbmcgui.ACTION_NAV_BACK,
            xbmcgui.ACTION_STOP,
        ):
            self.selected_index = -1
            self.close()


class LoadingManager:

    def __init__(self):
        self.window = None
        self._lock = threading.Lock()
        self._monitor_thread = None
        self._should_close = False
        self._busy_suppress_thread = None
        self._suppress_busy = False
        self._player_monitor = _PlaybackMonitor()

    def _run_busy_suppressor(self):
        while self._suppress_busy:
            try:
                xbmc.executebuiltin('Dialog.Close(busydialog,true)')
                xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
            except Exception:
                pass
            xbmc.sleep(100)

    def _start_busy_suppressor(self):
        self._suppress_busy = True
        if self._busy_suppress_thread is None or not self._busy_suppress_thread.is_alive():
            self._busy_suppress_thread = threading.Thread(target=self._run_busy_suppressor)
            self._busy_suppress_thread.daemon = True
            self._busy_suppress_thread.start()

    def show(self, fanart_path=None):
        with self._lock:
            try:
                if self.window:
                    try:
                        self.window.close_dialog()
                    except Exception:
                        pass
                    self.window = None

                addon = xbmcaddon.Addon()
                addon_path = addon.getAddonInfo('path')

                if fanart_path is None:
                    fanart_path = os.path.join(addon_path, 'resources', 'skins', 'Default', 'media', 'fanart.jpg')

                xbmcgui.Window(10000).setProperty('loading.fanart', fanart_path)

                self._should_close = False
                self._start_busy_suppressor()

                self.window = LoadingWindow(
                    'DialogLoadingKing.xml',
                    addon_path,
                    'Default',
                    '1080i'
                )
                self.window.show()
                xbmc.sleep(100)

            except Exception:
                pass

    def show_source_select(self, players, fanart_path=None):
        """
        Fase 2: DialogSourceSelect abre em cima do loading (que continua aberto).
        Bloqueia até o usuário escolher.
        Retorna o índice selecionado ou -1 se cancelado.
        """
        try:
            addon = xbmcaddon.Addon()
            addon_path = addon.getAddonInfo('path')

            if fanart_path is None:
                fanart_path = os.path.join(addon_path, 'resources', 'skins', 'Default', 'media', 'fanart.jpg')

            xbmcgui.Window(10000).setProperty('mdl.loading.fanart', fanart_path)
            xbmcgui.Window(10000).setProperty('loading.phase', '2')

            labels = [label for label, _ in players]

            dialog = SourceSelectWindow(
                'DialogSourceSelect.xml',
                addon_path,
                'Default',
                '1080i',
                labels=labels
            )
            dialog.doModal()

            xbmcgui.Window(10000).clearProperty('mdl.loading.fanart')

            return dialog.selected_index

        except Exception:
            return -1

    def set_phase3(self):
        """Fase 3: resolvendo o link."""
        try:
            xbmcgui.Window(10000).setProperty('loading.phase', '3')
            xbmcgui.Window(10000).setProperty('loading.phase2', 'true')
        except Exception:
            pass

    def set_phase2(self):
        """Compat: equivalente a set_phase3() no fluxo com source select."""
        self.set_phase3()

    def close(self):
        if self.window:
            self._should_close = True
            if self._monitor_thread is None or not self._monitor_thread.is_alive():
                self._player_monitor.reset()
                self._monitor_thread = threading.Thread(target=self._wait_for_playback)
                self._monitor_thread.daemon = True
                self._monitor_thread.start()

    def _wait_for_playback(self):
        self._player_monitor.wait_for_playback(timeout=20)
        with self._lock:
            if self.window and self._should_close:
                try:
                    self._suppress_busy = False
                    self.window.close_dialog()
                    self.window = None
                except Exception:
                    pass

    def force_close(self):
        with self._lock:
            self._suppress_busy = False
            self._should_close = False
            if self.window:
                try:
                    self.window.close_dialog()
                    self.window = None
                except Exception:
                    pass


loading_manager = LoadingManager()
