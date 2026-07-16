# -*- coding: utf-8 -*-

import os
import threading
import time
import xbmc
import xbmcaddon
import xbmcgui


class PlaybackMonitor(xbmc.Player):
    def __init__(self):
        super().__init__()
        self.av_ready = threading.Event()
        self.failed = threading.Event()
        self.cancelled = threading.Event()
        self.av_fired = False

    def onAVStarted(self):
        if not self.av_fired:
            self.av_fired = True
            self.av_ready.set()

    def onPlayBackError(self):
        if not self.av_fired:
            self.failed.set()

    def onPlayBackStopped(self):
        if not self.av_fired:
            self.failed.set()

    def onPlayBackFailed(self):
        if not self.av_fired:
            self.failed.set()

    def reset(self):
        self.av_ready.clear()
        self.failed.clear()
        self.cancelled.clear()
        self.av_fired = False

    def cancel(self):
        self.cancelled.set()

    def wait_until_playing(self, max_wait=20.0):
        kodi_monitor = xbmc.Monitor()
        deadline = time.monotonic() + max_wait
        poll_interval = 0.1

        while time.monotonic() < deadline:
            if self.cancelled.is_set() or self.failed.is_set():
                return False
            if kodi_monitor.abortRequested():
                return False
            if self.av_ready.is_set():
                return True
            try:
                if self.isPlaying() and self.getTime() > 0.0:
                    return True
            except Exception:
                pass
            kodi_monitor.waitForAbort(poll_interval)

        try:
            return self.av_ready.is_set() or (self.isPlaying() and self.getTime() > 0.0)
        except Exception:
            return self.av_ready.is_set()


class LoadingWindow(xbmcgui.WindowXMLDialog):
    PROGRESS_CONTROL = 100

    def __init__(self, *args, **kwargs):
        self.stop_anim = threading.Event()
        self.controls_ready = False
        self.anim_thread = None
        self._closed = False

    def onInit(self):
        self.controls_ready = True
        xbmcgui.Window(10000).setProperty('loading.phase', '1')
        xbmcgui.Window(10000).clearProperty('loading.phase2')
        self.start_animation()

    def start_animation(self):
        self.stop_anim.clear()
        self.anim_thread = threading.Thread(target=self.animate, daemon=True)
        self.anim_thread.start()

    def animate(self):
        try:
            while not self.stop_anim.is_set():
                for pct in range(0, 101, 2):
                    if self.stop_anim.is_set():
                        return
                    if self.controls_ready:
                        try:
                            self.getControl(self.PROGRESS_CONTROL).setPercent(pct)
                        except Exception:
                            pass
                    xbmcgui.Window(10000).setProperty('loading.progress', str(pct))
                    time.sleep(0.05)
                if not self.stop_anim.is_set():
                    time.sleep(0.1)
        except Exception:
            pass

    def dismiss(self):
        if self._closed:
            return
        self._closed = True
        self.stop_anim.set()
        if self.anim_thread and self.anim_thread.is_alive():
            self.anim_thread.join(timeout=0.5)

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
        self.lock = threading.Lock()
        self.window = None
        self.generation = 0
        self.monitor = PlaybackMonitor()
        self.busy_stop = threading.Event()
        self.busy_thread = None
        self._closing = False
        self.window_ready = threading.Event()

    def close_native_busy(self):
        try:
            xbmc.executebuiltin('Dialog.Close(busydialog,true)')
            xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
        except Exception:
            pass

    def run_busy_suppressor(self):
        while True:
            self.close_native_busy()
            if self.busy_stop.wait(0.03):
                break

    def start_busy_suppressor(self):
        with self.lock:
            if self.busy_thread is not None and self.busy_thread.is_alive():
                return
            self.busy_stop.clear()
            self.close_native_busy()
            self.busy_thread = threading.Thread(target=self.run_busy_suppressor, daemon=True)
            self.busy_thread.start()

    def stop_busy_suppressor(self):
        self.busy_stop.set()
        self.close_native_busy()

    def addon_path(self):
        return xbmcaddon.Addon().getAddonInfo('path')

    def default_fanart(self):
        return os.path.join(
            self.addon_path(), 'resources', 'skins', 'Default', 'media', 'fanart.jpg'
        )

    def do_dismiss(self, window):
        self.stop_busy_suppressor()
        if window is not None:
            try:
                window.dismiss()
            except Exception:
                pass

    def show(self, fanart_path=None, xml_filename='DialogLoadingLive.xml'):
        with self.lock:
            old_window = self.window
            self.window = None
            self.generation += 1
            current_gen = self.generation
            self._closing = False
            self.window_ready.clear()

        if old_window is not None:
            self.do_dismiss(old_window)

        self.monitor.cancel()
        self.monitor.reset()

        fanart = fanart_path or self.default_fanart()
        addon_path = self.addon_path()
        xbmcgui.Window(10000).setProperty('loading.fanart', fanart)

        self.start_busy_suppressor()

        new_window = LoadingWindow(
            xml_filename, addon_path, 'Default', '1080i'
        )
        new_window.show()
        xbmc.sleep(80)

        with self.lock:
            if self.generation == current_gen and not self._closing:
                self.window = new_window
                self.window_ready.set()
            else:
                self.do_dismiss(new_window)

        def force_close_after_timeout():
            time.sleep(30)
            with self.lock:
                if self.window is not None and self.generation == current_gen:
                    self.force_close()

        timer = threading.Thread(target=force_close_after_timeout, daemon=True)
        timer.start()

    def set_phase2(self):
        try:
            xbmcgui.Window(10000).setProperty('loading.phase', '2')
            xbmcgui.Window(10000).setProperty('loading.phase2', 'true')
        except Exception:
            pass

    def close(self, max_wait=20.0):
        if not self.window_ready.wait(timeout=1.5):
            pass

        with self.lock:
            window = self.window
            self.window = None
            gen = self.generation
            self._closing = True

        if window is None:
            return

        success = self.monitor.wait_until_playing(max_wait=max_wait)

        with self.lock:
            if self.generation != gen:
                return
        self.do_dismiss(window)

    def force_close(self):
        with self.lock:
            window = self.window
            self.window = None
            self.generation += 1
            self._closing = True
        self.monitor.cancel()
        self.do_dismiss(window)


loading_manager = LoadingManager()
