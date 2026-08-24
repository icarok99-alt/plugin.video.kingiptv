# -*- coding: utf-8 -*-

def ensure_access(category_name=''):
    from lib import king_dialog
    return king_dialog.prompt_password(category_name)
