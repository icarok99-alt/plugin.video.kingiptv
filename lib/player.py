# -*- coding: utf-8 -*-

import xbmc
import threading
from lib.upnext import UpNextService
from lib.skipservice import SkipService
from lib.database import KingDatabase

db = KingDatabase()

RESUME_MIN_FRACTION = 0.02
RESUME_MAX_FRACTION = 0.85
WATCHED_FRACTION = 0.90
MIN_DURATION = 60

DURATION_WAIT_SECONDS = 30
POLL_INTERVAL = 0.5


def _log(message):
    try:
        xbmc.log('[KingIPTV][player] {}'.format(message), xbmc.LOGDEBUG)
    except Exception:
        pass


def _persist_watched(imdb_id, season, episode):
    try:
        db.mark_watched(imdb_id, season, episode)
        db.clear_resume_time(imdb_id, season, episode)
    except Exception as e:
        _log('falha ao marcar como assistido {} S{}E{}: {}'.format(imdb_id, season, episode, e))


def _persist_resume(imdb_id, season, episode, last_time, total_time):
    try:
        if RESUME_MIN_FRACTION < (last_time / total_time) < RESUME_MAX_FRACTION:
            db.save_resume_time(imdb_id, season, episode, last_time, total_time)
        else:
            db.clear_resume_time(imdb_id, season, episode)
    except Exception as e:
        _log('falha ao salvar resume {} S{}E{}: {}'.format(imdb_id, season, episode, e))


