#!/usr/bin/env python3
"""Сообщает сессии pi о завершении команды, запущенной в панели tmux.

Вызывается обёрткой tmux.py изнутри панели, после того как команда
закончилась: несёт исход, время, команду и урезанный финальный вывод.
Доставка идёт через брокер pi-intercom (intercom.py); сессия получает
сообщение и ход, поэтому агенту не нужно опрашивать панель.

See SKILL.md (same directory) -- the skill these scripts implement.
"""

from __future__ import annotations

import argparse
import sys

from intercom import IntercomError, send
from output import format_duration, hint, truncate


def main() -> int:
    args = _parse_args()
    try:
        send(args.to, _compose_text(args))
    except IntercomError as e:
        print(f'уведомление не отправлено: {e}', file=sys.stderr)
        return 1
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--to',
        required=True,
        help='идентификатор сессии pi (PI_INTERCOM_SESSION_ID)',
    )
    parser.add_argument(
        '--code', required=True, type=int, help='код возврата команды'
    )
    parser.add_argument(
        '--place',
        required=True,
        help='где выполнялась команда, фразой обёртки',
    )
    parser.add_argument(
        '--elapsed',
        type=int,
        default=None,
        help='время работы команды в секундах',
    )
    parser.add_argument(
        '--command',
        default=None,
        help='команда, о завершении которой сообщаем',
    )
    parser.add_argument(
        '--dump',
        default=None,
        help='путь к файлу с сохранённым выводом панели',
    )
    return parser.parse_args()


def _compose_text(args: argparse.Namespace) -> str:
    """Текст сообщения: исход, команда, урезанный вывод и хвост."""
    outcome = 'успешно' if args.code == 0 else f'с кодом возврата {args.code}'
    duration = (
        f' за {format_duration(args.elapsed)}'
        if args.elapsed is not None
        else ''
    )
    lines = [f'{args.place}: команда завершилась {outcome}{duration}.']
    if args.command:
        lines += ['', f'`{args.command}`']
    if args.dump:
        lines += _output_lines(args.dump)
    return '\n'.join(lines)


def _output_lines(dump: str) -> list[str]:
    """Блок вывода и хвост; дамп не читается — честная строка вместо них."""
    text = _read_dump(dump)
    if text is None:
        return ['', f'Вывод не удалось прочитать: `{dump}`']
    truncation = truncate(text)
    lines = ['', truncation.block] if truncation.block else []
    lines += ['', f'Вывод сохранён: `{dump}`']
    tail = hint(truncation, dump)
    if tail:
        lines.append(tail)
    return lines


def _read_dump(path: str) -> str | None:
    """Содержимое дампа или None, если файл не читается."""
    try:
        with open(path, encoding='utf-8', errors='replace') as handle:
            return handle.read()
    except OSError:
        return None


if __name__ == '__main__':
    raise SystemExit(main())
