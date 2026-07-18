import time
import threading
import json
import xbmc
import xbmcgui
import xbmcaddon
from lib.db_manager import get_db, get_thumb_path, is_thumb_cached
import requests

addon = xbmcaddon.Addon()
INTROHATER_URL = 'https://introhater.com/api/v1/segments/'
API_KEY = 'fdabaadfd236074b11787e0a0bda805baf91b881f86c28223c57944d7c3bf112'


class UpNextDialog(xbmcgui.WindowXMLDialog):
    BUTTON_PLAY_NOW = 3001
    BUTTON_CANCEL = 3002
    LABEL_NEXT_EPISODE = 3003
    IMAGE_THUMBNAIL = 3004
    PROGRESS_BAR = 3005

    def __init__(self, *args, **kwargs):
        self.next_episode_info = kwargs.get('next_episode_info', {})
        self.countdown_seconds = kwargs.get('countdown_seconds', 10)
        self.auto_play = False
        self.cancelled = False
        self.stop_countdown = False
        self.player = xbmc.Player()

    def do_advance(self):
        try:
            total_time = self.player.getTotalTime()
            if total_time > 1.0:
                try:
                    self.player.seekTime(total_time - 0.5)
                except Exception:
                    self.player.stop()
            else:
                self.player.stop()
        except Exception:
            pass

    def onInit(self):
        try:
            next_season = self.next_episode_info.get('next_season', 0)
            next_episode = self.next_episode_info.get('next_episode', 0)
            episode_title = self.next_episode_info.get('episode_title', '')
            if episode_title:
                next_text = '{}x{:02d} - {}'.format(next_season, next_episode, episode_title)
            else:
                next_text = '{}x{:02d}'.format(next_season, next_episode)
            self.getControl(self.LABEL_NEXT_EPISODE).setLabel(next_text)
            thumbnail = self.next_episode_info.get('thumbnail', '')
            if thumbnail:
                self.getControl(self.IMAGE_THUMBNAIL).setImage(thumbnail)
            try:
                self.setFocusId(self.BUTTON_PLAY_NOW)
            except Exception:
                pass
            self.start_countdown()
        except Exception:
            pass

    def start_countdown(self):
        self.stop_countdown = False
        threading.Thread(target=self.countdown_loop, daemon=True).start()

    def countdown_loop(self):
        remaining = self.countdown_seconds
        while remaining > 0 and not self.stop_countdown:
            try:
                progress = int((remaining / float(self.countdown_seconds)) * 100)
                self.getControl(self.PROGRESS_BAR).setPercent(progress)
                self.getControl(self.BUTTON_PLAY_NOW).setLabel('Reproduzir ({0}s)'.format(remaining))
                time.sleep(1)
                remaining -= 1
            except Exception:
                break
        if not self.stop_countdown and remaining == 0:
            self.auto_play = True
            self.do_advance()
            self.close()

    def onClick(self, controlId):
        if controlId == self.BUTTON_PLAY_NOW:
            self.auto_play = True
            self.stop_countdown = True
            self.do_advance()
            self.close()
        elif controlId == self.BUTTON_CANCEL:
            self.cancelled = True
            self.stop_countdown = True
            self.close()

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (xbmcgui.ACTION_SELECT_ITEM, xbmcgui.ACTION_PLAYER_PLAY):
            try:
                focused = self.getFocusId()
                if focused == self.BUTTON_PLAY_NOW:
                    self.auto_play = True
                    self.stop_countdown = True
                    self.do_advance()
                    self.close()
                elif focused == self.BUTTON_CANCEL:
                    self.cancelled = True
                    self.stop_countdown = True
                    self.close()
            except Exception:
                pass
        elif action_id in (
            xbmcgui.ACTION_NAV_BACK,
            xbmcgui.ACTION_PREVIOUS_MENU,
            xbmcgui.ACTION_STOP,
        ):
            self.cancelled = True
            self.stop_countdown = True
            self.close()


