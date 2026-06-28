# -*- coding: utf-8 -*-

import os
import sys
import hashlib
import shutil
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

def get_thumb_path(url, force_download=False):
    if not url:
        return None
    _ensure_cache_dir()
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    ext = os.path.splitext(url.split('?')[0])[1] or '.jpg'
    filename = url_hash + ext
    local_path = os.path.join(CACHE_DIR, filename)
    if force_download or not xbmcvfs.exists(local_path):
        try:
            response = requests.get(url, timeout=10, stream=True)
            if response.status_code == 200:
                with xbmcvfs.File(local_path, 'wb') as f:
                    for chunk in response.iter_content(1024):
                        f.write(chunk)
            else:
                return None
        except Exception:
            return None
    return local_path

def clear_cache():
    real_cache_dir = xbmcvfs.translatePath(CACHE_DIR) if CACHE_DIR.startswith('special://') else CACHE_DIR
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