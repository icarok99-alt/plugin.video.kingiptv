# -*- coding: utf-8 -*-

import threading
import xbmc


class LoadingManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.busy_stop = threading.Event()
        self.busy_thread = None

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


loading_manager = LoadingManager()
