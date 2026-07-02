import os
import re
import sys
import sqlite3
import threading
import xbmcvfs
from datetime import datetime
try:
    from urllib.parse import parse_qs as _parse_qs
except ImportError:
    from urlparse import parse_qs as _parse_qs
_textures_db_path = None
_textures_db_lock = threading.Lock()

def _find_textures_db():
    global _textures_db_path
    with _textures_db_lock:
        if _textures_db_path and os.path.exists(_textures_db_path):
            return _textures_db_path
        db_dir = xbmcvfs.translatePath('special://database/')
        try:
            _, files = xbmcvfs.listdir(db_dir)
            for fname in sorted(files, reverse=True):
                if fname.lower().startswith('textures') and fname.lower().endswith('.db'):
                    path = os.path.join(db_dir, fname)
                    if os.path.exists(path):
                        _textures_db_path = path
                        return path
        except Exception:
            pass
        return None

def _get_kodi_cached_thumb(url):
    db_path = _find_textures_db()
    if not db_path:
        return None
    try:
        import sqlite3
        uri = 'file:{}?mode=ro'.format(db_path.replace('\\', '/'))
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        cur = conn.cursor()
        cur.execute('SELECT cachedurl FROM texture WHERE url = ?', (url,))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            cached = xbmcvfs.translatePath('special://thumbnails/' + row[0])
            if os.path.exists(cached):
                return cached
    except Exception:
        pass
    return None
_myvideos_db_path = None
_myvideos_db_lock = threading.Lock()

def clear_kodi_video_cache():
    global _myvideos_db_path, _textures_db_path
    deleted_files = []
    errors = []
    db_dir = xbmcvfs.translatePath('special://database/')
    try:
        _, files = xbmcvfs.listdir(db_dir)
    except Exception as e:
        files = []
        errors.append(str(e))
    for fname in files:
        lower = fname.lower()
        if (lower.startswith('myvideos') or lower.startswith('textures')) and (
            lower.endswith('.db') or lower.endswith('.db-wal')
            or lower.endswith('.db-shm') or lower.endswith('.db-journal')
        ):
            path = os.path.join(db_dir, fname)
            try:
                if xbmcvfs.delete(path):
                    deleted_files.append(fname)
                else:
                    errors.append(fname)
            except Exception:
                errors.append(fname)
    thumbnails_cleared = False
    thumbs_dir = xbmcvfs.translatePath('special://thumbnails/')
    try:
        thumbnails_cleared = bool(xbmcvfs.rmdir(thumbs_dir, True))
    except Exception as e:
        errors.append(str(e))
    with _myvideos_db_lock:
        _myvideos_db_path = None
    with _textures_db_lock:
        _textures_db_path = None
    return deleted_files, thumbnails_cleared, errors

def _find_myvideos_db():
    global _myvideos_db_path
    with _myvideos_db_lock:
        if _myvideos_db_path and os.path.exists(_myvideos_db_path):
            return _myvideos_db_path
        db_dir = xbmcvfs.translatePath('special://database/')
        try:
            _, files = xbmcvfs.listdir(db_dir)
            def _db_num(name):
                m = re.search(r'(\d+)', name)
                return int(m.group(1)) if m else 0
            for fname in sorted(files, key=_db_num, reverse=True):
                if fname.lower().startswith('myvideos') and fname.lower().endswith('.db'):
                    path = os.path.join(db_dir, fname)
                    if os.path.exists(path):
                        _myvideos_db_path = path
                        return path
        except Exception:
            pass
        return None

def _parse_plugin_url(url):
    try:
        if '?' in url:
            qs = url.split('?', 1)[1]
        else:
            qs = url.rsplit('/', 1)[-1]
        return _parse_qs(qs, keep_blank_values=False)
    except Exception:
        return {}

def _parsed_identity(filename):
    params = _parse_plugin_url(filename)
    imdbnumber = params.get('imdbnumber', [None])[0]
    season_num = params.get('season_num', [None])[0]
    episode_num = params.get('episode_num', [None])[0]
    return imdbnumber, season_num, episode_num

