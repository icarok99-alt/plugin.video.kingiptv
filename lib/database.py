# -*- coding: utf-8 -*-

import sqlite3
import threading
import xbmc
from contextlib import contextmanager
from datetime import datetime
from lib.db_manager import (
    find_myvideos_db,
    get_kodi_watched,
    get_kodi_watched_season,
    set_kodi_watched,
    set_kodi_unwatched,
    get_kodi_resume,
    save_kodi_resume,
    clear_kodi_resume,
    get_kodi_season_resumes,
    clear_kodi_video_cache,
)
TABLE_EPISODES_METADATA = 'kingiptv_episodes_metadata'
TABLE_SKIP_TIMESTAMPS = 'kingiptv_skip_timestamps'
init_lock = threading.Lock()
initialized_dbs = set()

def locate_myvideos_db(retries=10, delay=1.0):
    for attempt in range(retries):
        db_path = find_myvideos_db()
        if db_path:
            return db_path
        xbmc.sleep(int(delay * 1000))
    return None

class KingDatabase:
    def __init__(self):
        self.db_path = locate_myvideos_db()
        if not self.db_path:
            pass
        else:
            self.init_database()
    @contextmanager
    def get_connection(self):
        if not self.db_path:
            raise RuntimeError('MyVideos*.db do Kodi indisponível.')
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA busy_timeout=5000')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    def init_database(self):
        with init_lock:
            if self.db_path in initialized_dbs:
                return
            try:
                with self.get_connection() as conn:
                    cursor = conn.cursor()
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS {} (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            imdb_id TEXT NOT NULL,
                            season INTEGER NOT NULL,
                            episode INTEGER NOT NULL,
                            episode_title TEXT,
                            description TEXT,
                            thumbnail TEXT,
                            fanart TEXT,
                            serie_name TEXT,
                            original_name TEXT,
                            is_last_episode TEXT DEFAULT 'no',
                            created_at TEXT,
                            updated_at TEXT,
                            UNIQUE(imdb_id, season, episode)
                        )
                    '''.format(TABLE_EPISODES_METADATA))
                    cursor.execute(
                        'CREATE INDEX IF NOT EXISTS idx_kingiptv_metadata_imdb_season '
                        'ON {}(imdb_id, season)'.format(TABLE_EPISODES_METADATA)
                    )
                    cursor.execute('''
                        CREATE TABLE IF NOT EXISTS {} (
                            imdb_id TEXT NOT NULL,
                            season INTEGER NOT NULL,
                            episode INTEGER NOT NULL,
                            intro_start REAL,
                            intro_end REAL,
                            source TEXT DEFAULT 'introhater',
                            updated_at TEXT,
                            PRIMARY KEY (imdb_id, season, episode)
                        )
                    '''.format(TABLE_SKIP_TIMESTAMPS))
                    cursor.execute(
                        'CREATE INDEX IF NOT EXISTS idx_kingiptv_skip_imdb '
                        'ON {}(imdb_id)'.format(TABLE_SKIP_TIMESTAMPS)
                    )
                initialized_dbs.add(self.db_path)
            except Exception:
                pass
    def get_next_episode_metadata(self, imdb_id, current_season, current_episode):
        if not self.db_path:
            return None
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM {}
                    WHERE imdb_id = ? AND season = ? AND episode IN (?, ?)
                    ORDER BY episode
                '''.format(TABLE_EPISODES_METADATA),
                    (imdb_id, current_season, current_episode, current_episode + 1))
                rows = cursor.fetchall()
                if not rows:
                    return None
                current_ep = None
                next_ep = None
                for row in rows:
                    row_dict = dict(row)
                    if row_dict['episode'] == current_episode:
                        current_ep = row_dict
                    elif row_dict['episode'] == current_episode + 1:
                        next_ep = row_dict
                if current_ep and current_ep.get('is_last_episode') == 'yes':
                    return None
                return next_ep
        except Exception:
            return None
    def save_season_episodes(self, imdb_id, season, serie_name, original_name, episodes_data, last_episode_num=None):
        if not episodes_data or not self.db_path:
            return
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if last_episode_num is None:
            last_episode_num = max([int(ep[0]) for ep in episodes_data])
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                batch_data = []
                for episode_num, title, thumbnail, fanart, description in episodes_data:
                    episode_num = int(episode_num)
                    is_last = 'yes' if episode_num == last_episode_num else 'no'
                    batch_data.append((
                        imdb_id,
                        season,
                        episode_num,
                        title,
                        description,
                        thumbnail,
                        fanart,
                        serie_name,
                        original_name,
                        is_last,
                        now,
                        now
                    ))
                cursor.executemany('''
                    INSERT INTO {}
                    (imdb_id, season, episode, episode_title, description,
                     thumbnail, fanart, serie_name, original_name, is_last_episode,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(imdb_id, season, episode)
                    DO UPDATE SET
                        episode_title = excluded.episode_title,
                        description = excluded.description,
                        thumbnail = excluded.thumbnail,
                        fanart = excluded.fanart,
                        serie_name = excluded.serie_name,
                        original_name = excluded.original_name,
                        is_last_episode = excluded.is_last_episode,
                        updated_at = excluded.updated_at
                '''.format(TABLE_EPISODES_METADATA), batch_data)
        except Exception:
            pass
    def get_season_episodes(self, imdb_id, season):
        if not self.db_path:
            return []
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM {}
                    WHERE imdb_id = ? AND season = ?
                    ORDER BY episode
                '''.format(TABLE_EPISODES_METADATA), (imdb_id, season))
                return [dict(row) for row in cursor.fetchall()]
        except Exception:
            return []
    def get_episode_metadata(self, imdb_id, season, episode):
        if not self.db_path:
            return None
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT * FROM {}
                    WHERE imdb_id = ? AND season = ? AND episode = ?
                '''.format(TABLE_EPISODES_METADATA), (imdb_id, season, episode))
                row = cursor.fetchone()
                return dict(row) if row else None
        except Exception:
            return None
    def mark_watched(self, imdb_id, season, episode):
        set_kodi_watched(imdb_id, season, episode)
    def mark_unwatched(self, imdb_id, season, episode):
        set_kodi_unwatched(imdb_id, season, episode)
    def clear_video_cache(self):
        return clear_kodi_video_cache()
    def is_watched(self, imdb_id, season, episode):
        return get_kodi_watched(imdb_id, season, episode)
    def get_watched_in_season(self, imdb_id, season):
        return get_kodi_watched_season(imdb_id, season)
    def get_skip_timestamps(self, imdb_id, season, episode):
        if not self.db_path:
            return None
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute('''
                    SELECT intro_start, intro_end, source
                    FROM {}
                    WHERE imdb_id = ? AND season = ? AND episode = ?
                    ORDER BY CASE source WHEN 'manual' THEN 0 ELSE 1 END
                    LIMIT 1
                '''.format(TABLE_SKIP_TIMESTAMPS), (imdb_id, int(season), int(episode)))
                row = cursor.fetchone()
                if not row:
                    return None
                keys = ('intro_start', 'intro_end', 'source')
                result = {k: v for k, v in zip(keys, row) if v is not None}
                return result if len(result) > 1 else None
        except Exception:
            return None
    def save_skip_timestamps(self, imdb_id, season, episode,
                             intro_start=None, intro_end=None,
                             source='introhater'):
        if not self.db_path:
            return
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if source == 'manual':
                    cursor.execute('''
                        INSERT INTO {}
                            (imdb_id, season, episode, intro_start, intro_end, source, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'manual', ?)
                        ON CONFLICT(imdb_id, season, episode)
                        DO UPDATE SET
                            intro_start = COALESCE(excluded.intro_start, intro_start),
                            intro_end = COALESCE(excluded.intro_end, intro_end),
                            source = 'manual',
                            updated_at = excluded.updated_at
                    '''.format(TABLE_SKIP_TIMESTAMPS),
                        (imdb_id, int(season), int(episode), intro_start, intro_end, now))
                else:
                    cursor.execute('''
                        INSERT INTO {}
                            (imdb_id, season, episode, intro_start, intro_end, source, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'introhater', ?)
                        ON CONFLICT(imdb_id, season, episode)
                        DO UPDATE SET
                            intro_start = CASE WHEN source = 'manual' THEN intro_start
                                         ELSE COALESCE(excluded.intro_start, intro_start) END,
                            intro_end = CASE WHEN source = 'manual' THEN intro_end
                                        ELSE COALESCE(excluded.intro_end, intro_end) END,
                            source = CASE WHEN source = 'manual' THEN 'manual' ELSE 'introhater' END,
                            updated_at = CASE WHEN source = 'manual' THEN updated_at
                                         ELSE excluded.updated_at END
                    '''.format(TABLE_SKIP_TIMESTAMPS),
                        (imdb_id, int(season), int(episode), intro_start, intro_end, now))
        except Exception:
            pass
    def skip_timestamps_checked(self, imdb_id, season, episode):
        if not self.db_path:
            return False
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    'SELECT 1 FROM {} WHERE imdb_id = ? AND season = ? AND episode = ?'.format(TABLE_SKIP_TIMESTAMPS),
                    (imdb_id, int(season), int(episode))
                )
                return cursor.fetchone() is not None
        except Exception:
            return False
    def save_skip_timestamps_batch(self, imdb_id, season, episodes_data, source='introhater'):
        if not episodes_data or not self.db_path:
            return 0
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        batch_data = []
        for ep_data in episodes_data:
            episode = int(ep_data.get('episode', 0))
            if episode <= 0:
                continue
            batch_data.append((
                imdb_id,
                int(season),
                episode,
                ep_data.get('intro_start'),
                ep_data.get('intro_end'),
                now
            ))
        if not batch_data:
            return 0
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                if source == 'manual':
                    cursor.executemany('''
                        INSERT INTO {}
                            (imdb_id, season, episode, intro_start, intro_end, source, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'manual', ?)
                        ON CONFLICT(imdb_id, season, episode)
                        DO UPDATE SET
                            intro_start = COALESCE(excluded.intro_start, intro_start),
                            intro_end = COALESCE(excluded.intro_end, intro_end),
                            source = 'manual',
                            updated_at = excluded.updated_at
                    '''.format(TABLE_SKIP_TIMESTAMPS), batch_data)
                else:
                    cursor.executemany('''
                        INSERT INTO {}
                            (imdb_id, season, episode, intro_start, intro_end, source, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'introhater', ?)
                        ON CONFLICT(imdb_id, season, episode)
                        DO UPDATE SET
                            intro_start = CASE WHEN source = 'manual' THEN intro_start
                                         ELSE COALESCE(excluded.intro_start, intro_start) END,
                            intro_end = CASE WHEN source = 'manual' THEN intro_end
                                        ELSE COALESCE(excluded.intro_end, intro_end) END,
                            source = CASE WHEN source = 'manual' THEN 'manual' ELSE 'introhater' END,
                            updated_at = CASE WHEN source = 'manual' THEN updated_at
                                         ELSE excluded.updated_at END
                    '''.format(TABLE_SKIP_TIMESTAMPS), batch_data)
        except Exception:
            return 0
        return len(batch_data)
    def save_resume_time(self, imdb_id, season, episode, position, total_time=0.0):
        save_kodi_resume(imdb_id, season, episode, float(position), float(total_time))
    def get_resume_time(self, imdb_id, season, episode):
        return get_kodi_resume(imdb_id, season, episode)
    def clear_resume_time(self, imdb_id, season, episode):
        clear_kodi_resume(imdb_id, season, episode)
    def get_season_resume_times(self, imdb_id, season):
        return get_kodi_season_resumes(imdb_id, season)
