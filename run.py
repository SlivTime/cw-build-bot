import sys
import logging
from datetime import datetime
from datetime import timedelta
import telethon
from telethon import events


logger = logging.getLogger()
logging.basicConfig(
    level=logging.INFO,
)
# Лишние id можно заменить на -1 наверно

ignore_id = [
    1304128519,
    1225237775,
]  # Не сливать же академку и червей?)


write_messages = True  # Надо ли выводить в консоль поступающие сообщения
write_errors = True
spy = False

CFG_EXAMPLE = """
API_ID = 0
API_HASH = ''
"""


def bootstrap():
    with open('config.py', 'w') as f:
        f.write(CFG_EXAMPLE)


class Matcher:
    def is_build_report(self, msg):
        return any([
            msg.startswith('Ты вернулся со стройки'),
            msg.startswith('Здание отремонтировано'),
        ])

    def cannot_build_now(self, msg):
        return any([
            msg.startswith(
                "В казне недостаточно ресурсов для строительства"
            ),
            msg.startswith(
                "Ветер завывает по окрестным лугам"
            ),
            msg.startswith(
                "Битва близко. Сейчас не до приключений."
            ),
            msg.startswith(
                "Ты сейчас занят другим приключением. Попробуй позже."
            ),
        ])

    def is_corovan_in_danger(self, msg):
        return msg.startswith("Ты заметил")

    def is_construction_report(self, msg):
        return 'Подробнее:' in msg

    def is_fight_message(self, msg):
        return '/fight' in msg


class ConstructionState:
    cw_id = 265204902
    get_state_msg = '🏘Постройки'
    repair_priority = [
        'wall',
        'hunters',
        'gladiators',
        'stash',
        'hq',
        'monument',
    ]
    update_interval = timedelta(minutes=3)

    def __init__(self):
        initial_state = {
            target: 0
            for target in self.repair_priority
        }
        self._state = initial_state
        self._last_updated = datetime.utcfromtimestamp(0)

    @property
    def state(self):
        return self._state

    def _parse(self, msg):
        state_dict = {}
        for item in msg.split('\n\n'):
            state = 0
            id_ = None
            state_line, id_line = item.strip().split('\n')

            if state_line.endswith('%'):
                full_state = state_line.split(' ')[-1]
                num_state = full_state.replace('%', '').strip()
                state = num_state

            if '/to' in id_line:
                id_ = id_line.split('_')[-1]

            state_dict[id_] = int(state)
        return state_dict

    def update(self, client):
        now = datetime.now()
        if now > self._last_updated + self.update_interval:
            cw = client.get_entity(self.cw_id)
            client.send_message(cw, self.get_state_msg)

    def update_from_message(self, msg):
        state_dict = self._parse(msg)
        self._state.update(state_dict)
        

    def get_current_target(self):
        for target in self.repair_priority:
            current_state = self._state.get(target)
            if current_state < 100:
                return f'/repair_{target}'


