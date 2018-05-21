import sys
import logging
from datetime import datetime
from datetime import timedelta
from datetime import time
from time import sleep
import random
import telethon
from telethon import events
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest


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


def from_cw_bot(method):
    def wrap(self, event):
        if self._is_cw_bot(event):
            return method(self, event)
    return wrap


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
        trigger = 'Подробнее:'
        return msg.count(trigger) > 1

    def is_fight_message(self, msg):
        return '/fight' in msg

    def is_buisy(self, msg):
        return 'Ты отправился искать приключения' in msg

    def is_stats(self, msg):
        return 'Подробнее: /hero' in msg

    def is_arena_message(self, msg):
        return 'выбери точку атаки и точку защиты' in msg

    def is_arena_state(self, msg):
        return 'Добро пожаловать на арену!' in msg

    def is_market_message(self, msg):
        marker = 'предлагает:'
        return msg.count(marker) == 2


class State:
    cw_id = 587303845
    update_interval = timedelta(minutes=10)
    _last_updated = datetime.utcfromtimestamp(0)
    _state = {}
    get_state_msg = ''

    def _parse(self, msg):
        raise NotImplementedError

    def get_current_target(self):
        raise NotImplementedError

    def update(self, client):
        now = datetime.now()
        if now > self._last_updated + self.update_interval:
            cw = client.get_entity(self.cw_id)
            client.send_message(cw, self.get_state_msg)

    def update_from_message(self, msg):
        state_dict = self._parse(msg)
        self._state.update(state_dict)
        self._last_updated = datetime.now()
        logger.info('Current state: %s', self._state)


def is_night(dt):
    night_hour_periods = (
        (0, 2),
        (8, 10),
        (16, 18),
    )
    current_hour = dt.hour
    for begin, end in night_hour_periods:
        if current_hour in range(begin, end):
            return True
    return False


def is_battle_soon(dt):
    border_period = timedelta(minutes=10)
    current_time = dt.time()
    current_hour = current_time.hour
    future_hour = (dt + border_period).hour
    return future_hour != current_hour and future_hour % 4 == 0


class HeroState(State):
    get_state_msg = '🏅Герой'

    def __init__(self):
        self._state = {
            'stamina': 0,
            'gold': 0,
        }

    @property
    def stamina(self):
        return self._state.get('stamina')

    def _parse(self, msg):
        gold_marker = '💰'
        stamina_marker = '🔋Выносливость:'

        state_dict = {}

        for line in msg.split('\n'):
            if stamina_marker in line:
                line = line.replace(stamina_marker, '').strip()
                current, total = line.split('/')
                state_dict['stamina'] = int(current)

            if gold_marker in line:
                gold, _ = line.split(' ')
                gold = gold.replace(gold_marker, '')
                state_dict['gold'] = int(gold)

        return state_dict

    def get_current_target(self):
        now = datetime.now()
        if is_night(now):
            return None
        return '🌲Лес' if self.stamina > 0 else None


class ArenaState(State):
    get_state_msg = '📯Арена'

    def __init__(self):
        self._state = {
            'current': 0,
            'total': 0,
        }

    @property
    def can_fight(self):
        return self._state['current'] < self._state['total']

    def _parse(self, msg):
        marker = '⌛Поединков сегодня'
        state_dict = {}
        for line in msg.split('\n'):
            if marker in line:
                state_line = line.replace(marker, '').replace('**', '').strip()
                current, total = state_line.split(' из ')
                state_dict['current'] = int(current)
                state_dict['total'] = int(total)
        return state_dict

    def get_current_target(self):
        now = datetime.now()
        good_times = (
            (13, 15),
            (21, 23),
        )

        now_is_good_time = any([
            now.hour in range(begin, end)
            for begin, end in good_times
        ])
        if not now_is_good_time:
            return None

        return '🔎Поиск соперника' if self.can_fight else None

    def get_attack_target(self):
        choices = [
            '🗡в голову',
            '🗡по корпусу',
            '🗡по ногам',
        ]
        return random.choice(choices)

    def get_defence_target(self):
        choices = [
            '🛡головы',
            '🛡корпуса',
            '🛡ног',
        ]
        return random.choice(choices)


class ConstructionState(State):

    get_state_msg = '🏘Постройки'
    repair_priority = [
        'wall',
        'hunters',
        'gladiators',
        'stash',
        'hq',
        'monument',
    ]

    def __init__(self):
        initial_state = {
            target: 0
            for target in self.repair_priority
        }
        self._state = initial_state
        self._target = None
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

    def get_current_target(self):
        for target in self.repair_priority:
            current_state = self._state.get(target)
            if current_state < 100:
                return f'/repair_{target}'
        return self._target

    def set_current_target(self, target):
        self._target = target