class SkipDialog(xbmcgui.WindowXMLDialog):
    BUTTON_SKIP = 4001
    BUTTON_CANCEL = 4002
    PROGRESS_BAR = 4004
    LABEL_TAG = 4005
    LABEL_EP = 4006
    IMAGE_THUMB = 4007

    def __init__(self, *args, **kwargs):
        self.seek_to = kwargs.get('seek_to', 0.0)
        self.countdown_seconds = kwargs.get('countdown_seconds', 5)
        self.episode_label = kwargs.get('episode_label', '')
        self.thumbnail = kwargs.get('thumbnail', '')
        self.stop_countdown = False
        self.player = xbmc.Player()

    def do_seek(self):
        try:
            total_time = self.player.getTotalTime()
            if total_time > 0 and self.seek_to < total_time - 0.5:
                self.player.seekTime(self.seek_to)
            elif total_time > 0:
                self.player.seekTime(total_time - 0.5)
        except Exception:
            pass

    def onInit(self):
        try:
            self.getControl(self.BUTTON_SKIP).setLabel(
                'Pular abertura ({}s)'.format(self.countdown_seconds)
            )
            if self.episode_label:
                self.getControl(self.LABEL_EP).setLabel(self.episode_label)
            if self.thumbnail:
                self.getControl(self.IMAGE_THUMB).setImage(self.thumbnail)
            try:
                self.setFocusId(self.BUTTON_SKIP)
            except Exception:
                pass
            self.start_countdown()
        except Exception:
            pass

    def start_countdown(self):
        self.stop_countdown = False
        threading.Thread(target=self.countdown_loop, daemon=True).start()

    def countdown_loop(self):
        remaining = self.countdown_seconds
        while remaining > 0 and not self.stop_countdown:
            try:
                progress = int((remaining / float(self.countdown_seconds)) * 100)
                self.getControl(self.PROGRESS_BAR).setPercent(progress)
                self.getControl(self.BUTTON_SKIP).setLabel('Pular abertura ({}s)'.format(remaining))
            except Exception:
                break
            time.sleep(1)
            remaining -= 1
        if not self.stop_countdown and remaining == 0:
            self.do_seek()
            self.close()

    def onClick(self, controlId):
        if controlId == self.BUTTON_SKIP:
            self.stop_countdown = True
            self.do_seek()
            self.close()
        elif controlId == self.BUTTON_CANCEL:
            self.stop_countdown = True
            self.close()

    def onAction(self, action):
        action_id = action.getId()
        if action_id in (xbmcgui.ACTION_SELECT_ITEM, xbmcgui.ACTION_PLAYER_PLAY):
            try:
                focused = self.getFocusId()
                if focused == self.BUTTON_SKIP:
                    self.stop_countdown = True
                    self.do_seek()
                    self.close()
                elif focused == self.BUTTON_CANCEL:
                    self.stop_countdown = True
                    self.close()
            except Exception:
                pass
        elif action_id in (
            xbmcgui.ACTION_NAV_BACK,
            xbmcgui.ACTION_PREVIOUS_MENU,
            xbmcgui.ACTION_STOP,
        ):
            self.stop_countdown = True
            self.close()