def _find_episode_rows(cur, imdb_id, season, episode):
    cur.execute(
        'SELECT idFile, strFilename, playCount, dateAdded, lastPlayed FROM files '
        'WHERE strFilename LIKE ? '
        'ORDER BY dateAdded ASC, idFile ASC',
        ('%{}%'.format(imdb_id),)
    )
    matches = []
    for file_id, filename, playcount, date_added, last_played in cur.fetchall():
        imdbnumber, season_num, episode_num = _parsed_identity(filename)
        if imdbnumber is None or season_num is None or episode_num is None:
            continue
        if imdbnumber != str(imdb_id):
            continue
        try:
            if int(season_num) != int(season) or int(episode_num) != int(episode):
                continue
        except (TypeError, ValueError):
            continue
        matches.append((file_id, filename, playcount, date_added, last_played))
    return matches

def _pick_canonical(matches):
    if not matches:
        return None
    played_rows = [m for m in matches if m[4]]
    if played_rows:
        return max(played_rows, key=lambda m: m[4])
    return matches[-1]

def _get_kodi_file_id(imdb_id, season, episode):
    db_path = _find_myvideos_db()
    if not db_path:
        return None, None
    try:
        uri = 'file:{}?mode=ro'.format(db_path.replace('\\', '/'))
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        cur = conn.cursor()
        matches = _find_episode_rows(cur, imdb_id, season, episode)
        conn.close()
        canonical = _pick_canonical(matches)
        if canonical:
            return db_path, canonical[0]
    except Exception:
        pass
    return None, None

def get_kodi_resume(imdb_id, season, episode):
    db_path, file_id = _get_kodi_file_id(imdb_id, season, episode)
    if not file_id:
        return None
    try:
        uri = 'file:{}?mode=ro'.format(db_path.replace('\\', '/'))
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        cur = conn.cursor()
        cur.execute(
            'SELECT timeInSeconds, totalTimeInSeconds FROM bookmark '
            'WHERE idFile=? AND type=1 LIMIT 1',
            (file_id,)
        )
        row = cur.fetchone()
        conn.close()
        if row and row[0] and float(row[0]) > 0:
            return (float(row[0]), float(row[1] or 0))
    except Exception:
        pass
    return None

def save_kodi_resume(imdb_id, season, episode, position, total_time):
    db_path, file_id = _get_kodi_file_id(imdb_id, season, episode)
    if not file_id:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        cur.execute(
            'SELECT idBookmark FROM bookmark WHERE idFile=? AND type=1 LIMIT 1',
            (file_id,)
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                'UPDATE bookmark SET timeInSeconds=?, totalTimeInSeconds=? WHERE idBookmark=?',
                (position, total_time, row[0])
            )
        else:
            cur.execute(
                'INSERT INTO bookmark '
                '(idFile, timeInSeconds, totalTimeInSeconds, thumbNailImage, player, playerState, type) '
                'VALUES (?,?,?,?,?,?,1)',
                (file_id, position, total_time, '', 'VideoPlayer', '')
            )
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def clear_kodi_resume(imdb_id, season, episode):
    db_path, file_id = _get_kodi_file_id(imdb_id, season, episode)
    if not file_id:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        cur.execute('DELETE FROM bookmark WHERE idFile=? AND type=1', (file_id,))
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def get_kodi_watched(imdb_id, season, episode):
    db_path = _find_myvideos_db()
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        matches = _find_episode_rows(cur, imdb_id, season, episode)
        canonical = _pick_canonical(matches)
        if not canonical:
            conn.close()
            return False
        canonical_watched = bool(canonical[2] and int(canonical[2]) > 0)
        _propagate_playcount(cur, matches, canonical_watched)
        conn.commit()
        conn.close()
        return canonical_watched
    except Exception:
        return False

def _propagate_playcount(cur, matches, watched):
    target = 1 if watched else 0
    for file_id, _filename, playcount, _date_added, _last_played in matches:
        current = int(playcount) if playcount else 0
        if (current > 0) != bool(watched):
            if watched:
                cur.execute(
                    'UPDATE files SET playCount = ?, lastPlayed = ? WHERE idFile = ?',
                    (target, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), file_id)
                )
            else:
                cur.execute('UPDATE files SET playCount = ? WHERE idFile = ?', (target, file_id))

def set_kodi_watched_state(imdb_id, season, episode, watched):
    db_path = _find_myvideos_db()
    if not db_path:
        return False
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        matches = _find_episode_rows(cur, imdb_id, season, episode)
        if not matches:
            conn.close()
            return False
        _propagate_playcount(cur, matches, watched)
        conn.commit()
        conn.close()
        return True
    except Exception:
        return False

