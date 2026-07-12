

import os
import json
from lib.helper import profile, translate, xbmcvfs
ACTIVE_LIST_FILE = translate(os.path.join(profile, 'active_list.json'))

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
                return data
    except Exception:
        pass
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
        return True
    except Exception:
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
