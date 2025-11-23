import logging

from database.models import User, TwitchUserSettings
from routers.schemas import ChatMessageWebhookEventSchema
from twitch.chat.commands import PantsCommand
from twitch.chat.handlers.handlers import CommonMessagesHandler, HandlerResult
from twitch.state_manager import SMParam

logger = logging.getLogger(__name__)


class PantsRaffleHandler(CommonMessagesHandler):
    def is_enabled(self, streamer_settings: TwitchUserSettings) -> bool:
        return streamer_settings.enable_pants

    async def handle(
        self, streamer: User, message: ChatMessageWebhookEventSchema
    ) -> HandlerResult:
        if message.message.text.strip() not in ("+", "-"):
            return HandlerResult.SKIPED

        logger.info(f"Handled `{message.message.text.strip()}`")
        target = await self._state_manager.get_state(
            channel=streamer.login_name,
            command=PantsCommand.command_name,
            param=SMParam.USER
        )
        participants: set[str] = await self._state_manager.get_state(
            channel=streamer.login_name,
            command=PantsCommand.command_name,
            param=SMParam.PARTICIPANTS
        )

        if message.message.text.strip() == '-':
            if message.chatter_user_login == target.lower():
                await self.send_response(
                    chat=streamer,
                    message=f'@{message.chatter_user_name} не хочет отдавать свои трусы, поэтому розыгрыш отменяется. Простите, ребят :<'
                )
                await self._state_manager.del_state(
                    channel=streamer.login_name,
                    command=PantsCommand.command_name,
                    param=SMParam.USER
                )
                await self._state_manager.del_state(
                    channel=streamer.login_name,
                    command=PantsCommand.command_name,
                    param=SMParam.PARTICIPANTS
                )
                return HandlerResult.HANDLED
            else:
                return HandlerResult.SKIPED

        if target is None or participants is None:
            logger.info("Raffle was not ran on the channel")
            return HandlerResult.SKIPED

        if message.chatter_user_name in participants:
            logger.info("Already in participants. Skip.")
            return HandlerResult.SKIPED

        participants.add(message.chatter_user_name)
        await self._state_manager.set_state(
            value=participants,
            channel=streamer.login_name,
            command=PantsCommand.command_name,
            param=SMParam.PARTICIPANTS,
        )
        logger.info("Added to raffle!")

        if len(participants) == 5:
            await self.send_response(
                chat=streamer,
                message=f'Уже целых 5 человек хотят заполучить трусы @{target}! Ничего себе! А ты пользуешься популярностью ;)'
            )
        return HandlerResult.HANDLED