class SkipService:
    def __init__(self, database):
        self.db = database
        addon = xbmcaddon.Addon()
        self.enabled = self.get_bool(addon, 'skip_intro_enabled', True)
        self.auto_skip = self.get_bool(addon, 'skip_auto_skip', False)
        self.countdown_seconds = self.get_int(addon, 'skip_countdown_seconds', 5)
        self.tolerance = 2.0
        self.prefetched_seasons = set()
        self.prefetch_running = set()
        self.lock = threading.Lock()

    @staticmethod
    def get_bool(addon, key, default):
        try:
            return addon.getSettingBool(key)
        except Exception:
            val = addon.getSetting(key)
            return default if val == '' else val.lower() == 'true'

    @staticmethod
    def get_int(addon, key, default):
        try:
            v = addon.getSettingInt(key)
            return v if v > 0 else default
        except Exception:
            try:
                return int(addon.getSetting(key)) or default
            except Exception:
                return default

    def load(self, imdb_id, season, episode):
        if not self.enabled:
            return {}
        skip_info = self.resolve_timestamps(imdb_id, season, episode)
        if skip_info:
            ep_label, thumbnail = self.resolve_episode_info(imdb_id, season, episode)
            skip_info['ep_label'] = ep_label
            skip_info['thumbnail'] = thumbnail
        return skip_info or {}

    def show_dialog(self, seek_to, episode_label='', thumbnail=''):
        try:
            addon = xbmcaddon.Addon()
            dialog = SkipDialog(
                'skip-dialog.xml',
                addon.getAddonInfo('path'),
                'default',
                '1080i',
                seek_to=seek_to,
                countdown_seconds=self.countdown_seconds,
                episode_label=episode_label,
                thumbnail=thumbnail,
            )
            dialog.doModal()
            del dialog
        except Exception:
            pass

    def prefetch_season(self, imdb_id, season):
        try:
            episodes = self.db.get_season_episodes(imdb_id, season)
            episode_count = len(episodes) if episodes else 0
            self.prefetch_skip_timestamps(imdb_id, season, episode_count)
        except Exception:
            pass

    def resolve_episode_info(self, imdb_id, season, episode):
        try:
            meta = self.db.get_episode_metadata(imdb_id, int(season), int(episode))
            if meta:
                title = meta.get('episode_title') or ''
                thumbnail = meta.get('thumbnail') or ''
                ep_label = (
                    '{}x{:02d} - {}'.format(int(season), int(episode), title)
                    if title
                    else '{}x{:02d}'.format(int(season), int(episode))
                )
                return ep_label, thumbnail
        except Exception:
            pass
        return '', ''

    def resolve_timestamps(self, imdb_id, season, episode):
        try:
            timestamps = self.db.get_skip_timestamps(imdb_id, season, episode)
            if timestamps:
                return timestamps
            video_id = f"{imdb_id}:{season}:{episode}"
            url = f"{INTROHATER_URL}{imdb_id}"
            try:
                response = requests.get(url, headers={"X-API-Key": API_KEY}, timeout=6)
                if response.status_code != 200:
                    return {}
            except requests.exceptions.Timeout:
                return {}
            except Exception:
                return {}
            data = response.json()
            timestamps = {}
            intro_segments = [
                seg
                for seg in data
                if seg.get('label') == 'Intro'
                and seg.get('verified')
                and seg.get('videoId') == video_id
            ]
            if intro_segments:
                intro_segments.sort(key=lambda s: s.get('votes', 0), reverse=True)
                seg = intro_segments[0]
                timestamps['intro_start'] = float(seg.get('start', 0))
                timestamps['intro_end'] = float(seg.get('end', 0))
            if timestamps:
                timestamps['source'] = 'introhater'
                self.db.save_skip_timestamps(imdb_id, season, episode, **timestamps)
                return timestamps
        except Exception:
            pass
        return {}

    def prefetch_skip_timestamps(self, imdb_id, season, episode_count):
        if not imdb_id or not season:
            return
        key = (imdb_id, int(season))
        with self.lock:
            if key in self.prefetched_seasons or key in self.prefetch_running:
                return
            self.prefetch_running.add(key)

        def worker():
            try:
                url = f"{INTROHATER_URL}{imdb_id}"
                headers = {"X-API-Key": API_KEY}
                for attempt in range(3):
                    try:
                        response = requests.get(url, headers=headers, timeout=8)
                        if response.status_code == 429:
                            time.sleep(10 * (attempt + 1))
                            continue
                        if response.status_code == 200:
                            data = response.json()
                            break
                    except Exception:
                        time.sleep(2)
                else:
                    return
                episodes_data = {}
                for seg in data:
                    if seg.get('label') != 'Intro' or not seg.get('verified'):
                        continue
                    video_id = seg.get('videoId', '')
                    if not video_id.startswith(imdb_id + ':'):
                        continue
                    parts = video_id.split(':')
                    if len(parts) != 3:
                        continue
                    _, seg_season, seg_episode = parts
                    if (
                        not seg_season.lstrip('-').isdigit()
                        or not seg_episode.lstrip('-').isdigit()
                    ):
                        continue
                    seg_season = int(seg_season)
                    seg_episode = int(seg_episode)
                    if seg_season != season:
                        continue
                    key_ep = (seg_season, seg_episode)
                    if key_ep not in episodes_data:
                        episodes_data[key_ep] = []
                    episodes_data[key_ep].append(seg)
                batch_save = []
                for (s, ep), segs in episodes_data.items():
                    if segs:
                        segs.sort(key=lambda seg: seg.get('votes', 0), reverse=True)
                        best_seg = segs[0]
                        batch_save.append(
                            {
                                'episode': ep,
                                'intro_start': float(best_seg.get('start', 0)),
                                'intro_end': float(best_seg.get('end', 0)),
                            }
                        )
                if batch_save:
                    self.db.save_skip_timestamps_batch(
                        imdb_id, season, batch_save, source='introhater'
                    )
                with self.lock:
                    self.prefetched_seasons.add(key)
            finally:
                with self.lock:
                    self.prefetch_running.discard(key)

        threading.Thread(target=worker, daemon=True).start()


