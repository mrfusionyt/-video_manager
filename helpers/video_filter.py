"""Сортировка и фильтрация видео по категориям."""


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

    if sort == 'date':
        videos.sort(key=lambda x: x.get('added', ''), reverse=True)
    elif sort == 'rating':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
    elif sort == 'duration_asc':
        videos.sort(key=lambda x: x.get('duration', 0))
    elif sort == 'duration_desc':
        videos.sort(key=lambda x: x.get('duration', 0), reverse=True)
    elif sort == 'orientation_vertical_first':
        videos.sort(key=lambda x: 0 if x.get('orientation', 'horizontal') == 'vertical' else 1)
    elif sort == 'orientation_horizontal_first':
        videos.sort(key=lambda x: 0 if x.get('orientation', 'horizontal') == 'horizontal' else 1)
    elif sort == 'top10':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
        videos = videos[:10]
    elif sort == 'top50':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
        videos = videos[:50]
    elif sort == 'top100':
        videos.sort(key=lambda x: x.get('rating', 0), reverse=True)
        videos = videos[:100]
    else:
        videos.sort(key=lambda x: x.get('added', ''), reverse=True)

    return videos