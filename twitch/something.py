import asyncio
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List, Callable, Set, Deque, Tuple
from collections import deque
import uuid

# =============================================================
# Utils: now()
# =============================================================

def now() -> float:
    return time.time()

# =============================================================
# Role Helpers (модер / стример)
# =============================================================

ROLE_MOD = "moderator"
ROLE_BROADCASTER = "broadcaster"


def extract_roles(tags: Optional[Dict[str, Any]]) -> Set[str]:
    """Парсим роли из IRC-тегов Twitch.
    Ожидаем badges строку вида: 'broadcaster/1,moderator/1,subscriber/12'.
    Возвращаем множество ролей (lowercase): { 'broadcaster', 'moderator', ... }.
    """
    roles: Set[str] = set()
    if not tags:
        return roles
    badges = tags.get("badges") or tags.get("badge-info")
    if isinstance(badges, str):
        for part in badges.split(','):
            if '/' in part:
                name, _ = part.split('/', 1)
                roles.add(name.strip().lower())
            else:
                roles.add(part.strip().lower())
    # иногда twitchAPI даёт уже структуру
    elif isinstance(badges, dict):
        roles.update(k.lower() for k in badges)
    return roles


def is_mod_or_broadcaster(tags: Optional[Dict[str, Any]]) -> bool:
    roles = extract_roles(tags)
    return bool(ROLE_MOD in roles or ROLE_BROADCASTER in roles)


# =============================================================
# State Backend (in-memory; расширяемо)
# =============================================================

class StateBackend(ABC):
    @abstractmethod
    async def get(self, key: str): ...

    @abstractmethod
    async def set(self, key: str, value: Any, ttl: Optional[float] = None): ...

    @abstractmethod
    async def delete(self, key: str): ...


class InMemoryBackend(StateBackend):
    """Простой in-memory k/v с TTL и мягким ограничением по max_entries.
    Не LRU в классическом виде, но достаточно для умеренных нагрузок.
    """
    def __init__(self, max_entries: int = 500):
        self._data: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}
        self._max_entries = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str):
        async with self._lock:
            exp = self._expiry.get(key)
            if exp and exp < now():
                await self._delete_unlocked(key)
                return None
            return self._data.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[float] = None):
        async with self._lock:
            if len(self._data) >= self._max_entries:
                # remove ближайший к истечению / произвольный
                # (можно улучшить до настоящего LRU)
                victim = min(self._expiry.items(), key=lambda kv: kv[1])[0] if self._expiry else next(iter(self._data))
                await self._delete_unlocked(victim)
            self._data[key] = value
            if ttl is not None:
                self._expiry[key] = now() + ttl
            else:
                self._expiry.pop(key, None)

    async def delete(self, key: str):
        async with self._lock:
            await self._delete_unlocked(key)

    async def _delete_unlocked(self, key: str):
        self._data.pop(key, None)
        self._expiry.pop(key, None)


# =============================================================
# StateStore (поверх backend-а специальные операции)
# =============================================================

class StateStore:
    def __init__(self, backend: StateBackend):
        self.backend = backend

    # generic
    async def get(self, key: str):
        return await self.backend.get(key)

    async def set(self, key: str, value: Any, ttl: Optional[float] = None):
        await self.backend.set(key, value, ttl)

    async def delete(self, key: str):
        await self.backend.delete(key)

    # ----- специализированные шорткаты -----
    async def get_cooldown(self, key: str) -> Optional[float]:
        return await self.get(key)

    async def set_cooldown(self, key: str, seconds: float):
        # значение: время-до-которого блок
        await self.set(key, now() + seconds, ttl=seconds)

    async def is_on_cooldown(self, key: str) -> bool:
        until = await self.get_cooldown(key)
        return bool(until and until > now())

    async def get_tail(self, user_login: str) -> Optional[int]:
        key = f"tail:{user_login.lower()}"
        return await self.get(key)

    async def set_tail(self, user_login: str, value: int, ttl: float = 600):
        key = f"tail:{user_login.lower()}"
        await self.set(key, value, ttl)


# =============================================================
# Channel Configuration
# =============================================================

