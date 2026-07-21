# -*- coding: utf-8 -*-

import threading
import xbmc
from lib.nav_dialog import open_skin, open_list_playback

__all__ = ['open_skin', 'open_list_playback', 'LiveMonitor', 'BusySuppressor']


class LiveMonitor(xbmc.Player):
    def __init__(self):
        super(LiveMonitor, self).__init__()
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


class BusySuppressor(object):
    def __init__(self):
        self.stop_event = threading.Event()
        self.thread = None

    def run(self):
        while not self.stop_event.wait(0.1):
            try:
                xbmc.executebuiltin('Dialog.Close(busydialog,true)')
                xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
            except Exception:
                pass

    def start(self):
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=0.5)
        try:
            xbmc.executebuiltin('Dialog.Close(busydialog,true)')
            xbmc.executebuiltin('Dialog.Close(busydialognocancel,true)')
        except Exception:
            pass
