from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from enum import StrEnum, auto
from typing import OrderedDict


class SMParam(StrEnum):
    DEFAULT = auto()
    COOLDOWN = auto()
    CALL_COUNT = auto()
    # COUNT_RECEIVED = auto()
    PREVIOUS_VALUE = auto()
    PREVIOUS_VALUE_TIME = auto()
    LAST_APPLY = auto()

    # Pyramid
    USER = auto()
    EMOTE = auto()
    HEIGHT = auto()
    DIRECTION = auto()

    # Pants
    TARGET_COOLDOWN = auto()
    PARTICIPANTS = auto()


COMMAND_TYPE = str
CHANNEL_TYPE = str
USER_TYPE = int
VALUE_TYPE = str | int | float | None
PARAM_TYPE = SMParam

COMMON_CHANNEL: CHANNEL_TYPE = "COMMON"
COMMON_USER: USER_TYPE = -1
COMMON_COMMAND: COMMAND_TYPE = "ALL_COMMANDS"


class StateManager(ABC):
    @abstractmethod
    async def get_state(
        self,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ):
        raise NotImplementedError

    @abstractmethod
    async def set_state(
        self,
        value: VALUE_TYPE,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ):
        raise NotImplementedError

    @abstractmethod
    async def del_state(
        self,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ):
        raise NotImplementedError

    @abstractmethod
    async def get_all_from_channel(
        self,
        *,
        channel: str = COMMON_CHANNEL,
    ) -> AsyncIterator[tuple[USER_TYPE, COMMAND_TYPE, PARAM_TYPE, VALUE_TYPE]]:
        raise NotImplementedError

    @abstractmethod
    async def cleanup(self):
        raise NotImplementedError


class InMemoryStateManager(StateManager):
    async def get_all_from_channel(
        self,
        *,
        channel: str = COMMON_CHANNEL,
    ) -> AsyncIterator[tuple[USER_TYPE, COMMAND_TYPE, PARAM_TYPE, VALUE_TYPE]]:
        if channel not in self._storage:
            return
        for user in self._storage[channel]:
            for command in self._storage[channel][user]:
                for param in self._storage[channel][user][command]:
                    yield (
                        user,
                        command,
                        param,
                        self._storage[channel][user][command][param],
                    )

    async def del_state(
        self,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ):
        await self.set_state(
            value=None, channel=channel, user=user, command=command, param=param
        )

    def __init__(self, channels_size: int = 30, users_size: int = 100):
        self.channels_size = channels_size
        self.users_size = users_size
        self._storage: OrderedDict[
            CHANNEL_TYPE,
            OrderedDict[
                USER_TYPE,
                OrderedDict[COMMAND_TYPE, OrderedDict[PARAM_TYPE, VALUE_TYPE]],
            ],
        ] = OrderedDict[
            CHANNEL_TYPE,
            OrderedDict[
                USER_TYPE,
                OrderedDict[COMMAND_TYPE, OrderedDict[PARAM_TYPE, VALUE_TYPE]],
            ],
        ]()

    async def get_state(
        self,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ) -> VALUE_TYPE:
        channel = channel.lower()
        command = command.lower()
        param = param.lower()
        if isinstance(user, str):
            user = user.lower()
        if channel in self._storage:
            if user in self._storage[channel]:
                if command in self._storage[channel][user]:
                    if param in self._storage[channel][user][command]:
                        return self._storage[channel][user][command][param]
        return None

    async def set_state(
        self,
        value: VALUE_TYPE,
        *,
        channel: str = COMMON_CHANNEL,
        user: int = COMMON_USER,
        command: str = COMMON_COMMAND,
        param: PARAM_TYPE = PARAM_TYPE.DEFAULT,
    ):
        channel = channel.lower()
        command = command.lower()
        param = param.lower()
        if isinstance(user, str):
            user = user.lower()
        if channel not in self._storage:
            self._storage[channel] = OrderedDict[
                int, OrderedDict[str, OrderedDict[SMParam, VALUE_TYPE]]
            ]()
        if user not in self._storage[channel]:
            self._storage[channel][user] = OrderedDict[
                str, OrderedDict[SMParam, VALUE_TYPE]
            ]()
        if command not in self._storage[channel][user]:
            self._storage[channel][user][command] = OrderedDict[SMParam, VALUE_TYPE]()
        if value is None and param in self._storage[channel][user][command]:
            del self._storage[channel][user][command][param]
        else:
            self._storage[channel][user][command][param] = value
        await self.cleanup()

    async def cleanup(self):
        for channel, users_dict in self._storage.items():
            for user, commands_dict in users_dict.items():
                for command, param_dict in commands_dict.items():
                    for param, value in param_dict.items():
                        pass  # TODO: придумать чота


_sm: StateManager = InMemoryStateManager()


def get_state_manager():
    return _sm
