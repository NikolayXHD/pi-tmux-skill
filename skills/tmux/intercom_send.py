#!/usr/bin/env python3
"""Сообщает сессии pi о завершении команды, запущенной в панели tmux.

Протокол брокера pi-intercom: кадр — 4 байта длины и JSON, сначала
регистрация отправителя, затем само сообщение.

Вызывается обёрткой tmux.py изнутри панели, после того как команда
закончилась. Доставка идёт через брокер pi-intercom: сессия получает
сообщение и ход, поэтому агенту не нужно опрашивать панель.

Отказ доставки печатается одной строкой и не влияет на панель.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import struct
import sys
import time
import uuid

AGENT_DIR = os.environ.get('PI_CODING_AGENT_DIR') or os.path.expanduser('~/.pi/agent')
SOCKET_PATH = os.path.join(AGENT_DIR, 'intercom', 'broker.sock')
CONNECT_TIMEOUT = 5.0
REPLY_TIMEOUT = 5.0


def main() -> int:
    args = _parse_args()
    try:
        reply = _send(args.to, _compose_text(args))
    except OSError as e:
        print(f'уведомление не отправлено: {e}', file=sys.stderr)
        return 1
    if reply.get('type') != 'delivered':
        print(f'уведомление не доставлено: {reply}', file=sys.stderr)
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
    """Текст сообщения: где, с каким исходом и что именно завершилось."""
    outcome = 'успешно' if args.code == 0 else f'с кодом возврата {args.code}'
    duration = (
        f' за {_format_duration(args.elapsed)}'
        if args.elapsed is not None
        else ''
    )
    lines = [f'{args.place}: команда завершилась {outcome}{duration}.']
    if args.command:
        lines += ['', f'`{args.command}`']
    if args.dump:
        lines += ['', f'Вывод сохранён: `{args.dump}`']
    return '\n'.join(lines)


def _format_duration(seconds: int) -> str:
    if seconds < 60:
        return f'{seconds} с'
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes} мин {rest} с'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} ч {minutes} мин'


def _send(target: str, text: str) -> dict:
    """Регистрируется в брокере, отправляет сообщение, возвращает его ответ."""
    now = int(time.time() * 1000)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(CONNECT_TIMEOUT)
        sock.connect(SOCKET_PATH)
        _write_frame(
            sock,
            {
                'type': 'register',
                'session': {
                    'name': 'tmux-pane',
                    'cwd': os.getcwd(),
                    'model': 'none',
                    'pid': os.getpid(),
                    'startedAt': now,
                    'lastActivity': now,
                },
            },
        )
        registered = _read_frame(sock)
        if not registered or registered.get('type') != 'registered':
            raise OSError(f'брокер не подтвердил регистрацию: {registered}')
        _write_frame(
            sock,
            {
                'type': 'send',
                'to': target,
                'message': {
                    'id': str(uuid.uuid4()),
                    'timestamp': now,
                    'content': {'text': text},
                },
            },
        )
        reply = _read_frame(sock)
        if reply is None:
            raise OSError('брокер не ответил на отправку')
        return reply


def _write_frame(sock: socket.socket, payload: dict) -> None:
    body = json.dumps(payload).encode()
    sock.sendall(struct.pack('>I', len(body)) + body)


def _read_frame(sock: socket.socket) -> dict | None:
    sock.settimeout(REPLY_TIMEOUT)
    header = _recv_exactly(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack('>I', header)
    body = _recv_exactly(sock, length)
    if body is None:
        return None
    return json.loads(body)


def _recv_exactly(sock: socket.socket, count: int) -> bytes | None:
    buf = b''
    while len(buf) < count:
        try:
            chunk = sock.recv(count - len(buf))
        except TimeoutError:
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


if __name__ == '__main__':
    raise SystemExit(main())