@dataclass
class CommandSettings:
    enabled: bool = True
    cooldown_s: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelConfig:
    channel: str
    commands: Dict[str, CommandSettings] = field(default_factory=dict)

    def is_enabled(self, cmd_name: str) -> bool:
        s = self.commands.get(cmd_name)
        return s.enabled if s else True  # default-on если нет записи

    def cooldown_for(self, cmd_name: str, default: Optional[float]) -> Optional[float]:
        s = self.commands.get(cmd_name)
        return s.cooldown_s if s and s.cooldown_s is not None else default


class ChannelConfigProvider:
    """Поставщик конфигов из БД. Тут упрощённо: хранит в памяти.
    Позже заменить на polling из PostgreSQL.
    """
    def __init__(self):
        self._cache: Dict[str, ChannelConfig] = {}

    def set_config(self, cfg: ChannelConfig):
        self._cache[cfg.channel] = cfg

    async def get(self, channel: str) -> ChannelConfig:
        return self._cache.get(channel) or ChannelConfig(channel)


# =============================================================
# Сообщение чата (нормализованное)
# =============================================================

@dataclass
class ChatMessage:
    channel: str
    user: str
    text: str
    tags: Optional[Dict[str, Any]] = None
    ts: float = field(default_factory=now)


# =============================================================
# ChannelState: активные пользователи, сессии
# =============================================================

@dataclass
class RiotSession:
    channel: str
    starter: str
    participants: Set[str] = field(default_factory=set)  # логины
    started_at: float = field(default_factory=now)


@dataclass
class SockSession:
    channel: str
    started_by: str
    target: str
    participants: Set[str] = field(default_factory=set)
    started_at: float = field(default_factory=now)
    duration_s: float = 60.0
    timer_task: Optional[asyncio.Task] = None  # для авто-завершения

    @property
    def ends_at(self) -> float:
        return self.started_at + self.duration_s


class ChannelState:
    """Runtime-состояние канала (в памяти)."""
    def __init__(self, channel: str, *, active_window_s: float = 1800):
        self.channel = channel
        self.active_window_s = active_window_s
        self.recent_msgs: Deque[Tuple[float, str]] = deque()  # (ts, user)
        self.riot: Optional[RiotSession] = None
        self.sock: Optional[SockSession] = None
        self.lock = asyncio.Lock()

    # ----- активные пользователи -----
    def record_message(self, msg: ChatMessage):
        self.recent_msgs.append((msg.ts, msg.user.lower()))
        # очистим старые
        cutoff = msg.ts - self.active_window_s
        while self.recent_msgs and self.recent_msgs[0][0] < cutoff:
            self.recent_msgs.popleft()

    def active_users(self, now_ts: Optional[float] = None) -> List[str]:
        if now_ts is None:
            now_ts = now()
        cutoff = now_ts - self.active_window_s
        users = {u for ts, u in self.recent_msgs if ts >= cutoff}
        return list(users)


# =============================================================
# Командный контекст (Invocation)
# =============================================================

@dataclass
class CommandContext:
    channel: str
    user: str
    message: str
    tags: Optional[Dict[str, Any]] = None
    ts: float = field(default_factory=now)


# =============================================================
# Базовый класс команды
# =============================================================

class Command(ABC):
    name: str = ""  # включительно с префиксом ! (для простоты)
    default_cooldown_s: Optional[float] = None  # None = без кулдауна

    def __init__(self, store: StateStore, cfg_provider: ChannelConfigProvider, send_fn: Callable[[str, str], Any], channel_states: Dict[str, ChannelState]):
        self.store = store
        self.cfg_provider = cfg_provider
        self._send_fn = send_fn  # (channel, text)
        self.channel_states = channel_states

    async def send(self, channel: str, text: str):
        await self._send_fn(channel, text)

    async def handle(self, ctx: CommandContext) -> None:
        # check enabled
        cfg = await self.cfg_provider.get(ctx.channel)
        if not cfg.is_enabled(self.name):
            return
        # cooldown
        cd = cfg.cooldown_for(self.name, self.default_cooldown_s)
        if cd is not None and cd > 0:
            # per-user кулдаун
            key = f"cd:{self.name}:{ctx.user.lower()}"
            if await self.store.is_on_cooldown(key):
                await self.on_cooldown(ctx)
                return
            await self.store.set_cooldown(key, cd)
        # actual
        await self.execute(ctx)

    async def on_cooldown(self, ctx: CommandContext):
        await self.send(ctx.channel, f"@{ctx.user}, нельзя использовать так часто!")

    @abstractmethod
    async def execute(self, ctx: CommandContext) -> None: ...


