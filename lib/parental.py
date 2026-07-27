# -*- coding: utf-8 -*-

import sys

PARENTAL_PASSWORD = '6969'

if __name__ == '__main__':
    import xbmcgui
    import xbmcaddon

    addon = xbmcaddon.Addon('plugin.video.kingiptv')
    action = sys.argv[1] if len(sys.argv) > 1 else ''

    if action == 'unlock':
        entered = xbmcgui.Dialog().input(
            'Digite a senha para liberar canais adultos',
            type=xbmcgui.INPUT_NUMERIC,
        )
        if entered == PARENTAL_PASSWORD:
            addon.setSetting('adult_unlocked', 'true')
            xbmcgui.Dialog().notification(
                addon.getAddonInfo('name'),
                'Canais adultos liberados',
                xbmcgui.NOTIFICATION_INFO, 3000,
            )
        elif entered:
            xbmcgui.Dialog().notification(
                addon.getAddonInfo('name'),
                'Senha incorreta',
                xbmcgui.NOTIFICATION_ERROR, 3000,
            )

    elif action == 'lock':
        addon.setSetting('adult_unlocked', 'false')
        xbmcgui.Dialog().notification(
            addon.getAddonInfo('name'),
            'Canais adultos bloqueados novamente',
            xbmcgui.NOTIFICATION_INFO, 3000,
        )
