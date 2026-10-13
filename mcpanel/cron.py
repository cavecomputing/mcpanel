"""Cron lines, read as Linux's cron (cronie) reads a crontab's times: minute, hour, day of the month,
month and day of the week, each a comma-separated list of *, */step, N, A-B or A-B/step. Months and
days of the week may be named by their first three letters (jan, mon), Sunday is 0 or 7, and @hourly,
@daily, @weekly, @monthly and @yearly stand for the lines cron gives them.

Where cronie quietly accepts what it then never runs (an hour of 22-2, which it reads as no hour at
all) or reads differently from how it looks (5/15, a step after a single number, which it refuses),
parse() refuses with a message instead.
"""
import re
from datetime import timedelta

MACROS = {'@yearly': '0 0 1 1 *', '@annually': '0 0 1 1 *', '@monthly': '0 0 1 * *', '@weekly': '0 0 * * 0',
          '@daily': '0 0 * * *', '@midnight': '0 0 * * *', '@hourly': '0 * * * *'}
# Each field: its name in messages, its lowest and highest value, and the names that stand for values.
FIELDS = (('minute', 0, 59, ()), ('hour', 0, 23, ()), ('day of the month', 1, 31, ()),
          ('month', 1, 12, ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')),
          ('day of the week', 0, 7, ('sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat')))
NUMBER = re.compile(r'[0-9]+')
# How far ahead next_after() looks before deciding a line never matches (30 February): well past the
# rarest lines that do, such as the 1st of February when it's a Monday.
SEARCH_DAYS = 40 * 366


class Cron:
    """A parsed cron line. Like cronie, a day matches the day of the month OR the day of the week when
    both are restricted, and both when either field starts with * (so */2 counts as unrestricted)."""

    def __init__(self, minutes, hours, days, months, weekdays, day_star, weekday_star, wildcard):
        self.minutes, self.hours, self.days, self.months = sorted(minutes), sorted(hours), days, months
        self.weekdays = {day % 7 for day in weekdays}  # 7 is Sunday too
        self.day_star, self.weekday_star = day_star, weekday_star
        # Its minute or hour starts with *: every so often rather than at set times, which cron treats
        # differently when the clock changes for daylight saving (see restarts.due()).
        self.wildcard = wildcard

    def day_matches(self, date):
        if date.month not in self.months:
            return False
        day = date.day in self.days
        weekday = (date.weekday() + 1) % 7 in self.weekdays  # Python's Monday is 0, cron's Sunday
        return day and weekday if self.day_star or self.weekday_star else day or weekday

    def matches(self, when):
        """Whether a wall-clock time's minute is one of this line's."""
        return when.minute in self.minutes and when.hour in self.hours and self.day_matches(when)

    def next_after(self, when):
        """The first minute after when (a naive wall-clock time) that matches, or None if none ever does."""
        start = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
        day = start.replace(hour=0, minute=0)
        for _ in range(SEARCH_DAYS):
            if self.day_matches(day):
                for hour in self.hours:
                    for minute in self.minutes:
                        if (time := day.replace(hour=hour, minute=minute)) >= start:
                            return time
            day += timedelta(days=1)
        return None


def parse(text):
    """A Cron from a line like '0 4 * * *', or ValueError saying what is wrong with it."""
    line = text.strip() if isinstance(text, str) else ''
    if line.startswith('@'):
        if line.lower() not in MACROS:
            raise ValueError(f'{line} is not one of {", ".join(MACROS)}')
        line = MACROS[line.lower()]
    fields = line.split()
    if len(fields) != 5:
        raise ValueError('A schedule is five fields: minute, hour, day of the month, month and day of the week')
    values = [field_values(field, *spec) for field, spec in zip(fields, FIELDS)]
    return Cron(*values, day_star=fields[2].startswith('*'), weekday_star=fields[4].startswith('*'),
                wildcard=fields[0].startswith('*') or fields[1].startswith('*'))


def field_values(field, name, low, high, names):
    """The set of values one field stands for."""
    values = set()
    for item in field.split(','):
        span, slash, step = item.partition('/')
        if slash and not (NUMBER.fullmatch(step) and int(step) > 0):
            raise ValueError(f'The step in "{item}" ({name}) must be a whole number above 0')
        if span == '*':
            first, last = low, high
        elif '-' in span:
            first, _, last = span.partition('-')
            first, last = value(first, item, name, low, high, names), value(last, item, name, low, high, names)
            if names and names[0] == 'sun' and last == 0:
                last = 7  # fri-sun
            if first > last:
                raise ValueError(f'"{item}" ({name}) runs backwards: write the smaller end first')
        elif slash:
            raise ValueError(f'"{item}" ({name}): a step goes after * or a range, like */15 or 0-30/15')
        else:
            first = last = value(span, item, name, low, high, names)
        values.update(range(first, last + 1, int(step) if slash else 1))
    return values


def value(token, item, name, low, high, names):
    """One number, or a name standing for one, checked against the field's range."""
    if token.lower() in names:
        return names.index(token.lower()) + low
    if not NUMBER.fullmatch(token):
        raise ValueError(f'"{item}" is not a valid {name}')
    if not low <= int(token) <= high:
        raise ValueError(f'"{item}" is not a valid {name}: those go from {low} to {high}')
    return int(token)
