"""Тесты транспорта брокера pi-intercom (intercom.py скила tmux)."""

import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import intercom as i


def test_when_frames_written_then_read_back():
    left, right = socket.socketpair()
    with left, right:
        i._write_frame(left, {'type': 'register', 'session': {'name': 'x'}})
        i._write_frame(left, {'type': 'send', 'to': 'sess-1'})
        assert i._read_frame(right) == {
            'type': 'register',
            'session': {'name': 'x'},
        }
        assert i._read_frame(right) == {'type': 'send', 'to': 'sess-1'}


class BrokerStub:
    """Брокер на Unix-сокете: отвечает заготовками, пишет принятые кадры."""

    def __init__(self, path, replies):
        self.received = []
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(path))
        self.server.listen(1)
        self.replies = replies
        self.thread = threading.Thread(target=self._serve)
        self.thread.start()

    def _serve(self):
        conn, _ = self.server.accept()
        with conn:
            for reply in self.replies:
                self.received.append(i._read_frame(conn))
                i._write_frame(conn, reply)
        self.server.close()

    def join(self):
        self.thread.join(timeout=5)
        assert not self.thread.is_alive()


@pytest.fixture
def broker(tmp_path, monkeypatch):
    """Брокер-заглушка вместо настоящего сокета pi-intercom."""

    def start(replies):
        stub = BrokerStub(tmp_path / 'broker.sock', replies)
        monkeypatch.setattr(i, 'SOCKET_PATH', str(tmp_path / 'broker.sock'))
        return stub

    return start


def test_when_broker_delivers_then_message_and_registration_sent(broker):
    stub = broker(
        [{'type': 'registered', 'sessionId': 'x'}, {'type': 'delivered'}]
    )
    i.send('sess-1', 'привет')
    stub.join()
    register, message = stub.received
    assert register['type'] == 'register'
    assert register['session']['name'] == 'tmux-pane'
    assert message['type'] == 'send'
    assert message['to'] == 'sess-1'
    assert message['message']['content']['text'] == 'привет'


def test_when_registration_rejected_then_intercom_error(broker):
    stub = broker([{'type': 'error', 'error': 'nope'}])
    with pytest.raises(i.IntercomError, match='не подтвердил регистрацию'):
        i.send('sess-1', 'привет')
    stub.join()


def test_when_broker_refuses_then_intercom_error(broker):
    stub = broker(
        [
            {'type': 'registered', 'sessionId': 'x'},
            {
                'type': 'delivery_failed',
                'reason': 'Session not found',
                'code': 'E_TARGET_NOT_FOUND',
            },
        ]
    )
    with pytest.raises(i.IntercomError, match='не доставил'):
        i.send('sess-1', 'привет')
    stub.join()


def test_when_broker_socket_missing_then_intercom_error(tmp_path, monkeypatch):
    monkeypatch.setattr(i, 'SOCKET_PATH', str(tmp_path / 'absent.sock'))
    with pytest.raises(i.IntercomError):
        i.send('sess-1', 'привет')
