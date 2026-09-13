#!/usr/bin/env python3
"""Вывод панели для сообщений: урезание, подсказка, файл полного вывода.

See SKILL.md (same directory) -- the skill these scripts implement.
"""

from __future__ import annotations

import os
import shlex
from typing import NamedTuple

# Предел литерала вывода в сообщении; заголовок и хвост сообщения вне лимита.
LIMIT_CHARS = 500


class Truncation(NamedTuple):
    """Блок вывода и его связь с полным выводом.

    total -- строк в полном выводе; ranges -- диапазоны номеров показанных
    строк (None, если урезания не было); partial -- показанные строки
    урезаны посередине.
    """

    block: str
    total: int
    ranges: tuple[tuple[int, int], ...] | None
    partial: bool


def truncate(text: str, limit: int = LIMIT_CHARS) -> Truncation:
    """Урезать вывод до лимита: пары строк с концов, середина — маркером.

    Строки считаются по \\n, как sed. Маркеры входят в лимит, поэтому
    лимит предполагается заметно больше их длины — таков LIMIT_CHARS.
    """
    lines = text.rstrip('\n').split('\n') if text else []
    full = '\n'.join(lines)
    if len(full) <= limit:
        return Truncation(full, len(lines), None, False)
    if len(lines) == 1:
        return Truncation(_cut_line(lines[0], limit), 1, ((1, 1),), True)
    if not _fits(lines, 1, limit):
        return Truncation(
            _cut_pair(lines, limit),
            len(lines),
            ((1, 1), (len(lines), len(lines))),
            True,
        )
    kept = 1
    while kept + 1 <= len(lines) // 2 and _fits(lines, kept + 1, limit):
        kept += 1
    return Truncation(
        _sandwich(lines, kept),
        len(lines),
        ((1, kept), (len(lines) - kept + 1, len(lines))),
        False,
    )


def hint(truncation: Truncation, source: str) -> str | None:
    """Как читать показанные строки полного вывода (source); None без урезания."""
    if truncation.ranges is None:
        return None
    sed = ';'.join(
        f'{start},{end}p' if start != end else f'{start}p'
        for start, end in truncation.ranges
    )
    spans = ' и '.join(
        str(start) if start == end else f'{start}–{end}'
        for start, end in truncation.ranges
    )
    shown = sum(end - start + 1 for start, end in truncation.ranges)
    if truncation.partial:
        if shown == 1:
            head = f'Строка {spans} из {truncation.total} показана частично'
        else:
            head = f'Строки {spans} из {truncation.total} показаны частично'
    else:
        head = f'Показаны строки {spans} из {truncation.total}'
    return f'{head}: `sed -n \'{sed}\' {shlex.quote(source)}`'


def format_duration(seconds: int) -> str:
    """Длительность фразой: 59 с, 1 мин 32 с, 1 ч 30 мин."""
    if seconds < 60:
        return f'{seconds} с'
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes} мин {rest} с'
    hours, minutes = divmod(minutes, 60)
    return f'{hours} ч {minutes} мин'


def dump_path(dump_dir: str, pane: str) -> str:
    """Файл полного вывода панели: один у промежуточных и финального."""
    return os.path.join(dump_dir, f'{pane}.log')


def write_atomic(path: str, text: str) -> None:
    """Записать файл целиком: читатель видит старую версию или новую."""
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as handle:
            handle.write(text)
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def _fits(lines: list[str], kept: int, limit: int) -> bool:
    return len(_sandwich(lines, kept)) <= limit


def _sandwich(lines: list[str], kept: int) -> str:
    """Строки с обоих концов; вырезанная середина — маркером."""
    skipped = len(lines) - 2 * kept
    head = lines[:kept]
    if skipped:
        head = head + [_lines_marker(skipped)]
    return '\n'.join(head + lines[-kept:])


def _cut_pair(lines: list[str], limit: int) -> str:
    """Пара строк, каждая длиннее бюджета: урезаются середины самих строк."""
    skipped = len(lines) - 2
    marker = _lines_marker(skipped) if skipped else None
    extra = len(marker) + 1 if marker else 0
    left = (limit - extra - 1) // 2
    parts = [_cut_line(lines[0], left)]
    if marker:
        parts.append(marker)
    parts.append(_cut_line(lines[-1], limit - extra - 1 - left))
    return '\n'.join(parts)


def _cut_line(line: str, budget: int) -> str:
    """Строка не длиннее бюджета: середина вырезана маркером.

    Число вырезанных символов входит в длину маркера, поэтому оно
    подбирается неподвижной точкой; бюджет заметно больше маркера.
    """
    if len(line) <= budget:
        return line
    cut = len(line) - budget + len(_chars_marker(len(line)))
    while True:
        keep = budget - len(_chars_marker(cut))
        next_cut = len(line) - keep
        if next_cut == cut:
            break
        cut = next_cut
    keep = budget - len(_chars_marker(cut))
    head = (keep + 1) // 2
    tail = keep - head
    return line[:head] + _chars_marker(cut) + line[len(line) - tail:]


def _lines_marker(count: int) -> str:
    verb = 'вырезана' if _one(count) else 'вырезано'
    return f'[… {verb} {count} {_plural(count, "строка", "строки", "строк")} …]'


def _chars_marker(count: int) -> str:
    verb = 'вырезан' if _one(count) else 'вырезано'
    return f'[… {verb} {count} {_plural(count, "символ", "символа", "символов")} …]'


def _one(count: int) -> bool:
    return count % 10 == 1 and count % 100 != 11


def _plural(count: int, one: str, few: str, many: str) -> str:
    if _one(count):
        return one
    if count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        return few
    return many
