# -*- coding: utf-8 -*-

from lib.helper import *
import requests


def radios_list(API):
    try:
        response = requests.get(API, allow_redirects=True, timeout=15)
        response.raise_for_status()
        radios = response.json()
    except Exception:
        return []

    result = []
    for item in radios:
        if not isinstance(item, dict):
            continue
        name = item.get('name', '')
        stream = item.get('stream', '') or item.get('url', '')
        icon = item.get('logo', '') or item.get('icon', '')
        if not name or not stream:
            continue
        result.append({'name': name, 'url': stream, 'icon': icon})

    return result