# =============================================================
# Простые команды
# =============================================================

class RandomCommand(Command):
    name = "!рандом"
    default_cooldown_s = 5

    async def execute(self, ctx: CommandContext) -> None:
        parts = ctx.message.split()
        lo, hi = 1, 100
        if len(parts) == 3:
            try:
                lo = int(parts[1]); hi = int(parts[2])
            except ValueError:
                pass
        num = random.randint(lo, hi)
        await self.send(ctx.channel, f"@{ctx.user}, твоё число: {num}")


class TailCommand(Command):
    name = "!хвост"
    default_cooldown_s = None  # нет кулдауна

    async def execute(self, ctx: CommandContext) -> None:
        # глобально по пользователю (без канала)
        tail = await self.store.get_tail(ctx.user)
        if tail is not None:
            await self.send(ctx.channel, f"@{ctx.user}, твой хвост всё ещё {tail} см.")
            return
        tail = random.randint(10, 50)
        await self.store.set_tail(ctx.user, tail, ttl=600)
        await self.send(ctx.channel, f"@{ctx.user}, твой хвост теперь {tail} см!")


class HugCommand(Command):
    name = "!обнять"
    default_cooldown_s = None
    hug_texts = ["обнял", "крепко обнимает", "стискивает в объятиях"]

    async def execute(self, ctx: CommandContext) -> None:
        parts = ctx.message.split()
        if len(parts) < 2 or not parts[1].startswith('@'):
            await self.send(ctx.channel, f"@{ctx.user}, укажи кого обнять: !обнять @ник")
            return
        target = parts[1]
        action = random.choice(self.hug_texts)
        await self.send(ctx.channel, f"@{ctx.user} {action} {target}")


# =============================================================
# Бунт: старт и стоп
# =============================================================

class RiotStartCommand(Command):
    name = "!начатьбунт"
    default_cooldown_s = None

    async def execute(self, ctx: CommandContext) -> None:
        ch_state = self.channel_states[ctx.channel]
        async with ch_state.lock:
            if ch_state.riot is not None:
                await self.send(ctx.channel, "Бунт уже идёт!")
                return
            sess = RiotSession(channel=ctx.channel, starter=ctx.user)
            sess.participants.add(ctx.user.lower())
            ch_state.riot = sess
        await self.send(ctx.channel, f"@{ctx.user} начал бунт! Пишите + или !бунт, чтобы присоединиться. Завершите командой !закончитьбунт.")


class RiotStopCommand(Command):
    name = "!закончитьбунт"
    default_cooldown_s = None

    async def execute(self, ctx: CommandContext) -> None:
        ch_state = self.channel_states[ctx.channel]
        async with ch_state.lock:
            sess = ch_state.riot
            if not sess:
                await self.send(ctx.channel, "Сейчас бунта нет.")
                return
            # Проверка прав: автор, мод, стример
            if ctx.user.lower() != sess.starter.lower() and not is_mod_or_broadcaster(ctx.tags):
                await self.send(ctx.channel, f"@{ctx.user}, у тебя нет прав завершить бунт.")
                return
            # Завершаем
            ch_state.riot = None
        dur = int(now() - sess.started_at)
        # собираем участников (кроме стартера в отдельном списке)
        others = [u for u in sess.participants if u != sess.starter.lower()]
        if others:
            part_str = ' '.join(f"@{u}" for u in others)
        else:
            part_str = "никто не поддержал"
        await self.send(ctx.channel, f"В канале @{ctx.channel} бунт начал @{sess.starter}; участвовали: {part_str}. Бунт длился {dur}с.")


async def riot_message_watcher(msg: ChatMessage, *, ch_state: ChannelState, send_fn: Callable[[str, str], Any]):
    """Каждое сообщение канала, когда активен бунт: добавляем участника, если '+', '!бунт'."""
    sess = ch_state.riot
    if not sess:
        return
    txt = msg.text.strip().lower()
    if txt == '+' or txt == '!бунт':
        async with ch_state.lock:
            sess.participants.add(msg.user.lower())
        # можно не спамить чат при каждом присоединении; тихо.
        # Если хочешь подтверждать: await send_fn(msg.channel, f"@{msg.user} присоединился к бунту!")


