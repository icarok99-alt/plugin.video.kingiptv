# -*- coding: utf-8 -*-

import os
import sys
import hashlib
import shutil
import threading
import xbmc
import xbmcgui
import xbmcaddon
import xbmcvfs
from datetime import datetime, timedelta

try:
    from lib.helper import requests
except Exception:
    import requests

ADDON_ID = 'plugin.video.kingiptv'
ADDON = xbmcaddon.Addon(ADDON_ID)
ADDON_DATA = xbmcvfs.translatePath('special://profile/addon_data/{}/'.format(ADDON_ID))
DATABASE_PATH = os.path.join(ADDON_DATA, 'kingiptv.db')
CACHE_DIR = os.path.join(ADDON_DATA, 'thumb_cache')

THUMB_TTL_DAYS = 30

_thumb_locks_meta = threading.Lock()
_thumb_locks = {}

_db_instance = None

def notify(message, time_ms=4000):
    xbmc.executebuiltin('Notification(KingIPTV, {}, {}, {})'.format(
        message, time_ms, ADDON.getAddonInfo('icon')
    ))

def get_db():
    global _db_instance
    if _db_instance is None:
        from lib.database import KingDatabase
        _db_instance = KingDatabase()
    return _db_instance

def _ensure_cache_dir():
    if not xbmcvfs.exists(CACHE_DIR):
        xbmcvfs.mkdirs(CACHE_DIR)

def _real_path(path):
    if path.startswith('special://'):
        return xbmcvfs.translatePath(path)
    return path

def _is_cache_valid(local_path):
    try:
        real = _real_path(local_path)
        if not os.path.exists(real):
            return False
        age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(real))
        return age.days < THUMB_TTL_DAYS
    except Exception:
        return xbmcvfs.exists(local_path)

def _get_url_lock(url_hash):
    with _thumb_locks_meta:
        if url_hash not in _thumb_locks:
            _thumb_locks[url_hash] = threading.Lock()
        return _thumb_locks[url_hash]

def is_thumb_cached(url):
    if not url:
        return False
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    ext = os.path.splitext(url.split('?')[0])[1] or '.jpg'
    return _is_cache_valid(os.path.join(CACHE_DIR, url_hash + ext))

def get_thumb_path(url, force_download=False):
    if not url:
        return None
    _ensure_cache_dir()

    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    ext = os.path.splitext(url.split('?')[0])[1] or '.jpg'
    local_path = os.path.join(CACHE_DIR, url_hash + ext)

    if not force_download and _is_cache_valid(local_path):
        return local_path

    url_lock = _get_url_lock(url_hash)
    with url_lock:
        if not force_download and _is_cache_valid(local_path):
            return local_path
        try:
            response = requests.get(url, timeout=10, stream=True)
            if response.status_code == 200:
                with xbmcvfs.File(local_path, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
                return local_path
            return None
        except Exception:
            return None

def clear_expired_cache():
    real_cache_dir = _real_path(CACHE_DIR)
    if not os.path.exists(real_cache_dir):
        return 0
    cutoff = datetime.now() - timedelta(days=THUMB_TTL_DAYS)
    removed = 0
    for fname in os.listdir(real_cache_dir):
        fpath = os.path.join(real_cache_dir, fname)
        try:
            if os.path.isfile(fpath) and datetime.fromtimestamp(os.path.getmtime(fpath)) < cutoff:
                os.remove(fpath)
                removed += 1
        except Exception:
            pass
    return removed

def clear_cache():
    real_cache_dir = _real_path(CACHE_DIR)
    if os.path.exists(real_cache_dir):
        try:
            shutil.rmtree(real_cache_dir, ignore_errors=True)
        except Exception:
            pass

class KingDatabaseManager:

    def _db_exists(self):
        return os.path.isfile(DATABASE_PATH)

    def _get_setting_int(self, key, default=7):
        try:
            return int(ADDON.getSetting(key))
        except (ValueError, TypeError):
            return default

    def _get_setting_bool(self, key):
        return ADDON.getSetting(key).lower() == 'true'

    def _last_modified_date(self):
        try:
            return datetime.fromtimestamp(os.path.getmtime(DATABASE_PATH))
        except OSError:
            return None

    def delete_database(self, confirm=True):
        if not self._db_exists():
            notify('Banco de dados não encontrado.')
            return False

        if confirm:
            ok = xbmcgui.Dialog().yesno('KingIPTV', ADDON.getLocalizedString(33004))
            if not ok:
                return False

        clear_cache()

        try:
            os.remove(DATABASE_PATH)
        except Exception:
            try:
                xbmcvfs.delete(DATABASE_PATH)
            except Exception:
                pass

        if self._db_exists():
            notify('Falha ao excluir o banco de dados.')
            return False

        notify(ADDON.getLocalizedString(33005))
        return True

    def check_auto_expiry(self):
        if not self._get_setting_bool('db_auto_cleanup_enabled'):
            return

        clear_expired_cache()

        if not self._db_exists():
            return

        expiry_days = self._get_setting_int('db_cleanup_days', default=7)
        last_modified = self._last_modified_date()

        if last_modified is None:
            return

        if expiry_days == 0 or datetime.now() - last_modified >= timedelta(days=expiry_days):
            self.delete_database(confirm=False)

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'clear_all':
        KingDatabaseManager().delete_database(confirm=True)
    else:
        KingDatabaseManager().delete_database(confirm=True)