#!/usr/bin/env python3
"""Промежуточные уведомления о ходе команды в панели tmux.

Живёт фоновым потомком обёртки tmux.py: спит по расписанию T·M^i, снимает
снапшот панели, атомарно перезаписывает файл полного вывода и шлёт сессии
pi урезанный блок. Сбои доставки и записи — только в лог: сообщение
вспомогательное. Сигнал INT (Ctrl+C) — тихий выход.

See SKILL.md (same directory) -- the skill these scripts implement.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

import intercom
import output
import tmux

# Паузы между отправками: T·M^i; всего N отправок.
T = 30
M = 4
N = 4


def main() -> int:
    args = _parse_args()
    pane = os.environ.get('TMUX_PANE')
    if not pane:
        print(
            'нет TMUX_PANE: нотификатор запускается обёрткой в панели',
            file=sys.stderr,
        )
        return 1
    signal.signal(signal.SIGINT, _quiet_exit)
    try:
        _notify_loop(
            pane=pane,
            place=args.place,
            command=args.command,
            target=args.to,
            dump_path=output.dump_path(os.path.expanduser(tmux.DUMP_DIR), pane),
        )
    except tmux.TmuxError as e:
        print(f'панель не читается: {e}', file=sys.stderr)
        return 1
    return 0


def _quiet_exit(signum, frame):
    """Ctrl+C: выход без traceback (обработчик-флаг не прервал бы sleep)."""
    raise SystemExit(0)


def _notify_loop(
    *,
    pane: str,
    place: str,
    command: str,
    target: str,
    dump_path: str,
    capture=tmux.tmux,
    deliver=intercom.send,
    sleep=time.sleep,
):
    """Снапшоты по расписанию: дедупликация, файл вывода, отправка.

    Время в сообщении — номинал дедлайна: паузы накопленные, стенные часы
    не измеряются.
    """
    elapsed = 0
    last_sent = None
    for pause in _pauses():
        elapsed += pause
        sleep(pause)
        snapshot = capture('capture-pane', '-p', '-S', '-', target=pane)
        if not snapshot or snapshot == last_sent:
            continue
        source = dump_path if _save_dump(dump_path, snapshot) else None
        text = _compose_text(place, command, elapsed, snapshot, source)
        if _deliver(deliver, target, text):
            last_sent = snapshot


def _pauses():
    """Паузы между отправками: T·M^i, i = 0..N-1."""
    pause = T
    for _ in range(N):
        yield pause
        pause *= M


def _compose_text(
    place: str, command: str, elapsed: int, snapshot: str, source: str | None
) -> str:
    """Текст сообщения: место, время, команда, урезанный блок и хвост.

    source — файл полного вывода, если запись прошла; None — хвост не
    выводится.
    """
    truncation = output.truncate(snapshot)
    lines = [
        f'{place}: промежуточный вывод после '
        f'{output.format_duration(elapsed)}.'
    ]
    if command:
        lines += ['', f'`{command}`']
    lines += ['', truncation.block]
    if source:
        lines += ['', f'Вывод на момент отправки: `{source}`']
        tail = output.hint(truncation, source)
        if tail:
            lines.append(tail)
    return '\n'.join(lines)


def _save_dump(path: str, text: str) -> bool:
    """Атомарно перезаписать файл полного вывода; сбой — только в лог."""
    try:
        output.write_atomic(path, text)
    except OSError as e:
        print(f'вывод не сохранён: {e}', file=sys.stderr)
        return False
    return True


def _deliver(deliver, target: str, text: str) -> bool:
    """Отправка сообщения; сбой — только в лог, следующая отправка по плану."""
    try:
        deliver(target, text)
    except intercom.IntercomError as e:
        print(f'уведомление не отправлено: {e}', file=sys.stderr)
        return False
    return True


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--to',
        required=True,
        help='идентификатор сессии pi (PI_INTERCOM_SESSION_ID)',
    )
    parser.add_argument(
        '--place',
        required=True,
        help='где выполнялась команда, фразой обёртки',
    )
    parser.add_argument(
        '--command',
        default=None,
        help='команда, о ходе которой сообщаем',
    )
    return parser.parse_args()


if __name__ == '__main__':
    raise SystemExit(main())
