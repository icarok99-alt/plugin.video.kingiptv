# -*- coding: utf-8 -*-

import os
import json
import xbmc
import xbmcaddon
import xbmcvfs

TAG = '[plugin.video.kingiptv][list_manager]'


def _log(msg, level=xbmc.LOGINFO):
    try:
        xbmc.log('{0} {1}'.format(TAG, msg), level)
    except Exception:
        pass


translate = xbmcvfs.translatePath
profile = translate(xbmcaddon.Addon().getAddonInfo('profile'))
ACTIVE_LIST_FILE = translate(os.path.join(profile, 'active_list.json'))
_log('modulo carregado; ACTIVE_LIST_FILE={0}'.format(ACTIVE_LIST_FILE))

def ensure_profile():
    try:
        if not xbmcvfs.exists(profile):
            xbmcvfs.mkdirs(profile)
    except Exception:
        try:
            if not os.path.exists(profile):
                os.makedirs(profile)
        except Exception:
            pass

def get_active_list():
    try:
        if os.path.exists(ACTIVE_LIST_FILE):
            with open(ACTIVE_LIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('dns') and data.get('username') and data.get('password'):
                _log('get_active_list: retornando dns={0} username={1} label={2}'.format(
                    data.get('dns'), data.get('username'), data.get('label')))
                return data
            _log('get_active_list: arquivo existe mas dados incompletos: {0}'.format(data))
        else:
            _log('get_active_list: arquivo nao existe ainda ({0})'.format(ACTIVE_LIST_FILE))
    except Exception:
        _log('get_active_list: EXCECAO ao ler arquivo', xbmc.LOGERROR)
        import traceback
        _log(traceback.format_exc(), xbmc.LOGERROR)
    return None

def set_active_list(dns, username, password, label=''):
    ensure_profile()
    data = {
        'dns': dns,
        'username': username,
        'password': password,
        'label': label,
    }
    try:
        with open(ACTIVE_LIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        _log('set_active_list: gravado com sucesso dns={0} username={1} label={2} arquivo={3}'.format(
            dns, username, label, ACTIVE_LIST_FILE))
        readback = get_active_list()
        if not readback or readback.get('dns') != dns or str(readback.get('username')) != str(username):
            _log('set_active_list: RELEITURA IMEDIATA DIVERGENTE! readback={0}'.format(readback), xbmc.LOGERROR)
        return True
    except Exception:
        _log('set_active_list: EXCECAO ao gravar arquivo', xbmc.LOGERROR)
        import traceback
        _log(traceback.format_exc(), xbmc.LOGERROR)
        return False

def clear_active_list():
    try:
        if os.path.exists(ACTIVE_LIST_FILE):
            os.remove(ACTIVE_LIST_FILE)
        return True
    except Exception:
        return False

def has_active_list():
    return get_active_list() is not None