# =============================================================
# Розыгрыш носка: старт и отмена
# =============================================================

class SockStartCommand(Command):
    name = "!носок"
    default_cooldown_s = None  # Можно настроить глоб. кулдаун если нужно

    raffle_duration_s = 60
    active_window_s = 1800  # 30 мин активных пользователей

    async def execute(self, ctx: CommandContext) -> None:
        ch_state = self.channel_states[ctx.channel]
        # разобрать target: !носок [@ник]
        parts = ctx.message.split()
        if len(parts) >= 2 and parts[1].startswith('@'):
            target = parts[1][1:]
        else:
            # выбрать случайного активного пользователя (кроме бота? кроме стримера?)
            users = ch_state.active_users()
            if not users:
                await self.send(ctx.channel, "Нет активных пользователей за последние 30 минут. Розыгрыш невозможен.")
                return
            target = random.choice(users)
        target_norm = target.lower()

        async with ch_state.lock:
            if ch_state.sock is not None:
                await self.send(ctx.channel, "Розыгрыш носка уже идёт!")
                return
            sess = SockSession(channel=ctx.channel, started_by=ctx.user, target=target_norm, duration_s=self.raffle_duration_s)
            ch_state.sock = sess
            # Создаём таймер
            loop = asyncio.get_running_loop()
            sess.timer_task = loop.create_task(_sock_timeout(sess, ch_state, self.send))

        await self.send(ctx.channel, f"Объявляется розыгрыш носка пользователя @{target_norm}! Напишите + в чат в течение {self.raffle_duration_s}с, чтобы участвовать. @{target_norm} может отменить командой !отмена.")


class SockCancelCommand(Command):
    name = "!отмена"
    default_cooldown_s = None

    async def execute(self, ctx: CommandContext) -> None:
        ch_state = self.channel_states[ctx.channel]
        async with ch_state.lock:
            sess = ch_state.sock
            if not sess:
                # молчим: нет активного розыгрыша
                return
            # Право отмены: владелец носка, мод, стример
            if ctx.user.lower() != sess.target.lower() and not is_mod_or_broadcaster(ctx.tags):
                await self.send(ctx.channel, f"@{ctx.user}, у тебя нет прав отменить розыгрыш.")
                return
            # отмена
            if sess.timer_task and not sess.timer_task.done():
                sess.timer_task.cancel()
            ch_state.sock = None
        await self.send(ctx.channel, f"Розыгрыш носка @{sess.target} отменён.")


async def sock_message_watcher(msg: ChatMessage, *, ch_state: ChannelState, send_fn: Callable[[str, str], Any]):
    sess = ch_state.sock
    if not sess:
        return
    txt = msg.text.strip()
    # участники добавляются по '+' (без !)
    if txt == '+':
        async with ch_state.lock:
            sess.participants.add(msg.user.lower())
        # тихо; можно подтвердить: await send_fn(msg.channel, f"@{msg.user} в розыгрыше!")


async def _sock_timeout(sess: SockSession, ch_state: ChannelState, send_fn: Callable[[str, str], Any]):
    try:
        remaining = max(0.0, sess.ends_at - now())
        await asyncio.sleep(remaining)
    except asyncio.CancelledError:
        return
    # завершить розыгрыш
    async with ch_state.lock:
        if ch_state.sock is not sess:
            return  # уже отменено/заменено
        ch_state.sock = None
    parts = list(sess.participants)
    if not parts:
        await send_fn(sess.channel, f"Никто не поучаствовал в розыгрыше носка @{sess.target}. Никто не выигрывает.")
        return
    winner = random.choice(parts)
    await send_fn(sess.channel, f"Победитель розыгрыша носка @{sess.target} — @{winner}! Поздравляем!")


# =============================================================
# CommandManager / Dispatcher
# =============================================================

