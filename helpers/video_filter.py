"""Сортировка и фильтрация видео по категориям."""

from config import video_quality_tag


def _quality_rank(video):
    """
    Числовой ранг качества: больше = лучше.
    Используется для сортировки quality_desc/quality_asc.
    """
    try:
        w = int(video.get('width', 0) or 0)
        h = int(video.get('height', 0) or 0)
    except (TypeError, ValueError):
        return 0
    return max(w, h)


def _is_low(video):
    return video_quality_tag(
        video.get('width', 0), video.get('height', 0)
    ) == 'low'


def _attach_quality(videos):
    for v in videos:
        try:
            v['quality_tag'] = video_quality_tag(
                v.get('width', 0), v.get('height', 0)
            )
        except Exception:
            v['quality_tag'] = None
    return videos


def process_videos(videos, category_ids, sort):
    if category_ids:
        for v in videos:
            v_cat_ids = [cat['id'] for cat in v.get('categories', [])]
            match_count = sum(1 for cid in category_ids if cid in v_cat_ids)
            v['match_count'] = match_count
        if len(category_ids) >= 6:
            min_match = 6
            videos = [v for v in videos if v.get('match_count', 0) >= min_match]
        else:
            max_match = max((v.get('match_count', 0) for v in videos), default=0)
            if max_match > 0:
                videos = [v for v in videos if v.get('match_count', 0) == max_match]
            else:
                videos = []

    _attach_quality(videos)

    # По умолчанию «low в конец» — стабильно, но не влияет на явные
    # quality_desc / quality_asc.
    def sort_stable(items, key, reverse=False):
        """
        Сортирует по key, но low-качество всегда едет в конец
        (кроме явных quality-сортировок).
        """
        # Сортируем: сначала не-low, потом low.
        # Внутри каждой группы — по key.
        non_low = [v for v in items if not _is_low(v)]
        low = [v for v in items if _is_low(v)]
        non_low.sort(key=key, reverse=reverse)
        low.sort(key=key, reverse=reverse)
        return non_low + low

    if sort == 'date':
        videos = sort_stable(videos, key=lambda x: x.get('added', ''), reverse=True)
    elif sort == 'rating':
        videos = sort_stable(videos, key=lambda x: x.get('rating', 0), reverse=True)
    elif sort == 'duration_asc':
        videos = sort_stable(videos, key=lambda x: x.get('duration', 0))
    elif sort == 'duration_desc':
        videos = sort_stable(videos, key=lambda x: x.get('duration', 0), reverse=True)
    elif sort == 'orientation_vertical_first':
        videos = sort_stable(
            videos,
            key=lambda x: 0 if x.get('orientation', 'horizontal') == 'vertical' else 1
        )
    elif sort == 'orientation_horizontal_first':
        videos = sort_stable(
            videos,
            key=lambda x: 0 if x.get('orientation', 'horizontal') == 'horizontal' else 1
        )
    elif sort == 'top10':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
        videos = videos[:10]
    elif sort == 'top50':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
        videos = videos[:50]
    elif sort == 'top100':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
        videos = videos[:100]
    elif sort == 'quality_desc':
        # 4K/FHD/HD сверху, low в самом низу
        videos.sort(key=_quality_rank, reverse=True)
    elif sort == 'quality_asc':
        # low сверху, 4K в самом низу (как есть, без переноса low в конец)
        videos.sort(key=_quality_rank)
    elif sort == 'filename_asc':
        videos.sort(key=lambda x: (x.get('filename') or '').lower())
    elif sort == 'filename_desc':
        videos.sort(key=lambda x: (x.get('filename') or '').lower(), reverse=True)
    else:
        videos = sort_stable(videos, key=lambda x: x.get('added', ''), reverse=True)

    return videos