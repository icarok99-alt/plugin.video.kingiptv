import threading
import time

import xbmc
import xbmcaddon
import xbmcgui

ADDON = xbmcaddon.Addon()
ADDON_PATH = ADDON.getAddonInfo('path')

ACTION_PREVIOUS_MENU = 10
ACTION_NAV_BACK = 92
ACTION_MOVE_LEFT = 1

HOME = xbmcgui.Window(10000)

LIST_CONTROL = 3001
UPCOMING_CONTROL = 3002
BACK_BUTTON = 3010
PEEK_BUTTON = 3099

NAV_PROPS = (
    'nav.mode', 'nav.fanart', 'nav.title',
    'nav.detail.poster', 'nav.detail.title', 'nav.detail.secondary',
    'nav.detail.desc', 'nav.detail.range', 'nav.detail.next',
    'nav.list.kind',
    'nav.backskin',
)

WINDOW_FULLSCREEN_VIDEO = 12005

REOPEN_GUARD_SECONDS = 0.6
DIALOG_READY_TIMEOUT = 15

TAG = '[plugin.video.kingiptv][nav_dialog]'

MAX_UPCOMING = 16


def _log(msg, level=xbmc.LOGINFO):
    try:
        xbmc.log('{0} {1}'.format(TAG, msg), level)
    except Exception:
        pass


def _log_exc(where):
    import traceback
    _log('EXCEPTION em {0}:\n{1}'.format(where, traceback.format_exc()), xbmc.LOGERROR)


MINI_PROGRESS_STEP = 10