class CommandManager:
    def __init__(self, store: StateStore, cfg_provider: ChannelConfigProvider, send_fn: Callable[[str, str], Any]):
        self.store = store
        self.cfg_provider = cfg_provider
        self.send_fn = send_fn
        self.commands: Dict[str, Command] = {}
        # runtime per-channel state
        self.channel_states: Dict[str, ChannelState] = {}

    def ensure_channel_state(self, channel: str) -> ChannelState:
        ch_state = self.channel_states.get(channel)
        if ch_state is None:
            ch_state = ChannelState(channel)
            self.channel_states[channel] = ch_state
        return ch_state

    def register(self, cmd_cls: type[Command]):
        # создаём экземпляр позже? или сразу? Поскольку командам нужен доступ к channel_states, создадим один shared экземпляр.
        inst = cmd_cls(self.store, self.cfg_provider, self.send_fn, self.channel_states)
        self.commands[cmd_cls.name] = inst

    async def handle_message(self, msg: ChatMessage) -> None:
        # записываем активность пользователя
        ch_state = self.ensure_channel_state(msg.channel)
        ch_state.record_message(msg)

        # watchers (зависят от активных сессий)
        await riot_message_watcher(msg, ch_state=ch_state, send_fn=self.send_fn)
        await sock_message_watcher(msg, ch_state=ch_state, send_fn=self.send_fn)

        # если не команда → всё
        if not msg.text.startswith('!'):
            return

        cmd_name = msg.text.split()[0].lower()
        cmd = self.commands.get(cmd_name)
        if not cmd:
            return
        ctx = CommandContext(channel=msg.channel, user=msg.user, message=msg.text, tags=msg.tags, ts=msg.ts)
        await cmd.handle(ctx)


# =============================================================
# Пример интеграции / заглушка транспорта
# =============================================================

async def example_send(channel: str, text: str):
    # тут надо подставить вызов twitchAPI.chat.send_message(...)
    print(f"[{channel}] BOT: {text}")


async def example_run():
    # backend + store
    store = StateStore(InMemoryBackend(max_entries=5000))
    cfg_provider = ChannelConfigProvider()

    # пример конфигов: во втором канале отключим !рандом
    cfg_provider.set_config(ChannelConfig(channel="streamer1", commands={
        "!рандом": CommandSettings(enabled=True, cooldown_s=5),
        "!хвост": CommandSettings(enabled=True),
        "!обнять": CommandSettings(enabled=True),
        "!начатьбунт": CommandSettings(enabled=True),
        "!закончитьбунт": CommandSettings(enabled=True),
        "!носок": CommandSettings(enabled=True),
        "!отмена": CommandSettings(enabled=True),
    }))
    cfg_provider.set_config(ChannelConfig(channel="streamer2", commands={
        "!рандом": CommandSettings(enabled=False),
        "!хвост": CommandSettings(enabled=True),
        "!обнять": CommandSettings(enabled=True),
        "!начатьбунт": CommandSettings(enabled=True),
        "!закончитьбунт": CommandSettings(enabled=True),
        "!носок": CommandSettings(enabled=True),
        "!отмена": CommandSettings(enabled=True),
    }))

    mgr = CommandManager(store, cfg_provider, example_send)
    # регистрируем команды
    for cls in (RandomCommand, TailCommand, HugCommand, RiotStartCommand, RiotStopCommand, SockStartCommand, SockCancelCommand):
        mgr.register(cls)

    # смоделируем поток сообщений
    msgs = [
        ChatMessage(channel="streamer1", user="Alice", text="!рандом"),
        ChatMessage(channel="streamer1", user="Bob", text="hi"),
        ChatMessage(channel="streamer1", user="Alice", text="!хвост"),
        ChatMessage(channel="streamer1", user="Bob", text="!начатьбунт"),
        ChatMessage(channel="streamer1", user="Charlie", text="+") ,
        ChatMessage(channel="streamer1", user="Dana", text="!бунт"),
        ChatMessage(channel="streamer1", user="Streamer", text="!закончитьбунт", tags={'badges':'broadcaster/1'}),
        ChatMessage(channel="streamer1", user="Eve", text="!носок"),
        ChatMessage(channel="streamer1", user="Foo", text="+"),
        ChatMessage(channel="streamer1", user="Bar", text="+"),
        ChatMessage(channel="streamer1", user="Eve", text="+"),
        # Попытка отмены не тем юзером
        ChatMessage(channel="streamer1", user="Alice", text="!отмена"),
        # Отмена владельцем носка (допустим выбранным был Bob; в примере target случайный, не гарантировано)
    ]

    for m in msgs:
        await mgr.handle_message(m)

    # Подождём завершение активного розыгрыша (если не отменили)
    await asyncio.sleep(65)


if __name__ == "__main__":
    asyncio.run(example_run())