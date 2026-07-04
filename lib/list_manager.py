# -*- coding: utf-8 -*-

import os
import json
try:
    from lib.helper import profile, translate, xbmcvfs
except Exception:
    from helper import profile, translate, xbmcvfs
_ACTIVE_LIST_FILE = translate(os.path.join(profile, 'active_list.json'))

def _ensure_profile():
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
        if os.path.exists(_ACTIVE_LIST_FILE):
            with open(_ACTIVE_LIST_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('dns') and data.get('username') and data.get('password'):
                return data
    except Exception:
        pass
    return None

def set_active_list(dns, username, password, label=''):
    _ensure_profile()
    data = {
        'dns': dns,
        'username': username,
        'password': password,
        'label': label,
    }
    try:
        with open(_ACTIVE_LIST_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception:
        return False

def clear_active_list():
    try:
        if os.path.exists(_ACTIVE_LIST_FILE):
            os.remove(_ACTIVE_LIST_FILE)
        return True
    except Exception:
        return False

def has_active_list():
    return get_active_list() is not None
