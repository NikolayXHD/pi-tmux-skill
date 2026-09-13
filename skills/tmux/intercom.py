#!/usr/bin/env python3
"""Транспорт брокера pi-intercom: кадры, регистрация, отправка сообщения.

Протокол брокера: кадр — 4 байта длины и JSON, сначала регистрация
отправителя, затем само сообщение.

See SKILL.md (same directory) -- the skill these scripts implement.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import time
import uuid

AGENT_DIR = os.environ.get('PI_CODING_AGENT_DIR') or os.path.expanduser(
    '~/.pi/agent'
)
SOCKET_PATH = os.path.join(AGENT_DIR, 'intercom', 'broker.sock')
CONNECT_TIMEOUT = 5.0
REPLY_TIMEOUT = 5.0


class IntercomError(Exception):
    """Сообщение не доставлено: сеть, протокол или отказ брокера."""


def send(target: str, text: str) -> None:
    """Отправить сообщение сессии pi; IntercomError, если не доставлено."""
    try:
        reply = _exchange(target, text)
    except (OSError, ValueError) as e:
        raise IntercomError(str(e)) from e
    if reply.get('type') != 'delivered':
        raise IntercomError(f'брокер не доставил сообщение: {reply}')


def _exchange(target: str, text: str) -> dict:
    """Регистрация в брокере и отправка; возвращает ответ на отправку."""
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
            raise IntercomError(f'брокер не подтвердил регистрацию: {registered}')
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
            raise IntercomError('брокер не ответил на отправку')
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