def set_kodi_watched(imdb_id, season, episode):
    return set_kodi_watched_state(imdb_id, season, episode, True)

def set_kodi_unwatched(imdb_id, season, episode):
    return set_kodi_watched_state(imdb_id, season, episode, False)

def get_kodi_watched_season(imdb_id, season):
    db_path = _find_myvideos_db()
    if not db_path:
        return set()
    try:
        conn = sqlite3.connect(db_path, timeout=5)
        cur = conn.cursor()
        cur.execute(
            'SELECT idFile, strFilename, playCount, dateAdded, lastPlayed FROM files '
            'WHERE strFilename LIKE ? '
            'ORDER BY dateAdded ASC, idFile ASC',
            ('%{}%'.format(imdb_id),)
        )
        rows = cur.fetchall()
        per_episode_rows = {}
        for file_id, filename, playcount, date_added, last_played in rows:
            imdbnumber, season_num, episode_num = _parsed_identity(filename)
            if imdbnumber != str(imdb_id) or season_num is None or episode_num is None:
                continue
            try:
                if int(season_num) != int(season):
                    continue
                ep = int(episode_num)
            except (TypeError, ValueError):
                continue
            per_episode_rows.setdefault(ep, []).append(
                (file_id, filename, playcount, date_added, last_played)
            )
        watched = set()
        for ep, matches in per_episode_rows.items():
            canonical = _pick_canonical(matches)
            canonical_watched = bool(canonical[2] and int(canonical[2]) > 0)
            _propagate_playcount(cur, matches, canonical_watched)
            if canonical_watched:
                watched.add(ep)
        conn.commit()
        conn.close()
        return watched
    except Exception:
        return set()

def get_kodi_season_resumes(imdb_id, season):
    db_path = _find_myvideos_db()
    if not db_path:
        return {}
    try:
        uri = 'file:{}?mode=ro'.format(db_path.replace('\\', '/'))
        conn = sqlite3.connect(uri, uri=True, timeout=3)
        cur = conn.cursor()
        cur.execute('''
            SELECT f.strFilename, b.timeInSeconds, b.totalTimeInSeconds, f.dateAdded
            FROM files f
            JOIN bookmark b ON b.idFile = f.idFile AND b.type = 1
            WHERE f.strFilename LIKE ?
            ORDER BY f.dateAdded ASC, f.idFile ASC
        ''', ('%{}%'.format(imdb_id),))
        rows = cur.fetchall()
        conn.close()
        resumes = {}
        for filename, time_s, total_s, _date_added in rows:
            imdbnumber, season_num, episode_num = _parsed_identity(filename)
            if imdbnumber != str(imdb_id) or season_num is None or episode_num is None:
                continue
            try:
                if int(season_num) != int(season):
                    continue
                ep = int(episode_num)
            except (TypeError, ValueError):
                continue
            if time_s:
                resumes[ep] = (float(time_s), float(total_s or 0))
        return resumes
    except Exception:
        return {}
_db_instance = None

def get_db():
    global _db_instance
    if _db_instance is None:
        from lib.database import KingDatabase
        _db_instance = KingDatabase()
    return _db_instance

def is_thumb_cached(url):
    if not url:
        return False
    return bool(_get_kodi_cached_thumb(url))

def get_thumb_path(url):
    if not url:
        return None
    return _get_kodi_cached_thumb(url)
if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'clear_all':
        import xbmc
        import xbmcgui
        import xbmcaddon
        addon = xbmcaddon.Addon('plugin.video.kingiptv')
        getString = addon.getLocalizedString
        confirmed = xbmcgui.Dialog().yesno(getString(32132), getString(32133))
        if confirmed:
            deleted_files, thumbnails_cleared, _errors = clear_kodi_video_cache()
            if deleted_files or thumbnails_cleared:
                xbmcgui.Dialog().notification(addon.getAddonInfo('name'), getString(32134), xbmcgui.NOTIFICATION_INFO, 4000)
            else:
                xbmcgui.Dialog().notification(addon.getAddonInfo('name'), getString(32135), xbmcgui.NOTIFICATION_WARNING, 4000)