class KingPlayer(xbmc.Player):

    def __init__(self):
        super(KingPlayer, self).__init__()
        self.imdb_id = None
        self.season = None
        self.episode = None
        self._state_lock = threading.Lock()
        self._monitoring = False
        self._monitor_thread = None
        self._skip_service = SkipService(db)
        self._upnext_service = UpNextService(db)
        self._last_time = 0.0
        self._total_time = 0.0
        self._watched_marked = False
        self._session = 0

    def start_monitoring(self, imdb_id, season, episode, resume_time=None):
        with self._state_lock:
            self._monitoring = False
            self._session += 1
            my_session = self._session

        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=3.0)

        with self._state_lock:
            if self._session == my_session:
                self.imdb_id = imdb_id
                self.season = season
                self.episode = episode
                self._monitoring = True
                self._last_time = 0.0
                self._total_time = 0.0
                self._watched_marked = False

        self._monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(imdb_id, season, episode, my_session),
            kwargs={'resume_time': resume_time},
            daemon=True,
        )
        self._monitor_thread.start()

    def _is_current_session(self, session):
        with self._state_lock:
            return self._monitoring and self._session == session

    def mark_skip_point(self, point):
        with self._state_lock:
            imdb_id = self.imdb_id
            season = self.season
            episode = self.episode
        if imdb_id and season is not None and episode is not None:
            self._skip_service.save_skip_point(imdb_id, season, episode, point)

    def _mark_watched_now(self, imdb_id, season, episode, session):
        with self._state_lock:
            if self._session != session or self._watched_marked:
                return
            self._watched_marked = True
        threading.Thread(
            target=_persist_watched,
            args=(imdb_id, season, episode),
            daemon=True,
        ).start()

    def onPlayBackSeek(self, time, seekOffset):
        self._recheck_watched_after_seek()

    def onPlayBackSeekChapter(self, chapter):
        self._recheck_watched_after_seek()

    def _recheck_watched_after_seek(self):
        with self._state_lock:
            if not self._monitoring:
                return
            imdb_id, season, episode = self.imdb_id, self.season, self.episode
            total_time = self._total_time
            session = self._session
        if not imdb_id or total_time <= MIN_DURATION:
            return
        try:
            ct = self.getTime()
        except Exception:
            return
        with self._state_lock:
            if self._session != session:
                return
            self._last_time = ct
        if ct >= total_time * WATCHED_FRACTION:
            self._mark_watched_now(imdb_id, season, episode, session)

    def _monitoring_loop(self, imdb_id, season, episode, session, resume_time=None):
        monitor = xbmc.Monitor()

        waited = 0
        while waited < 30 and not monitor.abortRequested():
            if not self._is_current_session(session):
                return
            if self.isPlayingVideo():
                break
            monitor.waitForAbort(POLL_INTERVAL)
            waited += POLL_INTERVAL

        if not self.isPlayingVideo() or not self._is_current_session(session):
            with self._state_lock:
                if self._session == session:
                    self._monitoring = False
            return

        total_time = 0.0
        attempts = int(DURATION_WAIT_SECONDS / POLL_INTERVAL)
        for _ in range(attempts):
            if not self._is_current_session(session):
                return
            try:
                total_time = self.getTotalTime()
                if total_time > MIN_DURATION:
                    break
            except Exception:
                pass
            monitor.waitForAbort(POLL_INTERVAL)

        if total_time <= MIN_DURATION or not self._is_current_session(session):
            with self._state_lock:
                if self._session == session:
                    self._monitoring = False
            return

        with self._state_lock:
            if self._session != session:
                return
            self._total_time = total_time

        if resume_time and 0 < resume_time < total_time * RESUME_MAX_FRACTION:
            try:
                self.seekTime(float(resume_time))
            except Exception:
                pass

        skip_data = self._skip_service.load(imdb_id, season, episode)
        next_info = self._upnext_service.load(imdb_id, season, episode)

        trigger_seconds = self._upnext_service.trigger_seconds
        watched_at = min(
            total_time * WATCHED_FRACTION,
            max(total_time - trigger_seconds, total_time * RESUME_MIN_FRACTION)
        )
        upnext_start_at = min(watched_at, total_time - trigger_seconds - 30)

        skip_shown = False
        upnext_shown = False

        intro_start = skip_data.get('intro_start') if skip_data else None
        intro_end = skip_data.get('intro_end')   if skip_data else None

        while self.isPlayingVideo():
            if not self._is_current_session(session):
                break
            if monitor.abortRequested():
                break

            try:
                ct = self.getTime()
            except Exception:
                monitor.waitForAbort(POLL_INTERVAL)
                continue

            with self._state_lock:
                if self._session != session:
                    break
                self._last_time = ct

            if not skip_shown and intro_start is not None and intro_end is not None:
                if (intro_start - self._skip_service.tolerance) <= ct <= intro_end:
                    skip_shown = True
                    if self._skip_service.auto_skip:
                        try:
                            self.seekTime(intro_end)
                        except Exception:
                            pass
                    else:
                        threading.Thread(
                            target=self._skip_service.show_dialog,
                            args=(intro_end, skip_data.get('_ep_label', ''), skip_data.get('_thumbnail', '')),
                            daemon=True,
                        ).start()

            if not self._watched_marked and ct >= watched_at:
                self._mark_watched_now(imdb_id, season, episode, session)

            if ct < upnext_start_at:
                monitor.waitForAbort(POLL_INTERVAL)
                continue

            if not upnext_shown and next_info:
                if (total_time - ct) <= trigger_seconds:
                    upnext_shown = True
                    threading.Thread(
                        target=self._upnext_service.show_dialog,
                        args=(next_info,),
                        daemon=True,
                    ).start()

            monitor.waitForAbort(POLL_INTERVAL)

        with self._state_lock:
            if self._session != session:
                return
            watched = self._watched_marked
            last_time = self._last_time
            self._monitoring = False

        if not watched and total_time > MIN_DURATION:
            _persist_resume(imdb_id, season, episode, last_time, total_time)

    def _on_stop(self):
        with self._state_lock:
            self._monitoring = False

    def onPlayBackEnded(self):
        self._on_stop()

    def onPlayBackStopped(self):
        self._on_stop()

    def onPlayBackError(self):
        self._on_stop()


_global_player = None
_player_lock = threading.Lock()

def get_player():
    global _global_player
    with _player_lock:
        if _global_player is None:
            _global_player = KingPlayer()
        return _global_player