class ChatController:

    son_id = 420406021  # Бот посылающий приказы, которым следовать
    tcb_id = 335184999  # Еще один бот для приказов, которым следовать
    cw_id = 265204902  # Бот чв
    squad_id = 1225237775  # id чата, откуда брать пины для следования в чв
    flags_there_id = 1338302986  # Куда скидывать пины

    flags = (
        "🇻🇦",  # жз
        "🇲🇴",  # мз
        "🇮🇲",  # кз
        "🇪🇺",  # сз
        "🇬🇵",  # чз
        "🇨🇾",  # бз
        "🇰🇮",  # суз
        "⚓️",  # мф
        "🌲",  # лф
        "⛰",  # гф
    )

    def __init__(self, client):
        self._client = client
        self._matcher = Matcher()
        self._state = ConstructionState()
        self.son = client.get_entity(self.son_id)
        self.tcb = client.get_entity(self.tcb_id)
        self.cw = client.get_entity(self.cw_id)
        self.squad = client.get_entity(self.squad_id)
        self.flags_there = client.get_entity(self.flags_there_id)

        self._last_try = None
        self._retry_at = None

        self._init_callbacks()

    def _init_callbacks(self):
        msg_event = self._client.on(events.NewMessage)
        for handler in self.message_handlers:
            # bound_handler = partial(handler, controller=self)
            msg_event(handler)

    @property
    def _can_retry(self):
        return datetime.now() > self._retry_at

    @property
    def message_handlers(self):
        """
        Return list of methods which names ends with `handler`
        """
        return [
            getattr(self, method_name)
            for method_name in dir(self)
            if method_name.endswith('handler')
        ]

    def _check_for_flag(self, msg):
        for flag in self.flags:
            if flag in msg:
                return flag

    def _group_handler(self, event):
        if event.is_group and event.chat.id in self.watch_ids:
            flag = self._check_for_flag(event.text)
            if flag:  # Если отправили сообщение с флагом
                logger.info(
                    "FLAG!! %s, %s \n message: %s",
                    flag,
                    self.flags.index(flag),
                    event.text,
                )
                if all([
                    event.chat.id == self.squad_id,
                    event.sender.id in (self.son_id, self.tcb_id),
                    event.message.reply_markup is not None
                ]):
                    # Следуем по пину автоматически
                    self._client.send_message(self.cw, flag)
                if all([
                    spy,
                    event.chat.id not in ignore_id,
                    self.flags_there is not None,
                    self.flags_there != -1,
                ]):
                    msg = f"{event.chat.title}\n`{flag}`"
                    client.send_message(self.flags_there, msg)
                    # Сливаем пин (если только это не академка)

    def _is_cw_bot(self, event):
        return all([
            event.is_private,
            event.chat.bot,
            event.chat.id == self.cw_id,
        ])

    def is_squad_chat(self, event):
        return event.chat.id == self.squad_id

    def _construction_state_handler(self, event):
        msg = str(event.text)
        if self._is_cw_bot and self._matcher.is_construction_report(msg):
            logging.info('Got message about construction state: %s', msg)
            self._state.update_from_message(msg)

    def _update_try(self, retry_after=30):
        self._last_try = datetime.now()
        if retry_after:
            self._retry_at = self._last_try + timedelta(seconds=retry_after)

    def _private_message_handler(self, event):
        if self._is_cw_bot:
            msg = str(event.text)
            m = self._matcher

            if m.is_build_report(msg):
                self._client.forward_messages(self.son, [event.message])
                self._client.forward_messages(self.tcb, [event.message])

                self._state.update(self._client)
                self.go_build()

                self._update_try(5 * 60)
                logger.info("Я построиль")

            elif m.cannot_build_now(msg):
                self._update_try(60)

            elif m.is_corovan_in_danger(msg):
                client.send_message(self.cw, "/go")

    def _fight_handler(self, event):
        if self.is_squad_chat(event) and \
                self._matcher.is_fight_message(event.text):
            logger.info('Got fight message: %s', event.text)
            self._client.forward_messages(self.cw, [event.message])

    def go_build(self):
        target = self._state.get_current_target()
        if not self._can_retry:
            diff = self._retry_at - datetime.now()
            logger.warning(
                'Cannot retry now. Will try in %s seconds.',
                diff.seconds,
            )
            return
        if target:
            logger.info('Got target for building %s', target)
            self._client.send_message(self.cw, target)
            self._update_try(5 * 60)
        else:
            logger.warning(
                'Could not get target for building. State: %s', self._state)

    @property
    def watch_ids(self):
        return (
            self.son_id,
            self.tcb_id,
            self.cw_id,
            self.squad_id,
            self.flags_there_id,
        )

    def run(self):
        logger.info('Started')

        self._state.update(self._client)
        self._update_try(5)

        while True:
            if self._can_retry:
                self.go_build()


if __name__ == '__main__':
    try:
        import config
    except ImportError:
        bootstrap()
        logger.error('Empty config is created. Add id and hash there.')
        sys.exit(0)

    client = telethon.TelegramClient(
        'session_name',
        config.API_ID,
        config.API_HASH,
        update_workers=1
    )
    client.start()

    controller = ChatController(client)
    try:
        controller.run()
    except KeyboardInterrupt:
        logger.info("Got SigInt. Closing now.")
        sys.exit(0)
