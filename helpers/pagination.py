"""
Компактный диапазон страниц для пагинации.

Пример:
    >>> paginate_range(1, 81)
    [1, 2, 3, 4, 5, '...', 81]
    >>> paginate_range(36, 81)
    [1, '...', 34, 35, 36, 37, 38, '...', 81]
    >>> paginate_range(79, 81)
    [1, '...', 77, 78, 79, 80, 81]
"""


def paginate_range(current, total, side=2, edge=5):
    """
    current: текущая страница (1-based)
    total:   всего страниц
    side:    сколько страниц показывать слева и справа от текущей
    edge:    размер «расширенного» блока у краёв (когда current близко к 1 или к total)

    Возвращает список: int | '...'
    """
    if total <= 0:
        return []
    if total <= 7:
        return list(range(1, total + 1))

    pages = set()
    pages.add(1)
    pages.add(total)

    # Окно вокруг текущей
    for i in range(current - side, current + side + 1):
        if 1 <= i <= total:
            pages.add(i)

    # У левого края — показываем больше первых
    if current <= edge:
        for i in range(1, min(edge, total) + 1):
            pages.add(i)

    # У правого края — показываем больше последних
    if current >= total - edge + 1:
        for i in range(max(1, total - edge + 1), total + 1):
            pages.add(i)

    sp = sorted(pages)

    result = []
    prev = 0
    for p in sp:
        if prev and p - prev > 1:
            result.append('...')
        result.append(p)
        prev = p
    return result