class ChatController:

    son_id = 420406021  # Бот посылающий приказы, которым следовать
    tcb_id = 335184999  # Еще один бот для приказов, которым следовать
    cw_id = 587303845  # Бот чв
    squad_id = 1225237775  # id чата, откуда брать пины для следования в чв
    flags_there_id = 1338302986  # Куда скидывать пины

    sleep_time = (
        time(hour=1),
        time(hour=8),
    )

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
        self._hero_state = HeroState()
        self._arena_state = ArenaState()
        self.son = client.get_entity(self.son_id)
        self.tcb = client.get_entity(self.tcb_id)
        self.cw = client.get_entity(self.cw_id)
        self.squad = client.get_entity(self.squad_id)
        self.flags_there = client.get_entity(self.flags_there_id)

        now = datetime.now()
        self._last_try = now
        self._retry_at = now

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
    def _is_sleeping(self):
        begin, end = self.sleep_time
        now = datetime.now()
        return begin < now.time() < end

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
        return event.chat.id == self.cw_id

    def is_squad_chat(self, event):
        return event.chat.id == self.squad_id

    def _construction_state_handler(self, event):
        msg = str(event.text)
        if self._is_cw_bot and self._matcher.is_construction_report(msg):
            logging.info('Got message about construction state: %s', msg)
            self._state.update_from_message(msg)

    @from_cw_bot
    def _arena_state_handler(self, event):
        msg = event.text
        if self._matcher.is_arena_state(msg):
            logging.info('Got message about construction state: %s', msg)
            self._arena_state.update_from_message(msg)

    def _is_buisy_handler(self, event):
        msg = str(event.text)
        if self._is_cw_bot and self._matcher.is_buisy(msg):
            self.update_state()

    def _update_try(self, retry_after=30):
        now = datetime.now()
        self._last_try = now
        if retry_after:
            # max_timeout = 5 * 60
            # new_retry_time = self._retry_at + timedelta(seconds=retry_after)
            # diff = now - new_retry_time
            # new_timeout = min(max_timeout, diff.seconds)
            self._retry_at = now + timedelta(seconds=retry_after)
            logger.info('Set new retry timer to %s', self._retry_at.time())

    def _private_message_handler(self, event):
        if self._is_cw_bot(event):
            msg = str(event.text)
            m = self._matcher

            # if 'foo' in event.text:
            #     buttons = event.message.reply_markup.rows[0].buttons
            #     first_btn_data = buttons[0].data
            #     self._client(
            #         GetBotCallbackAnswerRequest(
            #             event.chat.id,
            #             event.message.id,
            #             data=first_btn_data,
            #         )
            #     )
            #     logger.info('Chose first button')

            if m.is_build_report(msg):
                self._client.forward_messages(self.son, [event.message])
                self._client.forward_messages(self.tcb, [event.message])

                self._state.update(self._client)
                self._update_try(10)
                logger.info("Я построиль")

            elif m.cannot_build_now(msg):
                self._update_try(60)

            elif m.is_corovan_in_danger(msg):
                client.send_message(self.cw, "/go")

    def _fight_handler(self, event):
        if self._matcher.is_fight_message(event.text):
            if self._is_cw_bot(event):
                logger.info('Got fight message: %s', event.text)
                self._client.forward_messages(self.cw, [event.message])
            #     self._client.forward_messages(self.squad, [event.message])
            else:
                logger.info('Got fight message: %s', event.text)
                self._client.forward_messages(self.cw, [event.message])

    def _hero_handler(self, event):
        if self._is_cw_bot(event) and self._matcher.is_stats(event.text):
            logger.info('Got stats message: %s', event.text)
            self._hero_state.update_from_message(event.text)

    @from_cw_bot
    def _arena_handler(self, event):
        if self._matcher.is_arena_message(event.text):
            logger.info('Got arena message: %s', event.text)
            attack = self._arena_state.get_attack_target()
            defence = self._arena_state.get_defence_target()

            sleep(3)
            client.send_message(self.cw, attack)
            sleep(3)
            client.send_message(self.cw, defence)

    # def _market_handler(self, event):
    #     if self._matcher.is_market_message(event.text):
    #         logger.info('got marget message: %s', event.text)

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

    def _get_current_target(self):
        hero_target = self._hero_state.get_current_target()
        if hero_target is not None:
            return hero_target

        arena_target = self._arena_state.get_current_target()
        if arena_target is not None and not self._is_sleeping:
            return arena_target

        build_target = self._state.get_current_target()
        if build_target is not None:
            return build_target

    def do_action(self):
        target = self._get_current_target()
        if not self._can_retry:
            diff = self._retry_at - datetime.now()
            logger.warning(
                'Cannot retry now. Will try in %s seconds.',
                diff.seconds,
            )
            return
        if target:
            logger.info('Got target %s', target)
            self._client.send_message(self.cw, target)
            self._update_try(5 * 60)
        else:
            logger.warning(
                'Could not get target. State: %s', self._state)

    @property
    def states(self):
        return (
            self._hero_state,
            self._arena_state,
            self._state,
        )

    def update_state(self):
        now = datetime.now()
        if is_battle_soon(now):
            self._update_try(10 * 60)
            return

        for state in self.states:
            state.update(self._client)
            sleep(5)

    def run(self):
        logger.info('Started')

        while True:
            if self._can_retry:
                self.update_state()
                self.do_action()


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
