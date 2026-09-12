"""Тесты формирования текста уведомления (intercom_send.py скила tmux)."""

import sys
from argparse import Namespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
import intercom_send as s


def notification(**kw):
    defaults = {
        'to': 'sess-1',
        'code': 0,
        'elapsed': None,
        'place': 'панель %56, окно sandbox:1',
        'command': None,
        'dump': None,
    }
    return Namespace(**{**defaults, **kw})


@pytest.mark.parametrize(
    'seconds,expected',
    [
        (0, '0 с'),
        (59, '59 с'),
        (60, '1 мин 0 с'),
        (92, '1 мин 32 с'),
        (3599, '59 мин 59 с'),
        (3600, '1 ч 0 мин'),
        (5430, '1 ч 30 мин'),
    ],
)
def test_when_duration_formatted_then_units_match_scale(seconds, expected):
    assert s._format_duration(seconds) == expected


def test_when_place_given_then_message_starts_with_it():
    text = s._compose_text(notification())
    assert text.splitlines()[0] == (
        'панель %56, окно sandbox:1: команда завершилась успешно.'
    )


@pytest.mark.parametrize(
    'code,expected_outcome',
    [
        (0, 'успешно'),
        (1, 'с кодом возврата 1'),
        (130, 'с кодом возврата 130'),
    ],
)
def test_when_exit_code_given_then_outcome_named(code, expected_outcome):
    text = s._compose_text(notification(code=code))
    assert f'команда завершилась {expected_outcome}.' in text


def test_when_elapsed_given_then_duration_included():
    text = s._compose_text(notification(elapsed=92))
    assert 'завершилась успешно за 1 мин 32 с.' in text


def test_when_command_given_then_quoted_on_own_line():
    text = s._compose_text(notification(command='cmake --build build -j 5'))
    assert text.splitlines()[-1] == '`cmake --build build -j 5`'


def test_when_dump_given_then_path_mentioned():
    text = s._compose_text(notification(dump='/tmp/out.log'))
    assert text.splitlines()[-1] == 'Вывод сохранён: `/tmp/out.log`'


@pytest.mark.parametrize(
    'argv,missing',
    [
        (['--to', 'sess-1', '--code', '0', '--command', 'true'], '--place'),
        (['--code', '0', '--place', 'панель %56'], '--to'),
        (['--to', 'sess-1', '--place', 'панель %56'], '--code'),
    ],
)
def test_when_required_argument_missing_then_argparse_error(
    monkeypatch, capsys, argv, missing
):
    monkeypatch.setattr(sys, 'argv', ['intercom_send.py'] + argv)
    with pytest.raises(SystemExit) as exc:
        s.main()
    assert exc.value.code == 2
    assert missing in capsys.readouterr().err