class UpNextService:
    def __init__(self, database):
        self.db = database
        addon = xbmcaddon.Addon()
        self.enabled = self.get_bool(addon, 'upnext_enabled', True)
        self.countdown_seconds = self.get_int(addon, 'upnext_countdown_seconds', 10)
        self.trigger_seconds = self.get_int(addon, 'upnext_trigger_seconds', 30)

    @staticmethod
    def get_bool(addon, key, default):
        try:
            return addon.getSettingBool(key)
        except Exception:
            val = addon.getSetting(key)
            return default if val == '' else val.lower() == 'true'

    @staticmethod
    def get_int(addon, key, default):
        try:
            v = addon.getSettingInt(key)
            return v if v > 0 else default
        except Exception:
            try:
                return int(addon.getSetting(key)) or default
            except Exception:
                return default

    def load(self, imdb_id, season, episode):
        if not self.enabled:
            return None
        next_info = None
        playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
        if playlist.size() > 0 and playlist.getposition() < (playlist.size() - 1):
            next_info = self.get_next_from_playlist()
        if not next_info or not next_info.get('next_season'):
            meta = self.db.get_next_episode_metadata(imdb_id, season, episode)
            if meta:
                next_info = {
                    'imdb_id': imdb_id,
                    'serie_name': meta.get('serie_name', ''),
                    'original_name': meta.get('original_name', ''),
                    'next_season': meta.get('season'),
                    'next_episode': meta.get('episode'),
                    'episode_title': meta.get('episode_title', ''),
                    'thumbnail': meta.get('thumbnail', ''),
                    'fanart': meta.get('fanart', ''),
                    'description': meta.get('description', ''),
                }
        if next_info and next_info.get('thumbnail'):
            cached = get_thumb_path(next_info['thumbnail'])
            if cached:
                next_info['thumbnail'] = cached
        return next_info

    def show_dialog(self, next_info):
        try:
            addon = xbmcaddon.Addon()
            dialog = UpNextDialog(
                'upnext-dialog.xml',
                addon.getAddonInfo('path'),
                'default',
                '1080i',
                next_episode_info=next_info,
                countdown_seconds=self.countdown_seconds,
            )
            dialog.doModal()
            del dialog
        except Exception:
            pass

    def parse_episode_format(self, text):
        import re

        if not text:
            return None, None, None
        match = re.match(r'^(\d+)x(\d+)\s*(.*)', text)
        if match:
            return int(match.group(1)), int(match.group(2)), match.group(3).strip()
        return None, None, None

    def get_next_from_playlist(self):
        try:
            playlist = xbmc.PlayList(xbmc.PLAYLIST_VIDEO)
            current_position = playlist.getposition()
            if current_position >= (playlist.size() - 1):
                return None
            next_item = playlist[current_position + 1]
            if hasattr(next_item, 'getVideoInfoTag'):
                info_tag = next_item.getVideoInfoTag()
                return {
                    'serie_name': (
                        info_tag.getTVShowTitle() if hasattr(info_tag, 'getTVShowTitle') else ''
                    ),
                    'original_name': (
                        info_tag.getOriginalTitle() if hasattr(info_tag, 'getOriginalTitle') else ''
                    ),
                    'next_season': info_tag.getSeason() if hasattr(info_tag, 'getSeason') else 0,
                    'next_episode': info_tag.getEpisode() if hasattr(info_tag, 'getEpisode') else 0,
                    'episode_title': info_tag.getTitle() if hasattr(info_tag, 'getTitle') else '',
                    'thumbnail': next_item.getArt('thumb'),
                    'fanart': next_item.getArt('fanart'),
                    'description': info_tag.getPlot() if hasattr(info_tag, 'getPlot') else '',
                }
            else:
                label = next_item.getLabel()
                season, episode, episode_title = self.parse_episode_format(label)
                return {
                    'serie_name': '',
                    'next_season': season or 0,
                    'next_episode': episode or 0,
                    'episode_title': episode_title or label,
                    'thumbnail': next_item.getArt('thumb'),
                    'fanart': next_item.getArt('fanart'),
                    'description': '',
                }
        except Exception:
            return None


