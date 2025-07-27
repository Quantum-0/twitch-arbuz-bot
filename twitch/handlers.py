import random
from time import time
from typing import TYPE_CHECKING

from twitchAPI.chat import ChatMessage

from twitch.state_manager import get_state_manager, SMParam
from twitch.utils import extract_targets, join_targets

if TYPE_CHECKING:
    from twitch.bot import ChatBot





# async def cmd_bite_handler(chatbot: "ChatBot", channel: str, message: ChatMessage):
#     targets = extract_targets(message.text, channel)
#     target = join_targets(targets)
#     sm = get_state_manager()
#     user = message.user.display_name
#     if not target:
#         await chatbot.send_message(channel, f"Чтобы укусить кого-то, нужно указать, кого именно кусаешь. Например \"!кусь @{message.user.display_name}\"")
#         return
#     kind_of_bite = ["злобный", "приятный", "мягкий", "нежный", "аккуратный", "агрессивный", "коварный"]
#     target_to_bite = ["ухо", "пятку", "хвост", "ногу", "пэрсики", "нос", "плечо", "жёпку"]
#     last_bite = await sm.get_state(channel=channel, user=user, command="bite", param=SMParam.COOLDOWN)
#     bites_count = await sm.get_state(channel=channel, user=user, command="bite", param=SMParam.CALL_COUNT)
#     if last_bite and time() - last_bite < 15:
#         if bites_count > 3:
#             delay = 15 - int(time() - last_bite)
#             # TODO: твои зубки в перезарядке
#             if delay > 4:
#                 await chatbot.send_message(channel, f"@{user} , твои зубки устали кусаться, подожди {delay} секундочек, прежде чем делать новый кусь!")
#             elif delay > 1:
#                 await chatbot.send_message(channel, f"@{user} , твои зубки устали кусаться, подожди {delay} секундочки, прежде чем делать новый кусь!")
#             else:
#                 await chatbot.send_message(channel, f"@{user} , твои зубки устали кусаться, подожди секундочку, прежде чем делать новый кусь!")
#             return
#         await sm.set_state(channel=channel, user=user, command="bite", param=SMParam.CALL_COUNT, value=bites_count + 1)
#     else:
#         await sm.set_state(channel=channel, user=user, command="bite", param=SMParam.CALL_COUNT, value=1)
#         await sm.set_state(channel=channel, user=user, command="bite", param=SMParam.COOLDOWN, value=time())
#     # if len(targets) == 1:
#     #     bites_received = await sm.get_state(channel=channel, user=target[1:], command="bite", param=SMParam.COUNT_RECEIVED)
#     if len(targets) == 1 and user.lower() == target[1:].lower():
#         await chatbot.send_message(channel, random.choice([f"@{user} кусает сам себя о.О", f"@{user} совершает САМОКУСЬ!"]))
#         return
#     if len(targets) == 1 and target[1:].lower() in ["streamelements", "wisebot"]:
#         await chatbot.send_message(channel, random.choice([f"{target} простите за беспокойство, коллега-бот, но пользователь @{user} делает вам кусьб"]))
#         return
#     if len(targets) == 1 and target[1:].lower() == "quantum075bot":
#         await chatbot.send_message(channel, random.choice([f"@{user}, а меня то за что?!", f"Меня кусать нельзя, кусай кого-нибудь другого!", f"Ну капец, уже на ботов своими зубами нападают..", f"@{user}, щас как сам тебя укушу >:c Банхамером!!!"]))
#         return
#     await chatbot.send_message(channel, f"@{user} делает {random.choice(kind_of_bite)} кусь {target} за {random.choice(target_to_bite)}")


# async def cmd_lick_handler(chatbot: "ChatBot", channel: str, message: ChatMessage):
#     target = join_targets(extract_targets(message.text, channel))
#     if not target:
#         await chatbot.send_message(channel, f"Чтобы кого-то лизнуть, нужно указать, кого именно ты хочешь лизнуть. Например \"!лизь @{message.user.display_name}\"")
#         return
#     user = message.user.display_name
#     random_variants = [
#         f'{user} вылизывает всё лицо {target}',
#         f'{user} облизывает ухо {target}',
#         f'{user} лижет в нос {target}',
#         f'{user} пытается лизнуть {target}, но {target} успешно уворачивается от нападения языком!',
#     ]
#     await chatbot.send_message(channel, random.choice(random_variants))


# async def cmd_boop_handler(chatbot: "ChatBot", channel: str, message: ChatMessage):
#     target = join_targets(extract_targets(message.text, channel))
#     if not target:
#         await chatbot.send_message(channel, f"Чтобы бупнуть кого-нибудь в носярку, нужно указать, кого ты хочешь бупнуть! Например \"!лизь @{message.user.display_name}\"")
#         return
#     user = message.user.display_name
#     await chatbot.send_message(channel, f"{user} делает буп в нось {target} !")
