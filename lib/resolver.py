# -*- coding: utf-8 -*-
import xbmc
import time

class Resolver:
    def __init__(self):
        self.ensure_resolveurl()
    
    def ensure_resolveurl(self):
        """Garante que o resolveurl está instalado"""
        try:
            import resolveurl
        except ImportError:
            try:
                xbmc.executebuiltin("UpdateLocalAddons()")
                xbmc.executebuiltin("UpdateAddonRepos()")
                xbmc.executebuiltin('InstallAddon(script.module.resolveurl.fork)')
                xbmc.executebuiltin('SendClick(11)')
                time.sleep(7)
            except:
                xbmc.log("[Resolver] Erro ao instalar resolveurl", xbmc.LOGERROR)
    
    def resolverurls(self, url, referer=None):
        """
        Resolve uma URL usando o módulo resolveurl do Kodi.
        
        :param url: URL que será resolvida para o stream final.
        :param referer: Referência do site (opcional).
        :return: Tupla (stream_url, subtitles) onde subtitles é None por padrão.
        """
        try:
            import resolveurl
            
            # Verifica se o host é suportado
            if not resolveurl.HostedMediaFile(url):
                xbmc.log(f"[Resolver] URL não suportada: {url}", xbmc.LOGWARNING)
                return None, None
            
            # Tenta resolver o link
            stream_url = resolveurl.resolve(url)
            
            if stream_url:
                xbmc.log(f"[Resolver] URL resolvida com sucesso: {url[:50]}...", xbmc.LOGINFO)
                return stream_url, None
            else:
                xbmc.log(f"[Resolver] Falha ao resolver URL: {url}", xbmc.LOGWARNING)
                return None, None
                
        except ImportError:
            xbmc.log("[Resolver] Módulo resolveurl não encontrado", xbmc.LOGERROR)
            
            # Tenta instalar novamente
            time.sleep(2)
            try:
                import resolveurl
                if resolveurl.HostedMediaFile(url):
                    stream_url = resolveurl.resolve(url)
                    if stream_url:
                        return stream_url, None
            except:
                pass
            
            return None, None
            
        except Exception as e:
            xbmc.log(f"[Resolver] Erro ao resolver URL: {str(e)}", xbmc.LOGERROR)
            return None, None