class PlaybackMonitor:
    def __init__(self):
        self.monitor = xbmc.Monitor()
        self.player = xbmc.Player()
        self.db = get_db()
        self.skip_service = SkipService(self.db)
        self.upnext_service = UpNextService(self.db)
        self.stop = False
        self.lock = threading.Lock()
        self.session = 0
        self.stop_event = threading.Event()
        self.stop_event.set()
        self.last_ep_key = None
        self.current_ep = None
        self.resume_time = None
        self.total_time = 0.0
        self.last_time = 0.0
        self.skip_data = {}
        self.next_info = None
        self.skip_loaded = False
        self.next_loaded = False
        self.skip_shown = False
        self.upnext_shown = False
        self.watched_marked = False
        self.semaphore = threading.Semaphore(2)

    def prefetch_thumbnails(self, imdb_id, season, episode):
        meta = self.db.get_episode_metadata(imdb_id, season, episode)
        if meta:
            url = meta.get('thumbnail')
            if url and not is_thumb_cached(url):
                get_thumb_path(url)
        next_meta = self.db.get_next_episode_metadata(imdb_id, season, episode)
        if next_meta:
            url = next_meta.get('thumbnail')
            if url and not is_thumb_cached(url):
                get_thumb_path(url)

    def check_episode_update(self):
        win = xbmcgui.Window(10000)
        prop = win.getProperty('kingiptv_episode')
        if not prop:
            return
        try:
            data = json.loads(prop)
            imdb_id = data.get('imdb_id')
            season = data.get('season')
            episode = data.get('episode')
            resume_time = data.get('resume_time')
            if imdb_id is None or season is None or episode is None:
                return
            new_ep = (imdb_id, season, episode)
            with self.lock:
                if new_ep != self.last_ep_key:
                    self.last_ep_key = new_ep
                    self.current_ep = {'imdb_id': imdb_id, 'season': season, 'episode': episode}
                    self.resume_time = resume_time
                    self.total_time = 0.0
                    self.skip_shown = False
                    self.upnext_shown = False
                    self.watched_marked = False
                    self.skip_data = {}
                    self.next_info = None
                    self.skip_loaded = False
                    self.next_loaded = False
                    self.session += 1
                    self.load_episode_data_async(imdb_id, season, episode)
                    threading.Thread(
                        target=self.prefetch_thumbnails,
                        args=(imdb_id, season, episode),
                        daemon=True,
                    ).start()
                else:
                    if resume_time is not None and resume_time != self.resume_time:
                        self.resume_time = resume_time
        except Exception:
            pass

    def load_episode_data_async(self, imdb_id, season, episode):
        def worker():
            skip = self.skip_service.load(imdb_id, season, episode)
            next_info = self.upnext_service.load(imdb_id, season, episode)
            with self.lock:
                self.skip_data = skip
                self.skip_loaded = True
                self.next_info = next_info
                self.next_loaded = True
            if next_info and next_info.get('next_season'):
                next_season = next_info['next_season']
                next_ep = next_info.get('next_episode', 1)
                self.prefetch_season_async(imdb_id, next_season, next_ep)

        threading.Thread(target=worker, daemon=True).start()

    def prefetch_season_async(self, imdb_id, season, episode):
        def worker():
            with self.semaphore:
                self.skip_service.prefetch_season(imdb_id, season)
                self.prefetch_thumbnails(imdb_id, season, episode)

        threading.Thread(target=worker, daemon=True).start()

    def wait_for_total_time(self, timeout=10):
        for _ in range(int(timeout / 0.5)):
            if not self.player.isPlayingVideo():
                return 0
            total = self.player.getTotalTime()
            if total > 60:
                return total
            self.monitor.waitForAbort(0.5)
        return 0

    def handle_playback(self):
        with self.lock:
            ep = self.current_ep
            if ep is None:
                return
            imdb_id = ep['imdb_id']
            season = ep['season']
            episode = ep['episode']
            session = self.session
            resume_time = self.resume_time
            skip_data = self.skip_data if self.skip_loaded else {}
            next_info = self.next_info if self.next_loaded else None
        total_time = self.wait_for_total_time()
        if total_time <= 60:
            return
        with self.lock:
            if self.session != session:
                return
            self.total_time = total_time
        trigger_seconds = min(self.upnext_service.trigger_seconds, total_time * 0.9)
        trigger_seconds = max(trigger_seconds, 5.0)
        if resume_time and 0 < resume_time < total_time * 0.85:
            xbmc.sleep(500)
            try:
                self.player.seekTime(resume_time)
                with self.lock:
                    self.resume_time = None
            except Exception:
                pass
        intro_start = skip_data.get('intro_start')
        intro_end = skip_data.get('intro_end')
        watched_at = min(total_time * 0.90, max(total_time - trigger_seconds, total_time * 0.02))
        while self.player.isPlayingVideo() and not self.stop and not self.monitor.abortRequested():
            if not self.stop_event.is_set():
                return
            with self.lock:
                if self.session != session:
                    return
                if self.skip_loaded:
                    skip_data = self.skip_data
                    intro_start = skip_data.get('intro_start')
                    intro_end = skip_data.get('intro_end')
                if self.next_loaded:
                    next_info = self.next_info
            try:
                current_time = self.player.getTime()
            except Exception:
                current_time = 0
            with self.lock:
                self.last_time = current_time
            if not self.skip_shown and intro_start is not None and intro_end is not None:
                if (intro_start - self.skip_service.tolerance) <= current_time <= intro_end:
                    self.skip_shown = True
                    if self.skip_service.auto_skip:
                        try:
                            seek_target = (
                                intro_end if intro_end < total_time - 0.5 else total_time - 0.5
                            )
                            self.player.seekTime(seek_target)
                        except Exception:
                            pass
                    else:
                        threading.Thread(
                            target=self.skip_service.show_dialog,
                            args=(
                                intro_end,
                                skip_data.get('ep_label', ''),
                                skip_data.get('thumbnail', ''),
                            ),
                            daemon=True,
                        ).start()
            if not self.watched_marked and current_time >= watched_at:
                self.watched_marked = True
                threading.Thread(
                    target=self.db.mark_watched, args=(imdb_id, season, episode), daemon=True
                ).start()
                threading.Thread(
                    target=self.db.clear_resume_time, args=(imdb_id, season, episode), daemon=True
                ).start()
            if (
                not self.upnext_shown
                and next_info
                and (total_time - current_time) <= trigger_seconds
                and current_time > 30.0
            ):
                self.upnext_shown = True
                threading.Thread(
                    target=self.upnext_service.show_dialog, args=(next_info,), daemon=True
                ).start()
            self.monitor.waitForAbort(0.5)
        try:
            final_time = self.player.getTime()
            if final_time <= 0:
                final_time = self.last_time
        except Exception:
            final_time = self.last_time
        if not self.watched_marked and total_time > 60:
            ratio = final_time / total_time if total_time > 0 else 0
            if 0.02 < ratio < 0.85:
                self.db.save_resume_time(imdb_id, season, episode, final_time, total_time)
            else:
                self.db.clear_resume_time(imdb_id, season, episode)

    def stop(self):
        self.stop = True

    def run(self):
        while not self.stop and not self.monitor.abortRequested():
            self.check_episode_update()
            if self.player.isPlayingVideo():
                self.handle_playback()
            self.monitor.waitForAbort(0.5)


def wait_for_series_category(kodi_monitor):
    win = xbmcgui.Window(10000)
    while not kodi_monitor.abortRequested():
        if win.getProperty('kingiptv_episode'):
            return True
        if kodi_monitor.waitForAbort(1.0):
            return False
    return False


if __name__ == '__main__':
    kodi_monitor = xbmc.Monitor()
    if wait_for_series_category(kodi_monitor):
        monitor = PlaybackMonitor()
        monitor.run()