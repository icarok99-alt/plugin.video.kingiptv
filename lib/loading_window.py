# -*- coding: utf-8 -*-

import os
import threading
import time
import xbmc
import xbmcaddon
import xbmcgui

class _PlaybackMonitor(xbmc.Player):
    def __init__(self):
        super().__init__()
        self._av_ready = threading.Event()
        self._failed = threading.Event()
        self._cancelled = threading.Event()
        self._av_fired = False
    def onAVStarted(self):
        if not self._av_fired:
            self._av_fired = True
            self._av_ready.set()
    def onPlayBackError(self):
        if not self._av_fired:
            self._failed.set()
    def onPlayBackStopped(self):
        if not self._av_fired:
            self._failed.set()
    def onPlayBackFailed(self):
        if not self._av_fired:
            self._failed.set()
    def reset(self):
        self._av_ready.clear()
        self._failed.clear()
        self._cancelled.clear()
        self._av_fired = False
    def cancel(self):
        self._cancelled.set()
    def wait_until_playing(self, max_wait=45.0, confirm_secs=0.4):
        kodi_monitor = xbmc.Monitor()
        deadline = time.monotonic() + max_wait
        poll_interval = 0.1
        phase1_ok = False
        while time.monotonic() < deadline:
            if self._cancelled.is_set() or self._failed.is_set():
                return False
            if kodi_monitor.abortRequested():
                return False
            if self._av_ready.is_set():
                phase1_ok = True
                break
            try:
                if self.isPlaying() and self.getTime() > 0.0:
                    phase1_ok = True
                    break
            except Exception:
                pass
            kodi_monitor.waitForAbort(poll_interval)
        if not phase1_ok:
            return False
        check_interval = 0.1
        last_pos = -1.0
        confirm_deadline = min(time.monotonic() + confirm_secs, deadline)
        while time.monotonic() < confirm_deadline:
            if self._cancelled.is_set() or self._failed.is_set():
                return False
            if kodi_monitor.abortRequested():
                return False
            try:
                if not self.isPlaying():
                    return False
                pos = self.getTime()
            except Exception:
                kodi_monitor.waitForAbort(check_interval)
                continue
            if last_pos >= 0.0 and pos > last_pos:
                return True
            last_pos = pos
            kodi_monitor.waitForAbort(check_interval)
        try:
            return self._av_ready.is_set() and self.isPlaying() and self.getTime() >= 0.0
        except Exception:
            return self._av_ready.is_set()

class LoadingWindow(xbmcgui.WindowXMLDialog):
    PROGRESS_CONTROL = 100
    def __init__(self, *args, **kwargs):
        self._stop_anim = threading.Event()
        self._controls_ready = False
        self._anim_thread = None
    def onInit(self):
        self._controls_ready = True
        xbmcgui.Window(10000).setProperty('loading.phase', '1')
        xbmcgui.Window(10000).clearProperty('loading.phase2')
        self._start_animation()
    def _start_animation(self):
        self._stop_anim.clear()
        self._anim_thread = threading.Thread(target=self._animate, daemon=True)
        self._anim_thread.start()
    def _animate(self):
        try:
            while not self._stop_anim.is_set():
                for pct in range(0, 101, 2):
                    if self._stop_anim.is_set():
                        return
                    if self._controls_ready:
                        try:
                            self.getControl(self.PROGRESS_CONTROL).setPercent(pct)
                        except Exception:
                            pass
                    xbmcgui.Window(10000).setProperty('loading.progress', str(pct))
                    time.sleep(0.05)
                if not self._stop_anim.is_set():
                    time.sleep(0.1)
        except Exception:
            pass
    def dismiss(self):
        self._stop_anim.set()
        if self._anim_thread and self._anim_thread.is_alive():
            self._anim_thread.join(timeout=0.5)
        for prop in ('loading.phase', 'loading.phase2', 'loading.progress', 'loading.fanart'):
            try:
                xbmcgui.Window(10000).clearProperty(prop)
            except Exception:
                pass
        try:
            self.close()
        except Exception:
            pass

class LoadingManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._window = None
        self._generation = 0
        self._monitor = _PlaybackMonitor()
        self._busy_stop = threading.Event()
    def _run_busy_suppressor(self):
        while not self._busy_stop.wait(0.1):
            try:
                xbmc.executebuiltin('Dialog.Close(busydialog,true)')
                xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
            except Exception:
                pass
    def _start_busy_suppressor(self):
        self._busy_stop.clear()
        threading.Thread(target=self._run_busy_suppressor, daemon=True).start()
    def _stop_busy_suppressor(self):
        self._busy_stop.set()
    def _addon_path(self):
        return xbmcaddon.Addon().getAddonInfo('path')
    def _default_fanart(self):
        return os.path.join(
            self._addon_path(), 'resources', 'skins', 'Default', 'media', 'fanart.jpg'
        )
    def _do_dismiss(self, window):
        self._stop_busy_suppressor()
        if window is not None:
            try:
                window.dismiss()
            except Exception:
                pass
    def show(self, fanart_path=None):
        with self._lock:
            old_window = self._window
            self._window = None
            self._generation += 1
            current_gen = self._generation
        if old_window is not None:
            self._do_dismiss(old_window)
        self._monitor.cancel()
        self._monitor.reset()
        fanart = fanart_path or self._default_fanart()
        addon_path = self._addon_path()
        xbmcgui.Window(10000).setProperty('loading.fanart', fanart)
        self._start_busy_suppressor()
        new_window = LoadingWindow(
            'DialogLoadingKing.xml', addon_path, 'Default', '1080i'
        )
        new_window.show()
        xbmc.sleep(80)
        with self._lock:
            if self._generation == current_gen:
                self._window = new_window
        if self._generation != current_gen:
            self._do_dismiss(new_window)
    def set_phase2(self):
        try:
            xbmcgui.Window(10000).setProperty('loading.phase', '2')
            xbmcgui.Window(10000).setProperty('loading.phase2', 'true')
        except Exception:
            pass
    def close(self, max_wait=20.0, confirm_secs=0.4):
        with self._lock:
            window = self._window
            self._window = None
            gen = self._generation
        if window is None:
            return
        self._monitor.wait_until_playing(max_wait=max_wait, confirm_secs=confirm_secs)
        with self._lock:
            if self._generation != gen:
                return
        self._do_dismiss(window)
    def force_close(self):
        with self._lock:
            window = self._window
            self._window = None
            self._generation += 1
        self._monitor.cancel()
        self._do_dismiss(window)
loading_manager = LoadingManager()