def _mini_progress_percent(start, end, now, step=MINI_PROGRESS_STEP):
    start = int(start or 0)
    end = int(end or 0)
    if end <= start:
        return 0
    pct = max(0, min(100, int((now - start) * 100 / (end - start))))
    return (pct // step) * step


def _next_decile_boundary_time(start, end, now, step=MINI_PROGRESS_STEP):
    start = int(start or 0)
    end = int(end or 0)
    if end <= start:
        return None
    duration = end - start
    pct = max(0, min(100, int((now - start) * 100 / duration)))
    next_pct = ((pct // step) + 1) * step
    if next_pct > 100:
        return None
    return start + (next_pct * duration) / 100.0


def _next_minute_boundary_time(end, now):
    end = int(end or 0)
    diff = end - now
    if diff <= 0:
        return None
    rem = diff % 60
    return now + (rem if rem else 60)


class NavPlayerMonitor(xbmc.Player):

    def __init__(self, dialog):
        super(NavPlayerMonitor, self).__init__()
        self._dialog = dialog

    def onAVStarted(self):
        self._dialog._on_playback_started()

    def onPlayBackStarted(self):
        self._dialog._on_playback_started()

    def onPlayBackStopped(self):
        self._dialog._on_playback_stopped()

    def onPlayBackEnded(self):
        self._dialog._on_playback_stopped()

    def onPlayBackError(self):
        self._dialog._on_playback_stopped()


class NavDialog(xbmcgui.WindowXML):

    def __new__(cls):
        return super(NavDialog, cls).__new__(cls, 'DialogNav.xml', ADDON_PATH, 'Default', '1080i')

    def __init__(self):
        super(NavDialog, self).__init__()
        self.mode = None
        self.items = []
        self.channels = []
        self.last_pos = 0
        self.opened_at = 0

        self.selected_query = None
        self.selected_index = None
        self.selected_channel = None
        self.back_requested = False
        self.video_reclaimed = False
        self.select_event = threading.Event()
        self._event_gen = -1

        self.alive = False
        self.ready_event = threading.Event()
        self.closed_event = threading.Event()

        self._generation = 0
        self._epg_lock = threading.Lock()
        self._epg_computed = set()
        self._epg_items = []
        self._epg_tick_signature = None
        self._epg_pending = []
        self._epg_pending_set = set()
        self._fetch_pending = []
        self._fetch_pending_set = set()
        self._mini_percent_cache = {}
        self._epg_wake_event = threading.Event()

        self._render_lock = threading.Lock()

        self._is_playing = False
        self._expecting_own_playback = False
        self._player_monitor = NavPlayerMonitor(self)
        self._upcoming_signature = None
        self._upcoming_initialized = False

        self._pending_update = None
        self._update_timer = None

    def onInit(self):
        _log('onInit chamado; thread={0}; mode atual={1}'.format(threading.current_thread().name, self.mode))
        self.alive = True
        self.ready_event.set()
        try:
            if xbmc.Player().isPlaying():
                self._on_playback_started()
        except Exception:
            _log_exc('onInit: checagem inicial de playback')

    def _reassert_list_focus(self):
        try:
            self.setFocusId(LIST_CONTROL)
        except Exception:
            pass
        try:
            xbmc.executebuiltin('SetFocus({0})'.format(LIST_CONTROL))
        except Exception:
            pass
        try:
            container = self.getControl(LIST_CONTROL)
            pos = container.getSelectedPosition()
            if pos < 0:
                pos = self.last_pos
            container.selectItem(pos)
        except Exception:
            pass

    def _on_playback_started(self):
        _log('_on_playback_started: reproducao detectada (evento onPlayBackStarted/onAVStarted)')
        self._is_playing = True
        if self.mode == 'epg' and self.alive and not self._expecting_own_playback:
            self.video_reclaimed = True
            self._reassert_list_focus()
            self._signal_selection()

    def _on_playback_stopped(self):
        _log('_on_playback_stopped: reproducao encerrada (evento onPlayBackStopped/Ended/Error)')
        self._is_playing = False

        if self.mode == 'epg' and self.alive:
            if self._update_timer is not None:
                self._update_timer.cancel()
                self._update_timer = None
            self._update_timer = threading.Timer(0.15, self._reassert_list_focus)
            self._update_timer.daemon = True
            self._update_timer.start()

    def close(self):
        _log('close() chamado; mode={0}'.format(self.mode))
        self.alive = False
        self._generation += 1
        if self._update_timer is not None:
            self._update_timer.cancel()
            self._update_timer = None
        for prop in NAV_PROPS + ('nav.loading', 'nav.loading.message'):
            HOME.clearProperty(prop)
        self.closed_event.set()
        try:
            xbmcgui.WindowXML.close(self)
        except Exception:
            _log_exc('close()')

    def _begin_screen(self, mode):
        self._generation += 1
        is_new_mode = self.mode != mode
        self.mode = mode
        self.selected_query = None
        self.selected_index = None
        self.selected_channel = None
        self.back_requested = False
        self.video_reclaimed = False
        self.select_event.clear()
        self._event_gen = -1
        if is_new_mode:
            self.opened_at = time.time()
        self._expecting_own_playback = False
        HOME.setProperty('nav.mode', mode)
        HOME.clearProperty('nav.list.playable')
        HOME.clearProperty('nav.list.kind')
        self._epg_tick_signature = None
        self._upcoming_initialized = False
        with self._epg_lock:
            self._epg_pending = []
            self._epg_pending_set = set()
            self._fetch_pending = []
            self._fetch_pending_set = set()

    def push_home(self, items, fanart=''):
        _log('push_home: {0} itens; thread={1}; alive={2}'.format(len(items or []), threading.current_thread().name, self.alive))
        self._begin_screen('home')
        self.items = items or []
        HOME.setProperty('nav.fanart', fanart or '')
        HOME.setProperty('nav.title', '::: KING IPTV ::: | MENU PRINCIPAL')
        ok = self._render_when_ready()
        _log('push_home: render ok={0}'.format(ok))

    def push_list(self, header, items, fanart='', start_pos=0, playable=False, content_kind=''):
        _log('push_list "{0}": {1} itens; thread={2}; alive={3}; playable={4}; content_kind={5}'.format(
            header, len(items or []), threading.current_thread().name, self.alive, playable, content_kind))
        self._begin_screen('list')
        self.items = items or []
        self.last_pos = start_pos if 0 <= start_pos < len(self.items) else 0
        HOME.setProperty('nav.fanart', fanart or '')
        HOME.setProperty('nav.title', '::: KING IPTV ::: | {0}'.format(header or ''))
        HOME.setProperty('nav.list.playable', 'true' if playable else '')
        HOME.setProperty('nav.list.kind', content_kind or '')
        ok = self._render_when_ready()
        _log('push_list "{0}": render ok={1}'.format(header, ok))

    def push_epg(self, header, channels, fanart='', start_pos=0):
        _log('push_epg "{0}": {1} canais; thread={2}; alive={3}'.format(header, len(channels or []), threading.current_thread().name, self.alive))
        self._begin_screen('epg')
        self.channels = [c for c in (channels or []) if c.get('name')]
        for ch in self.channels:
            if ch.get('programs') is not None:
                ch['programs'] = sorted(ch.get('programs') or [], key=lambda p: p.get('start') or 0)
        self.last_pos = start_pos if 0 <= start_pos < len(self.channels) else 0
        self._epg_computed = set()
        self._mini_percent_cache = {}
        self._upcoming_signature = None
        self._upcoming_initialized = False
        HOME.setProperty('nav.fanart', fanart or '')
        HOME.setProperty('nav.title', '{0}  |  GUIA DE PROGRAMAÇÃO'.format(header or ''))
        ok = self._render_when_ready()
        _log('push_epg "{0}": render ok={1}'.format(header, ok))
        if ok:
            self._start_epg_threads()

    def set_loading(self, loading, message=''):
        if loading:
            HOME.setProperty('nav.loading', 'true')
            HOME.setProperty('nav.loading.message', message or '')
        else:
            HOME.clearProperty('nav.loading')
            HOME.clearProperty('nav.loading.message')

    def _signal_selection(self):
        self._event_gen = self._generation
        self.select_event.set()

    def wait_for_selection(self, timeout=None):
        while True:
            if not self.select_event.wait(timeout):
                return
            if timeout is not None:
                return
            if not self.alive:
                return
            if self._event_gen == self._generation:
                return
            _log('wait_for_selection: descartando evento obsoleto (event_gen={0}, generation={1})'.format(
                self._event_gen, self._generation))
            self.select_event.clear()
            self.back_requested = False
            self.selected_query = None
            self.selected_index = None
            self.selected_channel = None

    def _render_when_ready(self, timeout=DIALOG_READY_TIMEOUT):
        if not self.alive:
            _log('_render_when_ready: janela ainda nao esta alive, aguardando ready_event...', xbmc.LOGWARNING)
            if not self.ready_event.wait(timeout) or not self.alive:
                _log('_render_when_ready: NavDialog nao ficou pronta para renderizar (timeout={0}s)'.format(timeout),
                     xbmc.LOGERROR)
                return False
        self._render_current(reset_focus=True)
        return True

    def _render_current(self, reset_focus=False):
        with self._render_lock:
            try:
                self._render_current_locked(reset_focus)
            except Exception:
                _log_exc('_render_current_locked (mode={0})'.format(self.mode))

    def _can_return_to_playback(self):
        return self._is_playing and self.mode == 'epg'

    def _return_to_playback(self):
        try:
            xbmc.executebuiltin('ActivateWindow({0})'.format(WINDOW_FULLSCREEN_VIDEO))
        except Exception:
            _log_exc('_return_to_playback: ActivateWindow')
            return False
        return True

    def _render_current_locked(self, reset_focus):
        container = self.getControl(LIST_CONTROL)
        container.reset()

        list_items = []
        if self.mode == 'home':
            for entry in self.items:
                li = xbmcgui.ListItem(label=entry.get('label', ''))
                icon = entry.get('icon') or ''
                li.setArt({'icon': icon, 'thumb': icon})
                list_items.append(li)
        elif self.mode == 'list':
            for entry in self.items:
                li = xbmcgui.ListItem(label=entry.get('label', ''))
                art = entry.get('icon') or entry.get('poster') or ''
                li.setArt({'icon': art, 'thumb': art})
                li.setProperty('secondary', entry.get('secondary', '') or '')
                li.setProperty('unavailable', 'true' if entry.get('unavailable') else '')
                list_items.append(li)
        elif self.mode == 'epg':
            for ch in self.channels:
                li = xbmcgui.ListItem(label=ch.get('name', ''))
                icon = ch.get('icon') or ''
                li.setArt({'icon': icon, 'thumb': icon})
                list_items.append(li)
            self._epg_items = list_items

        _log('_render_current_locked: mode={0} -> {1} ListItems construidos'.format(self.mode, len(list_items)))

        if list_items:
            container.addItems(list_items)
            container.selectItem(self.last_pos)

        _log('_render_current_locked: container agora tem {0} itens (container.size())'.format(container.size()))

        if reset_focus:
            self.setFocusId(LIST_CONTROL)
        self._update_details(self.last_pos)

    def _update_details(self, pos):
        try:
            self._update_details_unsafe(pos)
        except Exception:
            _log_exc('_update_details (mode={0}, pos={1})'.format(self.mode, pos))

    def _update_details_unsafe(self, pos):
        if self.mode == 'home':
            if pos < 0 or pos >= len(self.items):
                HOME.clearProperty('nav.detail.title')
                HOME.clearProperty('nav.detail.desc')
                HOME.clearProperty('nav.detail.crown')
                return
            entry = self.items[pos]
            HOME.clearProperty('nav.detail.secondary')
            HOME.clearProperty('nav.detail.year')
            HOME.clearProperty('nav.detail.range')
            HOME.clearProperty('nav.detail.title')
            HOME.clearProperty('nav.detail.poster')
            HOME.setProperty('nav.detail.crown', 'icon.png')
            HOME.setProperty('nav.detail.desc', entry.get('description', '') or '')
        elif self.mode == 'list':
            if pos < 0 or pos >= len(self.items):
                for prop in ('nav.detail.title', 'nav.detail.desc', 'nav.detail.secondary', 'nav.detail.year', 'nav.detail.poster', 'nav.detail.crown'):
                    HOME.clearProperty(prop)
                return
            entry = self.items[pos]
            HOME.clearProperty('nav.detail.range')
            HOME.setProperty('nav.detail.desc', entry.get('description', '') or '')
            HOME.setProperty('nav.detail.secondary', entry.get('secondary', '') or '')
            HOME.setProperty('nav.detail.year', entry.get('year', '') or '')
            poster = entry.get('poster') or ''
            HOME.setProperty('nav.detail.poster', poster)
            HOME.setProperty('nav.detail.crown', 'icon.png')
            HOME.setProperty('nav.detail.title', entry.get('label', '') if poster else '')
        elif self.mode == 'epg':
            self._update_epg_details(pos, force=False)

    def onAction(self, action):
        try:
            action_id = action.getId()
        except Exception:
            return xbmcgui.WindowXML.onAction(self, action)

        if action_id == ACTION_MOVE_LEFT:
            focus_id = self.getFocusId()
            if focus_id == UPCOMING_CONTROL:
                self.setFocusId(LIST_CONTROL)
                return

        if action_id in (ACTION_PREVIOUS_MENU, ACTION_NAV_BACK):
            if time.time() - self.opened_at < REOPEN_GUARD_SECONDS:
                _log('onAction: back ignorado (guarda de reabertura, mode={0})'.format(self.mode))
                return
            if self._can_return_to_playback():
                self._return_to_playback()
                return
            _log('onAction: BACK solicitado (mode={0})'.format(self.mode))
            self.back_requested = True
            self._signal_selection()
            return

        xbmcgui.WindowXML.onAction(self, action)
        try:
            if self.getFocusId() == LIST_CONTROL:
                pos = self.getControl(LIST_CONTROL).getSelectedPosition()
                if pos != self.last_pos:
                    self.last_pos = pos
                    if self.mode == 'epg':
                        self._ensure_epg_window(pos)
                        self._epg_wake_event.set()
                    self._update_details(pos)
        except Exception:
            _log_exc('onAction (mode={0})'.format(self.mode))

    def onClick(self, control_id):
        _log('onClick: control_id={0} mode={1}'.format(control_id, self.mode))
        try:
            if control_id == LIST_CONTROL:
                pos = self.getControl(LIST_CONTROL).getSelectedPosition()
                if self.mode == 'home':
                    if 0 <= pos < len(self.items):
                        entry = self.items[pos]
                        if entry.get('noop'):
                            _log('onClick: item home[{0}] eh noop, ignorando'.format(pos))
                            return
                        self.selected_query = entry.get('query')
                        _log('onClick: home selecionou query={0}'.format(self.selected_query))
                        self._signal_selection()
                elif self.mode == 'list':
                    if 0 <= pos < len(self.items):
                        self.selected_index = pos
                        _log('onClick: list selecionou indice={0}'.format(pos))
                        self._signal_selection()
                elif self.mode == 'epg':
                    if 0 <= pos < len(self.channels):
                        self.selected_channel = self.channels[pos]
                        _log('onClick: epg selecionou canal={0}'.format(self.selected_channel.get('name')))
                        self._signal_selection()
            elif control_id == BACK_BUTTON:
                _log('onClick: botao BACK (mode={0})'.format(self.mode))
                self.back_requested = True
                self._signal_selection()
        except Exception:
            _log_exc('onClick (control_id={0}, mode={1})'.format(control_id, self.mode))

    def _get_programs(self, channel):
        from lib.xtream import get_epg_programs
        from lib import pluto

        programs = channel.get('programs')
        if programs is None:
            epg_channel_id = channel.get('epg_channel_id') or ''
            dns = channel.get('epg_dns') or ''
            if epg_channel_id and dns == pluto.PLUTO_DNS_SENTINEL:
                try:
                    programs = pluto.get_pluto_epg_programs(epg_channel_id, limit=48)
                    programs = sorted(programs or [], key=lambda p: p.get('start') or 0)
                except Exception:
                    programs = []
            elif epg_channel_id and dns:
                try:
                    programs = get_epg_programs(epg_channel_id, dns, limit=48)
                    programs = sorted(programs or [], key=lambda p: p.get('start') or 0)
                except Exception:
                    programs = []
            else:
                programs = []
            channel['programs'] = programs
        return programs

    def _compute_epg_for_index(self, idx):
        with self._epg_lock:
            if idx in self._epg_computed:
                return
            self._epg_computed.add(idx)
        if idx < 0 or idx >= len(self.channels) or idx >= len(self._epg_items):
            return
        from lib.xtream import epg_lookup_current_next

        channel = self.channels[idx]
        li = self._epg_items[idx]
        now = int(time.time())
        current, _next = epg_lookup_current_next(self._get_programs(channel))
        if current:
            li.setProperty('current', current.get('title', '') or '')
            start = int(current.get('start') or 0)
            end = int(current.get('end') or 0)
            pct = _mini_progress_percent(start, end, now)
            li.setProperty('percent', str(pct))

    def _sync_list_item_current(self, pos, current, pct):
        if pos < 0 or pos >= len(self._epg_items):
            return
        li = self._epg_items[pos]
        if current:
            li.setProperty('current', current.get('title', '') or '')
            if self._mini_percent_cache.get(pos) != pct:
                li.setProperty('percent', str(pct))
                self._mini_percent_cache[pos] = pct
        else:
            li.setProperty('current', '')
            if self._mini_percent_cache.get(pos) != 0:
                li.setProperty('percent', '0')
                self._mini_percent_cache[pos] = 0
        self._epg_computed.add(pos)

    def _ensure_epg_window(self, pos, radius=16):
        if not self.channels:
            return
        lo = max(0, pos - radius)
        hi = min(len(self.channels) - 1, pos + radius)
        with self._epg_lock:
            for idx in range(lo, hi + 1):
                if idx not in self._epg_computed and idx not in self._epg_pending_set:
                    self._epg_pending.append(idx)
                    self._epg_pending_set.add(idx)

    def _init_upcoming_container(self):
        if self._upcoming_initialized:
            return
        try:
            container = self.getControl(UPCOMING_CONTROL)
        except Exception:
            return
        container.reset()
        items = []
        for _ in range(MAX_UPCOMING):
            li = xbmcgui.ListItem(label='')
            li.setProperty('range', '')
            items.append(li)
        container.addItems(items)
        self._upcoming_initialized = True

    def _update_epg_details(self, pos, force=False):
        if self.mode != 'epg':
            return
        if pos < 0 or pos >= len(self.channels):
            for prop in ('nav.detail.title', 'nav.detail.secondary', 'nav.detail.range', 'nav.detail.next'):
                HOME.clearProperty(prop)
            self._epg_tick_signature = None
            self._upcoming_signature = None
            return
        from lib.xtream import epg_lookup_current_next

        channel = self.channels[pos]
        programs = channel.get('programs')
        if programs is None:
            self._epg_tick_signature = None
            self._upcoming_signature = None
            HOME.setProperty('nav.detail.title', channel.get('name', '') or '')
            HOME.clearProperty('nav.detail.secondary')
            HOME.clearProperty('nav.detail.range')
            HOME.clearProperty('nav.detail.next')
            HOME.setProperty('nav.detail.desc', 'Carregando programação...')
            with self._epg_lock:
                if pos not in self._fetch_pending_set:
                    self._fetch_pending.insert(0, pos)
                    self._fetch_pending_set.add(pos)
            return

        current, nextp = epg_lookup_current_next(programs)
        now = int(time.time())
        sig = (pos, current.get('start'), current.get('title')) if current else (pos, None, None)

        if not force and sig == self._epg_tick_signature:
            self._apply_progress_tick(pos, current, now)
            return

        self._epg_tick_signature = sig
        self._apply_full_program_details(pos, channel, programs, current, nextp, now)

    def _apply_progress_tick(self, pos, current, now):
        if not current:
            return
        start = int(current.get('start') or 0)
        end = int(current.get('end') or 0)
        pct = _mini_progress_percent(start, end, now)
        self._sync_list_item_current(pos, current, pct)

    def _apply_full_program_details(self, pos, channel, programs, current, nextp, now):
        from lib.xtream import epg_format_range

        HOME.setProperty('nav.detail.title', channel.get('name', '') or '')

        next_title = str(nextp.get('title') or '').strip() if nextp else ''
        next_range = epg_format_range(nextp) if nextp else ''
        if next_title and next_range:
            next_label = '{} ({})'.format(next_title, next_range)
        else:
            next_label = next_title

        if current:
            start = int(current.get('start') or 0)
            end = int(current.get('end') or 0)
            pct = _mini_progress_percent(start, end, now)
            HOME.setProperty('nav.detail.secondary', current.get('title', '') or '')
            HOME.setProperty('nav.detail.desc', current.get('desc') or 'Sem descricao disponivel.')
            HOME.setProperty('nav.detail.range', epg_format_range(current))
            if next_label:
                HOME.setProperty('nav.detail.next', next_label)
            else:
                HOME.clearProperty('nav.detail.next')
            self._sync_list_item_current(pos, current, pct)
        else:
            HOME.clearProperty('nav.detail.secondary')
            HOME.clearProperty('nav.detail.range')
            if next_label:
                HOME.setProperty('nav.detail.next', next_label)
                desc = 'Sem informacao da programacao atual para este canal.\n{}'.format(next_label)
            else:
                HOME.clearProperty('nav.detail.next')
                desc = 'Programacao nao disponivel para este canal no momento.'
            HOME.setProperty('nav.detail.desc', desc)
            self._sync_list_item_current(pos, None, 0)

        upcoming = [p for p in programs if int(p.get('start') or 0) > now]
        upcoming_limited = upcoming[:MAX_UPCOMING]
        signature = tuple((p.get('title', ''), p.get('start')) for p in upcoming_limited)

        if signature == self._upcoming_signature:
            return

        self._upcoming_signature = signature
        self._init_upcoming_container()

        try:
            container = self.getControl(UPCOMING_CONTROL)
        except Exception:
            return

        for i in range(MAX_UPCOMING):
            li = container.getListItem(i)
            if i < len(upcoming_limited):
                p = upcoming_limited[i]
                li.setLabel(p.get('title', '') or '')
                li.setProperty('range', epg_format_range(p))
            else:
                li.setLabel('')
                li.setProperty('range', '')

    def _fetch_programs_then_refresh(self, pos, gen):
        if pos < 0 or pos >= len(self.channels):
            return
        channel = self.channels[pos]
        try:
            self._get_programs(channel)
        except Exception:
            _log_exc('_fetch_programs_then_refresh')
        self._compute_epg_for_index(pos)
        if self.alive and self._generation == gen and self.mode == 'epg' and self.last_pos == pos:
            self._update_details(pos)

    def _refresh_visible_percents(self, now, radius=16):
        if not self.channels or not self._epg_items:
            return None
        from lib.xtream import epg_lookup_current_next

        pos = self.last_pos
        lo = max(0, pos - radius)
        hi = min(len(self.channels) - 1, pos + radius)
        with self._epg_lock:
            computed = self._epg_computed
            visible_idx = [idx for idx in range(lo, hi + 1) if idx in computed]
        next_wake = None
        for idx in visible_idx:
            if idx >= len(self._epg_items):
                continue
            channel = self.channels[idx]
            programs = channel.get('programs')
            if not programs:
                continue
            current, _next = epg_lookup_current_next(programs)
            li = self._epg_items[idx]
            if current:
                start = int(current.get('start') or 0)
                end = int(current.get('end') or 0)
                pct = _mini_progress_percent(start, end, now)
                li.setProperty('current', current.get('title', '') or '')
                if self._mini_percent_cache.get(idx) != pct:
                    li.setProperty('percent', str(pct))
                    self._mini_percent_cache[idx] = pct
                boundary = _next_decile_boundary_time(start, end, now)
                if boundary is not None and (next_wake is None or boundary < next_wake):
                    next_wake = boundary
            else:
                li.setProperty('current', '')
                if self._mini_percent_cache.get(idx) != 0:
                    li.setProperty('percent', '0')
                    self._mini_percent_cache[idx] = 0
        return next_wake

    def _drain_fetch_pending(self, gen, budget):
        processed = 0
        while processed < budget:
            with self._epg_lock:
                if not self._fetch_pending:
                    return
                pos = self._fetch_pending.pop(0)
                self._fetch_pending_set.discard(pos)
            if not self.alive or self._generation != gen:
                return
            self._fetch_programs_then_refresh(pos, gen)
            processed += 1

    def _drain_epg_pending(self, gen, budget):
        processed = 0
        while processed < budget:
            with self._epg_lock:
                if not self._epg_pending:
                    return
                idx = self._epg_pending.pop(0)
                self._epg_pending_set.discard(idx)
            if not self.alive or self._generation != gen:
                return
            self._compute_epg_for_index(idx)
            if idx == self.last_pos and self.mode == 'epg':
                self._update_details(idx)
            processed += 1

    def _start_epg_threads(self):
        gen = self._generation
        threading.Thread(target=self._epg_background_loop, args=(gen,), daemon=True).start()

    def _tick_focused_progress(self, now):
        if self.mode != 'epg':
            return None
        pos = self.last_pos
        if pos < 0 or pos >= len(self.channels):
            return None
        self._update_epg_details(pos, force=False)

        channel = self.channels[pos]
        programs = channel.get('programs')
        if not programs:
            return None
        from lib.xtream import epg_lookup_current_next

        current, _next = epg_lookup_current_next(programs)
        if not current:
            return None
        start = current.get('start')
        end = current.get('end')
        candidates = [t for t in (
            _next_minute_boundary_time(end, now),
            _next_decile_boundary_time(start, end, now),
        ) if t]
        return min(candidates) if candidates else None

    def _wait_for_epg_wake(self, monitor, total_seconds, poll_step):
        deadline = time.time() + total_seconds
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self._epg_wake_event.clear()
                return False, False
            chunk = min(remaining, poll_step)
            if monitor.waitForAbort(chunk):
                return True, False
            if self._epg_wake_event.is_set():
                self._epg_wake_event.clear()
                return False, True

    def _epg_background_loop(self, gen):
        monitor = xbmc.Monitor()

        fetch_budget = 2
        compute_budget = 8
        drain_poll = 0.05
        idle_poll = 1.0
        min_wake = 1.0
        max_wake = 60.0
        fullscreen_poll = 1.0

        was_fullscreen = False

        total = len(self.channels)
        idx = 0
        processed_since_pause = 0
        batch_size = 25
        batch_pause = 0.05
        while self.alive and self._generation == gen and idx < total:
            if idx not in self._epg_computed:
                self._compute_epg_for_index(idx)
                processed_since_pause += 1
                if processed_since_pause >= batch_size:
                    processed_since_pause = 0
                    if monitor.waitForAbort(batch_pause):
                        return
                    if not self.alive or self._generation != gen:
                        return
            idx += 1

        if not self.alive or self._generation != gen:
            return

        next_wake = 0.0
        while True:
            sleep_for = idle_poll if next_wake <= 0 else max(min_wake, min(max_wake, next_wake - time.time()))
            aborted, _woke_early = self._wait_for_epg_wake(monitor, sleep_for, idle_poll)
            if aborted:
                return
            if not self.alive or self._generation != gen:
                return

            try:
                self._drain_fetch_pending(gen, fetch_budget)
                self._drain_epg_pending(gen, compute_budget)
            except Exception:
                _log_exc('_epg_background_loop: drenagem de filas')

            if not self.alive or self._generation != gen:
                return

            with self._epg_lock:
                pending_remaining = bool(self._fetch_pending or self._epg_pending)

            now = time.time()

            if xbmc.getCondVisibility('Window.IsActive(fullscreenvideo)'):
                was_fullscreen = True
                next_wake = now + fullscreen_poll
                continue

            if was_fullscreen:
                was_fullscreen = False
                try:
                    self._reassert_list_focus()
                except Exception:
                    _log_exc('_epg_background_loop: reassert de foco pos-fullscreen')

            if pending_remaining:
                next_wake = now + drain_poll
                continue

            next_focus_wake = None
            try:
                next_focus_wake = self._tick_focused_progress(now)
            except Exception:
                _log_exc('_epg_background_loop: tick do foco')

            next_visible_wake = None
            try:
                next_visible_wake = self._refresh_visible_percents(now)
            except Exception:
                _log_exc('_epg_background_loop: refresh dos visiveis')

            candidates = [t for t in (next_focus_wake, next_visible_wake) if t]
            next_wake = min(candidates) if candidates else (now + max_wake)


_dialog = None
_dialog_lock = threading.Lock()


def _reset_nav_properties():
    for prop in NAV_PROPS + ('nav.loading', 'nav.loading.message'):
        HOME.clearProperty(prop)


def _dialog_runner(holder):
    _log('_dialog_runner: thread iniciada ({0}), construindo NavDialog...'.format(threading.current_thread().name))
    try:
        dlg = NavDialog()
    except Exception:
        _log_exc('_dialog_runner: falha ao construir NavDialog')
        holder['error'] = True
        holder['ready'].set()
        return
    _log('_dialog_runner: NavDialog construido, chamando doModal()...')
    holder['dlg'] = dlg
    try:
        dlg.doModal()
    except Exception:
        _log_exc('_dialog_runner: doModal() lancou excecao')
    finally:
        _log('_dialog_runner: doModal() retornou (janela fechada)')
        dlg.alive = False
        dlg.closed_event.set()


def _ensure_dialog():
    global _dialog
    with _dialog_lock:
        dlg = _dialog

        if dlg is not None and dlg.alive:
            _log('_ensure_dialog: reutilizando instancia existente (alive=True)')
            return dlg

        if dlg is not None and not dlg.closed_event.is_set():
            _log('_ensure_dialog: instancia existente ainda inicializando, aguardando...')
            if dlg.ready_event.wait(DIALOG_READY_TIMEOUT) and dlg.alive:
                _log('_ensure_dialog: instancia existente ficou pronta durante a espera')
                return dlg
            _log('NavDialog anterior nao ficou pronta a tempo; recriando', xbmc.LOGWARNING)
            try:
                dlg.close()
            except Exception:
                _log_exc('_ensure_dialog fechando instancia travada')

        _log('_ensure_dialog: criando nova instancia de NavDialog (construcao + doModal na mesma thread)')
        _reset_nav_properties()
        holder = {'dlg': None, 'ready': threading.Event(), 'error': False}
        thread = threading.Thread(target=_dialog_runner, args=(holder,), daemon=True, name='NavDialog-doModal')
        thread.start()

        deadline = time.time() + DIALOG_READY_TIMEOUT
        while holder['dlg'] is None and not holder['error'] and time.time() < deadline:
            time.sleep(0.02)

        dlg = holder['dlg']
        if dlg is None:
            _log('NavDialog: thread nao conseguiu construir o objeto a tempo', xbmc.LOGERROR)
            dlg = NavDialog()

        _dialog = dlg
        remaining = max(0.5, deadline - time.time())
        if not dlg.ready_event.wait(remaining):
            _log('NavDialog nao inicializou dentro do tempo esperado ({0}s)'.format(DIALOG_READY_TIMEOUT),
                 xbmc.LOGWARNING)
        else:
            _log('_ensure_dialog: nova instancia pronta (alive={0})'.format(dlg.alive))
        return dlg


def close_session():
    global _dialog
    with _dialog_lock:
        dlg = _dialog
    if dlg is not None:
        _log('close_session: fechando instancia atual')
        dlg.close()
        dlg.closed_event.wait(2)


def prerender_home(items, fanart=''):
    _log('prerender_home: chamado com {0} itens'.format(len(items or [])))
    dlg = _ensure_dialog()
    if dlg.alive and dlg.mode is None:
        dlg.push_home(items, fanart=fanart)
    return dlg


def open_home_menu(items, fanart=''):
    _log('open_home_menu: chamado com {0} itens'.format(len(items or [])))
    for attempt in range(2):
        dlg = _ensure_dialog()
        dlg.push_home(items, fanart=fanart)
        dlg.wait_for_selection()
        _log('open_home_menu: selecao recebida (back_requested={0}, alive={1}, query={2})'.format(
            dlg.back_requested, dlg.alive, dlg.selected_query))
        if dlg.back_requested:
            close_session()
            xbmc.executebuiltin('Action(back)')
            return None
        if not dlg.alive:
            close_session()
            continue
        return dlg.selected_query
    return None


def open_list_menu(header, items, fanart='', start_pos=0, playable=False, content_kind=''):
    _log('open_list_menu: chamado "{0}" com {1} itens; content_kind={2}'.format(
        header, len(items or []), content_kind))
    if not items:
        return None, None
    for attempt in range(2):
        dlg = _ensure_dialog()
        dlg.push_list(header, items, fanart=fanart, start_pos=start_pos, playable=playable, content_kind=content_kind)
        dlg.wait_for_selection()
        _log('open_list_menu: selecao recebida (back_requested={0}, alive={1}, index={2})'.format(
            dlg.back_requested, dlg.alive, dlg.selected_index))
        if dlg.back_requested:
            return None, None
        if not dlg.alive:
            close_session()
            continue
        if dlg.selected_index is None:
            return None, None
        idx = dlg.selected_index
        return idx, items[idx]
    return None, None


def _wait_playback(dlg, live_monitor, monitor, current_channel):
    while not live_monitor.stopped.is_set():
        if dlg.back_requested:
            return 'back'
        if dlg.select_event.is_set():
            candidate = dlg.selected_channel
            if candidate is not None and candidate is not current_channel and not dlg.back_requested:
                return 'switch'
            dlg.select_event.clear()
        if monitor.waitForAbort(0.2):
            return 'abort'
    return 'stopped'


def _wait_playback_list(dlg, live_monitor, monitor, current_index):
    while not live_monitor.stopped.is_set():
        if dlg.back_requested:
            return 'back'
        if dlg.select_event.is_set():
            candidate = dlg.selected_index
            if candidate is not None and candidate != current_index and not dlg.back_requested:
                return 'switch'
            dlg.select_event.clear()
        if monitor.waitForAbort(0.2):
            return 'abort'
    return 'stopped'


def open_skin(header, channels, build_listitem, fanart=''):
    channels = [c for c in (channels or []) if c.get('name')]
    if not channels:
        return

    _log('open_skin: chamado "{0}" com {1} canais'.format(header, len(channels)))

    from lib.epg_dialog import LiveMonitor, BusySuppressor

    monitor = xbmc.Monitor()
    live_monitor = LiveMonitor()
    pos = 0
    need_render = True
    pending_selection = None

    while True:
        dlg = _ensure_dialog()
        if not dlg.alive:
            return

        if pending_selection is not None:
            selected = pending_selection
            pending_selection = None
            back_requested = False
            video_reclaimed = False
        else:
            if need_render:
                dlg.push_epg(header, channels, fanart=fanart, start_pos=pos)
            else:
                dlg.select_event.clear()
            dlg.wait_for_selection()
            if not dlg.alive:
                return

            selected = dlg.selected_channel
            back_requested = dlg.back_requested
            video_reclaimed = dlg.video_reclaimed

        _log('open_skin: selecao (back={0}, video_reclaimed={1}, canal={2})'.format(
            back_requested, video_reclaimed, selected.get('name') if selected else None))
        try:
            pos = channels.index(selected) if selected else dlg.last_pos
        except ValueError:
            pos = dlg.last_pos

        if video_reclaimed:
            live_monitor.started.set()
            live_monitor.stopped.clear()
            dlg.select_event.clear()
            dlg._expecting_own_playback = True
            outcome = _wait_playback(dlg, live_monitor, monitor, selected)
            dlg._expecting_own_playback = False
            if outcome in ('abort', 'back'):
                return
            if outcome == 'switch':
                pending_selection = dlg.selected_channel
                need_render = False
            else:
                dlg.back_requested = False
                dlg.video_reclaimed = False
                dlg.selected_channel = None
                need_render = False
            continue

        if back_requested or not selected:
            return

        url, listitem = build_listitem(selected)
        if not url or not listitem:
            xbmcgui.Dialog().notification(header, 'Nao foi possivel abrir este canal', xbmcgui.NOTIFICATION_ERROR, 3000)
            need_render = True
            continue

        live_monitor = LiveMonitor()
        busy_suppressor = BusySuppressor()
        busy_suppressor.start()
        dlg.set_loading(True, 'Aguarde...')
        dlg.select_event.clear()
        dlg._expecting_own_playback = True
        live_monitor.play(url, listitem)

        waited = 0.0
        while not live_monitor.started.is_set() and not live_monitor.stopped.is_set():
            if monitor.waitForAbort(0.1):
                busy_suppressor.stop()
                dlg.set_loading(False)
                dlg._expecting_own_playback = False
                return
            waited += 0.1
            if waited >= 30:
                try:
                    live_monitor.stop()
                except Exception:
                    pass
                break
        busy_suppressor.stop()

        if monitor.abortRequested():
            dlg.set_loading(False)
            dlg._expecting_own_playback = False
            return

        if not live_monitor.started.is_set():
            dlg.set_loading(False)
            dlg._expecting_own_playback = False
            xbmcgui.Dialog().notification(header, 'Nao foi possivel iniciar a reproducao deste canal', xbmcgui.NOTIFICATION_ERROR, 3000)
            need_render = True
            continue

        dlg.set_loading(False)
        dlg.select_event.clear()

        outcome = _wait_playback(dlg, live_monitor, monitor, selected)
        dlg._expecting_own_playback = False
        if outcome in ('abort', 'back'):
            return
        if outcome == 'switch':
            pending_selection = dlg.selected_channel
            need_render = False
        else:
            dlg.back_requested = False
            dlg.video_reclaimed = False
            dlg.selected_channel = None
            need_render = False
        continue


def open_list_playback(header, items, build_listitem, fanart=''):
    items = [it for it in (items or []) if it.get('label')]
    if not items:
        return

    _log('open_list_playback: chamado "{0}" com {1} itens'.format(header, len(items)))

    from lib.epg_dialog import LiveMonitor, BusySuppressor

    monitor = xbmc.Monitor()
    live_monitor = LiveMonitor()
    pos = 0
    need_render = True
    pending_index = None

    while True:
        dlg = _ensure_dialog()
        if not dlg.alive:
            return

        if pending_index is not None:
            idx = pending_index
            pending_index = None
            back_requested = False
        else:
            if need_render:
                dlg.push_list(header, items, fanart=fanart, start_pos=pos, playable=True)
            else:
                dlg.select_event.clear()
            dlg.wait_for_selection()
            if not dlg.alive:
                return

            idx = dlg.selected_index
            back_requested = dlg.back_requested

        _log('open_list_playback: selecao (back={0}, index={1})'.format(back_requested, idx))

        if back_requested or idx is None:
            return

        pos = idx
        entry = items[idx]

        url, listitem = build_listitem(entry)
        if not url or not listitem:
            xbmcgui.Dialog().notification(header, 'Nao foi possivel abrir este item', xbmcgui.NOTIFICATION_ERROR, 3000)
            need_render = True
            continue

        live_monitor = LiveMonitor()
        busy_suppressor = BusySuppressor()
        busy_suppressor.start()
        dlg.set_loading(True, 'Aguarde...')
        dlg.select_event.clear()
        dlg._expecting_own_playback = True
        live_monitor.play(url, listitem)

        waited = 0.0
        while not live_monitor.started.is_set() and not live_monitor.stopped.is_set():
            if monitor.waitForAbort(0.1):
                busy_suppressor.stop()
                dlg.set_loading(False)
                dlg._expecting_own_playback = False
                return
            waited += 0.1
            if waited >= 30:
                try:
                    live_monitor.stop()
                except Exception:
                    pass
                break
        busy_suppressor.stop()

        if monitor.abortRequested():
            dlg.set_loading(False)
            dlg._expecting_own_playback = False
            return

        if not live_monitor.started.is_set():
            dlg.set_loading(False)
            dlg._expecting_own_playback = False
            xbmcgui.Dialog().notification(header, 'Nao foi possivel iniciar a reproducao', xbmcgui.NOTIFICATION_ERROR, 3000)
            need_render = True
            continue

        dlg.set_loading(False)
        dlg.select_event.clear()

        outcome = _wait_playback_list(dlg, live_monitor, monitor, idx)
        dlg._expecting_own_playback = False
        if outcome in ('abort', 'back'):
            return
        if outcome == 'switch':
            pending_index = dlg.selected_index
            need_render = False
        else:
            need_render = True
        continue


def set_loading_off():
    dlg = _ensure_dialog()
    try:
        dlg.set_loading(False)
    except Exception:
        pass


def run_with_loading(fetch_func, message='', fanart='', close_when_done=True):
    dlg = _ensure_dialog()
    if fanart and not HOME.getProperty('nav.fanart'):
        HOME.setProperty('nav.fanart', fanart)
    dlg.set_loading(True, message)
    try:
        return fetch_func()
    finally:
        if close_when_done:
            try:
                dlg.set_loading(False)
            except Exception:
                pass


def play_and_release(start_playback, message='Aguarde...', timeout=30):
    from lib.epg_dialog import LiveMonitor, BusySuppressor

    dlg = _ensure_dialog()
    monitor = xbmc.Monitor()
    player_monitor = LiveMonitor()
    player_monitor.reset()

    busy_suppressor = BusySuppressor()
    busy_suppressor.start()
    dlg.set_loading(True, message)

    try:
        start_playback(player_monitor)
    except Exception:
        _log_exc('play_and_release: start_playback')
        busy_suppressor.stop()
        dlg.set_loading(False)
        return False

    waited = 0.0
    while not player_monitor.started.is_set() and not player_monitor.stopped.is_set():
        if monitor.waitForAbort(0.1):
            busy_suppressor.stop()
            dlg.set_loading(False)
            return False
        waited += 0.1
        if waited >= timeout:
            break
    busy_suppressor.stop()

    if not player_monitor.started.is_set():
        dlg.set_loading(False)
        return False

    dlg.set_loading(False)

    while not player_monitor.stopped.is_set():
        if monitor.waitForAbort(0.2):
            break

    return True
